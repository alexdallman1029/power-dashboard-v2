"""One-time: pull ALL available Ambient Weather history, keep every 5-minute reading as a file
you can download, and store hourly values for the dashboard.

    python -m ingest.backfill_ambient OUT.csv.gz
"""
import csv
import gzip
import sys
from datetime import datetime, timezone

from shared.ambient import fetch_all, find_base
from shared.weather import aggregate_hourly, save_hourly


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def main(out_path: str) -> None:
    base, devices = find_base()
    mac = devices[0]["macAddress"]
    print(f"Keys accepted; {len(devices)} station(s). Reading the full history (about one request per second)...")
    rows = fetch_all(base, mac)
    if not rows:
        raise RuntimeError("Ambient returned no history")
    print(f"Got {len(rows):,} readings from {_day(rows[0]['dateutc'])} to {_day(rows[-1]['dateutc'])} (UTC).")

    # every reading, every field, as a downloadable file
    fields = sorted({k for r in rows for k in r})
    with gzip.open(out_path, "wt", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")

    n = save_hourly(aggregate_hourly(rows))
    print(f"Saved {n:,} hourly rows to the weather database.")


if __name__ == "__main__":
    main(sys.argv[1])
