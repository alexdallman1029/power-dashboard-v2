"""Diagnostic: what does Ambient return for this account, and how far back does history go?

Read-only. Prints field names, counts and dates only. The station's address, coordinates, MAC
address and the keys are never printed (this repo's Actions logs are public).
"""
import datetime as dt

from shared.ambient import find_base, history

UTC = dt.timezone.utc


def day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    base, devices = find_base()
    print(f"Keys accepted. {len(devices)} station(s) on the account. (base address: {base.split('//')[1].split('/')[0]})")
    for i, d in enumerate(devices, 1):
        last = d.get("lastData") or {}
        print(f"\nStation {i}: fields it reports right now ({len(last)}):")
        print("  " + ", ".join(sorted(last)))
    mac = devices[0]["macAddress"]

    print("\n== Newest page of history (288 requested)")
    page = history(base, mac)
    stamps = sorted(r["dateutc"] for r in page if "dateutc" in r)
    print(f"  {len(page)} readings, {day(stamps[0])} to {day(stamps[-1])} UTC")
    if len(stamps) > 2:
        gaps = sorted({round((b - a) / 60000) for a, b in zip(stamps, stamps[1:])})
        print(f"  minutes between readings seen: {gaps[:6]}")

    print("\n== How far back does the history reach? (one reading requested at each point)")
    now = dt.datetime.now(UTC)
    for label, days in [("1 week", 7), ("1 month", 30), ("3 months", 90), ("6 months", 183), ("1 year", 365),
                        ("18 months", 548), ("2 years", 730), ("3 years", 1095), ("5 years", 1826)]:
        end_ms = int((now - dt.timedelta(days=days)).timestamp() * 1000)
        try:
            rows = history(base, mac, end_ms=end_ms, limit=1)
        except RuntimeError as e:
            print(f"  {label:9}: error: {e}")
            continue
        print(f"  {label:9}: " + (f"data found, nearest reading {day(rows[0]['dateutc'])} UTC" if rows else "no data"))


if __name__ == "__main__":
    main()
