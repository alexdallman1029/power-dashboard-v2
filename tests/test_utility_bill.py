"""Run with: python -m pytest tests  (or: python tests/test_utility_bill.py)

The sample text mirrors the real bill layout, using the two example locations from the
bill (803 kWh and 2,047 kWh). Names, addresses and account numbers are fake.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing.utility_bill import parse_bill_text

PAGE1 = """\
Member Name TEST MEMBER
Account # 1234567
Previous Balance $100.00
Service Address: 123 TEST RD District #: 1
Description Meter No. Reading Dates Readings Days Multiplier UsageFrom To Previous Present
Residential Rural - 1 Phase R123 08/20/26 09/21/26 12345 13148 32 1 803
Current Service Detail
kWh Charge 803 kWh @ $0.11190 $89.86
Distribution Capacity Charge $40.47
Distribution Service Charge $18.53
Prompt Payment Credit -$3.00
Total Current Charges for this Location $145.86
Blachly-Lane County Cooperative
"""

PAGE2 = """\
Service Address: 123 TEST RD District #: 1
Description Meter No. Reading Dates Readings Days Multiplier UsageFrom To Previous Present
Residential Rural - 3 Phase R4567 08/20/26 09/21/26 100000 102047 32 1 2,047
Current Service Detail
Blachly-Lane Energy Share (BLES) $0.82
kWh Charge 2,000 kWh @ $0.11190 $223.80
Over 2,000 kWh 47 kWh @ $0.12880 $6.05
Distribution Capacity Charge $40.47
Prompt Payment Credit -$3.00
Total Current Charges for this Location $268.14
"""


def test_two_meters_parse_and_balance():
    a, b = parse_bill_text(PAGE1 + "\n" + PAGE2)
    assert (a.meter, a.usage_kwh, a.total, a.lines_ok) == ("R123", 803.0, 145.86, True)
    assert (b.meter, b.usage_kwh, b.total, b.lines_ok) == ("R4567", 2047.0, 268.14, True)
    assert a.read_from == dt.date(2026, 8, 20) and a.read_to == dt.date(2026, 9, 21)
    assert [l.description for l in a.lines] == [
        "kWh Charge", "Distribution Capacity Charge", "Distribution Service Charge", "Prompt Payment Credit"]
    assert (a.lines[0].kwh, a.lines[0].rate, a.lines[0].amount) == (803.0, 0.1119, 89.86)
    assert a.lines[3].amount == -3.00


def test_tiered_and_share_lines():
    _, b = parse_bill_text(PAGE1 + "\n" + PAGE2)
    by = {l.description: l for l in b.lines}
    assert by["Blachly-Lane Energy Share (BLES)"].amount == 0.82
    assert by["Blachly-Lane Energy Share (BLES)"].kwh is None
    assert (by["kWh Charge"].kwh, by["kWh Charge"].amount) == (2000.0, 223.80)
    assert (by["Over 2,000 kWh"].kwh, by["Over 2,000 kWh"].rate, by["Over 2,000 kWh"].amount) == (47.0, 0.1288, 6.05)


def test_mismatched_total_is_flagged():
    bad = PAGE1.replace("$145.86", "$150.00")
    (a,) = parse_bill_text(bad)
    assert a.lines_ok is False


def test_no_meter_rows_gives_nothing():
    assert parse_bill_text("Just some text\nTotal Current Charges for this Location $1.00") == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
