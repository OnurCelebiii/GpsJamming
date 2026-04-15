"""
Visualization module for GPS Jamming Detection.

Provides:
- Interactive Folium map with colour-coded jamming zones
- Static matplotlib analysis charts (MLAT ratio, altitude discrepancy,
  confidence heatmap, time-series trend)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import config
from src.detector import CellResult, DetectionReport

logger = logging.getLogger(__name__)

# Deferred heavy imports so the module loads fast even without the libraries
try:
    import folium
    from folium.plugins import HeatMap
    _FOLIUM_OK = True
except ImportError:
    _FOLIUM_OK = False
    logger.warning("folium not installed — interactive map disabled.")

try:
    import matplotlib
    matplotlib.use("Agg")  # headless backend
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    _MPL_OK = True
except ImportError:
    _MPL_OK = False
    logger.warning("matplotlib not installed — static charts disabled.")


# ---------------------------------------------------------------------------
# Colour mapping
# ---------------------------------------------------------------------------

_LEVEL_COLOR_HEX = {
    "CRITICAL": "#e74c3c",   # red
    "ALERT":    "#e67e22",   # orange
    "WARNING":  "#f1c40f",   # yellow
    "CLEAR":    "#2ecc71",   # green
}

_LEVEL_FILL_OPACITY = {
    "CRITICAL": 0.55,
    "ALERT":    0.45,
    "WARNING":  0.35,
    "CLEAR":    0.10,
}


# ---------------------------------------------------------------------------
# Interactive map
# ---------------------------------------------------------------------------

def build_map(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.MAP_FILENAME}",
    show_all_cells: bool = False,
) -> Optional[str]:
    """
    Build and save an interactive Folium map.

    Parameters
    ----------
    report : DetectionReport from JammingDetector
    output_path : where to write the HTML file
    show_all_cells : if True, render CLEAR cells too (greyed-out rectangles)

    Returns
    -------
    str path of saved file, or None on failure.
    """
    if not _FOLIUM_OK:
        logger.error("folium is not installed. Run: pip install folium")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    m = folium.Map(
        location=[20, 0],
        zoom_start=2,
        tiles="CartoDB dark_matter",
    )

    # --- Heat-map layer (confidence scores) ---
    heat_data = [
        [c.center_lat, c.center_lon, c.confidence]
        for c in report.cell_results
        if c.confidence > 0
    ]
    if heat_data:
        HeatMap(
            heat_data,
            radius=25,
            blur=15,
            max_zoom=6,
            gradient={0.0: "blue", 0.4: "lime", 0.65: "yellow", 0.8: "orange", 1.0: "red"},
            name="Confidence heatmap",
        ).add_to(m)

    # --- Grid-cell rectangles ---
    cells_to_draw = (
        report.cell_results if show_all_cells else report.flagged_cells
    )

    for cell in cells_to_draw:
        color   = _LEVEL_COLOR_HEX[cell.level]
        opacity = _LEVEL_FILL_OPACITY[cell.level]

        popup_html = _cell_popup_html(cell)

        folium.Rectangle(
            bounds=[[cell.lat_min, cell.lon_min], [cell.lat_max, cell.lon_max]],
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=opacity,
            weight=1.5,
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=(
                f"{cell.level} | conf={cell.confidence:.0%} "
                f"| MLAT {cell.mlat_ratio:.0%} | Δalt {cell.mean_alt_diff_m:.0f}m"
            ),
        ).add_to(m)

    # --- Legend ---
    legend_html = _build_legend_html(report)
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- Layer control ---
    folium.LayerControl().add_to(m)

    m.save(output_path)
    logger.info("Interactive map saved → %s", output_path)
    return output_path


def _cell_popup_html(cell: CellResult) -> str:
    color = _LEVEL_COLOR_HEX[cell.level]
    return f"""
    <div style="font-family:monospace;font-size:12px;min-width:260px">
      <b style="color:{color};font-size:14px">{cell.level}</b>
      <hr style="margin:4px 0">
      <b>Confidence:</b> {cell.confidence:.2%}<br>
      <b>Center:</b> {cell.center_lat:+.3f}°, {cell.center_lon:+.3f}°<br>
      <b>Cell:</b> {cell.lat_min:.1f}–{cell.lat_max:.1f}°N,
                   {cell.lon_min:.1f}–{cell.lon_max:.1f}°E<br>
      <hr style="margin:4px 0">
      <b>Aircraft total:</b> {cell.total_aircraft}<br>
      <b>ADS-B (GPS):</b>   {cell.adsb_count}<br>
      <b>MLAT:</b>          {cell.mlat_count}
                            ({cell.mlat_ratio:.0%})<br>
      <b>Other:</b>         {cell.other_count}<br>
      <hr style="margin:4px 0">
      <b>MLAT score:</b>    {cell.mlat_score:.2%}<br>
      <b>Alt diff score:</b>{cell.alt_score:.2%}<br>
      <b>Mean Δalt:</b>     {cell.mean_alt_diff_m:.0f} m<br>
      <b>Max  Δalt:</b>     {cell.max_alt_diff_m:.0f} m<br>
    </div>
    """


def _build_legend_html(report: DetectionReport) -> str:
    ts = report.fetch_time or "unknown"
    items = "".join(
        f'<div><span style="background:{c};display:inline-block;'
        f'width:14px;height:14px;margin-right:6px;border-radius:2px"></span>{lvl}</div>'
        for lvl, c in _LEVEL_COLOR_HEX.items()
    )
    return f"""
    <div style="
        position:fixed;bottom:40px;right:10px;z-index:9999;
        background:rgba(0,0,0,0.75);color:#fff;
        padding:10px 14px;border-radius:6px;font-family:monospace;font-size:12px">
      <b>GPS Jamming Detector</b><br>
      <span style="font-size:10px;color:#aaa">{ts}</span><br><br>
      {items}
      <hr style="border-color:#555;margin:6px 0">
      Aircraft: {report.total_aircraft_analyzed}<br>
      Cells: {report.total_cells_analyzed}<br>
      Flagged: {len(report.flagged_cells)}
    </div>
    """


# ---------------------------------------------------------------------------
# Static charts (matplotlib)
# ---------------------------------------------------------------------------

def build_analysis_charts(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.PLOT_FILENAME}",
) -> Optional[str]:
    """
    Generate a 2×2 matplotlib figure with analysis charts and save as PNG.

    Panels:
      [0,0] Bar chart — cell counts by level
      [0,1] Scatter — MLAT ratio vs confidence, coloured by level
      [1,0] Scatter — mean altitude discrepancy vs confidence
      [1,1] Histogram — confidence score distribution
    """
    if not _MPL_OK:
        logger.error("matplotlib not installed.")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cells = report.cell_results
    if not cells:
        logger.warning("No cells to plot.")
        return None

    levels      = [c.level for c in cells]
    confidences = [c.confidence for c in cells]
    mlat_ratios = [c.mlat_ratio for c in cells]
    alt_diffs   = [c.mean_alt_diff_m for c in cells]
    colors      = [_LEVEL_COLOR_HEX[lv] for lv in levels]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.patch.set_facecolor("#1a1a2e")
    for ax in axes.flat:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#ccc")
        ax.xaxis.label.set_color("#ccc")
        ax.yaxis.label.set_color("#ccc")
        ax.title.set_color("#eee")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

    # --- Panel 0,0: cell counts by level ---
    ax = axes[0, 0]
    level_order = ["CRITICAL", "ALERT", "WARNING", "CLEAR"]
    counts = [levels.count(lv) for lv in level_order]
    bar_colors = [_LEVEL_COLOR_HEX[lv] for lv in level_order]
    bars = ax.bar(level_order, counts, color=bar_colors, edgecolor="#333", linewidth=0.8)
    for bar, cnt in zip(bars, counts):
        if cnt > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.3,
                str(cnt),
                ha="center", va="bottom", color="#eee", fontsize=11, fontweight="bold",
            )
    ax.set_title("Cell Classification Summary", fontsize=12, fontweight="bold")
    ax.set_ylabel("Number of cells")
    ax.set_ylim(0, max(counts) * 1.25 if any(counts) else 5)

    # --- Panel 0,1: MLAT ratio vs confidence ---
    ax = axes[0, 1]
    sc = ax.scatter(mlat_ratios, confidences, c=colors, s=60, alpha=0.8, edgecolors="#333", linewidths=0.5)
    ax.axhline(config.CONFIDENCE_WARN,     color="#f1c40f", lw=0.8, ls="--", label="Warning threshold")
    ax.axhline(config.CONFIDENCE_ALERT,    color="#e67e22", lw=0.8, ls="--", label="Alert threshold")
    ax.axhline(config.CONFIDENCE_CRITICAL, color="#e74c3c", lw=0.8, ls="--", label="Critical threshold")
    ax.axvline(config.MLAT_RATIO_WARN,     color="#aaa",    lw=0.6, ls=":")
    ax.axvline(config.MLAT_RATIO_ALERT,    color="#aaa",    lw=0.6, ls=":")
    ax.set_xlabel("MLAT Ratio (MLAT aircraft / total)")
    ax.set_ylabel("Composite Confidence Score")
    ax.set_title("MLAT Ratio vs Confidence", fontsize=12, fontweight="bold")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#1a1a2e", labelcolor="#ccc", framealpha=0.7)

    # --- Panel 1,0: altitude diff vs confidence ---
    ax = axes[1, 0]
    ax.scatter(alt_diffs, confidences, c=colors, s=60, alpha=0.8, edgecolors="#333", linewidths=0.5)
    ax.axvline(config.ALT_DIFF_WARN_M,  color="#f1c40f", lw=0.8, ls="--", label=f"Warn {config.ALT_DIFF_WARN_M}m")
    ax.axvline(config.ALT_DIFF_ALERT_M, color="#e74c3c", lw=0.8, ls="--", label=f"Alert {config.ALT_DIFF_ALERT_M}m")
    ax.set_xlabel("Mean |Geo − Baro| Altitude (m)")
    ax.set_ylabel("Composite Confidence Score")
    ax.set_title("Altitude Discrepancy vs Confidence", fontsize=12, fontweight="bold")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#1a1a2e", labelcolor="#ccc", framealpha=0.7)

    # --- Panel 1,1: confidence distribution ---
    ax = axes[1, 1]
    bins = np.linspace(0, 1, 21)
    ax.hist(confidences, bins=bins, color="#3498db", edgecolor="#1a1a2e", linewidth=0.5, alpha=0.85)
    ax.axvline(config.CONFIDENCE_WARN,     color="#f1c40f", lw=1.0, ls="--")
    ax.axvline(config.CONFIDENCE_ALERT,    color="#e67e22", lw=1.0, ls="--")
    ax.axvline(config.CONFIDENCE_CRITICAL, color="#e74c3c", lw=1.0, ls="--")
    ax.set_xlabel("Confidence Score")
    ax.set_ylabel("Number of cells")
    ax.set_title("Confidence Score Distribution", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 1)

    # Shared legend patches
    patches = [
        mpatches.Patch(color=_LEVEL_COLOR_HEX[lv], label=lv)
        for lv in level_order
    ]
    fig.legend(
        handles=patches, loc="upper center", ncol=4,
        facecolor="#1a1a2e", labelcolor="#ccc",
        framealpha=0.8, fontsize=10,
        bbox_to_anchor=(0.5, 1.01),
    )

    fig.suptitle(
        f"GPS Jamming Detection Analysis\n{report.fetch_time or ''}",
        color="#eee", fontsize=14, fontweight="bold", y=1.03,
    )

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Analysis chart saved → %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Trend chart (multiple snapshots)
# ---------------------------------------------------------------------------

def build_trend_chart(
    trend_df,
    output_path: str = f"{config.OUTPUT_DIR}/trend.png",
) -> Optional[str]:
    """
    Time-series chart of flagged cell count and max confidence over snapshots.

    Parameters
    ----------
    trend_df : output of analyzer.build_trend_dataframe()
    """
    if not _MPL_OK:
        return None
    if trend_df is None or trend_df.empty:
        logger.warning("No trend data to plot.")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    flagged = trend_df[trend_df["level"] != "CLEAR"]
    by_time = flagged.groupby("fetch_time").agg(
        flagged_cells=("cell_id", "nunique"),
        max_confidence=("confidence", "max"),
    )

    fig, ax1 = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax1.set_facecolor("#16213e")

    ax1.bar(by_time.index, by_time["flagged_cells"], color="#3498db", alpha=0.7, label="Flagged cells")
    ax1.set_ylabel("Flagged cells", color="#3498db")
    ax1.tick_params(axis="y", colors="#3498db")

    ax2 = ax1.twinx()
    ax2.plot(by_time.index, by_time["max_confidence"], color="#e74c3c", lw=2, marker="o", label="Max confidence")
    ax2.set_ylabel("Max confidence", color="#e74c3c")
    ax2.tick_params(axis="y", colors="#e74c3c")
    ax2.set_ylim(0, 1)

    for spine in ax1.spines.values():
        spine.set_edgecolor("#444")
    ax1.tick_params(colors="#ccc")
    ax1.xaxis.label.set_color("#ccc")

    ax1.set_title("GPS Jamming Trend Over Time", color="#eee", fontweight="bold")
    ax1.set_xlabel("Snapshot time")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, facecolor="#1a1a2e", labelcolor="#ccc", framealpha=0.8)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Trend chart saved → %s", output_path)
    return output_path
