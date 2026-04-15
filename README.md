# GPS Jamming Detection System

Real-time GPS jamming detection using live ADS-B data from the [OpenSky Network](https://opensky-network.org/).  
This methodology is standard in academic GNSS interference research.

---

## How It Works

Aircraft equipped with ADS-B transponders broadcast their position using onboard GPS receivers.  
When GPS signals are jammed or spoofed, two measurable anomalies appear in the ADS-B data stream:

### Indicator 1 — MLAT Ratio (Primary)
| Source | `position_source` value | Meaning |
|--------|------------------------|---------|
| ADS-B  | `0` | Aircraft GPS is working normally |
| MLAT   | `2` | Aircraft lost GPS; position computed by ground station multilateration |

A geographic cell where **≥ 40 % of aircraft have switched to MLAT** is suspicious.  
A cell with **≥ 65 % MLAT** triggers an alert.

### Indicator 2 — Barometric / Geometric Altitude Discrepancy (Secondary)
- **`baro_altitude`** — derived from air pressure (immune to GPS jamming)  
- **`geo_altitude`** — derived from the GPS/GNSS receiver (vulnerable)

When a jammer corrupts GNSS signals the geometric altitude diverges from barometric altitude by **hundreds of metres** while baro stays stable.

### Composite Confidence Score
Both indicators are normalised to `[0, 1]` and combined (60 % MLAT weight, 40 % altitude weight):

| Level    | Confidence | Colour |
|----------|-----------|--------|
| CLEAR    | < 0.40    | Green  |
| WARNING  | 0.40–0.65 | Yellow |
| ALERT    | 0.65–0.80 | Orange |
| CRITICAL | ≥ 0.80    | Red    |

---

## Installation

```bash
git clone https://github.com/OnurCelebiii/GpsJamming.git
cd GpsJamming
pip install -r requirements.txt
```

---

## Usage

### Single snapshot (anonymous, free)
```bash
python main.py
```
Fetches the current global ADS-B snapshot from OpenSky, runs detection, and writes:
- `output/jamming_map.html` — interactive Folium map
- `output/jamming_analysis.png` — static analysis charts
- `output/report_<timestamp>.json` — machine-readable report

### Restrict to a bounding box
```bash
# Middle East / Eastern Mediterranean (known active jamming region)
python main.py --bbox 28 25 42 55

# Eastern Europe / Black Sea
python main.py --bbox 44 22 52 40

# Baltic Sea region
python main.py --bbox 54 18 60 30
```

### Continuous monitoring
```bash
# Poll every 60 s for 2 hours
python main.py --monitor --interval 60 --duration 7200
```
Also generates `output/trend.png` after the second poll and logs persistent hotspots.

### Load a cached snapshot (offline / replay)
```bash
python main.py --from-cache data/opensky_20260414T120000Z.json
```

### Authenticated access (higher rate limit)
```bash
export OPENSKY_USER=your_username
export OPENSKY_PASS=your_password
python main.py --monitor --interval 30 --duration 3600
```
Register for free at [opensky-network.org](https://opensky-network.org/index.php?option=com_users&view=registration).

### All options
```
python main.py --help

optional arguments:
  --from-cache PATH         Load cached JSON instead of live API
  --monitor                 Continuous polling mode
  --bbox LAT_MIN LON_MIN LAT_MAX LON_MAX
  --interval SECONDS        Poll interval for --monitor (default: 60)
  --duration SECONDS        Stop after N seconds, 0=forever (default: 0)
  --cell-deg FLOAT          Grid cell size in degrees (default: 1.0)
  --min-aircraft INT        Min aircraft per cell (default: 3)
  --no-map                  Skip HTML map generation
  --no-chart                Skip PNG chart generation
  --show-all-cells          Draw CLEAR cells on the map too
  --output-dir PATH         Output directory (default: output/)
  --verbose / -v            Enable DEBUG logging
```

---

## Project Structure

```
GpsJamming/
├── main.py              # Entry point & CLI
├── config.py            # All tuneable parameters
├── requirements.txt
├── src/
│   ├── fetcher.py       # OpenSky Network REST API client
│   ├── detector.py      # Jamming detection engine (grid + scoring)
│   ├── analyzer.py      # Statistical analysis & JSON serialisation
│   └── visualizer.py    # Folium map + matplotlib charts
├── data/                # Cached API snapshots (auto-created)
└── output/              # Detection results (auto-created)
    ├── jamming_map.html
    ├── jamming_analysis.png
    ├── trend.png
    └── report_<ts>.json
```

---

## Data Source

[OpenSky Network](https://opensky-network.org/) — a non-profit, community-based receiver network  
collecting real ADS-B, MLAT, and other aviation data worldwide.

**API limits:**
| Access | Rate limit |
|--------|-----------|
| Anonymous | 1 request / 10 seconds |
| Registered (free) | 1 request / 5 seconds |

Raw data is cached under `data/` as timestamped JSON files for offline replay and audit.

---

## References

- Mitch, R. et al. (2011). *Signal Characteristics of Civil GPS Jammers*. ION GNSS 2011.
- Shepard, D. et al. (2012). *Evaluation of Smart Grid and Civilian UAV Vulnerability to GPS Spoofing Attacks*. ION GNSS 2012.
- OpenSky Network REST API documentation: <https://openskynetwork.github.io/opensky-api/rest.html>
- Schafer, M. et al. (2014). *Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research*. IPSN 2014.

---

## License

MIT License — see `LICENSE` for details.
