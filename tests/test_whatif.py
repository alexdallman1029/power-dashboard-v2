"""What-if people counts: same math as real billing, just with different headcounts."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing.compute import tenant_charges_for_ym

DB = os.path.join(os.path.dirname(__file__), "..", "data", "energy.sqlite")


def test_no_override_matches_real_billing():
    conn = sqlite3.connect(DB)
    a = tenant_charges_for_ym(conn, "2026-09")
    b = tenant_charges_for_ym(conn, "2026-09", people_override={})
    assert a.equals(b)


def test_overriding_to_the_real_values_changes_nothing():
    conn = sqlite3.connect(DB)
    real = tenant_charges_for_ym(conn, "2026-09")
    same = tenant_charges_for_ym(conn, "2026-09", {"East Site": 2, "West Site": 2, "Yurt": 1, "Middle Site": 0, "Airstream": 0})
    assert real.equals(same)


def test_adding_a_tenant_lowers_everyone_elses_shared_share_and_vacating_raises_it():
    conn = sqlite3.connect(DB)
    base = tenant_charges_for_ym(conn, "2026-09").set_index("Tenant")
    more = tenant_charges_for_ym(conn, "2026-09", {"Airstream": 1}).set_index("Tenant")
    assert "Airstream" in more.index and "Airstream" not in base.index
    for site in ["East Site", "West Site", "Yurt"]:
        assert more.loc[site, "Kitchen kWh"] < base.loc[site, "Kitchen kWh"]
    fewer = tenant_charges_for_ym(conn, "2026-09", {"West Site": 0}).set_index("Tenant")
    assert "West Site" not in fewer.index
    assert fewer.loc["East Site", "Kitchen kWh"] > base.loc["East Site", "Kitchen kWh"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
