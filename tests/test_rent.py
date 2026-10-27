"""The rent rules. Every number here is made up; real rent lives only in the RENT_CONFIG secret."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import billing.send_bills as sb
from billing import rent as R

CFG = {
    "greeting": "Hello there.",
    "payment": "Pay by magic.",
    "sites": {
        "East Site": {"base_rent": 1000, "extra_person": 100, "internet_per_person": 10, "storage": 0},
        "West Site": {"base_rent": 1000, "extra_person": 100, "internet_per_person": 10},
        "Middle Site": {"base_rent": 1000, "extra_person": 100, "internet_per_person": 10},
        "Yurt": {"base_rent": 900, "internet_per_person": 10,
                 "storage": [{"from": "2026-08", "amount": 40}, {"from": "2027-01", "amount": 0}]},
        "Airstream": {"idle": True},
    },
}


def test_extra_people_pay_extra_rent_and_everyone_pays_internet():
    east = CFG["sites"]["East Site"]
    assert R.charges_for(east, 1, "2026-11") == {"rent": 1000.0, "internet": 10.0, "storage": 0.0}
    assert R.charges_for(east, 2, "2026-11") == {"rent": 1100.0, "internet": 20.0, "storage": 0.0}
    assert R.charges_for(east, 3, "2026-11")["rent"] == 1200.0
    assert R.charges_for(east, 0, "2026-11") == {"rent": 0.0, "internet": 0.0, "storage": 0.0}


def test_yurt_has_no_extra_person_fee_and_storage_follows_its_history():
    yurt = CFG["sites"]["Yurt"]
    assert R.charges_for(yurt, 2, "2026-11")["rent"] == 900.0                   # no extra_person set
    assert R.charges_for(yurt, 1, "2026-07")["storage"] == 0.0                   # before the first entry
    assert R.charges_for(yurt, 1, "2026-11")["storage"] == 40.0
    assert R.charges_for(yurt, 1, "2027-01")["storage"] == 0.0                   # goes back to zero


def test_text_lists_each_charge_in_order_and_hides_storage_at_zero():
    body, total = R.compose_text(CFG, R.charges_for(CFG["sites"]["East Site"], 2, "2026-11"), 131.19)
    assert body.splitlines() == ["Hello there.", "Base rent for this month is $1,100.00.", "Internet is $20.00.",
                                 "Power is $131.19.", "Total: $1,251.19.", "Pay by magic."]
    assert abs(total - 1251.19) < 1e-9
    body, total = R.compose_text(CFG, R.charges_for(CFG["sites"]["Yurt"], 1, "2026-11"), 17.03)
    assert "Storage is $40.00." in body and body.index("Power is") < body.index("Storage is") < body.index("Total:")
    assert abs(total - 967.03) < 1e-9


def test_months_roll_over_the_year():
    assert R.next_month("2026-10") == "2026-11" and R.next_month("2026-12") == "2027-01"


@pytest.mark.parametrize("raw, message", [
    ("{not json", "not valid JSON"),
    ('{"sites": {}}', 'needs a "sites" object'),
    ('{"sites": {"A": {"internet_per_person": 5}}}', 'base_rent'),
    ('{"sites": {"A": {"base_rent": 5, "internet_per_person": 5, "storage": [{"from": "soon"}]}}}', "storage entries"),
])
def test_a_mistyped_secret_gets_a_plain_error(raw, message):
    with pytest.raises(ValueError, match=message):
        R.parse_rent_config(raw)


def test_emoji_switch_the_whole_text_to_the_longer_encoding():
    plain = "Greetings. I am Alex's billing bot.\n" + "x" * 120
    assert R.sms_segments(plain) == ("GSM-7", 1)
    enc, n = R.sms_segments(plain + " \U0001F47E")          # one emoji
    assert enc == "UCS-2" and n >= 3                          # 157 characters would be 1 segment in GSM-7, now 3
    assert R.sms_segments("Alex’s")[0] == "UCS-2"        # even a curly apostrophe does it


def test_tenant_texts_for_a_real_cycle_follow_the_rules():
    msgs = {m["tenant"]: m for m in sb.build_messages("2026-09", CFG)}
    assert set(msgs) == {"East Site", "West Site", "Yurt"}      # Middle is vacant, Airstream is idle
    east = msgs["East Site"]                                    # 2 people in October
    assert east["rent"] == 1100.0 and east["internet"] == 20.0 and east["storage"] == 0.0
    assert abs(east["total"] - (1120.0 + east["amount"])) < 1e-9
    assert "Storage" not in east["body"] and "Base rent for this month is $1,100.00." in east["body"]
    assert "Storage is $40.00." in msgs["Yurt"]["body"] and msgs["Yurt"]["rent"] == 900.0


def test_owner_preview_shows_totals_and_names_idle_sites():
    msgs = sb.build_messages("2026-09", CFG)
    body = sb.build_owner_message("2026-09", msgs, preview=True, rent_cfg=CFG)
    assert "East Site $" in body and "rent $1,100.00 + internet $20.00 + power $" in body
    assert "Not billed (idle): Airstream." in body and "Tenant texts go out on the 1st." in body


def test_missing_or_unknown_sites_in_the_secret_are_caught():
    bad = json.loads(json.dumps(CFG)); del bad["sites"]["Yurt"]
    with pytest.raises(RuntimeError, match="no settings for Yurt"):
        sb.build_messages("2026-09", bad)
    bad = json.loads(json.dumps(CFG)); bad["sites"]["Barn Loft"] = {"idle": True}
    with pytest.raises(RuntimeError, match="do not exist"):
        sb.build_messages("2026-09", bad)


def test_no_secret_means_no_real_send_and_the_local_file_is_never_committed(monkeypatch):
    monkeypatch.delenv("RENT_CONFIG", raising=False)
    monkeypatch.setattr(R, "LOCAL_FILE", R.Path("does-not-exist.json"))
    assert R.load_rent_config() is None
    root = os.path.join(os.path.dirname(__file__), "..")
    assert "rent_config.local.json" in open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert "rent_config.local.json" not in tracked
