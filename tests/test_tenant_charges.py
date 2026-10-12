import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing.tenant_charges import calculate_site_charges

KW = dict(rate=0.13, fee=5.43, base_kwh=45.0)


def _calc(people, **over):
    args = dict(
        site_kwh={"A": 100.0, "B": 50.0, "C": 0.0},
        site_people=people,
        unbilled_people=2,
        kitchen_lounge_kwh=200.0,
        shared_shop_kwh=140.0,
        **KW,
    )
    args.update(over)
    return calculate_site_charges(**args)


def test_components_sum_to_total_and_dollars():
    df = _calc({"A": 2, "B": 1, "C": 1})
    for _, r in df.iterrows():
        parts = r["Own kWh"] + r["Kitchen kWh"] + r["Shop kWh"] + r["Base kWh"]
        assert abs(parts - r["kWh (calc)"]) < 1e-9
        assert abs(round(parts * 0.13 + r["Fee ($)"], 2) - r["Charge ($)"]) < 0.006


def test_shared_pools_fully_allocated():
    # base + shop go to everyone (billed 4 + unbilled 2); kitchen only to billed
    df = _calc({"A": 2, "B": 1, "C": 1})
    assert abs(df["Kitchen kWh"].sum() - 200.0) < 1e-9
    assert abs(df["Shop kWh"].sum() - 140.0 * 4 / 6) < 1e-9
    assert abs(df["Base kWh"].sum() - 45.0 * 4 / 6) < 1e-9


def test_vacant_sites_are_dropped_and_all_vacant_is_empty():
    df = _calc({"A": 2, "B": 0, "C": 1})
    assert list(df["Tenant"]) == ["A", "C"]
    assert _calc({"A": 0, "B": 0, "C": 0}).empty


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
