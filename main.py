#!/usr/bin/env python3
"""
GPS Jamming Detection System — Main Entry Point
================================================

Dual-source, multi-snapshot GPS jamming and spoofing detection.

Detection indicators (5 total)
-------------------------------
1. Non-GPS ratio       (MLAT source=2 + ASTERIX source=1) / total
   High-altitude multiplier: non-GPS aircraft at cruise altitude
   are a far stronger jamming signal than near airports.

2. Altitude variance   std_dev(geo_alt − baro_alt) within each cell.
   Normal: all aircraft see the same geoid offset → low variance.
   GPS spoofing: some aircraft get wrong altitudes → high variance.
   Catches Israel/Iran-style SPOOFING that MLAT ratio misses.

3. Mean altitude diff  mean |geo_alt − baro_alt| with raised scale
   (200 m → warn, 2000 m → alert).  A 4000 m discrepancy now reaches
   ALERT on its own.

4. Position staleness  fraction of aircraft where
   last_contact − time_position > 30 s.
   ADS-B still transmitting but GPS position not updating = GPS lost.

5. Cell GPS range      OpenCelliD crowd-sourced GPS positioning error
   per cell tower.  Detects ground-level jamming where no aircraft fly.

Usage
-----
# Single global snapshot (3 merged, anonymous):
    python main.py

# Bounding box (Israel / Middle East):
    python main.py --bbox 28 25 42 55

# Bounding box (Eastern Europe / Baltic):
    python main.py --bbox 44 18 62 42

# Continuous monitoring every 60 s:
    python main.py --monitor --interval 60 --duration 7200

# Load cached snapshot:
    python main.py --from-cache data/opensky_20260415T041927Z.json

# With OpenCelliD cell layer:
    OPENCELLID_KEY=<token> python main.py

# Authenticated OpenSky (faster polling):
    OPENSKY_USER=user OPENSKY_PASS=pass python main.py
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
                     help="Load cached OpenSky JSON instead of live API.")
    src.add_argument("--monitor", action="store_true",
                     help="Continuously poll OpenSky.")

    p.add_argument("--bbox", nargs=4, type=float,
                   metavar=("LAT_MIN", "LON_MIN", "LAT_MAX", "LON_MAX"))
    p.add_argument("--snapshots", type=int, default=config.MULTI_SNAPSHOT_COUNT,
                   help=f"Snapshots to merge per run (default: {config.MULTI_SNAPSHOT_COUNT}).")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--duration", type=int, default=0)
    p.add_argument("--cell-deg", type=float, default=config.GRID_CELL_DEG,
                   help=f"Grid cell size in degrees (default: {config.GRID_CELL_DEG}).")
    p.add_argument("--min-aircraft", type=int, default=config.GRID_MIN_AIRCRAFT)
    p.add_argument("--no-map",   action="store_true")
    p.add_argument("--no-chart", action="store_true")
    p.add_argument("--show-all-cells", action="store_true")
    p.add_argument("--output-dir", default=config.OUTPUT_DIR)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def run_once(sky: OpenSkyFetcher, cell: CellFetcher,
             detector: JammingDetector, args: argparse.Namespace):
    import pandas as pd

    # ADS-B: merge multiple snapshots for stability
    if args.from_cache:
        adsb_df = OpenSkyFetcher.load_cached(args.from_cache)
        snapshots = 1
    else:
        bbox = tuple(args.bbox) if args.bbox else None
        adsb_df = sky.fetch_multi(n=args.snapshots, bbox=bbox)
        snapshots = args.snapshots

    if adsb_df.empty:
        logger.error("No ADS-B data — check network or OpenSky status.")
        from src.detector import DetectionReport
        return DetectionReport()

    # Cell towers
    cell_df = pd.DataFrame()
    if cell.available:
        la1, lo1, la2, lo2 = args.bbox if args.bbox else (-60, -180, 75, 180)
        cell_df = cell.fetch_bbox(la1, lo1, la2, lo2)

    report = detector.analyze(
        adsb_df,
        cell_df=cell_df if not cell_df.empty else None,
        snapshots_merged=snapshots,
    )
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
# Monitoring loop
# ---------------------------------------------------------------------------

def run_monitor(sky: OpenSkyFetcher, cell: CellFetcher,
                detector: JammingDetector, args: argparse.Namespace):
    reports = []
    start   = time.time()
    poll    = 0
    cell_df = None

    while True:
        poll += 1
        logger.info("--- Poll #%d ---", poll)

        bbox = tuple(args.bbox) if args.bbox else None
        adsb_df = sky.fetch_multi(n=args.snapshots, bbox=bbox)

        if cell.available and (cell_df is None or poll % 30 == 0):
            la1, lo1, la2, lo2 = args.bbox if args.bbox else (-60, -180, 75, 180)
            cell_df = cell.fetch_bbox(la1, lo1, la2, lo2)

        if not adsb_df.empty:
            report = detector.analyze(
                adsb_df,
                cell_df=cell_df,
                snapshots_merged=args.snapshots,
            )
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
                hs = summarize_hotspots(reports, min_appearances=2)
                if not hs.empty:
                    logger.info("Persistent hotspots:\n%s", hs.to_string(index=False))

        elapsed = time.time() - start
        if args.duration and elapsed >= args.duration:
            break

        wait = max(sky._interval * args.snapshots, args.interval)
        logger.info("Next poll in %ds …", int(wait))
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = _build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    sky      = OpenSkyFetcher(username=os.environ.get("OPENSKY_USER"),
                               password=os.environ.get("OPENSKY_PASS"))
    cell     = CellFetcher(api_key=os.environ.get("OPENCELLID_KEY"))
    detector = JammingDetector(cell_deg=args.cell_deg, min_aircraft=args.min_aircraft)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if args.monitor:
        run_monitor(sky, cell, detector, args)
    else:
        run_once(sky, cell, detector, args)


if __name__ == "__main__":
    main()
