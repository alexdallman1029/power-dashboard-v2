"""Single source of truth for per-tenant kWh inputs and charges.

Used by both the Streamlit dashboard and billing/send_bills.py so the amounts
shown on the dashboard and the amounts texted to tenants cannot disagree.
"""
import datetime as dt
import sqlite3

import pandas as pd

from billing.period import billing_period_for_ym
from billing.tenant_charges import calculate_tenant_charges

RV_SITES_GID = 417380
POLE_BARN_MAIN_GID = 422491
POLE_BARN_SUB_GID = 432058


def _scalar(conn, sql: str, params: tuple):
    return conn.execute(sql, params).fetchone()


def _device_main_kwh(conn, gid: int, start: str, end: str) -> float:
    (v,) = _scalar(
        conn,
        "select sum(kwh) from usage_daily "
        "where device_gid = ? and channel_num = 0 and day >= ? and day <= ?",
        (gid, start, end),
    )
    return float(v) if v is not None else 0.0


def _device_total_pref_main(conn, gid: int, start: str, end: str) -> float:
    main, sub = _scalar(
        conn,
        "select sum(case when channel_num = 0 then kwh else 0 end), "
        "sum(case when channel_num != 0 then kwh else 0 end) "
        "from usage_daily where device_gid = ? and day >= ? and day <= ?",
        (gid, start, end),
    )
    main, sub = float(main or 0.0), float(sub or 0.0)
    return main if main > 0 else sub


def _channel_kwh(conn, gid: int, channel_name: str, start: str, end: str) -> float:
    (v,) = _scalar(
        conn,
        "select sum(kwh) from usage_daily "
        "where device_gid = ? and day >= ? and day <= ? "
        "and lower(trim(coalesce(channel_name,''))) = lower(trim(?))",
        (gid, start, end, channel_name),
    )
    return float(v) if v is not None else 0.0


def tenant_charges_for_period(
    conn: sqlite3.Connection, start: dt.date, end: dt.date
) -> pd.DataFrame:
    s, e = start.isoformat(), end.isoformat()

    rv_total = _device_total_pref_main(conn, RV_SITES_GID, s, e)
    pole_sub_total = _device_total_pref_main(conn, POLE_BARN_SUB_GID, s, e)
    pole_main_total = _device_main_kwh(conn, POLE_BARN_MAIN_GID, s, e)

    airstream_kwh = _channel_kwh(conn, POLE_BARN_MAIN_GID, "Airstream", s, e)
    ryan_kwh = _channel_kwh(conn, RV_SITES_GID, "Ryan", s, e)
    marc_kwh = _channel_kwh(conn, RV_SITES_GID, "Marc", s, e)
    kelly_russ_kwh = _channel_kwh(conn, RV_SITES_GID, "Kelly & Russ", s, e)

    return calculate_tenant_charges(
        kelly_russ_kwh=kelly_russ_kwh,
        airstream_kwh=airstream_kwh,
        ryan_kwh=ryan_kwh,
        marc_kwh=marc_kwh,
        kitchen_lounge_kwh=pole_sub_total - rv_total,
        shared_shop_kwh=max(0.0, pole_main_total - pole_sub_total - airstream_kwh),
    )


def tenant_charges_for_ym(conn: sqlite3.Connection, billing_ym: str) -> pd.DataFrame:
    start, end = billing_period_for_ym(billing_ym)
    return tenant_charges_for_period(conn, start, end)
