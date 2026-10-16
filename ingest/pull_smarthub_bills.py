"""Pull the utility's bills from SmartHub into data/energy.sqlite.

    python -m ingest.pull_smarthub_bills [pdf_limit]     # default 24 newest bills

Saves, for every bill in the billing-history list: date, amount, adjustments, total
kWh and charges. For the `pdf_limit` newest bills it also downloads the PDF and saves
per-meter detail (readings, kWh, each charge line). Names, addresses and the PDFs
themselves are never stored. Re-running is safe: rows upsert, and bills that already
have PDF detail are skipped.
"""
import datetime as dt
import io
import os
import sqlite3
import sys
import time

from billing.utility_bill import parse_bill_text
from shared.smarthub import PACIFIC, billing_overview, fetch_bill_pdf, login

DB_PATH = "data/energy.sqlite"

SCHEMA = [
    """
    create table if not exists smarthub_bills (
        bill_ts            integer primary key,   -- billingDateTimestamp (ms)
        bill_date          text not null,         -- Pacific date, YYYY-MM-DD
        period_year        integer,
        period_month       integer,
        original_amount    real,
        total_adjustments  real,
        adjusted_amount    real,
        total_current_charge real,
        total_usage_kwh    real,
        total_usage_charge real,
        usage_estimated    integer,
        updated_at         text not null default (datetime('now'))
    )""",
    """
    create table if not exists smarthub_bill_meters (
        bill_ts        integer not null,
        meter          text not null,
        read_from      text,
        read_to        text,
        prev_reading   integer,
        present_reading integer,
        days           integer,
        multiplier     real,
        usage_kwh      real,
        total_charges  real,
        lines_ok       integer,       -- 1 when the charge lines add up to the printed total
        primary key (bill_ts, meter)
    )""",
    """
    create table if not exists smarthub_bill_lines (
        bill_ts     integer not null,
        meter       text not null,
        seq         integer not null,
        description text not null,
        kwh         real,
        rate        real,
        amount      real not null,
        primary key (bill_ts, meter, seq)
    )""",
]


def pacific_date(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc).astimezone(PACIFIC).date().isoformat()


def save_bill(conn: sqlite3.Connection, b: dict) -> None:
    period = b.get("billPeriod") or {}
    conn.execute(
        """insert into smarthub_bills (bill_ts, bill_date, period_year, period_month, original_amount,
               total_adjustments, adjusted_amount, total_current_charge, total_usage_kwh,
               total_usage_charge, usage_estimated)
           values (?,?,?,?,?,?,?,?,?,?,?)
           on conflict (bill_ts) do update set
               original_amount=excluded.original_amount, total_adjustments=excluded.total_adjustments,
               adjusted_amount=excluded.adjusted_amount, total_current_charge=excluded.total_current_charge,
               total_usage_kwh=excluded.total_usage_kwh, total_usage_charge=excluded.total_usage_charge,
               usage_estimated=excluded.usage_estimated, updated_at=datetime('now')""",
        (
            b["billingDateTimestamp"], pacific_date(b["billingDateTimestamp"]),
            period.get("year"), period.get("monthInt"),
            b.get("originalBillAmount"), b.get("totalAdjustments"), b.get("adjustedBillAmount"),
            b.get("totalCurrentCharge"), b.get("totalUsage"), b.get("totalUsageCharge"),
            int(bool(b.get("usageEstimated"))),
        ),
    )


def save_meters(conn: sqlite3.Connection, bill_ts: int, blocks) -> None:
    conn.execute("delete from smarthub_bill_meters where bill_ts = ?", (bill_ts,))
    conn.execute("delete from smarthub_bill_lines where bill_ts = ?", (bill_ts,))
    for blk in blocks:
        conn.execute(
            "insert into smarthub_bill_meters values (?,?,?,?,?,?,?,?,?,?,?)",
            (bill_ts, blk.meter, blk.read_from.isoformat(), blk.read_to.isoformat(), blk.prev_reading,
             blk.present_reading, blk.days, blk.multiplier, blk.usage_kwh, blk.total, int(blk.lines_ok)),
        )
        for i, ln in enumerate(blk.lines):
            conn.execute(
                "insert into smarthub_bill_lines values (?,?,?,?,?,?,?)",
                (bill_ts, blk.meter, i, ln.description, ln.kwh, ln.rate, ln.amount),
            )


def pdf_text(body: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(body)).pages)


def main(pdf_limit: int = 24) -> None:
    token = login()
    bills = sorted(billing_overview(token), key=lambda b: b["billingDateTimestamp"], reverse=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        for stmt in SCHEMA:
            conn.execute(stmt)
        for b in bills:
            save_bill(conn, b)
        conn.commit()
        print(f"Saved {len(bills)} bills from the billing-history list")

        have = {r[0] for r in conn.execute("select distinct bill_ts from smarthub_bill_meters")}
        todo = [b for b in bills[:pdf_limit] if b.get("showViewBillLink") and b["billingDateTimestamp"] not in have]
        print(f"Reading {len(todo)} bill PDFs (newest {pdf_limit} bills, skipping ones already saved)")
        bad = 0
        for b in todo:
            ts = b["billingDateTimestamp"]
            status, body = fetch_bill_pdf(token, b)
            if status != 200 or not body.startswith(b"%PDF"):
                print(f"  {pacific_date(ts)}: PDF request failed (HTTP {status})")
                bad += 1
                continue
            blocks = parse_bill_text(pdf_text(body))
            if not blocks:
                print(f"  {pacific_date(ts)}: no meter blocks found in the PDF")
                bad += 1
                continue
            save_meters(conn, ts, blocks)
            conn.commit()
            if os.environ.get("SHOW_DETAILS") == "1":  # amounts stay out of the public logs by default
                summary = ", ".join(
                    f"{blk.meter} {blk.usage_kwh:,.0f} kWh ${blk.total:,.2f} {'ok' if blk.lines_ok else 'LINES DO NOT ADD UP'}"
                    if blk.total is not None else f"{blk.meter} {blk.usage_kwh:,.0f} kWh NO TOTAL FOUND"
                    for blk in blocks
                )
            else:
                problems = [b for b in blocks if b.total is None or not b.lines_ok]
                summary = f"{len(blocks)} meters read, " + ("all charge lines add up" if not problems else f"{len(problems)} did NOT add up")
            print(f"  {pacific_date(ts)}: {summary}")
            time.sleep(1)
        if bad:
            print(f"{bad} bills could not be read")
    finally:
        conn.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
