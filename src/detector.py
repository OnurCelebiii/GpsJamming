"""
GPS Jamming Detection Engine — 5-indicator dual-source system.

Why 5 indicators?
-----------------
Single-indicator systems (MLAT ratio alone) miss:
  • GPS SPOOFING  — aircraft keeps ADS-B GPS (source=0) but reports a false
    position. MLAT ratio stays 0 but altitude variance spikes.
  • GPS DROPOUT   — aircraft position timestamp stops updating while
    last_contact continues. Position staleness ratio rises.
  • RADAR OVERRIDE (ASTERIX) — military radar overwrites GPS in conflict
    zones. Counted as non-GPS along with MLAT.
  • CRUISE-ALTITUDE anomalies — MLAT at FL300 is almost never normal.

The 5 indicators
----------------
1. Non-GPS ratio          (MLAT source=2 + ASTERIX source=1) / total
   Primary indicator.  High-altitude multiplier applied for cruise flights.
   Weight: 40 %

2. Altitude variance      std_dev(geo_alt − baro_alt) within cell
   Catches GPS spoofing: spoofed aircraft get wrong altitudes while
   baro stays correct.  Normal std-dev < 50 m.
   Weight: 30 %

3. Mean altitude diff     mean |geo_alt − baro_alt| within cell
   Secondary altitude check with raised scale (2000 m = ALERT).
   A 4000 m discrepancy alone pushes to ALERT level.
   Weight: 20 %

4. Position staleness     fraction where last_contact − time_position > 30 s
   GPS signal lost but ADS-B transmitter still active.
   Weight: 10 %

References
----------
  EASA SIB 2022-02R2 "GPS/GNSS jamming and spoofing"
  ICAO Cir 344 "Global Navigation Satellite System (GNSS) Manual"
  Mitch et al. (2011) ION GNSS — signal characteristics of civil GPS jammers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    # ADS-B counts
    total_aircraft: int = 0
    adsb_count: int = 0       # source = 0
    mlat_count: int = 0       # source = 2
    asterix_count: int = 0    # source = 1
    other_count: int = 0
    non_gps_count: int = 0    # mlat + asterix

    # Indicator values (raw)
    non_gps_ratio: float = 0.0         # (mlat + asterix) / total
    non_gps_ratio_adj: float = 0.0     # after high-altitude multiplier
    alt_variance_m: float = 0.0        # std-dev of (geo-baro) diff
    mean_alt_diff_m: float = 0.0       # mean |geo-baro|
    max_alt_diff_m: float = 0.0
    pos_stale_ratio: float = 0.0       # fraction with stale GPS
    mean_cruise_alt_m: float = 0.0     # mean altitude of cell aircraft

    # Indicator scores [0, 1]
    non_gps_score: float = 0.0
    alt_variance_score: float = 0.0
    alt_mean_score: float = 0.0
    pos_stale_score: float = 0.0

    # Cell layer (OpenCelliD)
    total_towers: int = 0
    mean_cell_range_m: float = 0.0
    max_cell_range_m: float = 0.0
    cell_score: float = 0.0
    has_cell: bool = False

    has_adsb: bool = False
    confidence: float = 0.0
    level: str = "CLEAR"

    center_lat: float = 0.0
    center_lon: float = 0.0
    affected_aircraft: list = field(default_factory=list)

    @property
    def cell_id(self) -> str:
        return (
            f"lat[{self.lat_min:.1f},{self.lat_max:.1f}]"
            f"_lon[{self.lon_min:.1f},{self.lon_max:.1f}]"
        )

    @property
    def center(self) -> tuple[float, float]:
        return (self.center_lat, self.center_lon)

    @property
    def source_label(self) -> str:
        parts = []
        if self.has_adsb:
            parts.append("ADS-B")
        if self.has_cell:
            parts.append("Cell")
        return " + ".join(parts) if parts else "?"


@dataclass
class DetectionReport:
    fetch_time: Optional[str] = None
    snapshots_merged: int = 1
    total_aircraft_analyzed: int = 0
    total_towers_analyzed: int = 0
    total_cells_analyzed: int = 0
    clear_cells: int = 0
    warning_cells: int = 0
    alert_cells: int = 0
    critical_cells: int = 0
    cell_results: list[CellResult] = field(default_factory=list)

    @property
    def flagged_cells(self) -> list[CellResult]:
        return [c for c in self.cell_results if c.level != "CLEAR"]

    @property
    def highest_confidence(self) -> float:
        return max((c.confidence for c in self.cell_results), default=0.0)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class JammingDetector:
    """
    Detects GPS jamming/spoofing zones using 5 independent ADS-B indicators
    plus an optional ground-level cell tower GPS quality indicator.

    Usage
    -----
    detector = JammingDetector()
    report   = detector.analyze(adsb_df)
    report   = detector.analyze(adsb_df, cell_df=cell_df)
    """

    def __init__(
        self,
        cell_deg: float = config.GRID_CELL_DEG,
        min_aircraft: int = config.GRID_MIN_AIRCRAFT,
        min_towers: int = config.CELL_MIN_TOWERS,
    ) -> None:
        self._cell_deg     = cell_deg
        self._min_aircraft = min_aircraft
        self._min_towers   = min_towers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        adsb_df: pd.DataFrame,
        cell_df: Optional[pd.DataFrame] = None,
        snapshots_merged: int = 1,
    ) -> DetectionReport:
        report = DetectionReport(snapshots_merged=snapshots_merged)

        airborne = pd.DataFrame()
        if not adsb_df.empty:
            airborne = adsb_df[
                (~adsb_df["on_ground"]) &
                adsb_df["latitude"].notna() &
                adsb_df["longitude"].notna()
            ].copy()
            airborne["_cell_lat"] = self._grid_bin(airborne["latitude"])
            airborne["_cell_lon"] = self._grid_bin(airborne["longitude"])
            report.total_aircraft_analyzed = len(airborne)
            if "fetch_time" in adsb_df.columns and not adsb_df["fetch_time"].isna().all():
                report.fetch_time = str(adsb_df["fetch_time"].iloc[0])

        cells_valid = pd.DataFrame()
        if cell_df is not None and not cell_df.empty:
            cells_valid = cell_df[
                cell_df["latitude"].notna() & cell_df["longitude"].notna()
            ].copy()
            cells_valid["_cell_lat"] = self._grid_bin(cells_valid["latitude"])
            cells_valid["_cell_lon"] = self._grid_bin(cells_valid["longitude"])
            report.total_towers_analyzed = len(cells_valid)

        logger.info(
            "Analyzing %d airborne aircraft + %d cell towers across %.1f° grid",
            len(airborne), len(cells_valid), self._cell_deg,
        )

        grid_keys: set[tuple] = set()
        if not airborne.empty:
            grid_keys |= set(zip(airborne["_cell_lat"], airborne["_cell_lon"]))
        if not cells_valid.empty:
            grid_keys |= set(zip(cells_valid["_cell_lat"], cells_valid["_cell_lon"]))

        results: list[CellResult] = []
        for (clat, clon) in grid_keys:
            ac_g = (
                airborne[(airborne["_cell_lat"] == clat) & (airborne["_cell_lon"] == clon)]
                if not airborne.empty else pd.DataFrame()
            )
            ce_g = (
                cells_valid[(cells_valid["_cell_lat"] == clat) & (cells_valid["_cell_lon"] == clon)]
                if not cells_valid.empty else pd.DataFrame()
            )
            if len(ac_g) < self._min_aircraft and len(ce_g) < self._min_towers:
                continue
            results.append(self._analyze_cell(clat, clon, ac_g, ce_g))

        report.cell_results         = results
        report.total_cells_analyzed = len(results)
        report.clear_cells    = sum(1 for c in results if c.level == "CLEAR")
        report.warning_cells  = sum(1 for c in results if c.level == "WARNING")
        report.alert_cells    = sum(1 for c in results if c.level == "ALERT")
        report.critical_cells = sum(1 for c in results if c.level == "CRITICAL")

        logger.info(
            "Detection complete — %d cells | %dW %dA %dC | peak conf %.0f%%",
            report.total_cells_analyzed,
            report.warning_cells, report.alert_cells, report.critical_cells,
            report.highest_confidence * 100,
        )
        return report

    # ------------------------------------------------------------------
    # Cell-level analysis
    # ------------------------------------------------------------------

    def _analyze_cell(
        self,
        cell_lat: float,
        cell_lon: float,
        ac: pd.DataFrame,
        ce: pd.DataFrame,
    ) -> CellResult:
        r = CellResult(
            lat_min=cell_lat, lat_max=cell_lat + self._cell_deg,
            lon_min=cell_lon, lon_max=cell_lon + self._cell_deg,
        )

        lats, lons = [], []

        # ----------------------------------------------------------------
        # ADS-B indicators
        # ----------------------------------------------------------------
        if len(ac) >= self._min_aircraft:
            r.has_adsb        = True
            r.total_aircraft  = len(ac)
            r.adsb_count      = int((ac["position_source"] == 0).sum())
            r.mlat_count      = int((ac["position_source"] == 2).sum())
            r.asterix_count   = int((ac["position_source"] == 1).sum())
            r.non_gps_count   = r.mlat_count + r.asterix_count
            r.other_count     = r.total_aircraft - r.adsb_count - r.non_gps_count

            r.affected_aircraft = ac["icao24"].dropna().tolist()
            lats.extend(ac["latitude"].dropna().tolist())
            lons.extend(ac["longitude"].dropna().tolist())

            # --- Indicator 1: Non-GPS ratio with high-altitude multiplier ---
            r.non_gps_ratio = r.non_gps_count / r.total_aircraft

            # For high-altitude aircraft, non-GPS is a much stronger signal.
            # Compute mean altitude of aircraft in cell that ARE non-GPS.
            if r.non_gps_count > 0 and "baro_altitude" in ac.columns:
                non_gps_ac = ac[ac["position_source"].isin([1, 2])]
                mean_alt = non_gps_ac["baro_altitude"].dropna().mean()
                r.mean_cruise_alt_m = float(mean_alt) if not np.isnan(mean_alt) else 0.0
                multiplier = config.HIGH_ALT_MULTIPLIER if r.mean_cruise_alt_m >= config.HIGH_ALT_M else 1.0
            else:
                multiplier = 1.0

            r.non_gps_ratio_adj = min(1.0, r.non_gps_ratio * multiplier)
            r.non_gps_score     = self._score_linear(
                r.non_gps_ratio_adj,
                config.NON_GPS_RATIO_WARN, config.NON_GPS_RATIO_ALERT,
            )

            # --- Indicator 2: Altitude variance ---
            alt_pair = ac[ac["baro_altitude"].notna() & ac["geo_altitude"].notna()].copy()
            if len(alt_pair) >= 3:
                diff_series = alt_pair["geo_altitude"] - alt_pair["baro_altitude"]
                r.alt_variance_m   = float(diff_series.std())
                r.mean_alt_diff_m  = float(diff_series.abs().mean())
                r.max_alt_diff_m   = float(diff_series.abs().max())
            elif len(alt_pair) >= 2:
                diff_series = alt_pair["geo_altitude"] - alt_pair["baro_altitude"]
                r.mean_alt_diff_m = float(diff_series.abs().mean())
                r.max_alt_diff_m  = float(diff_series.abs().max())
                r.alt_variance_m  = 0.0

            r.alt_variance_score = self._score_linear(
                r.alt_variance_m,
                config.ALT_VARIANCE_WARN_M, config.ALT_VARIANCE_ALERT_M,
            )

            # --- Indicator 3: Mean altitude discrepancy ---
            r.alt_mean_score = self._score_linear(
                r.mean_alt_diff_m,
                config.ALT_MEAN_WARN_M, config.ALT_MEAN_ALERT_M,
            )

            # --- Indicator 4: Position staleness ---
            if "time_position" in ac.columns and "last_contact" in ac.columns:
                tp = pd.to_numeric(ac["time_position"], errors="coerce")
                lc = pd.to_numeric(ac["last_contact"],  errors="coerce")
                valid_mask = tp.notna() & lc.notna()
                if valid_mask.sum() >= 2:
                    age = (lc[valid_mask] - tp[valid_mask])
                    stale = (age > config.POS_STALE_THRESH_S).sum()
                    r.pos_stale_ratio = float(stale) / valid_mask.sum()
            r.pos_stale_score = self._score_linear(
                r.pos_stale_ratio,
                config.POS_STALE_WARN, config.POS_STALE_ALERT,
            )

        # ----------------------------------------------------------------
        # Cell tower indicator
        # ----------------------------------------------------------------
        if len(ce) >= self._min_towers:
            r.has_cell      = True
            r.total_towers  = len(ce)
            ranges = ce["range"].dropna() if "range" in ce.columns else pd.Series(dtype=float)
            if not ranges.empty:
                r.mean_cell_range_m = float(ranges.mean())
                r.max_cell_range_m  = float(ranges.max())
            r.cell_score = self._score_linear(
                r.mean_cell_range_m,
                config.CELL_RANGE_NORMAL_M, config.CELL_RANGE_ALERT_M,
            )
            lats.extend(ce["latitude"].dropna().tolist())
            lons.extend(ce["longitude"].dropna().tolist())

        # ----------------------------------------------------------------
        # Centroid & composite score
        # ----------------------------------------------------------------
        if lats:
            r.center_lat = float(sum(lats) / len(lats))
            r.center_lon = float(sum(lons) / len(lons))

        r.confidence = self._composite(r)
        r.level      = self._classify(r.confidence)
        return r

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _composite(r: CellResult) -> float:
        if r.has_adsb and r.has_cell:
            cw = config.CELL_WEIGHT            # 0.30
            aw = 1.0 - cw                      # 0.70
            adsb_part = (
                config.W_NON_GPS   * r.non_gps_score +
                config.W_ALT_VAR   * r.alt_variance_score +
                config.W_ALT_MEAN  * r.alt_mean_score +
                config.W_POS_STALE * r.pos_stale_score
            )
            return aw * adsb_part + cw * r.cell_score

        if r.has_adsb:
            return (
                config.W_NON_GPS   * r.non_gps_score +
                config.W_ALT_VAR   * r.alt_variance_score +
                config.W_ALT_MEAN  * r.alt_mean_score +
                config.W_POS_STALE * r.pos_stale_score
            )
        # cell only
        return r.cell_score

    @staticmethod
    def _score_linear(value: float, low: float, high: float) -> float:
        if value <= low:  return 0.0
        if value >= high: return 1.0
        return (value - low) / (high - low)

    @staticmethod
    def _classify(confidence: float) -> str:
        if confidence >= config.CONFIDENCE_CRITICAL: return "CRITICAL"
        if confidence >= config.CONFIDENCE_ALERT:    return "ALERT"
        if confidence >= config.CONFIDENCE_WARN:     return "WARNING"
        return "CLEAR"

    def _grid_bin(self, series: pd.Series) -> pd.Series:
        return (series // self._cell_deg) * self._cell_deg
