"""Recalculate bills for a range of past months (uses the current sites.toml).

    python -m billing.report --from 2025-03 --to 2025-12
    python -m billing.report --from 2025-03 --to 2025-12 --csv bills.csv
"""
import argparse
import sqlite3

import pandas as pd

from billing.compute import tenant_charges_for_ym

DB_PATH = "data/energy.sqlite"


def months(a: str, b: str):
    y, m = map(int, a.split("-"))
    while f"{y:04d}-{m:02d}" <= b:
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--csv")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    frames = []
    for ym in months(args.start, args.end):
        df = tenant_charges_for_ym(conn, ym)
        df.insert(0, "Month", ym)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["kWh (calc)"] = out["kWh (calc)"].round(1)

    print(out.pivot(index="Month", columns="Tenant", values="Charge ($)").to_string())
    if args.csv:
        out.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
