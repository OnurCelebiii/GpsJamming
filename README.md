# GNSS Jamming Tracker

Real-time global GNSS signal jamming detection using live satellite GPS data from aircraft ADS-B transponders via OpenSky Network.

## What It Does

- Fetches real-time aircraft GPS data from OpenSky Network every 10 minutes
- Detects GNSS anomalies: position source degradation (GPS → MLAT fallback), geometric vs barometric altitude discrepancies
- Renders a live world heatmap of jamming-affected areas
- Highlights known hotspots: Ukraine/Russia, Middle East, Baltic, North Korea
- WebSocket push for instant frontend updates

## Data Source

**OpenSky Network** (`opensky-network.org`) — crowdsourced ADS-B flight data from 5000+ receivers worldwide.

Anomalies detected per aircraft:
1. `position_source != 0` → aircraft fell back from GPS-ADS-B to MLAT/ASTERIX (jamming indicator)
2. `|geo_altitude - baro_altitude| > 300 m` → GPS altitude spoofing/jamming
3. `spi = true` → special purpose indicator (abnormal condition)

## Quick Start

```bash
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`

## Stack

- **Backend**: Python FastAPI + httpx + WebSocket
- **Frontend**: Leaflet.js (dark CartoDB tiles) + vanilla JS
- **Data**: OpenSky Network REST API (free, no auth required)
