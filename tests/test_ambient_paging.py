"""Paging back through Ambient's history: complete, ordered, de-duplicated, gap-safe."""
import csv
import gzip
import os
import sqlite3
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AMBIENT_API_KEY", "k")
os.environ.setdefault("AMBIENT_APPLICATION_KEY", "a")

import ingest.backfill_ambient as bf
import shared.ambient as amb
import shared.weather as wx

STEP = 300_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % 3_600_000)
# 3 days of 5-minute data, a 2-day outage, then 3 more days
TIMES = [T0 + i * STEP for i in range(864)] + [T0 + (576 + 864 + i) * STEP for i in range(864)]
DATA = [
    {"dateutc": t, "tempf": 55.0, "humidity": 60, "windspeedmph": 3.0, "windgustmph": 5.0, "winddir": 200,
     "dailyrainin": 0.0, "solarradiation": 50.0, "uv": 0, "feelsLike": 54, "dewPoint": 40}
    for t in TIMES
]


def fake_history(base, mac, end_ms=None, limit=288):
    pool = [r for r in DATA if end_ms is None or r["dateutc"] <= end_ms]
    return sorted(pool, key=lambda r: -r["dateutc"])[:limit]


def test_fetch_all_gets_every_reading_in_order_across_an_outage():
    with mock.patch.object(amb, "history", fake_history):
        rows = amb.fetch_all("b", "m")
    assert [r["dateutc"] for r in rows] == TIMES


def test_stop_before_limits_how_far_back_it_goes():
    stop = TIMES[-1] - 86_400_000  # one day back
    with mock.patch.object(amb, "history", fake_history):
        rows = amb.fetch_all("b", "m", stop_before_ms=stop)
    assert 288 <= len(rows) <= 288 * 3 and rows[-1]["dateutc"] == TIMES[-1]


def test_backfill_writes_a_full_csv_and_hourly_rows():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "w.sqlite")
    real_save = wx.save_hourly
    with mock.patch.object(amb, "history", fake_history), \
         mock.patch.object(bf, "find_base", lambda: ("b", [{"macAddress": "m"}])), \
         mock.patch.object(bf, "save_hourly", lambda h: real_save(h, db)):
        bf.main(os.path.join(tmp, "out.csv.gz"))
    with gzip.open(os.path.join(tmp, "out.csv.gz"), "rt") as f:
        assert sum(1 for _ in csv.DictReader(f)) == len(DATA)
    conn = sqlite3.connect(db)
    assert conn.execute("select count(*) from weather_hourly where readings = 12").fetchone()[0] == len(DATA) // 12


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
