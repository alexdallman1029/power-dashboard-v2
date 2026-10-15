"""Daily: add the newest Ambient Weather readings to the hourly weather table.

    python -m ingest.pull_ambient [days_back]       # default 3

Re-reads the last few days and rewrites those hours, so a missed day fixes itself.
"""
import sys
import time

from shared.ambient import fetch_all, find_base
from shared.weather import aggregate_hourly, save_hourly


def main(days_back: int = 3) -> None:
    base, devices = find_base()
    mac = devices[0]["macAddress"]
    stop = int((time.time() - days_back * 86400) * 1000)
    rows = fetch_all(base, mac, stop_before_ms=stop)
    if not rows:
        raise RuntimeError("Ambient returned no readings")
    hourly = aggregate_hourly(rows)
    if len(hourly) > 1:
        hourly = hourly.iloc[1:]  # the oldest hour is cut off by the window, so it would be incomplete
    n = save_hourly(hourly)
    print(f"Saved {n} hourly rows ({len(rows):,} readings).")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
