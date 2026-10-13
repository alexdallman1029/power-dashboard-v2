"""Text each tenant their monthly electric amount via Twilio.

Usage:
    python -m billing.send_bills                 # dry run for the cycle that last closed
    python -m billing.send_bills --ym 2026-01    # dry run for a specific billing month
    python -m billing.send_bills --send          # actually send
    python -m billing.send_bills --send --only Owner          # send just one message (a careful test)

Environment (only needed with --send):
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
    TENANT_PHONES   JSON, e.g. {"West Site": "+15551234567", "East Site": "+1555..."}
                    Keys must match the site names in billing/sites.toml. An optional
                    "Owner" key gets the monthly utility-bill summary text.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import requests

from billing.compute import owner_share_for_ym, tenant_charges_for_ym, vacant_site_usage
from billing.period import billing_period_for_ym, last_closed_billing_ym
from billing.utility import has_bill_tables, reconcile

DB_PATH = "data/energy.sqlite"

MESSAGE = "Hi, power charges for the month are ${amount:,.2f}."


def build_messages(ym: str) -> list[dict]:
    start, end = billing_period_for_ym(ym)
    conn = sqlite3.connect(DB_PATH)
    try:
        last_day = conn.execute("select max(day) from usage_daily").fetchone()[0]
        results = tenant_charges_for_ym(conn, ym)
        for site, kwh in vacant_site_usage(conn, ym).items():
            print(f"::warning::{site} is vacant but metered {kwh:,.1f} kWh (added to shared shop)")
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
                "body": MESSAGE.format(amount=float(row["Charge ($)"])),
            }
        )
    return msgs


OWNER_MESSAGE = (
    "Blachly-Lane bill {bill}: house {house_kwh:,.0f} kWh ${house_cost:,.2f}; "
    "farm meter {farm_kwh:,.0f} kWh ${farm_cost:,.2f} (Emporia saw {seen:.1f}% of farm kWh). "
    "Tenants billed for {start} - {end}: ${tenants:,.2f}. "
    "Farm power not billed to tenants: {owner_kwh:,.0f} kWh (~${owner_cost:,.2f}) = "
    "you and your partner's share of shared power {share_kwh:,.0f} kWh + grow barn {grow_kwh:,.0f} kWh."
)


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def build_owner_message(ym: str) -> str | None:
    """Monthly utility-bill summary for the owner, using the newest bill on file."""
    start, end = billing_period_for_ym(ym)
    conn = sqlite3.connect(DB_PATH)
    try:
        if not has_bill_tables(conn):
            print("::warning::No utility bills in the database; skipping the owner text")
            return None
        rec = reconcile(conn)
        if rec.empty:
            print("::warning::No utility bills with meter detail; skipping the owner text")
            return None
        latest = rec.sort_values("Bill date").iloc[-1]
        tenants = float(tenant_charges_for_ym(conn, ym)["Charge ($)"].sum())
        owner = owner_share_for_ym(conn, ym)
    finally:
        conn.close()

    farm = float(latest["Farm kWh"])
    return OWNER_MESSAGE.format(
        bill=_md(dt.date.fromisoformat(latest["Bill date"])),
        house_kwh=float(latest["House kWh"]),
        house_cost=float(latest["House $"]),
        farm_kwh=farm,
        farm_cost=float(latest["Farm $"]),
        seen=(float(latest["Emporia kWh"]) / farm * 100.0) if farm else 0.0,
        start=_md(start),
        end=_md(end),
        tenants=tenants,
        owner_kwh=owner["total_kwh"],
        owner_cost=owner["total_cost"],
        share_kwh=owner["kwh"],
        grow_kwh=owner["grow_kwh"],
    )


def send_sms(to: str, body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"To": to, "From": os.environ["TWILIO_FROM_NUMBER"], "Body": body},
        timeout=30,
    )
    if not r.ok:
        # Twilio explains every rejection with a code and message (no secrets in it)
        try:
            err = r.json()
            detail = f"Twilio error {err.get('code')}: {err.get('message')}"
        except ValueError:
            detail = r.text[:300]
        raise RuntimeError(f"HTTP {r.status_code}. {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ym", help="billing month YYYY-MM (default: the cycle that last closed)")
    ap.add_argument("--send", action="store_true", help="actually send (default is dry run)")
    ap.add_argument("--only", help='only these recipients, comma separated, e.g. "Owner" or "Owner,East Site"')
    args = ap.parse_args()

    ym = args.ym or last_closed_billing_ym(dt.date.today())
    msgs = build_messages(ym)
    owner_body = build_owner_message(ym)
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        unknown = wanted - {m["tenant"] for m in msgs} - {"Owner"}
        if unknown:
            raise RuntimeError(f"--only names not found among this month's recipients: {sorted(unknown)}")
        msgs = [m for m in msgs if m["tenant"] in wanted]
        if "Owner" not in wanted:
            owner_body = None

    phones = json.loads(os.environ.get("TENANT_PHONES", "{}")) if args.send else {}
    if args.send:
        missing = [m["tenant"] for m in msgs if m["tenant"] not in phones]
        if missing:
            raise RuntimeError(f"No phone number for: {missing}")

    print(f"Billing month {ym} ({'SEND' if args.send else 'DRY RUN'})")
    failed = []
    if owner_body:
        print(f"- Owner: {owner_body}")
        if args.send:
            if "Owner" not in phones:
                print("  skipped: no \"Owner\" entry in TENANT_PHONES")
            else:
                try:
                    send_sms(phones["Owner"], owner_body)
                    print("  sent")
                except Exception as e:
                    print(f"  FAILED: {e}")
                    failed.append("Owner")
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
