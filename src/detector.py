"""
GPS Jamming Detection Engine — dual-source (ADS-B + Cell towers).

Detection methodology
---------------------

Source A — ADS-B (OpenSky Network, airborne layer):
  1. MLAT-ratio indicator
     Aircraft that lose GPS lock fall back to MLAT (position_source=2).
     A cell with too many MLAT aircraft signals airborne GPS disruption.

  2. Barometric / geometric altitude discrepancy
     Jamming corrupts geo_altitude while baro_altitude (pressure) stays
     stable.  Large |geo − baro| difference is a second airborne indicator.

Source B — Cell towers (OpenCelliD, ground layer):
  3. Cell tower GPS range indicator
     OpenCelliD stores, per tower, the GPS error (`range` in metres) of the
     phones that crowd-sourced it.  High average range in a grid cell = poor
     phone GPS in that area = potential ground-level jamming.

     This fills blind spots where no aircraft fly (sea level, terrain,
     restricted airspace).

Composite confidence score
--------------------------
  conf = w_mlat  * mlat_score
       + w_alt   * alt_score
       + w_cell  * cell_score

  Weights adapt based on data availability:
    - Both sources   → 42 % MLAT + 28 % alt + 30 % cell
    - ADS-B only     → 60 % MLAT + 40 % alt
    - Cell only      → 100 % cell

References
----------
  Mitch et al. (2011), "Signal Characteristics of Civil GPS Jammers". ION GNSS.
  Shepard et al. (2012), "GPS Spoofing Attack Evaluation". ION GNSS.
  OpenCelliD documentation: https://opencellid.org/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    """Detection result for one geographic grid cell (both sources)."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    # --- ADS-B layer ---
    total_aircraft: int = 0
    adsb_count: int = 0
    mlat_count: int = 0
    other_count: int = 0
    mlat_ratio: float = 0.0
    mean_alt_diff_m: float = 0.0
    max_alt_diff_m: float = 0.0
    mlat_score: float = 0.0
    alt_score: float = 0.0
    has_adsb: bool = False

    # --- Cell tower layer ---
    total_towers: int = 0
    mean_cell_range_m: float = 0.0   # average GPS error when towers were measured
    max_cell_range_m: float = 0.0
    cell_score: float = 0.0
    has_cell: bool = False

    # --- Composite ---
    confidence: float = 0.0
    level: str = "CLEAR"

    # Representative location
    center_lat: float = 0.0
    center_lon: float = 0.0

    # Affected aircraft ICAO24 identifiers
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
        if self.has_adsb and self.has_cell:
            return "ADS-B + Cell"
        if self.has_adsb:
            return "ADS-B"
        return "Cell"


@dataclass
class DetectionReport:
    """Aggregated detection report for an entire snapshot."""
    fetch_time: Optional[str] = None
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
# Main detector
# ---------------------------------------------------------------------------

class JammingDetector:
    """
    Detects GPS jamming zones from ADS-B and/or cell tower DataFrames.

    Usage
    -----
    detector = JammingDetector()
    report   = detector.analyze(adsb_df)                     # ADS-B only
    report   = detector.analyze(adsb_df, cell_df=cell_df)    # both sources
    """

    def __init__(
        self,
        cell_deg: float = config.GRID_CELL_DEG,
        min_aircraft: int = config.GRID_MIN_AIRCRAFT,
        min_towers: int = config.CELL_MIN_TOWERS,
    ) -> None:
        self._cell_deg    = cell_deg
        self._min_aircraft = min_aircraft
        self._min_towers   = min_towers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        adsb_df: pd.DataFrame,
        cell_df: Optional[pd.DataFrame] = None,
    ) -> DetectionReport:
        """
        Run full detection pipeline.

        Parameters
        ----------
        adsb_df  : output of OpenSkyFetcher.fetch_all_states()
        cell_df  : output of CellFetcher.fetch_bbox()  (optional)
        """
        report = DetectionReport()

        # Pre-process ADS-B
        airborne = pd.DataFrame()
        if not adsb_df.empty:
            airborne = adsb_df[
                (~adsb_df["on_ground"]) &
                adsb_df["latitude"].notna() &
                adsb_df["longitude"].notna()
            ].copy()
            airborne["cell_lat"] = self._grid_bin(airborne["latitude"])
            airborne["cell_lon"] = self._grid_bin(airborne["longitude"])
            report.total_aircraft_analyzed = len(airborne)
            if "fetch_time" in adsb_df.columns:
                report.fetch_time = str(adsb_df["fetch_time"].iloc[0])

        # Pre-process cell towers
        cells_valid = pd.DataFrame()
        if cell_df is not None and not cell_df.empty:
            cells_valid = cell_df[
                cell_df["latitude"].notna() &
                cell_df["longitude"].notna()
            ].copy()
            cells_valid["cell_lat"] = self._grid_bin(cells_valid["latitude"])
            cells_valid["cell_lon"] = self._grid_bin(cells_valid["longitude"])
            report.total_towers_analyzed = len(cells_valid)

        logger.info(
            "Analyzing %d airborne aircraft + %d cell towers",
            len(airborne), len(cells_valid),
        )

        # Build union of all grid keys present in either source
        grid_keys: set[tuple] = set()
        if not airborne.empty:
            grid_keys |= set(zip(airborne["cell_lat"], airborne["cell_lon"]))
        if not cells_valid.empty:
            grid_keys |= set(zip(cells_valid["cell_lat"], cells_valid["cell_lon"]))

        results: list[CellResult] = []
        for (clat, clon) in grid_keys:
            ac_group   = airborne[
                (airborne["cell_lat"] == clat) & (airborne["cell_lon"] == clon)
            ] if not airborne.empty else pd.DataFrame()

            cell_group = cells_valid[
                (cells_valid["cell_lat"] == clat) & (cells_valid["cell_lon"] == clon)
            ] if not cells_valid.empty else pd.DataFrame()

            # Skip if neither source has enough data
            enough_ac   = len(ac_group) >= self._min_aircraft
            enough_cell = len(cell_group) >= self._min_towers
            if not enough_ac and not enough_cell:
                continue

            result = self._analyze_cell(clat, clon, ac_group, cell_group)
            results.append(result)

        report.cell_results    = results
        report.total_cells_analyzed = len(results)
        report.clear_cells    = sum(1 for c in results if c.level == "CLEAR")
        report.warning_cells  = sum(1 for c in results if c.level == "WARNING")
        report.alert_cells    = sum(1 for c in results if c.level == "ALERT")
        report.critical_cells = sum(1 for c in results if c.level == "CRITICAL")

        logger.info(
            "Detection complete — %d cells | %dW %dA %dC",
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
        ac_group: pd.DataFrame,
        cell_group: pd.DataFrame,
    ) -> CellResult:
        result = CellResult(
            lat_min=cell_lat,
            lat_max=cell_lat + self._cell_deg,
            lon_min=cell_lon,
            lon_max=cell_lon + self._cell_deg,
        )

        # Centroid from whichever source(s) are available
        lats, lons = [], []

        # --- ADS-B indicators ---
        if len(ac_group) >= self._min_aircraft:
            result.has_adsb       = True
            result.total_aircraft = len(ac_group)
            result.adsb_count     = int((ac_group["position_source"] == 0).sum())
            result.mlat_count     = int((ac_group["position_source"] == 2).sum())
            result.other_count    = result.total_aircraft - result.adsb_count - result.mlat_count
            result.mlat_ratio     = result.mlat_count / result.total_aircraft
            result.mlat_score     = self._score_mlat(result.mlat_ratio)

            valid_alt = ac_group[
                ac_group["baro_altitude"].notna() & ac_group["geo_altitude"].notna()
            ]
            if len(valid_alt) >= 2:
                diff = (valid_alt["geo_altitude"] - valid_alt["baro_altitude"]).abs()
                result.mean_alt_diff_m = float(diff.mean())
                result.max_alt_diff_m  = float(diff.max())
            result.alt_score = self._score_alt_diff(result.mean_alt_diff_m)

            result.affected_aircraft = ac_group["icao24"].dropna().tolist()
            lats.extend(ac_group["latitude"].dropna().tolist())
            lons.extend(ac_group["longitude"].dropna().tolist())

        # --- Cell tower indicator ---
        if len(cell_group) >= self._min_towers:
            result.has_cell       = True
            result.total_towers   = len(cell_group)
            ranges = cell_group["range"].dropna()
            if not ranges.empty:
                result.mean_cell_range_m = float(ranges.mean())
                result.max_cell_range_m  = float(ranges.max())
            result.cell_score = self._score_cell_range(result.mean_cell_range_m)

            lats.extend(cell_group["latitude"].dropna().tolist())
            lons.extend(cell_group["longitude"].dropna().tolist())

        if lats:
            result.center_lat = float(sum(lats) / len(lats))
            result.center_lon = float(sum(lons) / len(lons))

        # --- Composite score ---
        result.confidence = self._composite(result)
        result.level      = self._classify(result.confidence)
        return result

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_mlat(ratio: float) -> float:
        low, high = config.MLAT_RATIO_WARN, config.MLAT_RATIO_ALERT
        if ratio <= low:  return 0.0
        if ratio >= high: return 1.0
        return (ratio - low) / (high - low)

    @staticmethod
    def _score_alt_diff(diff_m: float) -> float:
        low, high = config.ALT_DIFF_WARN_M, config.ALT_DIFF_ALERT_M
        if diff_m <= low:  return 0.0
        if diff_m >= high: return 1.0
        return (diff_m - low) / (high - low)

    @staticmethod
    def _score_cell_range(range_m: float) -> float:
        """
        Map mean cell tower GPS range → score.
        Low range  (<150 m)  = good GPS  → 0.0
        High range (>2000 m) = poor GPS  → 1.0
        """
        low  = config.CELL_RANGE_NORMAL_M
        high = config.CELL_RANGE_ALERT_M
        if range_m <= low:  return 0.0
        if range_m >= high: return 1.0
        return (range_m - low) / (high - low)

    @staticmethod
    def _composite(r: CellResult) -> float:
        """
        Weighted composite score — weights adapt to available sources.
        """
        if r.has_adsb and r.has_cell:
            cw = config.CELL_WEIGHT          # 0.30
            aw = 1.0 - cw                    # 0.70  (split 60/40 → 42/28)
            return aw * 0.60 * r.mlat_score + aw * 0.40 * r.alt_score + cw * r.cell_score
        if r.has_adsb:
            return 0.60 * r.mlat_score + 0.40 * r.alt_score
        # cell only
        return r.cell_score

    @staticmethod
    def _classify(confidence: float) -> str:
        if confidence >= config.CONFIDENCE_CRITICAL: return "CRITICAL"
        if confidence >= config.CONFIDENCE_ALERT:    return "ALERT"
        if confidence >= config.CONFIDENCE_WARN:     return "WARNING"
        return "CLEAR"

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------

    def _grid_bin(self, series: pd.Series) -> pd.Series:
        return (series // self._cell_deg) * self._cell_deg
