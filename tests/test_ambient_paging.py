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
    """Behaves like Ambient appears to: only readings within about a day before endDate come back,
    so a request whose window falls inside an outage returns nothing."""
    end = end_ms if end_ms is not None else max(TIMES) + 1
    pool = [r for r in DATA if end - limit * STEP < r["dateutc"] <= end]
    return sorted(pool, key=lambda r: -r["dateutc"])[:limit]


def fake_history_newest_first(base, mac, end_ms=None, limit=288):
    """The simpler behavior: the newest `limit` readings at or before endDate, whatever the gap."""
    pool = [r for r in DATA if end_ms is None or r["dateutc"] <= end_ms]
    return sorted(pool, key=lambda r: -r["dateutc"])[:limit]


def test_a_long_outage_does_not_end_the_history():
    # a 20-day outage: windows inside it are empty, so the loop must step back until data resumes
    gap_start = T0 + 864 * STEP
    gap_end = gap_start + 20 * 288 * STEP
    times = [T0 + i * STEP for i in range(864)] + [gap_end + i * STEP for i in range(864)]
    data = [{"dateutc": t, "tempf": 50.0} for t in times]

    def history(base, mac, end_ms=None, limit=288):
        end = end_ms if end_ms is not None else max(times) + 1
        return sorted([r for r in data if end - limit * STEP < r["dateutc"] <= end], key=lambda r: -r["dateutc"])[:limit]

    with mock.patch.object(amb, "history", history), mock.patch.object(amb.time, "time", lambda: (max(times) + STEP) / 1000):
        rows = amb.fetch_all("b", "m")
    assert [r["dateutc"] for r in rows] == times


def test_the_true_start_of_history_ends_the_search():
    calls = []

    def history(base, mac, end_ms=None, limit=288):
        calls.append(end_ms)
        return fake_history(base, mac, end_ms, limit)

    with mock.patch.object(amb, "history", history):
        amb.fetch_all("b", "m", max_empty_days=5)
    assert len(calls) < 60  # stopped soon after the data ran out, not hundreds of requests later


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




def test_a_dropped_connection_is_retried_not_fatal():
    import http.client
    import urllib.error

    attempts = {"n": 0}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def flaky(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise http.client.IncompleteRead(b"")   # the exact error that crashed the first long run
        return Resp()

    with mock.patch.object(amb.urllib.request, "urlopen", flaky), mock.patch.object(amb, "PAUSE", 0), \
         mock.patch.object(amb.time, "sleep", lambda s: None):
        status, data = amb.get("https://x", "/devices")
    assert (status, data, attempts["n"]) == (200, [], 3)


def test_a_failure_that_will_not_go_away_keeps_the_rows_already_fetched():
    calls = {"n": 0}

    def history(base, mac, end_ms=None, limit=288):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("Ambient request kept failing after 6 tries (IncompleteRead)")
        return fake_history(base, mac, end_ms, limit)

    with mock.patch.object(amb, "history", history):
        try:
            amb.fetch_all("b", "m")
            raise AssertionError("expected FetchInterrupted")
        except amb.FetchInterrupted as e:
            assert len(e.rows) == 576 and e.rows == sorted(e.rows, key=lambda r: r["dateutc"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
