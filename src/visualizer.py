"""
Visualization module — GPS Jamming Detection (commercial-grade map).

Map layers (toggle in the layer control panel):
  1. Live Detection Heatmap  — composite confidence from ADS-B + cell data
  2. Non-GPS Heatmap         — MLAT + ASTERIX ratio layer
  3. Altitude Variance       — GPS spoofing indicator
  4. Cell/Phone Heatmap      — OpenCelliD ground-level GPS quality
  5. Flagged Grid Cells      — coloured rectangles with full detail popups
  6. Known Jamming Zones     — documented interference regions (static)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Sequence

import config
from src.detector import CellResult, DetectionReport
from src.known_zones import get_geojson_feature_collection, KNOWN_ZONES

logger = logging.getLogger(__name__)

try:
    import folium
    from folium.plugins import HeatMap, MiniMap
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
    logger.warning("matplotlib not installed — charts disabled.")

# ---------------------------------------------------------------------------
# Colour mappings
# ---------------------------------------------------------------------------

_LEVEL_COLOR = {
    "CRITICAL": "#e74c3c",
    "ALERT":    "#e67e22",
    "WARNING":  "#f1c40f",
    "CLEAR":    "#27ae60",
}
_LEVEL_FILL_OPACITY = {
    "CRITICAL": 0.65,
    "ALERT":    0.50,
    "WARNING":  0.38,
    "CLEAR":    0.08,
}
_KNOWN_SEVERITY_COLOR = {
    "CRITICAL": "#ff073a",
    "ALERT":    "#ff6b35",
    "WARNING":  "#ffd166",
}
_HEATMAP_COMPOSITE = {0.0: "#03071e", 0.25: "#370617", 0.40: "#6a040f",
                       0.55: "#d00000", 0.75: "#e85d04", 1.0: "#ffba08"}
_HEATMAP_NONGPS    = {0.0: "#00214d", 0.25: "#005792", 0.40: "#00a8e8",
                       0.65: "#ff595e", 1.0: "#ffca3a"}
_HEATMAP_ALTVAR    = {0.0: "#10002b", 0.25: "#5a189a", 0.50: "#c77dff",
                       0.75: "#ff6b6b", 1.0: "#ffd60a"}
_HEATMAP_CELL      = {0.0: "#0d0221", 0.35: "#6a0572", 0.65: "#c77dff",
                       0.85: "#ff6b6b", 1.0: "#ffbe0b"}


# ---------------------------------------------------------------------------
# Interactive map
# ---------------------------------------------------------------------------

def build_map(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.MAP_FILENAME}",
    show_all_cells: bool = False,
) -> Optional[str]:
    if not _FOLIUM_OK:
        logger.error("folium not installed. pip install folium")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    m = folium.Map(location=[30, 25], zoom_start=3, tiles="CartoDB dark_matter")
    MiniMap(tile_layer="CartoDB dark_matter", toggle_display=True).add_to(m)

    cells = report.cell_results
    adsb_cells = [c for c in cells if c.has_adsb]
    cell_cells = [c for c in cells if c.has_cell]

    # ---- Layer 1: Composite heatmap (main signal) -----------------------
    heat_composite = [
        [c.center_lat, c.center_lon, c.confidence]
        for c in cells if c.confidence > 0.01
    ]
    if heat_composite:
        fg = folium.FeatureGroup(name="Live Detection Heatmap (Composite)", show=True)
        HeatMap(heat_composite, radius=22, blur=18, max_zoom=7,
                gradient=_HEATMAP_COMPOSITE).add_to(fg)
        fg.add_to(m)

    # ---- Layer 2: Non-GPS ratio heatmap ---------------------------------
    heat_nongps = [
        [c.center_lat, c.center_lon, c.non_gps_score]
        for c in adsb_cells if c.non_gps_score > 0.01
    ]
    if heat_nongps:
        fg = folium.FeatureGroup(name="Non-GPS Ratio (MLAT + ASTERIX)", show=False)
        HeatMap(heat_nongps, radius=20, blur=15, max_zoom=7,
                gradient=_HEATMAP_NONGPS).add_to(fg)
        fg.add_to(m)

    # ---- Layer 3: Altitude variance heatmap (spoofing indicator) --------
    heat_altvar = [
        [c.center_lat, c.center_lon, c.alt_variance_score]
        for c in adsb_cells if c.alt_variance_score > 0.01
    ]
    if heat_altvar:
        fg = folium.FeatureGroup(name="Altitude Variance (GPS Spoofing)", show=False)
        HeatMap(heat_altvar, radius=20, blur=15, max_zoom=7,
                gradient=_HEATMAP_ALTVAR).add_to(fg)
        fg.add_to(m)

    # ---- Layer 4: Cell/phone GPS quality heatmap ------------------------
    heat_cell = [
        [c.center_lat, c.center_lon, c.cell_score]
        for c in cell_cells if c.cell_score > 0.01
    ]
    if heat_cell:
        fg = folium.FeatureGroup(name="Cell / Phone GPS Quality (OpenCelliD)", show=False)
        HeatMap(heat_cell, radius=20, blur=18, max_zoom=7,
                gradient=_HEATMAP_CELL).add_to(fg)
        fg.add_to(m)

    # ---- Layer 5: Flagged grid cell rectangles --------------------------
    cells_to_draw = cells if show_all_cells else report.flagged_cells
    if cells_to_draw:
        fg = folium.FeatureGroup(name="Live Flagged Grid Cells", show=True)
        for cell in cells_to_draw:
            color   = _LEVEL_COLOR[cell.level]
            opacity = _LEVEL_FILL_OPACITY[cell.level]
            folium.Rectangle(
                bounds=[[cell.lat_min, cell.lon_min], [cell.lat_max, cell.lon_max]],
                color=color, fill=True, fill_color=color,
                fill_opacity=opacity, weight=1.2,
                popup=folium.Popup(_cell_popup_html(cell), max_width=380),
                tooltip=_cell_tooltip(cell),
            ).add_to(fg)
        fg.add_to(m)

    # ---- Layer 6: Known jamming zones (documented) ----------------------
    _add_known_zones_layer(m)

    # ---- Legend & layer control -----------------------------------------
    m.get_root().html.add_child(folium.Element(_build_legend_html(report)))
    folium.LayerControl(collapsed=False, position="topright").add_to(m)

    m.save(output_path)
    logger.info("Interactive map saved → %s", output_path)
    return output_path


def _cell_tooltip(cell: CellResult) -> str:
    parts = [f"{cell.level}"]
    if cell.has_adsb:
        parts.append(f"conf={cell.confidence:.0%}")
        parts.append(f"MLAT+ASTERIX={cell.non_gps_ratio:.0%}")
        parts.append(f"Δalt={cell.mean_alt_diff_m:.0f}m")
        parts.append(f"AltVar={cell.alt_variance_m:.0f}m")
    if cell.has_cell:
        parts.append(f"CellRange={cell.mean_cell_range_m:.0f}m")
    return "  |  ".join(parts)


def _cell_popup_html(cell: CellResult) -> str:
    color = _LEVEL_COLOR[cell.level]
    bar = lambda score, c="#3498db": (
        f'<div style="background:#222;border-radius:3px;height:8px;margin:2px 0 4px">'
        f'<div style="background:{c};width:{score*100:.0f}%;height:8px;border-radius:3px"></div></div>'
    )

    adsb_html = ""
    if cell.has_adsb:
        adsb_html = f"""
      <hr style="border-color:#333;margin:5px 0">
      <b style="color:#0074D9">ADS-B Layer ({cell.total_aircraft} aircraft)</b><br>
      <small>
        ADS-B: {cell.adsb_count} &nbsp; MLAT: {cell.mlat_count} &nbsp;
        ASTERIX: {cell.asterix_count} &nbsp; Other: {cell.other_count}
      </small><br><br>
      <b>Non-GPS ratio</b> {cell.non_gps_ratio:.0%}
        (adj {cell.non_gps_ratio_adj:.0%}, score {cell.non_gps_score:.0%}){bar(cell.non_gps_score,"#e74c3c")}
      <b>Altitude variance</b> {cell.alt_variance_m:.0f} m std-dev
        (score {cell.alt_variance_score:.0%}){bar(cell.alt_variance_score,"#9b59b6")}
      <b>Mean alt discrepancy</b> {cell.mean_alt_diff_m:.0f} m
        (max {cell.max_alt_diff_m:.0f} m, score {cell.alt_mean_score:.0%}){bar(cell.alt_mean_score,"#f39c12")}
      <b>Position staleness</b> {cell.pos_stale_ratio:.0%}
        (score {cell.pos_stale_score:.0%}){bar(cell.pos_stale_score,"#1abc9c")}
      <small style="color:#888">Mean cruise alt: {cell.mean_cruise_alt_m:.0f} m</small>
        """

    cell_html = ""
    if cell.has_cell:
        cell_html = f"""
      <hr style="border-color:#333;margin:5px 0">
      <b style="color:#C77DFF">Cell/Phone Layer ({cell.total_towers} towers)</b><br>
      <b>Mean GPS range</b> {cell.mean_cell_range_m:.0f} m
        (max {cell.max_cell_range_m:.0f} m, score {cell.cell_score:.0%}){bar(cell.cell_score,"#8e44ad")}
        """

    return f"""
    <div style="font-family:monospace;font-size:12px;min-width:300px;color:#ddd;background:#111;padding:8px;border-radius:4px">
      <b style="color:{color};font-size:15px">{cell.level}</b>
      <span style="color:#aaa;font-size:11px"> [{cell.source_label}]</span><br>
      <b style="font-size:18px;color:{color}">{cell.confidence:.0%}</b>
      <span style="color:#aaa"> confidence</span><br>
      <small style="color:#888">Center {cell.center_lat:+.3f}°, {cell.center_lon:+.3f}°
        &nbsp; Cell {cell.lat_min:.1f}–{cell.lat_max:.1f}°N,
        {cell.lon_min:.1f}–{cell.lon_max:.1f}°E</small>
      {adsb_html}
      {cell_html}
    </div>
    """


def _add_known_zones_layer(m) -> None:
    """Add documented GPS interference zones as a GeoJSON overlay."""
    fc = get_geojson_feature_collection()

    severity_style = {
        "CRITICAL": {"color": _KNOWN_SEVERITY_COLOR["CRITICAL"], "fillOpacity": 0.12, "weight": 2.5},
        "ALERT":    {"color": _KNOWN_SEVERITY_COLOR["ALERT"],    "fillOpacity": 0.10, "weight": 2.0},
        "WARNING":  {"color": _KNOWN_SEVERITY_COLOR["WARNING"],  "fillOpacity": 0.08, "weight": 1.5},
    }

    fg = folium.FeatureGroup(name="Documented GPS Jamming Zones (Reference)", show=True)

    for feature in fc["features"]:
        sev   = feature["properties"]["severity"]
        style = severity_style.get(sev, severity_style["WARNING"])
        name  = feature["properties"]["name"]
        desc  = feature["properties"]["description"]
        src   = feature["properties"]["source"]
        clr   = _KNOWN_SEVERITY_COLOR.get(sev, "#ffd166")

        popup_html = f"""
        <div style="font-family:monospace;font-size:12px;min-width:280px;
                    color:#ddd;background:#111;padding:8px;border-radius:4px">
          <b style="color:{clr};font-size:13px">{name}</b><br>
          <span style="color:#f1c40f">{sev}</span>
          <hr style="border-color:#333;margin:4px 0">
          <p style="margin:4px 0;color:#ccc;font-size:11px">{desc}</p>
          <hr style="border-color:#333;margin:4px 0">
          <small style="color:#888">Source: {src}</small>
        </div>
        """

        folium.GeoJson(
            feature,
            style_function=lambda feat, s=style: {
                "fillColor": s["color"],
                "color":     s["color"],
                "weight":    s["weight"],
                "fillOpacity": s["fillOpacity"],
                "dashArray": "6 4",
            },
            tooltip=f"{name} [{sev}]",
            popup=folium.Popup(popup_html, max_width=340),
        ).add_to(fg)

    fg.add_to(m)


def _build_legend_html(report: DetectionReport) -> str:
    ts   = report.fetch_time or "live"
    snaps = report.snapshots_merged

    level_items = "".join(
        f'<div style="margin:2px 0"><span style="background:{c};display:inline-block;'
        f'width:13px;height:13px;margin-right:6px;border-radius:2px;vertical-align:middle">'
        f'</span>{lv}</div>'
        for lv, c in _LEVEL_COLOR.items() if lv != "CLEAR"
    )
    src_items = (
        '<div style="margin:2px 0"><span style="background:#0074D9;display:inline-block;'
        'width:13px;height:13px;margin-right:6px;border-radius:2px;vertical-align:middle">'
        '</span>ADS-B (airborne)</div>'
        '<div style="margin:2px 0"><span style="background:#C77DFF;display:inline-block;'
        'width:13px;height:13px;margin-right:6px;border-radius:2px;vertical-align:middle">'
        '</span>Cell / Phone (ground)</div>'
        '<div style="margin:2px 0"><span style="border:2px dashed #ff073a;display:inline-block;'
        'width:13px;height:13px;margin-right:6px;vertical-align:middle">'
        '</span>Documented zones</div>'
    )

    return f"""
    <div style="
        position:fixed;bottom:30px;right:10px;z-index:9999;
        background:rgba(0,0,0,0.85);color:#ddd;
        padding:12px 15px;border-radius:8px;font-family:monospace;font-size:12px;
        min-width:200px;border:1px solid #333">
      <b style="font-size:13px">GPS Jamming Detector</b><br>
      <span style="font-size:10px;color:#888">{ts}</span><br>
      <span style="font-size:10px;color:#888">{snaps} snapshot(s) merged</span>
      <hr style="border-color:#333;margin:6px 0">
      <b style="font-size:11px">Detection Levels</b><br>
      {level_items}
      <hr style="border-color:#333;margin:6px 0">
      <b style="font-size:11px">Data Sources</b><br>
      {src_items}
      <hr style="border-color:#333;margin:6px 0">
      Aircraft: <b>{report.total_aircraft_analyzed}</b><br>
      Cell towers: <b>{report.total_towers_analyzed}</b><br>
      Grid cells: <b>{report.total_cells_analyzed}</b><br>
      Flagged: <b style="color:#f1c40f">{len(report.flagged_cells)}</b><br>
      Peak conf: <b style="color:#e74c3c">{report.highest_confidence:.0%}</b>
    </div>
    """


# ---------------------------------------------------------------------------
# Static analysis charts
# ---------------------------------------------------------------------------

def build_analysis_charts(
    report: DetectionReport,
    output_path: str = f"{config.OUTPUT_DIR}/{config.PLOT_FILENAME}",
) -> Optional[str]:
    if not _MPL_OK:
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cells = report.cell_results
    if not cells:
        return None

    lv_order     = ["CRITICAL", "ALERT", "WARNING", "CLEAR"]
    levels       = [c.level for c in cells]
    confidences  = [c.confidence for c in cells]
    cell_colors  = [_LEVEL_COLOR[lv] for lv in levels]
    adsb_cells   = [c for c in cells if c.has_adsb]
    cell_c       = [c for c in cells if c.has_cell]

    fig, axes = plt.subplots(3, 2, figsize=(16, 15))
    fig.patch.set_facecolor("#0a0a14")
    for ax in axes.flat:
        ax.set_facecolor("#11112a")
        ax.tick_params(colors="#bbb")
        ax.xaxis.label.set_color("#bbb")
        ax.yaxis.label.set_color("#bbb")
        ax.title.set_color("#eee")
        for sp in ax.spines.values():
            sp.set_edgecolor("#2a2a4a")

    # [0,0] Classification bar
    ax = axes[0, 0]
    counts = [levels.count(lv) for lv in lv_order]
    bars = ax.bar(lv_order, counts, color=[_LEVEL_COLOR[lv] for lv in lv_order],
                  edgecolor="#111", linewidth=0.8)
    for bar, cnt in zip(bars, counts):
        if cnt > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    str(cnt), ha="center", va="bottom", color="#eee", fontsize=11, fontweight="bold")
    ax.set_title("Cell Classification Summary", fontweight="bold")
    ax.set_ylabel("Cells")
    ax.set_ylim(0, max(counts) * 1.25 if any(counts) else 5)

    # [0,1] Non-GPS ratio vs confidence
    ax = axes[0, 1]
    if adsb_cells:
        ax.scatter([c.non_gps_ratio_adj for c in adsb_cells],
                   [c.confidence for c in adsb_cells],
                   c=[_LEVEL_COLOR[c.level] for c in adsb_cells],
                   s=50, alpha=0.85, edgecolors="#111", linewidths=0.4)
    ax.axhline(config.CONFIDENCE_WARN,     color="#f1c40f", lw=0.9, ls="--", label="Warn")
    ax.axhline(config.CONFIDENCE_ALERT,    color="#e67e22", lw=0.9, ls="--", label="Alert")
    ax.axhline(config.CONFIDENCE_CRITICAL, color="#e74c3c", lw=0.9, ls="--", label="Critical")
    ax.axvline(config.NON_GPS_RATIO_WARN,  color="#555",    lw=0.7, ls=":")
    ax.axvline(config.NON_GPS_RATIO_ALERT, color="#555",    lw=0.7, ls=":")
    ax.set_xlabel("Non-GPS Ratio (MLAT + ASTERIX, adj.)")
    ax.set_ylabel("Confidence")
    ax.set_title("Non-GPS Ratio vs Confidence", fontweight="bold")
    ax.set_xlim(-0.05, 1.05); ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#11112a", labelcolor="#bbb", framealpha=0.7)

    # [1,0] Altitude variance vs confidence
    ax = axes[1, 0]
    if adsb_cells:
        ax.scatter([c.alt_variance_m for c in adsb_cells],
                   [c.confidence for c in adsb_cells],
                   c=[_LEVEL_COLOR[c.level] for c in adsb_cells],
                   s=50, alpha=0.85, edgecolors="#111", linewidths=0.4, marker="D")
    ax.axvline(config.ALT_VARIANCE_WARN_M,  color="#f1c40f", lw=0.9, ls="--",
               label=f"Warn {config.ALT_VARIANCE_WARN_M}m")
    ax.axvline(config.ALT_VARIANCE_ALERT_M, color="#e74c3c", lw=0.9, ls="--",
               label=f"Alert {config.ALT_VARIANCE_ALERT_M}m")
    ax.set_xlabel("Altitude Variance std-dev (m)  ← GPS spoofing indicator")
    ax.set_ylabel("Confidence")
    ax.set_title("Altitude Variance vs Confidence", fontweight="bold")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#11112a", labelcolor="#bbb", framealpha=0.7)

    # [1,1] Mean alt diff vs confidence (with log scale for wide range)
    ax = axes[1, 1]
    alt_vals = [max(c.mean_alt_diff_m, 1) for c in adsb_cells]
    if adsb_cells:
        ax.scatter(alt_vals,
                   [c.confidence for c in adsb_cells],
                   c=[_LEVEL_COLOR[c.level] for c in adsb_cells],
                   s=50, alpha=0.85, edgecolors="#111", linewidths=0.4, marker="^")
    ax.axvline(config.ALT_MEAN_WARN_M,  color="#f1c40f", lw=0.9, ls="--",
               label=f"Warn {config.ALT_MEAN_WARN_M}m")
    ax.axvline(config.ALT_MEAN_ALERT_M, color="#e74c3c", lw=0.9, ls="--",
               label=f"Alert {config.ALT_MEAN_ALERT_M}m")
    ax.set_xscale("log")
    ax.set_xlabel("Mean |Geo − Baro| Altitude (m, log scale)")
    ax.set_ylabel("Confidence")
    ax.set_title("Altitude Discrepancy vs Confidence", fontweight="bold")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, facecolor="#11112a", labelcolor="#bbb", framealpha=0.7)

    # [2,0] Confidence histogram
    ax = axes[2, 0]
    bins = np.linspace(0, 1, 26)
    ax.hist(confidences, bins=bins, color="#3498db", edgecolor="#0a0a14", lw=0.5, alpha=0.85)
    for thr, c in [(config.CONFIDENCE_WARN, "#f1c40f"),
                   (config.CONFIDENCE_ALERT, "#e67e22"),
                   (config.CONFIDENCE_CRITICAL, "#e74c3c")]:
        ax.axvline(thr, color=c, lw=1.2, ls="--")
    ax.set_xlabel("Confidence Score")
    ax.set_ylabel("Number of cells")
    ax.set_title("Confidence Distribution", fontweight="bold")
    ax.set_xlim(0, 1)

    # [2,1] Source coverage world map
    ax = axes[2, 1]
    adsb_only = [c for c in cells if c.has_adsb and not c.has_cell]
    cell_only = [c for c in cells if c.has_cell and not c.has_adsb]
    both      = [c for c in cells if c.has_adsb and c.has_cell]
    if adsb_only:
        ax.scatter([c.center_lon for c in adsb_only], [c.center_lat for c in adsb_only],
                   s=6, color="#0074D9", alpha=0.55, label=f"ADS-B ({len(adsb_only)})")
    if cell_only:
        ax.scatter([c.center_lon for c in cell_only], [c.center_lat for c in cell_only],
                   s=6, color="#C77DFF", alpha=0.55, marker="^", label=f"Cell ({len(cell_only)})")
    if both:
        ax.scatter([c.center_lon for c in both], [c.center_lat for c in both],
                   s=10, color="#fff", alpha=0.9, marker="*", label=f"Both ({len(both)})")
    # Draw known zones on scatter map
    for zone in KNOWN_ZONES:
        poly = zone["polygon"]
        lons = [p[0] for p in poly] + [poly[0][0]]
        lats = [p[1] for p in poly] + [poly[0][1]]
        clr  = _KNOWN_SEVERITY_COLOR.get(zone["severity"], "#ffd166")
        ax.plot(lons, lats, color=clr, lw=0.8, alpha=0.6, ls="--")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_xlim(-180, 180); ax.set_ylim(-90, 90)
    ax.set_title("Data Coverage + Known Zones", fontweight="bold")
    ax.legend(fontsize=7, facecolor="#11112a", labelcolor="#bbb", framealpha=0.7, markerscale=2)

    patches = [mpatches.Patch(color=_LEVEL_COLOR[lv], label=lv) for lv in lv_order]
    fig.legend(handles=patches, loc="upper center", ncol=4,
               facecolor="#0a0a14", labelcolor="#bbb", framealpha=0.8, fontsize=10,
               bbox_to_anchor=(0.5, 1.01))

    src_info = (
        f"ADS-B: {report.total_aircraft_analyzed} aircraft  |  "
        f"Cell towers: {report.total_towers_analyzed}  |  "
        f"Snapshots merged: {report.snapshots_merged}"
    )
    fig.suptitle(
        f"GPS Jamming Detection Analysis\n{report.fetch_time or ''}  —  {src_info}",
        color="#eee", fontsize=12, fontweight="bold", y=1.03,
    )
    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Analysis chart saved → %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Trend chart
# ---------------------------------------------------------------------------

def build_trend_chart(trend_df, output_path: str = f"{config.OUTPUT_DIR}/trend.png") -> Optional[str]:
    if not _MPL_OK or trend_df is None or trend_df.empty:
        return None
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    flagged = trend_df[trend_df["level"] != "CLEAR"]
    by_time = flagged.groupby("fetch_time").agg(
        flagged_cells=("cell_id", "nunique"),
        max_confidence=("confidence", "max"),
    )
    fig, ax1 = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#0a0a14")
    ax1.set_facecolor("#11112a")
    ax1.bar(by_time.index, by_time["flagged_cells"], color="#3498db", alpha=0.7, label="Flagged cells")
    ax1.set_ylabel("Flagged cells", color="#3498db")
    ax1.tick_params(axis="y", colors="#3498db")
    ax2 = ax1.twinx()
    ax2.plot(by_time.index, by_time["max_confidence"], color="#e74c3c", lw=2, marker="o", label="Max conf")
    ax2.set_ylabel("Max confidence", color="#e74c3c")
    ax2.tick_params(axis="y", colors="#e74c3c")
    ax2.set_ylim(0, 1)
    for sp in ax1.spines.values(): sp.set_edgecolor("#2a2a4a")
    ax1.tick_params(colors="#bbb")
    ax1.set_title("GPS Jamming Trend", color="#eee", fontweight="bold")
    lines1, lbl1 = ax1.get_legend_handles_labels()
    lines2, lbl2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, lbl1 + lbl2, facecolor="#0a0a14", labelcolor="#bbb", framealpha=0.8)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Trend chart → %s", output_path)
    return output_path
