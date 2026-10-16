import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from billing import views as V

W = os.path.join(os.path.dirname(__file__), "..", "data", "weather.sqlite")
pytestmark = pytest.mark.skipif(not os.path.exists(W), reason="weather data not built yet")


def test_compass_sectors_are_45_degrees_wide_and_wrap():
    assert [V.compass_point(d) for d in (0, 22, 23, 90, 180, 270, 337, 338, 359, 360)] == \
        ["N", "N", "NE", "E", "S", "W", "NW", "N", "N", "N"]
    assert V.compass_point(None) is None and V.compass_point(float("nan")) is None


def test_rain_adds_up_the_same_way_at_every_resolution():
    w = sqlite3.connect(W)
    a, b = dt.date(2025, 3, 1), dt.date(2025, 3, 31)
    hourly = V.weather_series(w, a, b, "Hour")["Rain in"].sum()
    daily = V.weather_series(w, a, b, "Day")["Rain in"].sum()
    monthly = V.weather_series(w, a, b, "Month")["Rain in"].sum()
    assert abs(hourly - daily) < 0.05 and abs(daily - monthly) < 0.05


def test_temperature_extremes_survive_aggregation():
    w = sqlite3.connect(W)
    a, b = dt.date(2025, 1, 1), dt.date(2025, 1, 31)
    h, m = V.weather_series(w, a, b, "Hour"), V.weather_series(w, a, b, "Month")
    assert m["Temp high °F"].iloc[0] == h["Temp high °F"].max()
    assert m["Temp low °F"].iloc[0] == h["Temp low °F"].min()
    assert h["Temp low °F"].min() <= m["Temp avg °F"].iloc[0] <= h["Temp high °F"].max()


def test_degree_days_follow_the_base_temperature():
    w = sqlite3.connect(W)
    a, b = dt.date(2025, 1, 1), dt.date(2025, 12, 31)
    hdd65 = V.weather_series(w, a, b, "Year", base_f=65)["Heating degree days"].iloc[0]
    hdd55 = V.weather_series(w, a, b, "Year", base_f=55)["Heating degree days"].iloc[0]
    cdd65 = V.weather_series(w, a, b, "Year", base_f=65)["Cooling degree days"].iloc[0]
    assert hdd55 < hdd65 and 3000 < hdd65 < 7000 and cdd65 >= 0
    day = V.weather_series(w, dt.date(2025, 1, 10), dt.date(2025, 1, 10), "Day", base_f=65).iloc[0]
    assert abs(day["Heating degree days"] - max(0, 65 - day["Temp avg °F"])) < 0.02   # one day: base minus its average


def test_hourly_resolution_blanks_the_per_day_measures():
    w = sqlite3.connect(W)
    h = V.weather_series(w, dt.date(2025, 6, 1), dt.date(2025, 6, 2), "Hour")
    assert h["Heating degree days"].isna().all() and h["Rainy days"].isna().all() and len(h) == 48


def test_rainy_days_use_the_wet_day_cutoff():
    w = sqlite3.connect(W)
    d = V.weather_series(w, dt.date(2025, 2, 1), dt.date(2025, 2, 28), "Day")
    m = V.weather_series(w, dt.date(2025, 2, 1), dt.date(2025, 2, 28), "Month")
    assert int(m["Rainy days"].iloc[0]) == int((d["Rain in"] >= V.RAINY_DAY_IN - 1e-6).sum())


def test_wind_rose_percentages_total_100_and_prevailing_is_a_compass_point():
    ws = V.wind_by_season(sqlite3.connect(W))
    assert set(ws["rose"]) == {"Winter", "Spring", "Summer", "Fall"}
    for season, table in ws["rose"].items():
        assert abs(table.to_numpy().sum() - 100.0) < 0.01
        assert ws["stats"][season]["prevailing"] in V.COMPASS
    assert len(V.wind_insights(ws["stats"])) == 4


def test_climate_status_and_winter_table_from_a_small_frame():
    clim = pd.DataFrame({
        "Period": pd.to_datetime(["2026-02-01", "2026-05-01", "2026-08-01", "2025-01-01", "2024-01-01"]),
        "oni": [-0.39, 0.5, 2.16, -0.46, 1.84], "oni_season": ["DJF", "AMJ", "JAS", "DJF", "DJF"],
        "pdo": [-1.0, -1.7, -1.9, -1.4, -1.5],
    })
    now = V.climate_now(clim)
    assert now["phase"] == "El Niño" and now["strength"] == "very strong" and abs(now["oni"] - 2.16) < 1e-9
    assert abs(now["oni_3mo_ago"] - 0.5) < 1e-9
    winters = V.winter_summary(sqlite3.connect(W), clim)
    assert {"Winter", "Avg temp °F", "Rain in", "ENSO (ONI)"} <= set(winters.columns) and len(winters) >= 2


def test_year_overlay_table_has_only_complete_months():
    m = V.weather_month_of_year(sqlite3.connect(W))
    assert not m.empty and {"Year", "MonthNum", "Rain in"} <= set(m.columns)
    assert (m["Hours"] >= 0.85 * m["Period"].dt.days_in_month * 24).all()
