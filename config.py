"""
Global configuration for GPS Jamming Detection System.
All tuneable parameters are centralized here.
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# OpenSky Network API
# ---------------------------------------------------------------------------
OPENSKY_BASE_URL = "https://opensky-network.org/api"
OPENSKY_STATES_ENDPOINT = "/states/all"

# Anonymous rate-limit: 1 request / 10 s  (registered: 1 / 5 s)
OPENSKY_REQUEST_INTERVAL_ANON = 10       # seconds between polls (anonymous)
OPENSKY_REQUEST_INTERVAL_AUTH = 5        # seconds between polls (authenticated)
OPENSKY_TIMEOUT = 30                     # HTTP timeout in seconds
OPENSKY_MAX_RETRIES = 4
OPENSKY_RETRY_BACKOFF = [2, 4, 8, 16]   # exponential back-off seconds

# OpenSky state-vector column indices
COL_ICAO24          = 0
COL_CALLSIGN        = 1
COL_ORIGIN_COUNTRY  = 2
COL_TIME_POSITION   = 3
COL_LAST_CONTACT    = 4
COL_LONGITUDE       = 5
COL_LATITUDE        = 6
COL_BARO_ALTITUDE   = 7
COL_ON_GROUND       = 8
COL_VELOCITY        = 9
COL_TRUE_TRACK      = 10
COL_VERTICAL_RATE   = 11
COL_SENSORS         = 12
COL_GEO_ALTITUDE    = 13
COL_SQUAWK          = 14
COL_SPI             = 15
COL_POSITION_SOURCE = 16   # 0=ADS-B, 1=ASTERIX, 2=MLAT, 3=FLARM

# Human-readable source names
POSITION_SOURCE_NAMES = {0: "ADS-B", 1: "ASTERIX", 2: "MLAT", 3: "FLARM"}

# ---------------------------------------------------------------------------
# Geographic grid
# ---------------------------------------------------------------------------
GRID_CELL_DEG = 1.0          # degrees (lat × lon) per detection cell
GRID_MIN_AIRCRAFT = 3        # minimum aircraft in a cell to run analysis

# ---------------------------------------------------------------------------
# Jamming detection thresholds
# ---------------------------------------------------------------------------

# MLAT / non-GPS ratio: cells with ratio above this are suspicious
MLAT_RATIO_WARN    = 0.40    # 40 % MLAT → warning
MLAT_RATIO_ALERT   = 0.65    # 65 % MLAT → alert

# Barometric vs geometric altitude discrepancy
# Normal baro/geo difference is ≤ ~30 m (ISA model + QNH correction)
# GPS jamming causes geo_altitude to jump while baro stays stable
ALT_DIFF_WARN_M    = 100     # metres
ALT_DIFF_ALERT_M   = 300     # metres

# Composite confidence score thresholds (0–1 scale)
CONFIDENCE_WARN    = 0.40
CONFIDENCE_ALERT   = 0.65
CONFIDENCE_CRITICAL = 0.80

# ---------------------------------------------------------------------------
# Output / caching
# ---------------------------------------------------------------------------
DATA_CACHE_DIR  = "data"
OUTPUT_DIR      = "output"
MAP_FILENAME    = "jamming_map.html"
REPORT_FILENAME = "jamming_report.json"
PLOT_FILENAME   = "jamming_analysis.png"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = "INFO"
