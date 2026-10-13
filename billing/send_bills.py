"""Text the monthly electric amounts via Twilio: a preview to the owners, then each tenant.

Usage:
    python -m billing.send_bills                          # dry run, everything, for the cycle that last closed
    python -m billing.send_bills --ym 2026-01             # dry run for a specific billing month
    python -m billing.send_bills --mode preview           # only the owners' preview (with every tenant's amount)
    python -m billing.send_bills --mode tenants           # only the tenant texts
    python -m billing.send_bills --send --mode tenants    # actually send
    python -m billing.send_bills --send --only Owner      # send just one recipient (a careful test)

Environment (only needed with --send):
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
    TENANT_PHONES   JSON mapping a recipient to a number or a list of numbers, e.g.
                    {"West Site": "+15551234567", "Owner": ["+15550001111", "+15550002222"]}
                    Tenant keys must match the site names in billing/sites.toml. "Owner" gets the
                    utility-bill summary and the preview of every tenant's amount.
    DASHBOARD_URL   optional; when set, the owner text ends with a link to the dashboard
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


def phones_for(phones: dict, key: str) -> list[str]:
    """Numbers for a recipient; the secret may hold one number or a list of them."""
    value = phones.get(key)
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


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
        msgs.append(
            {
                "tenant": row["Tenant"],
                "amount": float(row["Charge ($)"]),
                "kwh": float(row["kWh (calc)"]),
                "body": MESSAGE.format(amount=float(row["Charge ($)"])),
            }
        )
    return msgs


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def build_owner_message(ym: str, tenant_msgs: list[dict], preview: bool = False, dashboard_url: str = "") -> str | None:
    """Utility-bill summary for the owners, with every tenant's amount so it can be checked
    before the tenant texts go out. Uses the newest utility bill on file."""
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
        owner = owner_share_for_ym(conn, ym)
    finally:
        conn.close()

    farm = float(latest["Farm kWh"])
    seen = (float(latest["Emporia kWh"]) / farm * 100.0) if farm else 0.0
    lines = [
        f"Blachly-Lane bill {_md(dt.date.fromisoformat(latest['Bill date']))}: "
        f"house {float(latest['House kWh']):,.0f} kWh ${float(latest['House $']):,.2f}; "
        f"farm meter {farm:,.0f} kWh ${float(latest['Farm $']):,.2f} (Emporia saw {seen:.1f}% of farm kWh).",
        f"Tenants for {_md(start)} - {_md(end)}:",
    ]
    for m in tenant_msgs:
        lines.append(f"{m['tenant']} ${m['amount']:,.2f} ({m['kwh']:,.0f} kWh)")
    lines.append(f"Total ${sum(m['amount'] for m in tenant_msgs):,.2f}.")
    lines.append(
        f"Not billed to tenants: {owner['total_kwh']:,.0f} kWh (~${owner['total_cost']:,.2f}) = "
        f"your share of shared power {owner['kwh']:,.0f} kWh + grow barn {owner['grow_kwh']:,.0f} kWh."
    )
    if preview:
        lines.append("Tenant texts go out on the 1st.")
    if dashboard_url:
        lines.append(f"Dashboard: {dashboard_url}")
    return "\n".join(lines)


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
    ap.add_argument("--mode", choices=["all", "preview", "tenants"], default="all",
                    help="preview = owners only, tenants = tenant texts only, all = both (default)")
    ap.add_argument("--only", help='only these recipients, comma separated, e.g. "Owner" or "Owner,East Site"')
    args = ap.parse_args()

    ym = args.ym or last_closed_billing_ym(dt.date.today())
    tenant_msgs = build_messages(ym)
    dashboard_url = os.environ.get("DASHBOARD_URL", "").strip()
    if dashboard_url and not dashboard_url.startswith("http"):
        raise RuntimeError("DASHBOARD_URL must start with http:// or https://")

    owner_body = None
    if args.mode in ("all", "preview"):
        owner_body = build_owner_message(ym, tenant_msgs, preview=args.mode == "preview", dashboard_url=dashboard_url)
    msgs = tenant_msgs if args.mode in ("all", "tenants") else []

    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        unknown = wanted - {m["tenant"] for m in tenant_msgs} - {"Owner"}
        if unknown:
            raise RuntimeError(f"--only names not found among this month's recipients: {sorted(unknown)}")
        msgs = [m for m in msgs if m["tenant"] in wanted]
        if "Owner" not in wanted:
            owner_body = None

    phones = json.loads(os.environ.get("TENANT_PHONES", "{}")) if args.send else {}
    if args.send:
        missing = [m["tenant"] for m in msgs if not phones_for(phones, m["tenant"])]
        if missing:
            raise RuntimeError(f"No phone number for: {missing}")

    print(f"Billing month {ym} ({'SEND' if args.send else 'DRY RUN'}, mode {args.mode})")
    failed = []
    if owner_body:
        print(f"- Owner:\n{owner_body}")
        if args.send:
            numbers = phones_for(phones, "Owner")
            if not numbers:
                print('  skipped: no "Owner" entry in TENANT_PHONES')
            for i, number in enumerate(numbers, 1):
                try:
                    send_sms(number, owner_body)
                    print(f"  sent to owner number {i}")
                except Exception as e:
                    print(f"  FAILED (owner number {i}): {e}")
                    failed.append(f"Owner {i}")
    for m in msgs:
        print(f"- {m['tenant']}: {m['body']}")
        if args.send:
            for i, number in enumerate(phones_for(phones, m["tenant"]), 1):
                try:
                    send_sms(number, m["body"])
                    print(f"  sent (number {i})")
                except Exception as e:  # keep going so one bad number doesn't block the rest
                    print(f"  FAILED (number {i}): {e}")
                    failed.append(m["tenant"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
