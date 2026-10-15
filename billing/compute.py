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
        site.name: (
            _channel_kwh(conn, site.device_gid, site.channel, start.isoformat(), end.isoformat())
            if site.device_gid is not None and site.channel
            else 0.0  # no meter yet: shares the pools but has no usage of its own
        )
        for site in cfg.sites
    }


def vacant_site_usage(conn: sqlite3.Connection, billing_ym: str) -> dict[str, float]:
    """Metered kWh at sites with nobody in them, when above the alert threshold.

    That usage is counted in the owners' share (see owner_share_for_ym), never in tenant
    bills, but it usually means something is on that shouldn't be -- worth finding out why.
    """
    cfg = load_config()
    kwh = _site_kwh(conn, billing_ym)
    return {
        site.name: kwh[site.name]
        for site in cfg.sites
        if cfg.people(site, billing_ym) == 0 and kwh[site.name] > cfg.vacant_alert_kwh
    }


def _pool_inputs(conn: sqlite3.Connection, billing_ym: str, people_override: dict | None = None) -> dict:
    """The kWh inputs shared by the tenant bills and the owner summary.

    `people_override` ({site name: people}) is for what-if views: it replaces the people count
    from sites.toml for the named sites. Real billing never passes it.
    """
    cfg = load_config()
    start, end = billing_period_for_ym(billing_ym)
    s, e = start.isoformat(), end.isoformat()

    rv_total = _device_total_pref_main(conn, RV_SITES_GID, s, e)
    pole_sub_total = _device_total_pref_main(conn, POLE_BARN_SUB_GID, s, e)
    pole_main_total = _device_main_kwh(conn, POLE_BARN_MAIN_GID, s, e)

    site_kwh = _site_kwh(conn, billing_ym)
    site_people = {site.name: cfg.people(site, billing_ym) for site in cfg.sites}
    site_people.update({k: int(v) for k, v in (people_override or {}).items() if k in site_people})
    occupied = [site for site in cfg.sites if site_people[site.name] > 0]
    vacant = [site for site in cfg.sites if site_people[site.name] == 0]

    # Sites on the pole barn main panel sit outside the sub-panel, so they are carved out of the
    # shared shop whether or not anyone lives there.
    on_main = sum(site_kwh[s_.name] for s_ in cfg.sites if s_.device_gid == POLE_BARN_MAIN_GID)
    # Power metered at a VACANT site is not split among tenants: it is counted in the owners' share.
    # (Vacant RV sites are already outside the kitchen/lounge, since that is sub-panel minus RV sites.)
    vacant_kwh = sum(site_kwh[s_.name] for s_ in vacant)

    return dict(
        site_kwh=site_kwh,
        site_people=site_people,
        unbilled_people=cfg.unbilled(billing_ym),
        kitchen_lounge_kwh=pole_sub_total - rv_total,
        shared_shop_kwh=max(0.0, pole_main_total - pole_sub_total - on_main),
        rate=cfg.rate_per_kwh,
        fee=cfg.monthly_fee,
        base_kwh=cfg.base_kwh,
        vacant_kwh=vacant_kwh,
    )


def tenant_charges_for_ym(
    conn: sqlite3.Connection, billing_ym: str, people_override: dict | None = None
) -> pd.DataFrame:
    inputs = _pool_inputs(conn, billing_ym, people_override)
    inputs.pop("vacant_kwh")  # goes to the owners' share, not into any tenant's bill
    return calculate_site_charges(**inputs)


def owner_share_for_ym(conn: sqlite3.Connection, billing_ym: str) -> dict:
    """Farm power Emporia assigns to the owners rather than tenants, for a billing month:
    the unbilled residents' share of the shared pools, power metered at vacant sites, and the
    grow barn.

    Base load and the shared shop are divided across ALL people, so the owners' portion is
    unbilled / (billed + unbilled) of those two pools. Dollars use the tenant kWh rate.
    """
    p = _pool_inputs(conn, billing_ym)
    billed = sum(n for n in p["site_people"].values() if n > 0)
    unbilled = p["unbilled_people"]
    everyone = billed + unbilled
    pool_kwh = p["base_kwh"] + p["shared_shop_kwh"]
    kwh = pool_kwh * unbilled / everyone if everyone else 0.0
    cfg = load_config()
    start, end = billing_period_for_ym(billing_ym)
    grow = (
        _device_total_pref_main(conn, cfg.grow_barn_device, start.isoformat(), end.isoformat())
        if cfg.grow_barn_device is not None
        else 0.0
    )
    return {
        "kwh": kwh,                      # unbilled residents' share of base + shop
        "vacant_kwh": p["vacant_kwh"],   # metered at sites nobody lives in
        "grow_kwh": grow,                # grow barn: farm power tenants never pay for
        "total_kwh": kwh + grow + p["vacant_kwh"],
        "total_cost": (kwh + grow + p["vacant_kwh"]) * p["rate"],
        "unbilled_people": unbilled,
        "all_people": everyone,
        "pool_kwh": pool_kwh,
    }
