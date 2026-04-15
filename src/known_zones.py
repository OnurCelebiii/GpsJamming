"""
Known GPS Jamming / Spoofing Zones Database.

Compiled from publicly available sources:
  - EASA Safety Information Bulletins (SIB 2022-02 R3, SIB 2023-01)
  - ICAO Air Navigation Commission publications
  - Eurocontrol MUAC GPS interference reports
  - FAA Special Notices and NOTAMs (public)
  - OPSGROUP GPS Jamming database (publicly reported regions)
  - Aviation press (AeroTime, The Air Current, Simple Flying)

Each zone has:
  name         : Short label shown on the map
  severity     : "CRITICAL" | "ALERT" | "WARNING"
  description  : What is causing the interference
  source       : Where this information comes from
  polygon      : List of [lon, lat] coordinates (GeoJSON order)

Note: These zones represent *documented* interference regions as of
early 2026.  Active status may change.  Always verify against current
NOTAMs before flight.
"""

from __future__ import annotations

KNOWN_ZONES: list[dict] = [

    # -------------------------------------------------------------------------
    # Israel / Palestinian Territories / Lebanon / Jordan
    # Active GPS jamming and spoofing by IDF since Oct 2023.
    # Aircraft report being shown over Cairo, Beirut, or the sea.
    # -------------------------------------------------------------------------
    {
        "name": "Israel / IDF GPS Jamming",
        "severity": "CRITICAL",
        "description": (
            "Continuous GPS jamming and spoofing by Israeli Defense Forces "
            "since October 2023.  Aircraft near LLBG (Tel Aviv) and LLHA (Haifa) "
            "frequently report GPS positions displaced by 40–200 km. "
            "EASA SIB 2022-02 R3 explicitly lists this region."
        ),
        "source": "EASA SIB 2022-02 R3 / OPSGROUP / FAA NOTAM",
        "polygon": [
            [34.2, 31.2], [36.0, 31.2], [36.0, 33.5],
            [35.7, 33.9], [35.1, 34.0], [34.9, 33.6],
            [34.2, 33.0], [34.2, 31.2],
        ],
    },

    # -------------------------------------------------------------------------
    # Lebanon / Southern Lebanon (Hezbollah electronic warfare zone)
    # -------------------------------------------------------------------------
    {
        "name": "Lebanon GPS Interference",
        "severity": "ALERT",
        "description": (
            "GPS interference reported in Lebanese airspace, particularly "
            "in southern Lebanon and Beirut FIR.  Linked to IDF operations "
            "and Hezbollah counter-measures.  Pilots report GNSS-UNRELIABLE "
            "NOTAMs issued by LCCC."
        ),
        "source": "LCCC NOTAMs / OPSGROUP",
        "polygon": [
            [35.1, 33.0], [36.6, 33.0], [36.6, 34.7],
            [35.8, 34.7], [35.1, 34.2], [35.1, 33.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Eastern Ukraine / Russia – active front line (2022–present)
    # Both Russian EW systems (Krasukha-4, Pole-21) and Ukrainian counter-EW
    # -------------------------------------------------------------------------
    {
        "name": "Eastern Ukraine War Zone (GPS EW)",
        "severity": "CRITICAL",
        "description": (
            "Active electronic warfare (GPS jamming / spoofing) by Russian "
            "and Ukrainian forces along the front line.  Russian Krasukha-4 "
            "and Pole-21 EW complexes confirmed active.  No civilian aviation "
            "permitted below FL260 in most of this area (EASA SIB 2023-01)."
        ),
        "source": "EASA SIB 2023-01 / Eurocontrol NOTAM / US DOT advisory",
        "polygon": [
            [31.0, 46.0], [40.0, 46.0], [40.0, 52.0],
            [31.0, 52.0], [31.0, 46.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Black Sea / Southern Russia
    # -------------------------------------------------------------------------
    {
        "name": "Black Sea GPS Spoofing Corridor",
        "severity": "ALERT",
        "description": (
            "Widespread GPS spoofing reported over the Black Sea since 2017, "
            "intensifying post-2022.  Ship AIS positions and aircraft GPS have "
            "been displaced.  Originates from Russian EW facilities in Crimea "
            "and the Krasnodar region."
        ),
        "source": "C4ADS spoofing report / OPSGROUP / Eurocontrol",
        "polygon": [
            [28.0, 41.0], [41.5, 41.0], [41.5, 46.5],
            [28.0, 46.5], [28.0, 41.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Baltic Sea / Kaliningrad region
    # Russian EW systems in Kaliningrad enclave (Iskander + EW brigade)
    # -------------------------------------------------------------------------
    {
        "name": "Baltic Sea / Kaliningrad EW",
        "severity": "ALERT",
        "description": (
            "GPS jamming from Russian EW assets in Kaliningrad affects aircraft "
            "flying over the Baltic Sea, southern Finland, Estonia, Latvia, and "
            "Lithuania.  MLAT-switch events documented by OpenSky and Flightradar24. "
            "Finnish CAA issued GPS unreliability warnings."
        ),
        "source": "Finnish CAA / EASA / OPSGROUP / OpenSky research",
        "polygon": [
            [18.0, 54.0], [28.0, 54.0], [28.0, 60.5],
            [18.0, 60.5], [18.0, 54.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Belarus
    # -------------------------------------------------------------------------
    {
        "name": "Belarus GPS Interference",
        "severity": "WARNING",
        "description": (
            "GPS interference reported in Belarusian airspace linked to "
            "Russian EW exercises and Belarusian military activities.  "
            "Most commercial traffic avoids Belarusian airspace (EASA ban)."
        ),
        "source": "EASA TEB2022004 / Eurocontrol",
        "polygon": [
            [23.0, 51.0], [32.5, 51.0], [32.5, 56.2],
            [23.0, 56.2], [23.0, 51.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Iran
    # -------------------------------------------------------------------------
    {
        "name": "Iran GPS Interference Zone",
        "severity": "ALERT",
        "description": (
            "Iran operates its own GPS jamming / spoofing infrastructure. "
            "Incidents documented at OIIE (IKA, Tehran) and along the "
            "Persian Gulf coast.  The US FAA and UK CAA have both issued "
            "safety bulletins on Iranian GPS interference."
        ),
        "source": "FAA SAFO 22007 / UK CAA AIC Y 037/2022 / OPSGROUP",
        "polygon": [
            [44.0, 25.0], [63.5, 25.0], [63.5, 40.0],
            [44.0, 40.0], [44.0, 25.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Syria / Iraq
    # Multiple actors: Russian, US coalition, Iranian, ISIS
    # -------------------------------------------------------------------------
    {
        "name": "Syria / Iraq GPS Jamming",
        "severity": "ALERT",
        "description": (
            "Complex GPS jamming environment with multiple actors: Russian "
            "forces near Latakia (ICAO area), US coalition EW in eastern Syria, "
            "Iranian-backed forces near Baghdad FIR.  ORBB (Baghdad) FIR has "
            "ongoing GPS unreliability NOTAMs."
        ),
        "source": "FAA / ICAO SIGMET / OPSGROUP",
        "polygon": [
            [35.5, 29.5], [48.5, 29.5], [48.5, 37.5],
            [35.5, 37.5], [35.5, 29.5],
        ],
    },

    # -------------------------------------------------------------------------
    # Moscow exclusion zone / Russia (Pskov / St. Petersburg area)
    # -------------------------------------------------------------------------
    {
        "name": "Moscow / NW Russia GPS Denial",
        "severity": "CRITICAL",
        "description": (
            "The Moscow area operates a permanent GPS denial zone protecting "
            "government and military assets.  Aircraft approaching Sheremetyevo "
            "(UUEE) and Domodedovo (UUDD) frequently experience GPS failures. "
            "North-western Russia near St. Petersburg and Pskov hosts major "
            "Russian EW brigades."
        ),
        "source": "C4ADS / OPSGROUP / Aviation research",
        "polygon": [
            [33.0, 54.5], [42.5, 54.5], [42.5, 58.5],
            [33.0, 58.5], [33.0, 54.5],
        ],
    },

    # -------------------------------------------------------------------------
    # China (South China Sea / East China Sea)
    # -------------------------------------------------------------------------
    {
        "name": "South / East China Sea GPS Spoofing",
        "severity": "WARNING",
        "description": (
            "Large-scale GPS spoofing detected in Chinese territorial waters "
            "and over the South China Sea, particularly around disputed island "
            "airstrips (Spratly Islands, Paracel Islands).  Ships and aircraft "
            "AIS/GPS displaced by up to 100 km in circular patterns consistent "
            "with Chinese PLA EW tactics."
        ),
        "source": "C4ADS 2019 report / MIT Lincoln Laboratory / OPSGROUP",
        "polygon": [
            [105.0, 3.0], [123.0, 3.0], [123.0, 26.0],
            [105.0, 26.0], [105.0, 3.0],
        ],
    },

    # -------------------------------------------------------------------------
    # North Korea border / Korean Peninsula
    # -------------------------------------------------------------------------
    {
        "name": "Korean Peninsula GPS Jamming (DPRK)",
        "severity": "ALERT",
        "description": (
            "North Korea operates mobile GPS jamming units that periodically "
            "affect civil aviation in South Korea and the Yellow Sea.  INCHEON "
            "FIR (RKRR) has issued multiple GPS unreliability NOTAMs.  "
            "Jamming events correlate with North Korean military exercises."
        ),
        "source": "ICAO / RKRR NOTAMs / South Korean MOLIT reports",
        "polygon": [
            [124.0, 34.0], [131.0, 34.0], [131.0, 42.5],
            [124.0, 42.5], [124.0, 34.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Persian Gulf / UAE / Saudi Arabia
    # -------------------------------------------------------------------------
    {
        "name": "Persian Gulf GPS Interference",
        "severity": "WARNING",
        "description": (
            "Sporadic GPS jamming reported in the Persian Gulf, particularly "
            "affecting OMAE (Dubai) and OEJN (Jeddah) approaches. "
            "Linked to Iranian naval EW exercises and Houthi-area EW "
            "activities in the Gulf of Aden."
        ),
        "source": "GCAA UAE NOTAMs / OPSGROUP",
        "polygon": [
            [48.0, 22.0], [57.0, 22.0], [57.0, 27.5],
            [48.0, 27.5], [48.0, 22.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Red Sea / Yemen / Horn of Africa
    # Houthi EW operations, US/UK maritime EW responses
    # -------------------------------------------------------------------------
    {
        "name": "Red Sea / Yemen GPS Jamming",
        "severity": "ALERT",
        "description": (
            "Active GPS jamming and spoofing in the Red Sea corridor linked "
            "to Houthi forces and US/UK naval operations.  Shipping AIS "
            "positions and aircraft GPS affected.  HYSF (Sana'a) FIR NOTAMs "
            "warn of GPS unreliability throughout Yemeni FIR."
        ),
        "source": "OPSGROUP / Lloyd's / Eurocontrol / HYSF NOTAMs",
        "polygon": [
            [32.0, 10.0], [45.0, 10.0], [45.0, 22.0],
            [32.0, 22.0], [32.0, 10.0],
        ],
    },

    # -------------------------------------------------------------------------
    # Finland / Norway border with Russia (Murmansk region)
    # -------------------------------------------------------------------------
    {
        "name": "Nordic-Russia Border EW",
        "severity": "WARNING",
        "description": (
            "GPS interference from Russian EW facilities near Murmansk and "
            "the Kola Peninsula affects northern Finnish and Norwegian airspace.  "
            "Documented MLAT switches in OpenSky data for aircraft transiting "
            "the EFIN (Helsinki) FIR northern sector."
        ),
        "source": "Finnish Trafi / OPSGROUP / OpenSky data analysis",
        "polygon": [
            [22.0, 68.0], [32.0, 68.0], [32.0, 72.0],
            [22.0, 72.0], [22.0, 68.0],
        ],
    },
]


def get_geojson_feature_collection() -> dict:
    """Return all known zones as a GeoJSON FeatureCollection."""
    features = []
    for zone in KNOWN_ZONES:
        coords = [[lon, lat] for lon, lat in zone["polygon"]]
        if coords[0] != coords[-1]:
            coords.append(coords[0])  # close ring
        features.append({
            "type": "Feature",
            "properties": {
                "name":        zone["name"],
                "severity":    zone["severity"],
                "description": zone["description"],
                "source":      zone["source"],
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords],
            },
        })
    return {"type": "FeatureCollection", "features": features}
