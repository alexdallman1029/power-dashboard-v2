"""Power metered at a vacant site goes to the owners' share, never into tenant bills."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing import views as V
from billing.compute import (
    POLE_BARN_MAIN_GID, POLE_BARN_SUB_GID, _device_main_kwh, _device_total_pref_main,
    _pool_inputs, owner_share_for_ym, tenant_charges_for_ym,
)
from billing.period import billing_period_for_ym

DB = os.path.join(os.path.dirname(__file__), "..", "data", "energy.sqlite")
YM = "2026-09"  # Middle Site and Airstream vacant, both with some usage


def test_vacant_usage_is_excluded_from_the_tenant_shop_pool():
    conn = sqlite3.connect(DB)
    p = _pool_inputs(conn, YM)
    s, e = (d.isoformat() for d in billing_period_for_ym(YM))
    main = _device_main_kwh(conn, POLE_BARN_MAIN_GID, s, e)
    sub = _device_total_pref_main(conn, POLE_BARN_SUB_GID, s, e)
    # Airstream (vacant, on the main panel) is carved out of the shop like an occupied main-panel site
    assert abs(p["shared_shop_kwh"] - (main - sub - p["site_kwh"]["Airstream"])) < 1e-9
    assert abs(p["vacant_kwh"] - (p["site_kwh"]["Middle Site"] + p["site_kwh"]["Airstream"])) < 1e-9
    assert p["vacant_kwh"] > 0


def test_tenant_bills_do_not_depend_on_vacant_usage_but_the_owners_total_does():
    conn = sqlite3.connect(DB)
    bills = tenant_charges_for_ym(conn, YM)
    assert "vacant_kwh" not in bills.columns
    o = owner_share_for_ym(conn, YM)
    assert abs(o["total_kwh"] - (o["kwh"] + o["grow_kwh"] + o["vacant_kwh"])) < 1e-9
    assert o["vacant_kwh"] > 0


def test_every_measured_kwh_is_in_exactly_one_bucket():
    conn = sqlite3.connect(DB)
    months = V.month_range("2026-03", "2026-09")
    fl = V.farm_ledger(conn, months)
    gap = (fl["Tenants"] + fl["Owners"] + fl["Grow barn"] - fl["Emporia total"]).abs().max()
    assert gap < 1e-6


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
