"""Single source of truth for per-site kWh inputs and charges.

Used by the Streamlit dashboard, billing/send_bills.py and billing/report.py so
the amounts on the dashboard and in tenant texts cannot disagree.
Who is billed / how many people: billing/sites.toml.
"""
import sqlite3

import pandas as pd

from billing.config import load_config
from billing.period import billing_period_for_ym
from billing.tenant_charges import calculate_site_charges

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


def _site_kwh(conn: sqlite3.Connection, billing_ym: str) -> dict[str, float]:
    cfg = load_config()
    start, end = billing_period_for_ym(billing_ym)
    return {
        site.name: _channel_kwh(conn, site.device_gid, site.channel, start.isoformat(), end.isoformat())
        for site in cfg.sites
    }


def vacant_site_usage(conn: sqlite3.Connection, billing_ym: str) -> dict[str, float]:
    """Metered kWh at sites with nobody in them, when above the alert threshold.

    That usage is added to the shared shop (see tenant_charges_for_ym) so the
    landlord isn't left covering it, but it usually means something is on that
    shouldn't be -- worth finding out why.
    """
    cfg = load_config()
    kwh = _site_kwh(conn, billing_ym)
    return {
        site.name: kwh[site.name]
        for site in cfg.sites
        if cfg.people(site, billing_ym) == 0 and kwh[site.name] > cfg.vacant_alert_kwh
    }


def tenant_charges_for_ym(conn: sqlite3.Connection, billing_ym: str) -> pd.DataFrame:
    cfg = load_config()
    start, end = billing_period_for_ym(billing_ym)
    s, e = start.isoformat(), end.isoformat()

    rv_total = _device_total_pref_main(conn, RV_SITES_GID, s, e)
    pole_sub_total = _device_total_pref_main(conn, POLE_BARN_SUB_GID, s, e)
    pole_main_total = _device_main_kwh(conn, POLE_BARN_MAIN_GID, s, e)

    site_kwh = _site_kwh(conn, billing_ym)
    site_people = {site.name: cfg.people(site, billing_ym) for site in cfg.sites}
    occupied = [site for site in cfg.sites if site_people[site.name] > 0]
    vacant = [site for site in cfg.sites if site_people[site.name] == 0]

    # Occupied sites on the pole barn main panel sit outside the sub-panel,
    # so they are carved out of the shared shop.
    on_main = sum(site_kwh[s_.name] for s_ in occupied if s_.device_gid == POLE_BARN_MAIN_GID)
    # Metered use at vacant sites nobody is billed for goes into the shared shop
    # (vacant main-panel sites are already left in it by the line above).
    vacant_extra = sum(site_kwh[s_.name] for s_ in vacant if s_.device_gid != POLE_BARN_MAIN_GID)

    return calculate_site_charges(
        site_kwh=site_kwh,
        site_people=site_people,
        unbilled_people=cfg.unbilled(billing_ym),
        kitchen_lounge_kwh=pole_sub_total - rv_total,
        shared_shop_kwh=max(0.0, pole_main_total - pole_sub_total - on_main) + vacant_extra,
        rate=cfg.rate_per_kwh,
        fee=cfg.monthly_fee,
        base_kwh=cfg.base_kwh,
    )
