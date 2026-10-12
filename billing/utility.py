"""Compare what the utility billed (SmartHub bills) with what Emporia measured.

Reads the smarthub_* tables written by ingest/pull_smarthub_bills.py. The farm meter is
every utility meter except the house meter (billing/sites.toml, [utility]).

Emporia days are taken over the bill's meter-reading dates, [read_from, read_to), which
matches the bill's day count. Emporia's day boundaries are UTC, so expect small edge
differences.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

from billing.compute import _device_total_pref_main
from billing.config import load_config

COLUMNS = [
    "Bill date", "Read from", "Read to", "Days",
    "Farm kWh", "Farm $", "House kWh", "House $",
    "Emporia kWh", "Unaccounted kWh", "Unaccounted %", "Farm $/kWh", "Checked",
]


def has_bill_tables(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    return {"smarthub_bills", "smarthub_bill_meters"} <= names


def reconcile(conn: sqlite3.Connection) -> pd.DataFrame:
    cfg = load_config()
    rows = []
    bills = conn.execute(
        "select distinct b.bill_ts, b.bill_date from smarthub_bills b "
        "join smarthub_bill_meters m on m.bill_ts = b.bill_ts order by b.bill_date"
    ).fetchall()
    for bill_ts, bill_date in bills:
        meters = conn.execute(
            "select meter, read_from, read_to, days, usage_kwh, total_charges, lines_ok "
            "from smarthub_bill_meters where bill_ts = ?",
            (bill_ts,),
        ).fetchall()
        farm = [m for m in meters if m[0] != cfg.house_meter]
        house = [m for m in meters if m[0] == cfg.house_meter]
        if not farm:
            continue
        read_from, read_to = min(m[1] for m in farm), max(m[2] for m in farm)
        last_day = (dt.date.fromisoformat(read_to) - dt.timedelta(days=1)).isoformat()
        emporia = sum(
            _device_total_pref_main(conn, gid, read_from, last_day) for gid in cfg.farm_emporia_devices
        )
        farm_kwh = sum(m[4] for m in farm)
        farm_cost = sum(m[5] or 0.0 for m in farm)
        unaccounted = farm_kwh - emporia
        rows.append(
            {
                "Bill date": bill_date,
                "Read from": read_from,
                "Read to": read_to,
                "Days": max(m[3] for m in farm),
                "Farm kWh": farm_kwh,
                "Farm $": farm_cost,
                "House kWh": sum(m[4] for m in house),
                "House $": sum(m[5] or 0.0 for m in house),
                "Emporia kWh": emporia,
                "Unaccounted kWh": unaccounted,
                "Unaccounted %": (unaccounted / farm_kwh * 100.0) if farm_kwh else None,
                "Farm $/kWh": (farm_cost / farm_kwh) if farm_kwh else None,
                "Checked": all(m[6] for m in meters),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)
