"""Large-scale climate indices from NOAA: ENSO (the Oceanic Nino Index) and the PDO.

Both are monthly, free, and published as plain text:
  ONI  https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt   (Climate Prediction Center)
  PDO  https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat   (NCEI, ERSSTv5)

Stored in data/weather.sqlite, table climate_monthly(month, oni, oni_season, pdo).
"""
from __future__ import annotations

import sqlite3
import time
import urllib.request

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
PDO_URL = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat"
WEATHER_DB = "data/weather.sqlite"

# The ONI is a 3-month running mean; each season is labeled by its middle month (DJF -> January).
SEASON_CENTER = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
                 "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}

SCHEMA = """
create table if not exists climate_monthly (
    month      text primary key,   -- 'YYYY-MM' (for the ONI, the middle month of its 3-month season)
    oni        real,               -- Oceanic Nino Index, degrees C
    oni_season text,               -- e.g. 'JAS' (July-August-September)
    pdo        real                -- Pacific Decadal Oscillation index
)
"""


def parse_oni(text: str) -> dict[str, tuple[float, str]]:
    """{'YYYY-MM': (anomaly, season)} from the CPC file ('SEAS YR TOTAL ANOM')."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] not in SEASON_CENTER:
            continue
        season, year, _total, anom = parts
        out[f"{int(year):04d}-{SEASON_CENTER[season]:02d}"] = (float(anom), season)
    return out


def parse_pdo(text: str) -> dict[str, float]:
    """{'YYYY-MM': value} from the NCEI file (a row per year, twelve months; 99.99 means missing)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13 or not parts[0].isdigit():
            continue
        for m, v in enumerate(parts[1:], start=1):
            value = float(v)
            if abs(value) < 90:  # the file marks months that have not happened yet as 99.99
                out[f"{int(parts[0]):04d}-{m:02d}"] = value
    return out


def phase(oni: float | None) -> str | None:
    """NOAA's definition: El Nino at +0.5 or above, La Nina at -0.5 or below, otherwise neutral."""
    if oni is None:
        return None
    return "El Niño" if oni >= 0.5 else "La Niña" if oni <= -0.5 else "Neutral"


def strength(oni: float | None) -> str | None:
    """The usual bands by size: weak 0.5-0.9, moderate 1.0-1.4, strong 1.5-1.9, very strong 2.0+."""
    if oni is None or abs(oni) < 0.5:
        return None
    a = abs(oni)
    return "weak" if a < 1.0 else "moderate" if a < 1.5 else "strong" if a < 2.0 else "very strong"


def fetch(url: str, attempts: int = 4) -> str:
    last = None
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fishdog-dashboard"}), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # network trouble: wait and try again
            last = e
            time.sleep(2 * i)
    raise RuntimeError(f"Could not download {url}: {type(last).__name__}")


def save(oni: dict, pdo: dict, db_path: str = WEATHER_DB) -> int:
    months = sorted(set(oni) | set(pdo))
    rows = [(m, oni.get(m, (None, None))[0], oni.get(m, (None, None))[1], pdo.get(m)) for m in months]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        conn.executemany(
            "insert into climate_monthly (month, oni, oni_season, pdo) values (?,?,?,?) "
            "on conflict (month) do update set oni = excluded.oni, oni_season = excluded.oni_season, pdo = excluded.pdo",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)
