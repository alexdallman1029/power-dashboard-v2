"""Load electric-usage-downloader CSV output into data/energy.sqlite.

    python -m ingest.load_smarthub_csv chunk1.csv chunk2.csv ...

Table smarthub_usage_hourly holds the utility's own readings (what the co-op bills
you for), separate from the Emporia usage_daily table. Re-running is safe: rows
upsert on (start_ms, meter).

Notes on the source data:
  * Times are Unix milliseconds. The downloader shifts them using the CURRENT
    UTC offset, so hours around a daylight-saving change can be off by one.
    Fine for daily/monthly totals.
  * cost_cents is SmartHub's own estimate from Usage Explorer, not your invoice.
"""
import csv
import sqlite3
import sys

DB_PATH = "data/energy.sqlite"

SCHEMA = """
create table if not exists smarthub_usage_hourly (
    start_ms   integer not null,
    end_ms     integer not null,
    meter      text    not null default '',
    wh         integer not null,
    cost_cents integer,
    updated_at text    not null default (datetime('now')),
    primary key (start_ms, meter)
)
"""

UPSERT = """
insert into smarthub_usage_hourly (start_ms, end_ms, meter, wh, cost_cents)
values (?, ?, ?, ?, ?)
on conflict (start_ms, meter) do update set
    end_ms = excluded.end_ms,
    wh = excluded.wh,
    cost_cents = excluded.cost_cents,
    updated_at = datetime('now')
"""


def load(paths: list[str]) -> int:
    rows = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                cost = r["CostInCents"].strip()
                rows.append(
                    (
                        int(r["StartUnixMillis"]),
                        int(r["EndUnixMillis"]),
                        (r["MeterName"] or "").strip(),
                        int(r["WattHours"]),
                        int(cost) if cost else None,
                    )
                )
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(SCHEMA)
        conn.executemany(UPSERT, rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m ingest.load_smarthub_csv FILE.csv [...]")
    n = load(sys.argv[1:])
    print(f"Loaded {n} hourly rows into smarthub_usage_hourly")
