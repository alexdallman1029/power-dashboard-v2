"""Diagnostic: show the SHAPE of what SmartHub returns (no tokens, no readings printed).

Uses the same login and poll requests as electric-usage-downloader, trying several
time frames so we can see which ones this utility actually serves.
"""
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

API = "https://blachlylane.smarthub.coop"
HOST = "blachlylane.smarthub.coop"
USER = os.environ["SMARTHUB_USERNAME"]


def post(url, body, headers):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def auth() -> str:
    body = urllib.parse.urlencode({"userId": USER, "password": os.environ["SMARTHUB_PASSWORD"]}).encode()
    status, raw = post(f"{API}/services/oauth/auth/v2", body, {"authority": HOST})
    token = json.loads(raw).get("authorizationToken", "")
    print(f"auth: HTTP {status}, token {'received' if token else 'MISSING'}")
    return token


def mask(s: str) -> str:
    return re.sub(r"\d", "#", s)


def poll(token, timeframe, start, end):
    body = json.dumps(
        {
            "timeFrame": timeframe,
            "userId": USER,
            "screen": "USAGE_EXPLORER",
            "includeDemand": False,
            "serviceLocationNumber": os.environ["SMARTHUB_SERVICE_LOCATION"].strip(),
            "accountNumber": os.environ["SMARTHUB_ACCOUNT"].strip(),
            "industries": ["ELECTRIC"],
            "startDateTime": int(start.timestamp() * 1000),
            "endDateTime": int(end.timestamp() * 1000),
        }
    ).encode()
    headers = {
        "authority": HOST,
        "authorization": "Bearer " + token,
        "x-nisc-smarthub-username": USER,
        "content-type": "application/json",
    }
    for attempt in range(1, 16):
        status, raw = post(f"{API}/services/secured/utility-usage/poll", body, headers)
        try:
            data = json.loads(raw)
        except Exception:
            print(f"  HTTP {status}, non-JSON body of {len(raw)} bytes")
            return
        if data.get("status") != "PENDING":
            return status, data
        time.sleep(2)
    print("  still PENDING after 15 tries")


SHOW_VALUES = {"timeFrame", "unitOfMeasure", "hasDaily", "hasHourly", "industry", "type", "estimatedSeries", "editedSeries"}


def shape(obj, depth=0, key=""):
    """Structure only: types and sizes, plus a few harmless flags. No readings or IDs."""
    if depth > 5:
        return "..."
    if isinstance(obj, dict):
        return {mask(str(k)): shape(v, depth + 1, str(k)) for k, v in list(obj.items())[:12]}
    if isinstance(obj, list):
        return [f"{len(obj)} items", shape(obj[0], depth + 1, key)] if obj else ["empty list"]
    if key in SHOW_VALUES:
        return repr(obj)
    return type(obj).__name__


def describe(label, result):
    print(f"\n== {label}")
    if not result:
        return
    status, data = result
    print(f"  HTTP {status}; top-level keys: {sorted(data.keys())}; status={data.get('status')!r}")
    for k, v in data.items():
        if isinstance(v, str) and k not in ("status",):
            print(f"  {k}: {mask(v)[:200]!r}")
    d = data.get("data") or {}
    print(f"  data keys: {sorted(d.keys())}")
    for industry, entries in d.items():
        if not isinstance(entries, list):
            print(f"  {industry}: {type(entries).__name__}")
            continue
        print(f"  {industry}: {len(entries)} entries")
        for e in entries:
            series = e.get("series") or []
            print(f"    type={e.get('type')!r} other keys={sorted(set(e) - {'type', 'series'})} series={len(series)}")
            for k in sorted(set(e) - {"type", "series", "accountNumber", "serviceLocationNumber"}):
                print(f"      [{k}] -> {json.dumps(shape(e[k], 0, k))[:700]}")
            for s in series:
                pts = s.get("data") or []
                span = ""
                if pts:
                    a = dt.datetime.fromtimestamp(pts[0]["x"] / 1000, dt.timezone.utc)
                    b = dt.datetime.fromtimestamp(pts[-1]["x"] / 1000, dt.timezone.utc)
                    span = f", {a:%Y-%m-%d %H:%M} .. {b:%Y-%m-%d %H:%M} (as returned)"
                print(f"      series name={mask(str(s.get('name')))!r}: {len(pts)} points{span}")


def main():
    token = auth()
    if not token:
        return
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    for label, tf, days_start, days_end in [
        ("HOURLY, last 10 days", "HOURLY", 10, 0),
        ("DAILY, last 40 days", "DAILY", 40, 0),
        ("MONTHLY, last 400 days", "MONTHLY", 400, 0),
    ]:
        describe(label, poll(token, tf, now - dt.timedelta(days=days_start), now - dt.timedelta(days=days_end)))


if __name__ == "__main__":
    main()
