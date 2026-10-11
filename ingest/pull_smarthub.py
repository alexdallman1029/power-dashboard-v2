"""Pull the utility's MONTHLY kWh from SmartHub into data/energy.sqlite.

    python -m ingest.pull_smarthub [months_back]      # default 24

Table smarthub_usage_monthly holds what the co-op itself recorded (the basis of your
power bill), separate from the Emporia usage_daily table. Re-running is safe.
Blachly-Lane's portal offers monthly data only (no hourly or daily).
"""
import datetime as dt
import sqlite3
import sys

from shared.smarthub import login, monthly_kwh, poll_usage

DB_PATH = "data/energy.sqlite"

SCHEMA = """
create table if not exists smarthub_usage_monthly (
    month      text primary key,   -- 'YYYY-MM' as labeled by SmartHub
    kwh        real not null,
    updated_at text not null default (datetime('now'))
)
"""

UPSERT = """
insert into smarthub_usage_monthly (month, kwh) values (?, ?)
on conflict (month) do update set kwh = excluded.kwh, updated_at = datetime('now')
"""


def save(rows: list[tuple[str, float]], db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        conn.executemany(UPSERT, rows)
        conn.commit()
    finally:
        conn.close()


def main(months_back: int = 24) -> None:
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    start = now - dt.timedelta(days=31 * months_back)
    rows = monthly_kwh(poll_usage(login(), "MONTHLY", start, now))
    if not rows:
        raise RuntimeError("SmartHub returned no monthly usage; refusing to continue")
    save(rows)
    print(f"Saved {len(rows)} months from SmartHub:")
    for month, kwh in rows:
        print(f"  {month}: {kwh:,.0f} kWh")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
