import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import billing.send_bills as sb

URL = "https://example.streamlit.app"


def _msgs():
    return sb.build_messages("2026-09")


def test_phones_accept_one_number_or_a_list():
    p = {"Owner": ["+15550001111", "+15550002222"], "East Site": "+15550003333"}
    assert sb.phones_for(p, "Owner") == ["+15550001111", "+15550002222"]
    assert sb.phones_for(p, "East Site") == ["+15550003333"]
    assert sb.phones_for(p, "Yurt") == []


def test_owner_preview_lists_every_tenant_and_the_total():
    msgs = _msgs()
    body = sb.build_owner_message("2026-09", msgs, preview=True, dashboard_url=URL)
    for m in msgs:
        assert f"{m['tenant']} ${m['amount']:,.2f}" in body
    assert f"Total ${sum(m['amount'] for m in msgs):,.2f}." in body
    assert "Tenant texts go out on the 1st." in body


def test_dashboard_link_only_on_the_owner_text_and_only_when_set():
    msgs = _msgs()
    assert URL in sb.build_owner_message("2026-09", msgs, preview=True, dashboard_url=URL)
    assert "Dashboard:" not in sb.build_owner_message("2026-09", msgs, preview=True, dashboard_url="")
    assert all("http" not in m["body"] for m in msgs)  # tenant texts never carry a link


def test_non_preview_owner_text_has_no_going_out_line():
    body = sb.build_owner_message("2026-09", _msgs(), preview=False)
    assert "go out" not in body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
