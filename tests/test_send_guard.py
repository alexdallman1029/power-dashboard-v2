"""The duplicate-send guard and the quiet logs."""
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import billing.send_bills as sb

YM = "2026-09"
PHONES = {"East Site": "+15550000001", "West Site": "+15550000002", "Yurt": ["+15550000003", "+15550000004"]}


def run(log_path, *flags, fail_for=None, capsys=None):
    sent = []

    def fake_send(to, body):
        if fail_for and to == fail_for:
            raise RuntimeError("HTTP 400. Twilio error 21211: bad number")
        sent.append(to)

    env = {"TENANT_PHONES": json.dumps(PHONES)}
    argv = ["send_bills", "--send", "--mode", "tenants", "--ym", YM, *flags]
    with mock.patch.object(sb, "SENT_LOG", log_path), mock.patch.object(sb, "send_sms", fake_send), \
         mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", argv):
        code = sb.main()
    return sent, code


def test_second_run_skips_everyone_already_sent():
    log = os.path.join(tempfile.mkdtemp(), "sent.json")
    first, code1 = run(log)
    assert code1 == 0
    assert sorted(first) == ["+15550000001", "+15550000002", "+15550000003", "+15550000004"]  # East, West, Yurt's two
    second, code2 = run(log)
    assert second == [] and code2 == 0   # nothing sent twice


def test_force_sends_again():
    log = os.path.join(tempfile.mkdtemp(), "sent.json")
    run(log)
    again, _ = run(log, "--force")
    assert len(again) == 4


def test_a_failure_is_not_recorded_so_only_it_is_retried():
    log = os.path.join(tempfile.mkdtemp(), "sent.json")
    first, code = run(log, fail_for="+15550000002")
    assert code == 1 and "+15550000002" not in first and len(first) == 3
    retry, code2 = run(log)
    assert retry == ["+15550000002"] and code2 == 0


def test_the_log_holds_month_label_and_time_but_no_phone_numbers():
    log = os.path.join(tempfile.mkdtemp(), "sent.json")
    run(log)
    raw = open(log, encoding="utf-8").read()
    rows = json.loads(raw)
    assert {r["who"] for r in rows} == {"East Site", "West Site", "Yurt", "Yurt #2"}
    assert all(set(r) == {"ym", "who", "at"} for r in rows) and "+1555" not in raw


def test_logs_do_not_show_message_text_or_amounts(capsys):
    log = os.path.join(tempfile.mkdtemp(), "sent.json")
    run(log)
    out = capsys.readouterr().out
    assert "power charges" not in out and "$" not in out
    assert "message ready" in out


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
