"""
GPS Jamming Detection Engine.

Detection methodology (academic standard used in GNSS research):

1. **MLAT-ratio indicator**
   Aircraft that lose GPS lock fall back to Multilateration (MLAT,
   position_source=2).  A geographic cell with a disproportionately high
   fraction of MLAT aircraft relative to ADS-B aircraft (position_source=0)
   is a strong indicator of GPS jamming.

2. **Barometric / geometric altitude discrepancy indicator**
   Barometric altitude is derived from air pressure (immune to GPS jamming).
   Geometric altitude comes directly from the GNSS receiver.  When a jammer
   spoofs or disrupts GPS signals the geometric altitude diverges from the
   barometric altitude by hundreds of metres.  We compute the mean absolute
   difference per cell and flag outliers.

3. **Composite confidence score**
   Both indicators are normalised to [0, 1] and combined with weighted
   averaging to produce a single confidence score per cell.  Cells are then
   classified as CLEAR / WARNING / ALERT / CRITICAL based on thresholds
   defined in config.py.

Reference:
  Raimund Zogg (2009), "GPS Fundamentals and Receiver Design" – chapter on
  GNSS interference and anomaly detection.
  Mitch et al. (2011), "Signal Characteristics of Civil GPS Jammers".
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
    """Detection result for a single geographic grid cell."""
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    # Aircraft counts
    total_aircraft: int = 0
    adsb_count: int = 0       # position_source == 0
    mlat_count: int = 0       # position_source == 2
    other_count: int = 0

    # Indicator values
    mlat_ratio: float = 0.0
    mean_alt_diff_m: float = 0.0   # mean |geo_alt - baro_alt|
    max_alt_diff_m: float = 0.0

    # Composite score and classification
    mlat_score: float = 0.0        # normalised [0, 1]
    alt_score: float = 0.0         # normalised [0, 1]
    confidence: float = 0.0        # weighted composite [0, 1]
    level: str = "CLEAR"           # CLEAR / WARNING / ALERT / CRITICAL

    # Representative location (centroid of aircraft in cell)
    center_lat: float = 0.0
    center_lon: float = 0.0

    # List of affected ICAO24 identifiers
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


@dataclass
class DetectionReport:
    """Aggregated detection report for an entire snapshot."""
    fetch_time: Optional[str] = None
    total_aircraft_analyzed: int = 0
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
        if not self.cell_results:
            return 0.0
        return max(c.confidence for c in self.cell_results)


# ---------------------------------------------------------------------------
# Main detector class
# ---------------------------------------------------------------------------

class JammingDetector:
    """
    Detects GPS jamming zones from ADS-B state-vector DataFrames.

    Usage
    -----
    detector = JammingDetector()
    report = detector.analyze(df)
    """

    # Weights for composite score (must sum to 1.0)
    _W_MLAT = 0.60
    _W_ALT  = 0.40

    def __init__(
        self,
        cell_deg: float = config.GRID_CELL_DEG,
        min_aircraft: int = config.GRID_MIN_AIRCRAFT,
    ) -> None:
        self._cell_deg = cell_deg
        self._min_aircraft = min_aircraft

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, df: pd.DataFrame) -> DetectionReport:
        """
        Run full jamming detection pipeline on a state-vector DataFrame.

        Parameters
        ----------
        df : output of OpenSkyFetcher.fetch_all_states()

        Returns
        -------
        DetectionReport
        """
        report = DetectionReport()

        if df.empty:
            logger.warning("Empty DataFrame — nothing to analyze.")
            return report

        # Use only airborne aircraft with a valid position
        airborne = df[
            (~df["on_ground"]) &
            df["latitude"].notna() &
            df["longitude"].notna()
        ].copy()

        logger.info(
            "Analyzing %d airborne aircraft (total in snapshot: %d)",
            len(airborne), len(df),
        )
        report.total_aircraft_analyzed = len(airborne)

        if "fetch_time" in df.columns:
            report.fetch_time = str(df["fetch_time"].iloc[0])

        # Assign grid cells
        airborne["cell_lat"] = self._grid_bin(airborne["latitude"])
        airborne["cell_lon"] = self._grid_bin(airborne["longitude"])

        cell_results: list[CellResult] = []

        for (cell_lat, cell_lon), group in airborne.groupby(["cell_lat", "cell_lon"]):
            if len(group) < self._min_aircraft:
                continue

            result = self._analyze_cell(cell_lat, cell_lon, group)
            cell_results.append(result)

        report.cell_results = cell_results
        report.total_cells_analyzed = len(cell_results)
        report.clear_cells    = sum(1 for c in cell_results if c.level == "CLEAR")
        report.warning_cells  = sum(1 for c in cell_results if c.level == "WARNING")
        report.alert_cells    = sum(1 for c in cell_results if c.level == "ALERT")
        report.critical_cells = sum(1 for c in cell_results if c.level == "CRITICAL")

        logger.info(
            "Detection complete — cells: %d total | %d WARNING | %d ALERT | %d CRITICAL",
            report.total_cells_analyzed,
            report.warning_cells,
            report.alert_cells,
            report.critical_cells,
        )
        return report

    # ------------------------------------------------------------------
    # Cell-level analysis
    # ------------------------------------------------------------------

    def _analyze_cell(
        self,
        cell_lat: float,
        cell_lon: float,
        group: pd.DataFrame,
    ) -> CellResult:
        lat_min = cell_lat
        lat_max = cell_lat + self._cell_deg
        lon_min = cell_lon
        lon_max = cell_lon + self._cell_deg

        result = CellResult(
            lat_min=lat_min, lat_max=lat_max,
            lon_min=lon_min, lon_max=lon_max,
        )

        result.total_aircraft = len(group)
        result.adsb_count  = int((group["position_source"] == 0).sum())
        result.mlat_count  = int((group["position_source"] == 2).sum())
        result.other_count = result.total_aircraft - result.adsb_count - result.mlat_count

        result.center_lat = float(group["latitude"].mean())
        result.center_lon = float(group["longitude"].mean())
        result.affected_aircraft = group["icao24"].dropna().tolist()

        # --- Indicator 1: MLAT ratio ---
        result.mlat_ratio = result.mlat_count / result.total_aircraft
        result.mlat_score = self._score_mlat(result.mlat_ratio)

        # --- Indicator 2: altitude discrepancy ---
        valid_alt = group[group["baro_altitude"].notna() & group["geo_altitude"].notna()].copy()
        if len(valid_alt) >= 2:
            alt_diff = (valid_alt["geo_altitude"] - valid_alt["baro_altitude"]).abs()
            result.mean_alt_diff_m = float(alt_diff.mean())
            result.max_alt_diff_m  = float(alt_diff.max())
        result.alt_score = self._score_alt_diff(result.mean_alt_diff_m)

        # --- Composite confidence ---
        result.confidence = self._W_MLAT * result.mlat_score + self._W_ALT * result.alt_score
        result.level = self._classify(result.confidence)

        return result

    # ------------------------------------------------------------------
    # Scoring functions (normalise indicators to [0, 1])
    # ------------------------------------------------------------------

    @staticmethod
    def _score_mlat(ratio: float) -> float:
        """
        Map MLAT ratio → score.
          ratio < WARN threshold  → 0.0
          ratio ≥ ALERT threshold → 1.0
          linear interpolation between
        """
        low  = config.MLAT_RATIO_WARN
        high = config.MLAT_RATIO_ALERT
        if ratio <= low:
            return 0.0
        if ratio >= high:
            return 1.0
        return (ratio - low) / (high - low)

    @staticmethod
    def _score_alt_diff(diff_m: float) -> float:
        """
        Map mean altitude discrepancy → score.
          diff < WARN threshold  → 0.0
          diff ≥ ALERT threshold → 1.0
        """
        low  = config.ALT_DIFF_WARN_M
        high = config.ALT_DIFF_ALERT_M
        if diff_m <= low:
            return 0.0
        if diff_m >= high:
            return 1.0
        return (diff_m - low) / (high - low)

    @staticmethod
    def _classify(confidence: float) -> str:
        if confidence >= config.CONFIDENCE_CRITICAL:
            return "CRITICAL"
        if confidence >= config.CONFIDENCE_ALERT:
            return "ALERT"
        if confidence >= config.CONFIDENCE_WARN:
            return "WARNING"
        return "CLEAR"

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------

    def _grid_bin(self, series: pd.Series) -> pd.Series:
        """Floor each coordinate to the nearest cell_deg boundary."""
        return (series // self._cell_deg) * self._cell_deg
