"""Minimal client for the SmartHub (NISC) customer-portal API used by Blachly-Lane.

Unofficial: these are the same requests the portal's own web pages make, so they can
change without notice. Credentials come from environment variables (GitHub secrets).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://blachlylane.smarthub.coop"
HOST = "blachlylane.smarthub.coop"


def _post(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def login() -> str:
    """Return a bearer token. Never print it."""
    body = urllib.parse.urlencode(
        {"userId": os.environ["SMARTHUB_USERNAME"], "password": os.environ["SMARTHUB_PASSWORD"]}
    ).encode()
    status, raw = _post(f"{API}/services/oauth/auth/v2", body, {"authority": HOST})
    token = json.loads(raw).get("authorizationToken", "")
    if not token:
        raise RuntimeError(f"SmartHub login failed (HTTP {status}); check username/password secrets")
    return token


def poll_usage(token: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> dict:
    """Ask the Usage Explorer endpoint for a time frame (HOURLY/DAILY/MONTHLY)."""
    user = os.environ["SMARTHUB_USERNAME"]
    body = json.dumps(
        {
            "timeFrame": timeframe,
            "userId": user,
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
        "x-nisc-smarthub-username": user,
        "content-type": "application/json",
    }
    for _ in range(15):
        status, raw = _post(f"{API}/services/secured/utility-usage/poll", body, headers)
        data = json.loads(raw)
        if data.get("status") == "COMPLETE":
            return data
        if data.get("status") != "PENDING":
            raise RuntimeError(f"SmartHub poll returned status {data.get('status')!r} (HTTP {status})")
        time.sleep(2)
    raise RuntimeError("SmartHub data still PENDING after 15 tries")


def _get(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def billing_overview(token: str) -> dict:
    """The bill list behind SmartHub's Billing page (dates, amounts, adjustments, totals)."""
    user = os.environ["SMARTHUB_USERNAME"]
    acct = urllib.parse.quote(os.environ["SMARTHUB_ACCOUNT"].strip())
    status, raw = _get(
        f"{API}/services/secured/billing/history/overview?acctNbr={acct}",
        {"authority": HOST, "authorization": "Bearer " + token, "x-nisc-smarthub-username": user},
    )
    if status != 200:
        raise RuntimeError(f"SmartHub billing overview returned HTTP {status}")
    return json.loads(raw)


def monthly_kwh(data: dict) -> list[tuple[str, float]]:
    """(month 'YYYY-MM', kWh) pairs from a MONTHLY poll response.

    SmartHub returns local-midnight month starts as if they were UTC, so reading the
    timestamp as UTC gives the right calendar month.
    """
    entries = (data.get("data") or {}).get("ELECTRIC") or []
    out: dict[str, float] = {}
    for entry in entries:
        if entry.get("type") != "USAGE":
            continue
        for series in entry.get("series") or []:
            for p in series.get("data") or []:
                month = dt.datetime.fromtimestamp(p["x"] / 1000, dt.timezone.utc).strftime("%Y-%m")
                out[month] = out.get(month, 0.0) + float(p["y"])
    return sorted(out.items())
