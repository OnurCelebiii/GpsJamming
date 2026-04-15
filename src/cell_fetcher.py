"""
OpenCelliD cell tower GPS quality fetcher.

Why cell tower data detects GPS jamming
----------------------------------------
OpenCelliD is a crowd-sourced database where mobile phones submit:
  - The cell towers they see (MCC / MNC / LAC / CID)
  - Their GPS position at that moment
  - GPS accuracy (expressed as `range` in metres)
  - Number of GPS-confirmed submissions (`samples`)

When a GPS jammer is active in an area:
  1. Phones lose GPS lock  → submissions drop  (low `samples` density)
  2. Phones get poor GPS   → large position error (high `range`)

This lets us detect jamming at GROUND LEVEL, complementing ADS-B which
only sees airborne aircraft.

API reference: https://opencellid.org/#zoom=16&lat=37.77889&lng=-122.41943
Free key at:   https://opencellid.org/users/sign_up

Environment variable
--------------------
  OPENCELLID_KEY=<your_token>  python main.py ...

Without the key the cell layer is silently skipped (graceful degradation).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

OPENCELLID_URL = "https://api.opencellid.org/cell/getInArea"

# OpenCelliD limits each bbox request to ~0.5° × 0.5° safely
_TILE_DEG = 5.0          # degrees per tile request (API allows larger but rate-limits)
_REQUEST_DELAY = 1.5     # seconds between tiled requests (free-tier safe)

CELL_COLUMNS = [
    "radio", "mcc", "mnc", "lac", "cid",
    "longitude", "latitude", "range", "samples",
    "changeable", "created", "updated", "averageSignal",
]


class CellFetcher:
    """
    Fetches cell tower data from OpenCelliD for a bounding box.

    Returns a DataFrame with one row per cell tower.  The key columns for
    jamming detection are:
      - `range`   : estimated GPS positioning error (metres) when the tower
                    was crowd-sourced.  High values indicate poor GPS.
      - `samples` : number of GPS-confirmed measurements.  Low values in an
                    area with many towers indicate GPS dropout.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._key = api_key or os.environ.get("OPENCELLID_KEY")
        if not self._key:
            logger.warning(
                "OPENCELLID_KEY not set. Cell tower layer disabled. "
                "Get a free key at https://opencellid.org/users/sign_up"
            )
        self._session = requests.Session()

    @property
    def available(self) -> bool:
        return bool(self._key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_bbox(
        self,
        lat_min: float,
        lon_min: float,
        lat_max: float,
        lon_max: float,
        cache_dir: str = config.DATA_CACHE_DIR,
    ) -> pd.DataFrame:
        """
        Fetch all cell towers inside the given bounding box.

        Large bboxes are automatically tiled into smaller requests.
        Results are cached to disk.
        """
        if not self.available:
            return pd.DataFrame(columns=CELL_COLUMNS)

        cache_path = self._cache_path(lat_min, lon_min, lat_max, lon_max, cache_dir)
        if cache_path.exists():
            logger.info("Cell cache hit → %s", cache_path)
            return self._load_cache(cache_path)

        tiles = list(self._make_tiles(lat_min, lon_min, lat_max, lon_max))
        logger.info("Fetching cell towers in %d tile(s) …", len(tiles))

        frames: list[pd.DataFrame] = []
        for i, (la1, lo1, la2, lo2) in enumerate(tiles, 1):
            logger.debug("Tile %d/%d: %.2f,%.2f → %.2f,%.2f", i, len(tiles), la1, lo1, la2, lo2)
            df_tile = self._fetch_tile(la1, lo1, la2, lo2)
            if not df_tile.empty:
                frames.append(df_tile)
            if i < len(tiles):
                time.sleep(_REQUEST_DELAY)

        if not frames:
            logger.warning("No cell tower data returned.")
            return pd.DataFrame(columns=CELL_COLUMNS)

        df = pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["mcc", "mnc", "lac", "cid"]
        )
        logger.info("Cell towers fetched: %d", len(df))

        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        df.to_json(cache_path, orient="records")
        logger.info("Cell cache saved → %s", cache_path)
        return df

    def fetch_global_sample(
        self,
        grid_deg: float = config.GRID_CELL_DEG,
        sample_step: float = 10.0,
        cache_dir: str = config.DATA_CACHE_DIR,
    ) -> pd.DataFrame:
        """
        Fetch a coarse global sample of cell towers by stepping through
        representative grid points.  Used when no bbox is specified.

        `sample_step` controls how far apart the sampled tiles are in degrees.
        Smaller = more coverage but more API calls.
        """
        frames: list[pd.DataFrame] = []
        lats = list(range(-60, 75, int(sample_step)))
        lons = list(range(-180, 180, int(sample_step)))
        total = len(lats) * len(lons)
        logger.info("Global cell sample: %d tiles …", total)

        for i, lat in enumerate(lats):
            for lon in lons:
                df_tile = self._fetch_tile(lat, lon, lat + sample_step, lon + sample_step)
                if not df_tile.empty:
                    frames.append(df_tile)
                time.sleep(_REQUEST_DELAY)

        if not frames:
            return pd.DataFrame(columns=CELL_COLUMNS)
        return pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["mcc", "mnc", "lac", "cid"]
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_tile(
        self,
        lat_min: float, lon_min: float,
        lat_max: float, lon_max: float,
    ) -> pd.DataFrame:
        params = {
            "key":    self._key,
            "BBOX":   f"{lat_min},{lon_min},{lat_max},{lon_max}",
            "format": "json",
        }
        try:
            resp = self._session.get(
                OPENCELLID_URL,
                params=params,
                timeout=config.OPENSKY_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                cells = data.get("cells") or []
                if not cells:
                    return pd.DataFrame(columns=CELL_COLUMNS)
                df = pd.DataFrame(cells)
                for col in ["range", "samples", "averageSignal"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                for col in ["latitude", "longitude"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
            elif resp.status_code == 429:
                logger.warning("OpenCelliD rate-limited — sleeping 5s")
                time.sleep(5)
            else:
                logger.error("OpenCelliD HTTP %d: %s", resp.status_code, resp.text[:200])
        except requests.exceptions.RequestException as exc:
            logger.error("OpenCelliD request error: %s", exc)
        return pd.DataFrame(columns=CELL_COLUMNS)

    @staticmethod
    def _make_tiles(
        lat_min: float, lon_min: float,
        lat_max: float, lon_max: float,
    ):
        lat = lat_min
        while lat < lat_max:
            lon = lon_min
            while lon < lon_max:
                yield (lat, lon, min(lat + _TILE_DEG, lat_max), min(lon + _TILE_DEG, lon_max))
                lon += _TILE_DEG
            lat += _TILE_DEG

    @staticmethod
    def _cache_path(la1, lo1, la2, lo2, cache_dir: str) -> Path:
        tag = f"{la1:.0f}_{lo1:.0f}_{la2:.0f}_{lo2:.0f}"
        return Path(cache_dir) / f"cells_{tag}.json"

    @staticmethod
    def _load_cache(path: Path) -> pd.DataFrame:
        df = pd.read_json(path, orient="records")
        for col in ["range", "samples", "averageSignal", "latitude", "longitude"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
