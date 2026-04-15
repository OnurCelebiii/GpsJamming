"""
OpenSky Network ADS-B data fetcher.

Pulls real-time state vectors from the OpenSky REST API and returns a
clean pandas DataFrame.  Supports optional HTTP Basic Auth for registered
accounts (higher rate limits).

Reference: https://openskynetwork.github.io/opensky-api/rest.html
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

import config

logger = logging.getLogger(__name__)

# DataFrame column names that match OpenSky state-vector indices
COLUMNS = [
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
]


class OpenSkyFetcher:
    """Fetches ADS-B state vectors from the OpenSky Network REST API."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._auth: Optional[HTTPBasicAuth] = None
        if username and password:
            self._auth = HTTPBasicAuth(username, password)
            self._interval = config.OPENSKY_REQUEST_INTERVAL_AUTH
            logger.info("OpenSky: authenticated mode (user=%s)", username)
        else:
            self._interval = config.OPENSKY_REQUEST_INTERVAL_ANON
            logger.info("OpenSky: anonymous mode (rate-limit: 1 req / %ds)", self._interval)

        self._last_request_ts: float = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all_states(
        self,
        bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> pd.DataFrame:
        """
        Fetch all current state vectors.

        Parameters
        ----------
        bbox : (lat_min, lon_min, lat_max, lon_max) in degrees, optional
            If provided, restricts the query to that bounding box.

        Returns
        -------
        pd.DataFrame
            One row per aircraft with typed columns.  Empty DataFrame on error.
        """
        self._rate_limit_wait()
        params: dict = {}
        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            params = {
                "lamin": lat_min,
                "lomin": lon_min,
                "lamax": lat_max,
                "lomax": lon_max,
            }

        url = config.OPENSKY_BASE_URL + config.OPENSKY_STATES_ENDPOINT
        raw = self._get_with_retry(url, params=params)
        if raw is None:
            return pd.DataFrame(columns=COLUMNS)

        states = raw.get("states") or []
        if not states:
            logger.warning("OpenSky returned 0 state vectors.")
            return pd.DataFrame(columns=COLUMNS)

        df = pd.DataFrame(states, columns=COLUMNS)
        df = self._cast_types(df)

        fetch_ts = raw.get("time", int(time.time()))
        df["fetch_time"] = datetime.fromtimestamp(fetch_ts, tz=timezone.utc)

        logger.info(
            "Fetched %d aircraft state vectors (ts=%s)",
            len(df),
            df["fetch_time"].iloc[0].isoformat() if len(df) else "n/a",
        )
        return df

    def fetch_and_cache(
        self,
        bbox: Optional[tuple[float, float, float, float]] = None,
        cache_dir: str = config.DATA_CACHE_DIR,
    ) -> pd.DataFrame:
        """
        Fetch state vectors and save a timestamped JSON snapshot to disk.

        Returns the same DataFrame as fetch_all_states().
        """
        df = self.fetch_all_states(bbox=bbox)
        if df.empty:
            return df

        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cache_path = Path(cache_dir) / f"opensky_{ts}.json"

        # Serialise: convert non-JSON-native types
        records = df.drop(columns=["fetch_time"]).to_dict(orient="records")
        cache_path.write_text(json.dumps(records, default=str), encoding="utf-8")
        logger.info("Snapshot cached → %s", cache_path)
        return df

    def fetch_multi(
        self,
        n: int = config.MULTI_SNAPSHOT_COUNT,
        bbox: Optional[tuple[float, float, float, float]] = None,
        cache_dir: str = config.DATA_CACHE_DIR,
    ) -> pd.DataFrame:
        """
        Fetch `n` consecutive snapshots and return a merged DataFrame.

        Merging multiple snapshots:
        - Increases the number of aircraft observations per grid cell
        - Stabilises MLAT/non-GPS ratios (reduces single-snapshot noise)
        - Improves altitude variance computation (more samples per cell)

        The `fetch_time` column is preserved per-row so the detector can
        distinguish snapshots if needed.
        """
        frames: list[pd.DataFrame] = []
        for i in range(n):
            logger.info("Snapshot %d/%d …", i + 1, n)
            df = self.fetch_and_cache(bbox=bbox, cache_dir=cache_dir)
            if not df.empty:
                frames.append(df)
            if i < n - 1:
                self._rate_limit_wait()

        if not frames:
            logger.error("All %d snapshot attempts returned empty.", n)
            return pd.DataFrame(columns=COLUMNS)

        merged = pd.concat(frames, ignore_index=True)
        logger.info(
            "Merged %d snapshots → %d total records (%d unique aircraft)",
            len(frames), len(merged),
            merged["icao24"].nunique() if "icao24" in merged.columns else 0,
        )
        return merged

    @staticmethod
    def load_cached(path: str) -> pd.DataFrame:
        """Load a previously cached JSON snapshot into a DataFrame."""
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        df = pd.DataFrame(records, columns=COLUMNS)
        df = OpenSkyFetcher._cast_types(df)
        logger.info("Loaded %d records from %s", len(df), path)
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit_wait(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self._interval:
            wait = self._interval - elapsed
            logger.debug("Rate-limit: sleeping %.1f s", wait)
            time.sleep(wait)

    def _get_with_retry(self, url: str, params: dict) -> Optional[dict]:
        backoff = config.OPENSKY_RETRY_BACKOFF
        for attempt in range(config.OPENSKY_MAX_RETRIES):
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    auth=self._auth,
                    timeout=config.OPENSKY_TIMEOUT,
                )
                self._last_request_ts = time.time()
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = backoff[min(attempt, len(backoff) - 1)]
                    logger.warning("Rate-limited (429). Waiting %ds before retry %d.", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.error("HTTP %d from OpenSky: %s", resp.status_code, resp.text[:200])
                return None
            except requests.exceptions.Timeout:
                wait = backoff[min(attempt, len(backoff) - 1)]
                logger.warning("Timeout on attempt %d/%d. Retry in %ds.", attempt + 1, config.OPENSKY_MAX_RETRIES, wait)
                time.sleep(wait)
            except requests.exceptions.RequestException as exc:
                logger.error("Request error: %s", exc)
                return None
        logger.error("All %d retries exhausted.", config.OPENSKY_MAX_RETRIES)
        return None

    @staticmethod
    def _cast_types(df: pd.DataFrame) -> pd.DataFrame:
        numeric_cols = [
            "longitude", "latitude", "baro_altitude", "geo_altitude",
            "velocity", "true_track", "vertical_rate",
            "time_position", "last_contact",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "position_source" in df.columns:
            df["position_source"] = pd.to_numeric(df["position_source"], errors="coerce").astype("Int64")

        if "on_ground" in df.columns:
            df["on_ground"] = df["on_ground"].astype(bool)

        if "callsign" in df.columns:
            df["callsign"] = df["callsign"].str.strip()

        return df
