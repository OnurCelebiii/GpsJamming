"""
Visualization module — dual-source GPS Jamming Detection.

Map layers
----------
1. ADS-B heatmap  (orange→red)   – airborne GPS disruption confidence
2. Cell heatmap   (blue→purple)  – ground-level GPS disruption (phone signal)
3. Merged heatmap (unified)      – composite confidence from both sources
4. Grid rectangles               – flagged cells with popup details
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import config
from src.detector import CellResult, DetectionReport

logger = logging.getLogger(__name__)

try:
    import folium
    from folium.plugins import HeatMap
    _FOLIUM_OK = True
except ImportError:
    _FOLIUM_OK = False
    logger.warning("folium not installed — interactive map disabled.")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    _MPL_OK = True
except ImportError:
    _MPL_OK = False
    logger.warning("matplotlib not installed — static charts disabled.")

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

_LEVEL_COLOR = {
    "CRITICAL": "#e74c3c",
    "ALERT":    "#e67e22",
    "WARNING":  "#f1c40f",
    "CLEAR":    "#2ecc71",
}

_LEVEL_FILL_OPACITY = {
    "CRITICAL": 0.55,
    "ALERT":    0.45,
    "WARNING":  0.35,
    "CLEAR":    0.10,
}

# Heatmap gradient colours
_ADSB_GRADIENT  = {0.0: "#001f3f", 0.4: "#0074D9", 0.65: "#FF851B", 0.8: "#FF4136", 1.0: "#85144b"}
_CELL_GRADIENT  = {0.0: "#0d0221", 0.4: "#6A0572", 0.65: "#C77DFF", 0.8: "#FF6B6B", 1.0: "#FFBE0B"}
_MERGE_GRADIENT = {0.0: "#03071e", 0.35: "#370617", 0.5: "#6a040f", 0.65: "#d00000", 0.8: "#e85d04", 1.0: "#ffba08"}


# ---------------------------------------------------------------------------
# Interactive map
# ---------------------------------------------------------------------------

def build_map(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.MAP_FILENAME}",
    show_all_cells: bool = False,
) -> Optional[str]:
    """
    Build a dual-source interactive Folium map and save as HTML.

    Layers:
      • ADS-B Heatmap  – confidence driven by MLAT ratio + altitude diff
      • Cell Heatmap   – confidence driven by phone GPS quality (OpenCelliD)
      • Merged Heatmap – composite (both sources)
      • Grid cells     – coloured rectangles for flagged zones
    """
    if not _FOLIUM_OK:
        logger.error("folium is not installed. Run: pip install folium")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB dark_matter")

    # Partition cells
    adsb_cells  = [c for c in report.cell_results if c.has_adsb]
    cell_cells  = [c for c in report.cell_results if c.has_cell]
    all_cells   = report.cell_results

    # ---- Layer 1: ADS-B heatmap ----------------------------------------
    adsb_heat_data = [
        [c.center_lat, c.center_lon, 0.6 * c.mlat_score + 0.4 * c.alt_score]
        for c in adsb_cells
        if (0.6 * c.mlat_score + 0.4 * c.alt_score) > 0
    ]
    if adsb_heat_data:
        fg_adsb = folium.FeatureGroup(name="ADS-B Heatmap (Airborne GPS)", show=True)
        HeatMap(
            adsb_heat_data,
            radius=20, blur=15, max_zoom=6,
            gradient=_ADSB_GRADIENT,
        ).add_to(fg_adsb)
        fg_adsb.add_to(m)

    # ---- Layer 2: Cell heatmap -----------------------------------------
    cell_heat_data = [
        [c.center_lat, c.center_lon, c.cell_score]
        for c in cell_cells
        if c.cell_score > 0
    ]
    if cell_heat_data:
        fg_cell = folium.FeatureGroup(name="Cell Heatmap (Ground GPS / Phones)", show=True)
        HeatMap(
            cell_heat_data,
            radius=20, blur=18, max_zoom=6,
            gradient=_CELL_GRADIENT,
        ).add_to(fg_cell)
        fg_cell.add_to(m)

    # ---- Layer 3: Merged heatmap ----------------------------------------
    merged_heat_data = [
        [c.center_lat, c.center_lon, c.confidence]
        for c in all_cells
        if c.confidence > 0
    ]
    if merged_heat_data:
        fg_merge = folium.FeatureGroup(name="MERGED Heatmap (Combined Confidence)", show=False)
        HeatMap(
            merged_heat_data,
            radius=25, blur=20, max_zoom=6,
            gradient=_MERGE_GRADIENT,
        ).add_to(fg_merge)
        fg_merge.add_to(m)

    # ---- Layer 4: flagged grid rectangles --------------------------------
    cells_to_draw = all_cells if show_all_cells else report.flagged_cells
    if cells_to_draw:
        fg_grid = folium.FeatureGroup(name="Flagged Grid Cells", show=True)
        for cell in cells_to_draw:
            color   = _LEVEL_COLOR[cell.level]
            opacity = _LEVEL_FILL_OPACITY[cell.level]
            folium.Rectangle(
                bounds=[[cell.lat_min, cell.lon_min], [cell.lat_max, cell.lon_max]],
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=opacity,
                weight=1.5,
                popup=folium.Popup(_cell_popup_html(cell), max_width=340),
                tooltip=(
                    f"{cell.level} [{cell.source_label}] "
                    f"conf={cell.confidence:.0%} "
                    f"MLAT={cell.mlat_ratio:.0%} "
                    f"Δalt={cell.mean_alt_diff_m:.0f}m "
                    f"CellRange={cell.mean_cell_range_m:.0f}m"
                ),
            ).add_to(fg_grid)
        fg_grid.add_to(m)

    # ---- Legend + layer control -----------------------------------------
    m.get_root().html.add_child(folium.Element(_build_legend_html(report)))
    folium.LayerControl(collapsed=False).add_to(m)

    m.save(output_path)
    logger.info("Interactive map saved → %s", output_path)
    return output_path


def _cell_popup_html(cell: CellResult) -> str:
    color = _LEVEL_COLOR[cell.level]
    adsb_block = ""
    if cell.has_adsb:
        adsb_block = f"""
      <b style="color:#0074D9">▶ ADS-B layer</b><br>
      &nbsp; Aircraft: {cell.total_aircraft}
        (ADS-B: {cell.adsb_count}, MLAT: {cell.mlat_count})<br>
      &nbsp; MLAT ratio: {cell.mlat_ratio:.0%}
        (score: {cell.mlat_score:.2%})<br>
      &nbsp; Mean Δalt: {cell.mean_alt_diff_m:.0f} m
        (score: {cell.alt_score:.2%})<br>
        """
    cell_block = ""
    if cell.has_cell:
        cell_block = f"""
      <b style="color:#C77DFF">▶ Cell layer (phones)</b><br>
      &nbsp; Towers: {cell.total_towers}<br>
      &nbsp; Mean GPS range: {cell.mean_cell_range_m:.0f} m
        (score: {cell.cell_score:.2%})<br>
      &nbsp; Max GPS range: {cell.max_cell_range_m:.0f} m<br>
        """
    return f"""
    <div style="font-family:monospace;font-size:12px;min-width:280px">
      <b style="color:{color};font-size:14px">{cell.level}</b>
      <span style="color:#aaa;font-size:11px"> [{cell.source_label}]</span>
      <hr style="margin:4px 0">
      <b>Confidence:</b> {cell.confidence:.2%}<br>
      <b>Center:</b> {cell.center_lat:+.3f}°, {cell.center_lon:+.3f}°<br>
      <hr style="margin:4px 0">
      {adsb_block}
      {cell_block}
    </div>
    """


def _build_legend_html(report: DetectionReport) -> str:
    ts = report.fetch_time or "unknown"
    level_items = "".join(
        f'<div><span style="background:{c};display:inline-block;'
        f'width:14px;height:14px;margin-right:6px;border-radius:2px"></span>{lv}</div>'
        for lv, c in _LEVEL_COLOR.items()
    )
    src_items = (
        '<div><span style="background:#0074D9;display:inline-block;'
        'width:14px;height:14px;margin-right:6px;border-radius:2px"></span>ADS-B (airborne)</div>'
        '<div><span style="background:#C77DFF;display:inline-block;'
        'width:14px;height:14px;margin-right:6px;border-radius:2px"></span>Cell/Phone (ground)</div>'
    )
    return f"""
    <div style="
        position:fixed;bottom:40px;right:10px;z-index:9999;
        background:rgba(0,0,0,0.80);color:#fff;
        padding:10px 14px;border-radius:6px;font-family:monospace;font-size:12px;
        min-width:190px">
      <b>GPS Jamming Detector</b><br>
      <span style="font-size:10px;color:#aaa">{ts}</span>
      <hr style="border-color:#555;margin:5px 0">
      <b style="font-size:11px">Sources</b><br>
      {src_items}
      <hr style="border-color:#555;margin:5px 0">
      <b style="font-size:11px">Levels</b><br>
      {level_items}
      <hr style="border-color:#555;margin:5px 0">
      Aircraft: {report.total_aircraft_analyzed}<br>
      Cell towers: {report.total_towers_analyzed}<br>
      Cells: {report.total_cells_analyzed}<br>
      Flagged: {len(report.flagged_cells)}
    </div>
    """


# ---------------------------------------------------------------------------
# Static charts
# ---------------------------------------------------------------------------

def build_analysis_charts(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.PLOT_FILENAME}",
) -> Optional[str]:
    """
    3×2 matplotlib figure:
      [0,0] Classification bar chart
      [0,1] MLAT ratio vs confidence (ADS-B cells)
      [1,0] Altitude discrepancy vs confidence (ADS-B cells)
      [1,1] Cell GPS range vs confidence (Cell cells)
      [2,0] Confidence distribution histogram
      [2,1] Source coverage map (scatter: ADS-B blue, Cell purple, both white)
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
    colors      = [_LEVEL_COLOR[lv] for lv in levels]
    lv_order    = ["CRITICAL", "ALERT", "WARNING", "CLEAR"]

    adsb_cells = [c for c in cells if c.has_adsb]
    cell_cells = [c for c in cells if c.has_cell]

    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.patch.set_facecolor("#0d0d1a")
    for ax in axes.flat:
        ax.set_facecolor("#12122a")
        ax.tick_params(colors="#ccc")
        ax.xaxis.label.set_color("#ccc")
        ax.yaxis.label.set_color("#ccc")
        ax.title.set_color("#eee")
        for spine in ax.spines.values():
            spine.set_edgecolor("#333")

    # [0,0] Bar chart
    ax = axes[0, 0]
    counts = [levels.count(lv) for lv in lv_order]
    bc = [_LEVEL_COLOR[lv] for lv in lv_order]
    bars = ax.bar(lv_order, counts, color=bc, edgecolor="#222", linewidth=0.8)
    for bar, cnt in zip(bars, counts):
        if cnt > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    str(cnt), ha="center", va="bottom", color="#eee", fontsize=11, fontweight="bold")
    ax.set_title("Cell Classification Summary", fontsize=12, fontweight="bold")
    ax.set_ylabel("Number of cells")
    ax.set_ylim(0, max(counts) * 1.25 if any(counts) else 5)

    # [0,1] MLAT ratio vs confidence
    ax = axes[0, 1]
    if adsb_cells:
        ax.scatter(
            [c.mlat_ratio for c in adsb_cells],
            [c.confidence for c in adsb_cells],
            c=[_LEVEL_COLOR[c.level] for c in adsb_cells],
            s=55, alpha=0.85, edgecolors="#222", linewidths=0.5,
        )
    ax.axhline(config.CONFIDENCE_WARN,     color="#f1c40f", lw=0.8, ls="--")
    ax.axhline(config.CONFIDENCE_ALERT,    color="#e67e22", lw=0.8, ls="--")
    ax.axhline(config.CONFIDENCE_CRITICAL, color="#e74c3c", lw=0.8, ls="--")
    ax.axvline(config.MLAT_RATIO_WARN,     color="#555",    lw=0.6, ls=":")
    ax.axvline(config.MLAT_RATIO_ALERT,    color="#555",    lw=0.6, ls=":")
    ax.set_xlabel("MLAT Ratio")
    ax.set_ylabel("Confidence")
    ax.set_title("ADS-B: MLAT Ratio vs Confidence", fontsize=12, fontweight="bold")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)

    # [1,0] Altitude diff vs confidence
    ax = axes[1, 0]
    if adsb_cells:
        ax.scatter(
            [c.mean_alt_diff_m for c in adsb_cells],
            [c.confidence for c in adsb_cells],
            c=[_LEVEL_COLOR[c.level] for c in adsb_cells],
            s=55, alpha=0.85, edgecolors="#222", linewidths=0.5,
        )
    ax.axvline(config.ALT_DIFF_WARN_M,  color="#f1c40f", lw=0.8, ls="--", label=f"Warn {config.ALT_DIFF_WARN_M}m")
    ax.axvline(config.ALT_DIFF_ALERT_M, color="#e74c3c", lw=0.8, ls="--", label=f"Alert {config.ALT_DIFF_ALERT_M}m")
    ax.set_xlabel("Mean |Geo − Baro| Altitude (m)")
    ax.set_ylabel("Confidence")
    ax.set_title("ADS-B: Altitude Discrepancy vs Confidence", fontsize=12, fontweight="bold")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#12122a", labelcolor="#ccc", framealpha=0.6)

    # [1,1] Cell GPS range vs confidence
    ax = axes[1, 1]
    if cell_cells:
        ax.scatter(
            [c.mean_cell_range_m for c in cell_cells],
            [c.confidence for c in cell_cells],
            c=[_LEVEL_COLOR[c.level] for c in cell_cells],
            s=55, alpha=0.85, marker="^", edgecolors="#222", linewidths=0.5,
        )
    ax.axvline(config.CELL_RANGE_NORMAL_M, color="#2ecc71", lw=0.8, ls="--", label=f"Normal <{config.CELL_RANGE_NORMAL_M}m")
    ax.axvline(config.CELL_RANGE_WARN_M,   color="#f1c40f", lw=0.8, ls="--", label=f"Warn {config.CELL_RANGE_WARN_M}m")
    ax.axvline(config.CELL_RANGE_ALERT_M,  color="#e74c3c", lw=0.8, ls="--", label=f"Alert {config.CELL_RANGE_ALERT_M}m")
    ax.set_xlabel("Mean Cell Tower GPS Range (m)")
    ax.set_ylabel("Confidence")
    ax.set_title("Cell/Phone: GPS Range vs Confidence", fontsize=12, fontweight="bold")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#12122a", labelcolor="#ccc", framealpha=0.6)

    # [2,0] Confidence histogram
    ax = axes[2, 0]
    bins = np.linspace(0, 1, 21)
    ax.hist(confidences, bins=bins, color="#3498db", edgecolor="#0d0d1a", linewidth=0.5, alpha=0.85)
    ax.axvline(config.CONFIDENCE_WARN,     color="#f1c40f", lw=1.0, ls="--")
    ax.axvline(config.CONFIDENCE_ALERT,    color="#e67e22", lw=1.0, ls="--")
    ax.axvline(config.CONFIDENCE_CRITICAL, color="#e74c3c", lw=1.0, ls="--")
    ax.set_xlabel("Confidence Score")
    ax.set_ylabel("Number of cells")
    ax.set_title("Confidence Distribution (all sources)", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 1)

    # [2,1] Source coverage scatter map
    ax = axes[2, 1]
    adsb_only = [c for c in cells if c.has_adsb and not c.has_cell]
    cell_only = [c for c in cells if c.has_cell and not c.has_adsb]
    both      = [c for c in cells if c.has_adsb and c.has_cell]
    if adsb_only:
        ax.scatter([c.center_lon for c in adsb_only], [c.center_lat for c in adsb_only],
                   s=8, color="#0074D9", alpha=0.6, label=f"ADS-B only ({len(adsb_only)})")
    if cell_only:
        ax.scatter([c.center_lon for c in cell_only], [c.center_lat for c in cell_only],
                   s=8, color="#C77DFF", alpha=0.6, marker="^", label=f"Cell only ({len(cell_only)})")
    if both:
        ax.scatter([c.center_lon for c in both], [c.center_lat for c in both],
                   s=12, color="#ffffff", alpha=0.9, marker="*", label=f"Both ({len(both)})")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.set_title("Data Source Coverage", fontsize=12, fontweight="bold")
    ax.legend(fontsize=8, facecolor="#12122a", labelcolor="#ccc", framealpha=0.7, markerscale=2)

    # Shared legend for levels
    patches = [mpatches.Patch(color=_LEVEL_COLOR[lv], label=lv) for lv in lv_order]
    fig.legend(handles=patches, loc="upper center", ncol=4,
               facecolor="#0d0d1a", labelcolor="#ccc", framealpha=0.8, fontsize=10,
               bbox_to_anchor=(0.5, 1.01))

    src_info = (
        f"ADS-B: {report.total_aircraft_analyzed} aircraft  |  "
        f"Cell towers: {report.total_towers_analyzed}"
    )
    fig.suptitle(
        f"GPS Jamming Detection Analysis — {report.fetch_time or ''}\n{src_info}",
        color="#eee", fontsize=13, fontweight="bold", y=1.04,
    )

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Analysis chart saved → %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Trend chart
# ---------------------------------------------------------------------------

def build_trend_chart(
    trend_df,
    output_path: str = f"{config.OUTPUT_DIR}/trend.png",
) -> Optional[str]:
    if not _MPL_OK:
        return None
    if trend_df is None or trend_df.empty:
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    flagged  = trend_df[trend_df["level"] != "CLEAR"]
    by_time  = flagged.groupby("fetch_time").agg(
        flagged_cells=("cell_id", "nunique"),
        max_confidence=("confidence", "max"),
    )

    fig, ax1 = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#0d0d1a")
    ax1.set_facecolor("#12122a")
    ax1.bar(by_time.index, by_time["flagged_cells"], color="#3498db", alpha=0.7, label="Flagged cells")
    ax1.set_ylabel("Flagged cells", color="#3498db")
    ax1.tick_params(axis="y", colors="#3498db")

    ax2 = ax1.twinx()
    ax2.plot(by_time.index, by_time["max_confidence"], color="#e74c3c", lw=2, marker="o", label="Max confidence")
    ax2.set_ylabel("Max confidence", color="#e74c3c")
    ax2.tick_params(axis="y", colors="#e74c3c")
    ax2.set_ylim(0, 1)

    for spine in ax1.spines.values():
        spine.set_edgecolor("#333")
    ax1.tick_params(colors="#ccc")
    ax1.set_title("GPS Jamming Trend", color="#eee", fontweight="bold")
    ax1.set_xlabel("Snapshot time")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, facecolor="#0d0d1a", labelcolor="#ccc", framealpha=0.8)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Trend chart saved → %s", output_path)
    return output_path
