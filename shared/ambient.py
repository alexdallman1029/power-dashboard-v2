"""Minimal client for the Ambient Weather REST API (https://ambientweather.docs.apiary.io/).

Two keys are required on every call: the API key (your account) and the application key
(this program). Both come from environment variables / GitHub secrets and are never printed.
The API allows roughly one request per second per key, so calls are paced.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

BASES = ["https://rt.ambientweather.net/v1", "https://api.ambientweather.net/v1"]
PAUSE = 1.2  # seconds between requests


def _keys() -> dict:
    return {
        "apiKey": os.environ["AMBIENT_API_KEY"].strip(),
        "applicationKey": os.environ["AMBIENT_APPLICATION_KEY"].strip(),
    }


def get(base: str, path: str, **params):
    """GET {base}{path}; returns (HTTP status, parsed JSON or None). Retries politely on 429."""
    query = urllib.parse.urlencode({**_keys(), **{k: v for k, v in params.items() if v is not None}})
    url = f"{base}{path}?{query}"
    for attempt in range(4):
        time.sleep(PAUSE)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as r:
                body = r.read()
                return r.status, json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
    return 429, None


def find_base() -> tuple[str, list]:
    """The first base URL that accepts the keys, with the list of stations on the account."""
    last = None
    for base in BASES:
        status, data = get(base, "/devices")
        last = status
        if status == 200 and isinstance(data, list):
            return base, data
    raise RuntimeError(f"Ambient rejected the keys or the address (last HTTP status {last})")


def history(base: str, mac: str, end_ms: int | None = None, limit: int = 288) -> list[dict]:
    """Up to 288 readings, newest first, ending at end_ms (default: now)."""
    status, data = get(base, f"/devices/{urllib.parse.quote(mac)}", endDate=end_ms, limit=limit)
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"Ambient history request failed (HTTP {status})")
    return data


def fetch_all(base: str, mac: str, stop_before_ms: int | None = None, max_pages: int = 4000) -> list[dict]:
    """Page backward from now until the history runs out (or reaches stop_before_ms).

    Each request returns up to 288 readings ending at `endDate`, newest first, so the next page
    ends one millisecond before the oldest reading already held. Gaps (station offline) are skipped
    automatically, because the API returns the newest readings before `endDate`.
    """
    rows: dict[int, dict] = {}
    end_ms = None
    for page in range(1, max_pages + 1):
        batch = [r for r in history(base, mac, end_ms=end_ms) if r.get("dateutc") is not None]
        if not batch:
            break
        added = 0
        for r in batch:
            if r["dateutc"] not in rows:
                rows[r["dateutc"]] = r
                added += 1
        oldest = min(r["dateutc"] for r in batch)
        if page == 1 or page % 50 == 0:
            when = datetime.fromtimestamp(oldest / 1000, timezone.utc).strftime("%Y-%m-%d")
            print(f"  page {page}: {len(rows):,} readings so far, back to {when}", flush=True)
        if added == 0 or (stop_before_ms is not None and oldest <= stop_before_ms):
            break
        end_ms = oldest - 1
    return sorted(rows.values(), key=lambda r: r["dateutc"])
