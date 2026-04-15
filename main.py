#!/usr/bin/env python3
"""
GPS Jamming Detection System — Main Entry Point
================================================

Uses real ADS-B data from the OpenSky Network to detect GPS jamming worldwide.

Detection methodology
---------------------
1. MLAT-ratio indicator
   Aircraft that lose GPS lock switch from ADS-B (position_source=0) to
   Multilateration (position_source=2, "MLAT").  A geographic grid cell with
   an abnormally high fraction of MLAT aircraft indicates GPS disruption.

2. Barometric / geometric altitude discrepancy
   Jamming corrupts the GNSS-derived geometric altitude while barometric
   altitude (pressure-based) remains unaffected.  A large difference between
   the two is a second independent indicator.

3. Composite confidence score
   Both indicators are normalised and combined.  Cells are classified as
   CLEAR / WARNING / ALERT / CRITICAL.

Usage examples
--------------
# Single live snapshot (anonymous OpenSky access):
    python main.py

# Continuous monitoring every 60 s for 1 hour:
    python main.py --monitor --interval 60 --duration 3600

# Restrict to a bounding box (e.g., Middle East):
    python main.py --bbox 20 25 45 60

# Use cached JSON snapshot instead of live API:
    python main.py --from-cache data/opensky_20260414T120000Z.json

# Authenticated (higher rate limit):
    OPENSKY_USER=myuser OPENSKY_PASS=mypass python main.py

# Skip generating output files:
    python main.py --no-map --no-chart
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import config

# ---------------------------------------------------------------------------
# Logging setup (must happen before any src imports so handlers are ready)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger("gpsjam")

from src.fetcher import OpenSkyFetcher
from src.detector import JammingDetector
from src.analyzer import print_summary, save_report, build_trend_dataframe, summarize_hotspots
from src.visualizer import build_map, build_analysis_charts, build_trend_chart


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="GPS Jamming Detection via OpenSky Network ADS-B data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage examples")[1] if "Usage examples" in __doc__ else "",
    )

    src_group = p.add_mutually_exclusive_group()
    src_group.add_argument(
        "--from-cache",
        metavar="PATH",
        help="Load a previously cached OpenSky JSON snapshot instead of live API.",
    )
    src_group.add_argument(
        "--monitor",
        action="store_true",
        help="Continuously poll OpenSky and accumulate trend data.",
    )

    p.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("LAT_MIN", "LON_MIN", "LAT_MAX", "LON_MAX"),
        help="Restrict analysis to a geographic bounding box.",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds for --monitor mode (default: 60).",
    )
    p.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Total monitoring duration in seconds, 0 = run forever (default: 0).",
    )
    p.add_argument(
        "--cell-deg",
        type=float,
        default=config.GRID_CELL_DEG,
        help=f"Grid cell size in degrees (default: {config.GRID_CELL_DEG}).",
    )
    p.add_argument(
        "--min-aircraft",
        type=int,
        default=config.GRID_MIN_AIRCRAFT,
        help=f"Minimum aircraft per cell to run analysis (default: {config.GRID_MIN_AIRCRAFT}).",
    )
    p.add_argument(
        "--no-map",
        action="store_true",
        help="Skip generating the interactive HTML map.",
    )
    p.add_argument(
        "--no-chart",
        action="store_true",
        help="Skip generating the static analysis PNG chart.",
    )
    p.add_argument(
        "--show-all-cells",
        action="store_true",
        help="Draw CLEAR cells on the map too (can be slow for global views).",
    )
    p.add_argument(
        "--output-dir",
        default=config.OUTPUT_DIR,
        help=f"Directory for output files (default: {config.OUTPUT_DIR}).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return p


# ---------------------------------------------------------------------------
# Single-snapshot run
# ---------------------------------------------------------------------------

def run_once(
    fetcher: OpenSkyFetcher,
    detector: JammingDetector,
    args: argparse.Namespace,
) -> "DetectionReport":
    """Fetch one snapshot, detect, visualise, and return the report."""
    import pandas as pd

    if args.from_cache:
        df = OpenSkyFetcher.load_cached(args.from_cache)
    else:
        bbox = tuple(args.bbox) if args.bbox else None
        df = fetcher.fetch_and_cache(bbox=bbox)

    if df.empty:
        logger.error("No data received — check your network or OpenSky availability.")
        from src.detector import DetectionReport
        return DetectionReport()

    report = detector.analyze(df)
    print_summary(report)
    save_report(report, output_dir=args.output_dir)

    if not args.no_map:
        map_path = str(Path(args.output_dir) / config.MAP_FILENAME)
        build_map(report, output_path=map_path, show_all_cells=args.show_all_cells)

    if not args.no_chart:
        chart_path = str(Path(args.output_dir) / config.PLOT_FILENAME)
        build_analysis_charts(report, output_path=chart_path)

    return report


# ---------------------------------------------------------------------------
# Continuous monitoring
# ---------------------------------------------------------------------------

def run_monitor(
    fetcher: OpenSkyFetcher,
    detector: JammingDetector,
    args: argparse.Namespace,
) -> None:
    """Poll OpenSky repeatedly, accumulating trend data."""
    reports = []
    start_ts = time.time()
    poll_num = 0

    logger.info(
        "Monitoring started (interval=%ds, duration=%ds)",
        args.interval,
        args.duration if args.duration else 0,
    )

    while True:
        poll_num += 1
        logger.info("--- Poll #%d ---", poll_num)

        bbox = tuple(args.bbox) if args.bbox else None
        df = fetcher.fetch_and_cache(bbox=bbox)

        if not df.empty:
            report = detector.analyze(df)
            print_summary(report)
            save_report(report, output_dir=args.output_dir)
            reports.append(report)

            # Regenerate map and charts on every poll
            if not args.no_map:
                map_path = str(Path(args.output_dir) / config.MAP_FILENAME)
                build_map(report, output_path=map_path, show_all_cells=args.show_all_cells)

            if not args.no_chart:
                chart_path = str(Path(args.output_dir) / config.PLOT_FILENAME)
                build_analysis_charts(report, output_path=chart_path)

            # Trend chart (requires ≥2 snapshots)
            if len(reports) >= 2 and not args.no_chart:
                trend_df = build_trend_dataframe(reports)
                trend_path = str(Path(args.output_dir) / "trend.png")
                build_trend_chart(trend_df, output_path=trend_path)

                hotspots = summarize_hotspots(reports, min_appearances=2)
                if not hotspots.empty:
                    logger.info("Persistent hotspots detected:\n%s", hotspots.to_string(index=False))

        elapsed = time.time() - start_ts
        if args.duration and elapsed >= args.duration:
            logger.info("Duration limit reached (%ds). Stopping.", args.duration)
            break

        wait = max(0, args.interval - (time.time() - start_ts - elapsed + elapsed))
        # Respect OpenSky rate limit: wait at least the configured minimum
        wait = max(wait, fetcher._interval)
        logger.info("Next poll in %ds …", int(wait))
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Credentials from environment variables (never hard-code)
    username = os.environ.get("OPENSKY_USER")
    password = os.environ.get("OPENSKY_PASS")

    fetcher  = OpenSkyFetcher(username=username, password=password)
    detector = JammingDetector(cell_deg=args.cell_deg, min_aircraft=args.min_aircraft)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if args.monitor:
        run_monitor(fetcher, detector, args)
    else:
        run_once(fetcher, detector, args)


if __name__ == "__main__":
    main()
