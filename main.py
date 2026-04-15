#!/usr/bin/env python3
"""
GPS Jamming Detection System — Main Entry Point
================================================

Dual-source detection: ADS-B (OpenSky Network) + Cell towers (OpenCelliD).

Detection methodology
---------------------
1. MLAT-ratio indicator (ADS-B)
   Aircraft losing GPS lock switch from ADS-B (position_source=0) to MLAT
   (position_source=2).  High MLAT fraction in a grid cell signals airborne
   GPS disruption.

2. Barometric / geometric altitude discrepancy (ADS-B)
   Jamming corrupts GNSS-derived geo_altitude while baro_altitude (pressure)
   stays stable.  A large |geo − baro| is a second airborne indicator.

3. Cell tower GPS range indicator (OpenCelliD / phones)
   OpenCelliD stores, per cell tower, the GPS positioning error of the phones
   that crowd-sourced it.  High average `range` in a grid cell = poor phone
   GPS = potential ground-level jamming.  This fills blind spots where no
   aircraft fly (coastlines, terrain, restricted airspace).

Composite confidence = 42 % MLAT + 28 % alt + 30 % cell  (when both available)

Usage examples
--------------
# ADS-B only (no OpenCelliD key):
    python main.py

# ADS-B + cell towers (requires free OpenCelliD key):
    OPENCELLID_KEY=mytoken python main.py

# Restrict to a bounding box (Middle East):
    OPENCELLID_KEY=mytoken python main.py --bbox 28 25 42 55

# Continuous monitoring every 60 s for 2 hours:
    OPENCELLID_KEY=mytoken python main.py --monitor --interval 60 --duration 7200

# Authenticated OpenSky (higher rate limit):
    OPENSKY_USER=user OPENSKY_PASS=pass python main.py

# Load cached ADS-B snapshot:
    python main.py --from-cache data/opensky_20260415T041927Z.json
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger("gpsjam")

from src.fetcher      import OpenSkyFetcher
from src.cell_fetcher import CellFetcher
from src.detector     import JammingDetector
from src.analyzer     import print_summary, save_report, build_trend_dataframe, summarize_hotspots
from src.visualizer   import build_map, build_analysis_charts, build_trend_chart


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="GPS Jamming Detection — ADS-B (OpenSky) + Cell/Phone (OpenCelliD)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--from-cache", metavar="PATH",
                     help="Load cached OpenSky JSON snapshot instead of live API.")
    src.add_argument("--monitor", action="store_true",
                     help="Continuously poll OpenSky and accumulate trend data.")

    p.add_argument("--bbox", nargs=4, type=float,
                   metavar=("LAT_MIN", "LON_MIN", "LAT_MAX", "LON_MAX"),
                   help="Restrict analysis to a geographic bounding box.")
    p.add_argument("--interval", type=int, default=60,
                   help="Polling interval in seconds for --monitor (default: 60).")
    p.add_argument("--duration", type=int, default=0,
                   help="Total monitoring duration in seconds, 0=forever (default: 0).")
    p.add_argument("--cell-deg", type=float, default=config.GRID_CELL_DEG,
                   help=f"Grid cell size in degrees (default: {config.GRID_CELL_DEG}).")
    p.add_argument("--min-aircraft", type=int, default=config.GRID_MIN_AIRCRAFT,
                   help=f"Min aircraft per cell (default: {config.GRID_MIN_AIRCRAFT}).")
    p.add_argument("--no-map",   action="store_true", help="Skip HTML map.")
    p.add_argument("--no-chart", action="store_true", help="Skip PNG chart.")
    p.add_argument("--show-all-cells", action="store_true",
                   help="Draw CLEAR cells on the map too.")
    p.add_argument("--output-dir", default=config.OUTPUT_DIR,
                   help=f"Output directory (default: {config.OUTPUT_DIR}).")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    return p


# ---------------------------------------------------------------------------
# Single-snapshot run
# ---------------------------------------------------------------------------

def run_once(
    sky_fetcher: OpenSkyFetcher,
    cell_fetcher: CellFetcher,
    detector: JammingDetector,
    args: argparse.Namespace,
) -> "DetectionReport":
    import pandas as pd

    # ADS-B
    if args.from_cache:
        adsb_df = OpenSkyFetcher.load_cached(args.from_cache)
    else:
        bbox = tuple(args.bbox) if args.bbox else None
        adsb_df = sky_fetcher.fetch_and_cache(bbox=bbox)

    if adsb_df.empty:
        logger.error("No ADS-B data received.")
        from src.detector import DetectionReport
        return DetectionReport()

    # Cell towers
    cell_df = pd.DataFrame()
    if cell_fetcher.available:
        if args.bbox:
            la1, lo1, la2, lo2 = args.bbox
        else:
            # Global: sample a coarse grid (keep API calls reasonable)
            la1, lo1, la2, lo2 = -60.0, -180.0, 75.0, 180.0
        cell_df = cell_fetcher.fetch_bbox(la1, lo1, la2, lo2)
        if not cell_df.empty:
            logger.info("Cell towers loaded: %d", len(cell_df))
    else:
        logger.info("Cell layer disabled (set OPENCELLID_KEY to enable).")

    report = detector.analyze(adsb_df, cell_df=cell_df if not cell_df.empty else None)
    print_summary(report)
    save_report(report, output_dir=args.output_dir)

    if not args.no_map:
        build_map(report,
                  output_path=str(Path(args.output_dir) / config.MAP_FILENAME),
                  show_all_cells=args.show_all_cells)
    if not args.no_chart:
        build_analysis_charts(report,
                               output_path=str(Path(args.output_dir) / config.PLOT_FILENAME))
    return report


# ---------------------------------------------------------------------------
# Continuous monitoring
# ---------------------------------------------------------------------------

def run_monitor(
    sky_fetcher: OpenSkyFetcher,
    cell_fetcher: CellFetcher,
    detector: JammingDetector,
    args: argparse.Namespace,
) -> None:
    reports = []
    start_ts = time.time()
    poll_num = 0

    logger.info("Monitoring started (interval=%ds, duration=%ds)",
                args.interval, args.duration)

    # Load cell data once per session (static dataset — refresh every 30 polls)
    cell_df = None
    cell_refresh_counter = 0

    while True:
        poll_num += 1
        logger.info("--- Poll #%d ---", poll_num)

        bbox = tuple(args.bbox) if args.bbox else None
        adsb_df = sky_fetcher.fetch_and_cache(bbox=bbox)

        # Refresh cell data every 30 polls (OpenCelliD data changes slowly)
        if cell_fetcher.available and (cell_df is None or cell_refresh_counter >= 30):
            la1, lo1, la2, lo2 = (args.bbox if args.bbox else (-60, -180, 75, 180))
            if args.bbox:
                la1, lo1, la2, lo2 = args.bbox
            cell_df = cell_fetcher.fetch_bbox(la1, lo1, la2, lo2)
            cell_refresh_counter = 0
        cell_refresh_counter += 1

        if not adsb_df.empty:
            report = detector.analyze(adsb_df, cell_df=cell_df)
            print_summary(report)
            save_report(report, output_dir=args.output_dir)
            reports.append(report)

            if not args.no_map:
                build_map(report,
                          output_path=str(Path(args.output_dir) / config.MAP_FILENAME),
                          show_all_cells=args.show_all_cells)
            if not args.no_chart:
                build_analysis_charts(report,
                                       output_path=str(Path(args.output_dir) / config.PLOT_FILENAME))
            if len(reports) >= 2 and not args.no_chart:
                trend_df = build_trend_dataframe(reports)
                build_trend_chart(trend_df,
                                  output_path=str(Path(args.output_dir) / "trend.png"))
                hotspots = summarize_hotspots(reports, min_appearances=2)
                if not hotspots.empty:
                    logger.info("Persistent hotspots:\n%s", hotspots.to_string(index=False))

        elapsed = time.time() - start_ts
        if args.duration and elapsed >= args.duration:
            logger.info("Duration limit reached. Stopping.")
            break

        wait = max(sky_fetcher._interval, args.interval)
        logger.info("Next poll in %ds …", int(wait))
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    sky_fetcher  = OpenSkyFetcher(
        username=os.environ.get("OPENSKY_USER"),
        password=os.environ.get("OPENSKY_PASS"),
    )
    cell_fetcher = CellFetcher(api_key=os.environ.get("OPENCELLID_KEY"))
    detector     = JammingDetector(cell_deg=args.cell_deg, min_aircraft=args.min_aircraft)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if args.monitor:
        run_monitor(sky_fetcher, cell_fetcher, detector, args)
    else:
        run_once(sky_fetcher, cell_fetcher, detector, args)


if __name__ == "__main__":
    main()
