"""Turn Ambient Weather's 5-minute readings into hourly values and store them.

Hourly rows go in data/weather.sqlite (a separate, small file, so the daily energy updates
don't re-store weather history). Times are UTC, matching the energy data's day boundaries.
"""
from __future__ import annotations

import math
import sqlite3

import pandas as pd

WEATHER_DB = "data/weather.sqlite"

SCHEMA = """
create table if not exists weather_hourly (
    hour_utc      text primary key,   -- 'YYYY-MM-DD HH:00' (UTC)
    readings      integer not null,   -- 5-minute readings that went into this hour (12 = complete)
    temp_f_avg    real, temp_f_min real, temp_f_max real,
    feels_like_f  real,
    dew_point_f   real,
    humidity_avg  real,
    wind_mph_avg  real,
    gust_mph_max  real,
    wind_dir_deg  real,               -- vector mean of the readings
    rain_in       real,               -- rain that fell during the hour
    solar_wm2_avg real, solar_wm2_max real,
    uv_max        real
)
"""

NUMERIC = ["tempf", "feelsLike", "dewPoint", "humidity", "windspeedmph", "windgustmph", "winddir",
           "dailyrainin", "solarradiation", "uv"]


def _rain_increments(df: pd.DataFrame) -> pd.Series:
    """Rain per reading from the running 'rain today' total, which resets each local midnight.

    Increase between readings = new rain. A drop means the total reset, so the new value is itself
    the rain since the reset. The very first reading has no previous value, so it contributes 0.
    """
    d = df["dailyrainin"].astype(float)
    step = d.diff()
    step = step.where(step >= 0, d)  # reset: what has fallen since the reset
    step.iloc[0] = 0.0
    return step.fillna(0.0)


def aggregate_hourly(rows: list[dict]) -> pd.DataFrame:
    """List of Ambient readings (dicts with dateutc in ms) -> one row per UTC hour."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["dateutc"].notna()].copy()
    for c in NUMERIC:
        if c not in df:
            df[c] = float("nan")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("dateutc").sort_values("dateutc").reset_index(drop=True)
    df["hour"] = pd.to_datetime(df["dateutc"], unit="ms", utc=True).dt.floor("h")
    df["rain_step"] = _rain_increments(df) if df["dailyrainin"].notna().any() else 0.0
    # Wind direction: average as vectors, since 350 and 10 degrees average to north, not 180
    rad = df["winddir"] * math.pi / 180.0
    df["wx"], df["wy"] = rad.map(math.sin), rad.map(math.cos)

    g = df.groupby("hour")
    out = pd.DataFrame(
        {
            "readings": g["dateutc"].count(),
            "temp_f_avg": g["tempf"].mean(), "temp_f_min": g["tempf"].min(), "temp_f_max": g["tempf"].max(),
            "feels_like_f": g["feelsLike"].mean(),
            "dew_point_f": g["dewPoint"].mean(),
            "humidity_avg": g["humidity"].mean(),
            "wind_mph_avg": g["windspeedmph"].mean(),
            "gust_mph_max": g["windgustmph"].max(),
            "rain_in": g["rain_step"].sum(),
            "solar_wm2_avg": g["solarradiation"].mean(), "solar_wm2_max": g["solarradiation"].max(),
            "uv_max": g["uv"].max(),
        }
    )
    wx, wy = g["wx"].mean(), g["wy"].mean()
    out["wind_dir_deg"] = (wx.combine(wy, lambda a, b: math.degrees(math.atan2(a, b)) % 360 if pd.notna(a) and pd.notna(b) else float("nan")))
    out = out.round(2)
    out.index = out.index.strftime("%Y-%m-%d %H:00")
    out.index.name = "hour_utc"
    return out.reset_index()


COLUMNS = ["hour_utc", "readings", "temp_f_avg", "temp_f_min", "temp_f_max", "feels_like_f", "dew_point_f",
           "humidity_avg", "wind_mph_avg", "gust_mph_max", "wind_dir_deg", "rain_in", "solar_wm2_avg",
           "solar_wm2_max", "uv_max"]


def save_hourly(hourly: pd.DataFrame, db_path: str = WEATHER_DB) -> int:
    if hourly.empty:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        marks = ",".join("?" * len(COLUMNS))
        updates = ",".join(f"{c}=excluded.{c}" for c in COLUMNS[1:])
        rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
                for r in hourly[COLUMNS].itertuples(index=False, name=None)]
        conn.executemany(
            f"insert into weather_hourly ({','.join(COLUMNS)}) values ({marks}) "
            f"on conflict (hour_utc) do update set {updates}", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()
