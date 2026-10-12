import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing.period import billing_period_for_ym, last_closed_billing_ym


def test_last_closed_cycle():
    d = dt.date
    assert last_closed_billing_ym(d(2026, 11, 1)) == "2026-10"     # the 1st -> cycle that ended Oct 22
    assert last_closed_billing_ym(d(2026, 11, 22)) == "2026-10"    # cycle still closing today
    assert last_closed_billing_ym(d(2026, 10, 23)) == "2026-10"    # first day after it ended
    assert last_closed_billing_ym(d(2027, 1, 1)) == "2026-12"      # year boundary
    assert last_closed_billing_ym(d(2026, 3, 1)) == "2026-02"      # short February


def test_closed_cycle_has_ended_and_next_has_not():
    for today in [dt.date(2026, 11, 1), dt.date(2027, 1, 1), dt.date(2026, 6, 23)]:
        _, end = billing_period_for_ym(last_closed_billing_ym(today))
        assert end < today


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
