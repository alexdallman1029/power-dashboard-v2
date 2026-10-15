import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from billing import views as V

W = os.path.join(os.path.dirname(__file__), "..", "data", "weather.sqlite")
E = os.path.join(os.path.dirname(__file__), "..", "data", "energy.sqlite")
pytestmark = pytest.mark.skipif(not os.path.exists(W), reason="weather data not built yet")


def test_daily_weather_uses_complete_days_and_sane_values():
    w = sqlite3.connect(W)
    d = V.weather_daily(w, dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    assert len(d) == 30 and (d["Hours"] >= 23).all()
    assert (d["Low °F"] <= d["Avg °F"]).all() and (d["Avg °F"] <= d["High °F"]).all()
    assert (d["Rain in"] >= 0).all()


def test_pacific_and_utc_days_differ_but_cover_the_same_period():
    w = sqlite3.connect(W)
    a = V.weather_daily(w, dt.date(2026, 6, 1), dt.date(2026, 6, 30), tz="America/Los_Angeles")
    b = V.weather_daily(w, dt.date(2026, 6, 1), dt.date(2026, 6, 30), tz="UTC")
    assert len(a) == len(b) == 30
    assert abs(a["Rain in"].sum() - b["Rain in"].sum()) < 0.5  # same rain, split across slightly different days


def test_cycle_summary_has_last_year_for_a_full_cycle():
    w = sqlite3.connect(W)
    s = V.cycle_weather_summary(w, "2026-06")
    assert s["now"] and s["last_year"]
    assert s["now"]["low"] <= s["now"]["avg"] <= s["now"]["high"]


def test_energy_vs_temp_lines_up_days_and_skips_gaps():
    e, w = sqlite3.connect(E), sqlite3.connect(W)
    m = V.energy_vs_temp(e, w, 417380, dt.date(2025, 6, 1), dt.date(2026, 6, 1))
    assert len(m) > 300 and m["day"].is_unique and (m["kWh"] > 0).all()
    assert m["kWh"].corr(m["Avg °F"]) < -0.5  # RV sites use more power when it is colder
