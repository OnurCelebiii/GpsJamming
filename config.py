"""
Global configuration for GPS Jamming Detection System.
All tuneable parameters are centralized here.
"""

# ---------------------------------------------------------------------------
# OpenSky Network API
# ---------------------------------------------------------------------------
OPENSKY_BASE_URL = "https://opensky-network.org/api"
OPENSKY_STATES_ENDPOINT = "/states/all"

OPENSKY_REQUEST_INTERVAL_ANON = 10
OPENSKY_REQUEST_INTERVAL_AUTH = 5
OPENSKY_TIMEOUT = 30
OPENSKY_MAX_RETRIES = 4
OPENSKY_RETRY_BACKOFF = [2, 4, 8, 16]

# State-vector column indices
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

POSITION_SOURCE_NAMES = {0: "ADS-B", 1: "ASTERIX", 2: "MLAT", 3: "FLARM"}

# Multi-snapshot: fetch this many consecutive snapshots and merge
# More snapshots = more stable statistics, fewer false positives
MULTI_SNAPSHOT_COUNT = 3

# ---------------------------------------------------------------------------
# Geographic grid
# ---------------------------------------------------------------------------
GRID_CELL_DEG  = 0.5   # 0.5° × 0.5° cells — 4× more detail than 1°
GRID_MIN_AIRCRAFT = 2  # lower minimum to detect sparse coverage areas

# ---------------------------------------------------------------------------
# Indicator 1 — Non-GPS ratio  (MLAT source=2 + ASTERIX source=1)
# Both indicate the aircraft is NOT using its own GPS for position.
# At CRUISE altitude (>6000m), non-GPS position is extremely unusual.
# ---------------------------------------------------------------------------
NON_GPS_RATIO_WARN   = 0.20   # 20 % non-GPS aircraft → warning
NON_GPS_RATIO_ALERT  = 0.40   # 40 % non-GPS aircraft → alert

# Altitude multiplier: finding non-GPS aircraft at cruise altitude is a
# much stronger jamming signal than near an airport.
HIGH_ALT_M           = 6000   # metres ≈ FL200
HIGH_ALT_MULTIPLIER  = 1.5    # multiply non-GPS ratio by this at cruise alt

# ---------------------------------------------------------------------------
# Indicator 2 — Altitude variance
# std dev of (geo_alt − baro_alt) within a cell.
# All aircraft in a cell see the same geoid offset → variance should be low.
# GPS spoofing causes some aircraft to get wrong altitudes → high variance.
# ---------------------------------------------------------------------------
ALT_VARIANCE_WARN_M  = 50    # metres std-dev
ALT_VARIANCE_ALERT_M = 300   # metres std-dev

# ---------------------------------------------------------------------------
# Indicator 3 — Mean altitude discrepancy (raised scale vs v1)
# A single aircraft with 4000m geo-baro diff should reach ALERT, not just 40%.
# ---------------------------------------------------------------------------
ALT_MEAN_WARN_M  = 200    # metres   (was 100)
ALT_MEAN_ALERT_M = 2000   # metres   (was 300)

# ---------------------------------------------------------------------------
# Indicator 4 — Position staleness ratio
# last_contact − time_position > threshold → GPS position is stale while
# the aircraft is still broadcasting.  GPS has been lost.
# ---------------------------------------------------------------------------
POS_STALE_THRESH_S = 30    # seconds: if age > this, GPS considered stale
POS_STALE_WARN     = 0.20  # 20 % stale aircraft → warning
POS_STALE_ALERT    = 0.40  # 40 % stale aircraft → alert

# ---------------------------------------------------------------------------
# Composite confidence score weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
W_NON_GPS    = 0.40   # non-GPS ratio      (primary — MLAT/ASTERIX based jamming)
W_ALT_VAR    = 0.30   # altitude variance  (GPS spoofing / corruption)
W_ALT_MEAN   = 0.20   # mean alt diff      (secondary altitude check)
W_POS_STALE  = 0.10   # position staleness (GPS dropout)

# Classification thresholds
CONFIDENCE_WARN     = 0.35   # lowered from 0.40 for better sensitivity
CONFIDENCE_ALERT    = 0.55   # lowered from 0.65
CONFIDENCE_CRITICAL = 0.75   # lowered from 0.80

# ---------------------------------------------------------------------------
# Cell tower GPS quality  (OpenCelliD)
# ---------------------------------------------------------------------------
CELL_RANGE_NORMAL_M = 150
CELL_RANGE_WARN_M   = 500
CELL_RANGE_ALERT_M  = 2000
CELL_MIN_TOWERS     = 2
CELL_WEIGHT         = 0.30

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
