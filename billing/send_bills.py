"""Text each tenant their monthly electric amount via Twilio.

Usage:
    python -m billing.send_bills                 # dry run for the period that just closed
    python -m billing.send_bills --ym 2026-01    # dry run for a specific billing month
    python -m billing.send_bills --send          # actually send

Environment (only needed with --send):
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
    TENANT_PHONES   JSON, e.g. {"West Site": "+15551234567", "East Site": "+1555..."}
                    Keys must match the site names in billing/sites.toml.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import requests

from billing.compute import tenant_charges_for_ym
from billing.config import load_config
from billing.period import billing_period_for_ym, billing_ym_for_day

DB_PATH = "data/energy.sqlite"

MESSAGE = (
    "Hi {first}, your electric bill for {start} - {end} is ${amount:,.2f} "
    "({kwh:,.0f} kWh). Thanks!"
)


def build_messages(ym: str) -> list[dict]:
    start, end = billing_period_for_ym(ym)
    greetings = {site.name: site.greeting for site in load_config().sites}
    conn = sqlite3.connect(DB_PATH)
    try:
        last_day = conn.execute("select max(day) from usage_daily").fetchone()[0]
        results = tenant_charges_for_ym(conn, ym)
    finally:
        conn.close()

    if last_day is None or dt.date.fromisoformat(last_day) < end:
        raise RuntimeError(
            f"Data ends {last_day}, but billing period {ym} ends {end}. "
            "Refusing to send bills from incomplete data."
        )

    msgs = []
    for _, row in results.iterrows():
        name = row["Tenant"]
        msgs.append(
            {
                "tenant": name,
                "amount": float(row["Charge ($)"]),
                "body": MESSAGE.format(
                    first=greetings[name],
                    start=f"{start:%b} {start.day}",
                    end=f"{end:%b} {end.day}",
                    amount=float(row["Charge ($)"]),
                    kwh=float(row["kWh (calc)"]),
                ),
            }
        )
    return msgs


def send_sms(to: str, body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"To": to, "From": os.environ["TWILIO_FROM_NUMBER"], "Body": body},
        timeout=30,
    )
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ym", help="billing month YYYY-MM (default: period that just closed)")
    ap.add_argument("--send", action="store_true", help="actually send (default is dry run)")
    args = ap.parse_args()

    ym = args.ym or billing_ym_for_day(dt.date.today() - dt.timedelta(days=1))
    msgs = build_messages(ym)

    phones = json.loads(os.environ.get("TENANT_PHONES", "{}")) if args.send else {}
    if args.send:
        missing = [m["tenant"] for m in msgs if m["tenant"] not in phones]
        if missing:
            raise RuntimeError(f"No phone number for: {missing}")

    print(f"Billing month {ym} ({'SEND' if args.send else 'DRY RUN'})")
    failed = []
    for m in msgs:
        print(f"- {m['tenant']}: {m['body']}")
        if args.send:
            try:
                send_sms(phones[m["tenant"]], m["body"])
                print("  sent")
            except Exception as e:  # keep going so one bad number doesn't block the rest
                print(f"  FAILED: {e}")
                failed.append(m["tenant"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
