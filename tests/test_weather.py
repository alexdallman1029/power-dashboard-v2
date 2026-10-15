import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.weather import aggregate_hourly, save_hourly

H = 3600_000
T0 = 1_760_000_000_000 - (1_760_000_000_000 % H)  # an exact UTC hour boundary


def reading(i, **kw):
    base = dict(dateutc=T0 + i * 300_000, tempf=60.0, humidity=50.0, windspeedmph=5.0, windgustmph=8.0,
                winddir=90.0, dailyrainin=0.0, solarradiation=100.0, uv=1.0, feelsLike=59.0, dewPoint=40.0)
    base.update(kw)
    return base


def test_hour_grouping_and_basic_stats():
    rows = [reading(i, tempf=60 + i) for i in range(12)] + [reading(12, tempf=80)]
    out = aggregate_hourly(rows)
    assert list(out["readings"]) == [12, 1]
    first = out.iloc[0]
    assert first["temp_f_min"] == 60 and first["temp_f_max"] == 71 and first["temp_f_avg"] == 65.5


def test_rain_comes_from_increases_in_the_running_daily_total():
    # 0.00 -> 0.02 -> 0.02 -> 0.05 : 0.05 in
    rows = [reading(0, dailyrainin=0.0), reading(1, dailyrainin=0.02), reading(2, dailyrainin=0.02), reading(3, dailyrainin=0.05)]
    assert aggregate_hourly(rows).iloc[0]["rain_in"] == 0.05


def test_rain_total_resetting_at_midnight_is_not_negative_rain():
    # total was 0.30 for the day, resets to 0.01 after midnight: the new rain is 0.01, not -0.29
    rows = [reading(0, dailyrainin=0.30), reading(1, dailyrainin=0.30), reading(2, dailyrainin=0.01), reading(3, dailyrainin=0.03)]
    out = aggregate_hourly(rows)
    assert abs(out.iloc[0]["rain_in"] - 0.03) < 1e-9


def test_wind_direction_averages_as_a_vector():
    rows = [reading(0, winddir=350.0), reading(1, winddir=10.0)]
    d = aggregate_hourly(rows).iloc[0]["wind_dir_deg"]
    assert d < 1 or d > 359  # north, not 180


def test_missing_fields_and_duplicates_do_not_crash():
    rows = [reading(0), reading(0), {"dateutc": T0 + 300_000, "tempf": 70.0}]
    out = aggregate_hourly(rows)
    assert out.iloc[0]["readings"] == 2


def test_save_is_idempotent_and_updates_in_place():
    path = os.path.join(tempfile.mkdtemp(), "w.sqlite")
    out = aggregate_hourly([reading(i) for i in range(12)])
    assert save_hourly(out, path) == 1
    assert save_hourly(aggregate_hourly([reading(i, tempf=70) for i in range(12)]), path) == 1
    conn = sqlite3.connect(path)
    assert conn.execute("select count(*), max(temp_f_avg) from weather_hourly").fetchone() == (1, 70.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
