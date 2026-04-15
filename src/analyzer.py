"""
Statistical analyzer for GPS jamming detection reports.

Provides:
- Temporal trend analysis across multiple snapshots
- Regional summary statistics
- JSON report serialization
- Console-friendly summary printing
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

import config
from src.detector import CellResult, DetectionReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------

def report_to_dict(report: DetectionReport) -> dict:
    """Convert a DetectionReport to a plain Python dict (JSON-serialisable)."""
    d = {
        "fetch_time": report.fetch_time,
        "summary": {
            "total_aircraft_analyzed": report.total_aircraft_analyzed,
            "total_towers_analyzed": report.total_towers_analyzed,
            "total_cells_analyzed": report.total_cells_analyzed,
            "clear_cells": report.clear_cells,
            "warning_cells": report.warning_cells,
            "alert_cells": report.alert_cells,
            "critical_cells": report.critical_cells,
            "highest_confidence": round(report.highest_confidence, 4),
        },
        "flagged_cells": [],
    }
    for cell in sorted(report.flagged_cells, key=lambda c: c.confidence, reverse=True):
        d["flagged_cells"].append({
            "cell_id": cell.cell_id,
            "source": cell.source_label,
            "level": cell.level,
            "confidence": round(cell.confidence, 4),
            "center_lat": round(cell.center_lat, 4),
            "center_lon": round(cell.center_lon, 4),
            "total_aircraft": cell.total_aircraft,
            "adsb_count": cell.adsb_count,
            "mlat_count": cell.mlat_count,
            "mlat_ratio": round(cell.mlat_ratio, 4),
            "mean_alt_diff_m": round(cell.mean_alt_diff_m, 1),
            "max_alt_diff_m": round(cell.max_alt_diff_m, 1),
            "mlat_score": round(cell.mlat_score, 4),
            "alt_score": round(cell.alt_score, 4),
            "total_towers": cell.total_towers,
            "mean_cell_range_m": round(cell.mean_cell_range_m, 1),
            "cell_score": round(cell.cell_score, 4),
            "affected_aircraft": cell.affected_aircraft[:20],
        })
    return d


def save_report(report: DetectionReport, output_dir: str = config.OUTPUT_DIR) -> Path:
    """Save detection report as a JSON file and return the path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(output_dir) / f"report_{ts}.json"
    path.write_text(
        json.dumps(report_to_dict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Report saved → %s", path)
    return path


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

_LEVEL_COLORS = {
    "CRITICAL": "\033[91m",   # bright red
    "ALERT":    "\033[93m",   # yellow
    "WARNING":  "\033[94m",   # blue
    "CLEAR":    "\033[92m",   # green
}
_RESET = "\033[0m"


def print_summary(report: DetectionReport, use_color: bool = True) -> None:
    """Print a human-readable summary to stdout."""
    def color(level: str, text: str) -> str:
        if use_color:
            return f"{_LEVEL_COLORS.get(level, '')}{text}{_RESET}"
        return text

    print("\n" + "=" * 70)
    print("  GPS JAMMING DETECTION REPORT  (ADS-B + Cell/Phone)")
    if report.fetch_time:
        print(f"  Snapshot time  : {report.fetch_time}")
    print("=" * 70)
    print(f"  Aircraft analyzed  : {report.total_aircraft_analyzed:>6}")
    print(f"  Cell towers        : {report.total_towers_analyzed:>6}")
    print(f"  Grid cells         : {report.total_cells_analyzed:>6}")
    print(f"  CLEAR              : {color('CLEAR',    str(report.clear_cells)):>6}")
    print(f"  WARNING            : {color('WARNING',  str(report.warning_cells)):>6}")
    print(f"  ALERT              : {color('ALERT',    str(report.alert_cells)):>6}")
    print(f"  CRITICAL           : {color('CRITICAL', str(report.critical_cells)):>6}")
    print(f"  Highest confidence : {report.highest_confidence:.2%}")
    print("=" * 70)

    if not report.flagged_cells:
        print(color("CLEAR", "  No jamming detected in this snapshot.\n"))
        return

    print(f"\n  Flagged zones ({len(report.flagged_cells)} cells):\n")
    for cell in sorted(report.flagged_cells, key=lambda c: c.confidence, reverse=True):
        badge = color(cell.level, f"[{cell.level:<8}]")
        src   = f"[{cell.source_label:<13}]"
        adsb_part = (
            f"mlat={cell.mlat_ratio:.0%}({cell.mlat_count}/{cell.total_aircraft}) "
            f"Δalt={cell.mean_alt_diff_m:.0f}m"
        ) if cell.has_adsb else ""
        cell_part = (
            f"CellRange={cell.mean_cell_range_m:.0f}m"
        ) if cell.has_cell else ""
        print(
            f"  {badge} {src} "
            f"({cell.center_lat:+.2f}, {cell.center_lon:+.2f})  "
            f"conf={cell.confidence:.2%}  "
            f"{adsb_part}  {cell_part}"
        )
    print()


# ---------------------------------------------------------------------------
# Temporal trend analysis (multiple snapshots)
# ---------------------------------------------------------------------------

def build_trend_dataframe(reports: Sequence[DetectionReport]) -> pd.DataFrame:
    """
    Convert a time-ordered list of DetectionReports into a tidy DataFrame
    suitable for time-series plotting.

    Each row = one flagged cell in one snapshot.
    """
    rows = []
    for rpt in reports:
        for cell in rpt.cell_results:
            rows.append({
                "fetch_time": rpt.fetch_time,
                "cell_id": cell.cell_id,
                "center_lat": cell.center_lat,
                "center_lon": cell.center_lon,
                "level": cell.level,
                "confidence": cell.confidence,
                "mlat_ratio": cell.mlat_ratio,
                "mean_alt_diff_m": cell.mean_alt_diff_m,
                "total_aircraft": cell.total_aircraft,
                "mlat_count": cell.mlat_count,
                "adsb_count": cell.adsb_count,
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["fetch_time"] = pd.to_datetime(df["fetch_time"], utc=True, errors="coerce")
    return df


def summarize_hotspots(
    reports: Sequence[DetectionReport],
    min_appearances: int = 2,
) -> pd.DataFrame:
    """
    Identify persistent jamming hotspots that appear across multiple snapshots.

    Parameters
    ----------
    reports : sequence of DetectionReport
    min_appearances : minimum number of snapshots in which a cell must be
                      flagged to be considered a persistent hotspot.

    Returns
    -------
    pd.DataFrame sorted by mean confidence descending.
    """
    trend = build_trend_dataframe(reports)
    if trend.empty:
        return pd.DataFrame()

    flagged = trend[trend["level"] != "CLEAR"]
    if flagged.empty:
        return pd.DataFrame()

    agg = (
        flagged.groupby("cell_id")
        .agg(
            appearances=("fetch_time", "nunique"),
            mean_confidence=("confidence", "mean"),
            max_confidence=("confidence", "max"),
            mean_mlat_ratio=("mlat_ratio", "mean"),
            mean_alt_diff_m=("mean_alt_diff_m", "mean"),
            center_lat=("center_lat", "first"),
            center_lon=("center_lon", "first"),
        )
        .reset_index()
    )

    hotspots = agg[agg["appearances"] >= min_appearances].sort_values(
        "mean_confidence", ascending=False
    )
    return hotspots
