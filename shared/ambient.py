"""Minimal client for the Ambient Weather REST API (https://ambientweather.docs.apiary.io/).

Two keys are required on every call: the API key (your account) and the application key
(this program). Both come from environment variables / GitHub secrets and are never printed.
The API allows roughly one request per second per key, so calls are paced.
"""
from __future__ import annotations

import http.client
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


ATTEMPTS = 6
# Network hiccups: a dropped connection, a half-received reply, a timeout, or a reply cut off mid-way
_TRANSIENT = (http.client.HTTPException, urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError)


def get(base: str, path: str, **params):
    """GET {base}{path}; returns (HTTP status, parsed JSON or None).

    Retries with a growing pause on rate limiting (429), server errors (5xx) and network hiccups,
    so one dropped connection in a thousand requests doesn't lose a long run.
    """
    query = urllib.parse.urlencode({**_keys(), **{k: v for k, v in params.items() if v is not None}})
    url = f"{base}{path}?{query}"
    for attempt in range(1, ATTEMPTS + 1):
        time.sleep(PAUSE)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as r:
                body = r.read()
                return r.status, json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < ATTEMPTS:
                time.sleep(3 * attempt)
                continue
            try:
                return e.code, json.loads(e.read() or b"null")
            except (ValueError, http.client.HTTPException, OSError):
                return e.code, None
        except _TRANSIENT as e:
            if attempt == ATTEMPTS:
                raise RuntimeError(f"Ambient request kept failing after {ATTEMPTS} tries ({type(e).__name__})") from e
            print(f"  network hiccup ({type(e).__name__}); retrying", flush=True)
            time.sleep(3 * attempt)
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


HOP_MS = 24 * 3600 * 1000  # a request only looks back about a day from its endDate


class FetchInterrupted(RuntimeError):
    """A request kept failing; `rows` holds the readings fetched before that."""

    def __init__(self, rows: list[dict], message: str):
        super().__init__(message)
        self.rows = rows


def fetch_all(
    base: str, mac: str, stop_before_ms: int | None = None, max_pages: int = 8000, max_empty_days: int = 150
) -> list[dict]:
    """Page backward from now until the history runs out (or reaches stop_before_ms).

    Each request returns up to 288 readings ending at `endDate`, newest first, and appears to look
    back only about a day, so the next page ends one millisecond before the oldest reading already
    held. When a request comes back empty (the station was offline), step back one day at a time
    until readings resume. After `max_empty_days` in a row with nothing, treat it as the true start.
    """
    rows: dict[int, dict] = {}
    end_ms = None
    empty_days = 0
    for page in range(1, max_pages + 1):
        try:
            batch = [r for r in history(base, mac, end_ms=end_ms) if r.get("dateutc") is not None]
        except RuntimeError as e:
            # keep everything fetched so far; the caller decides whether to save it
            raise FetchInterrupted(sorted(rows.values(), key=lambda r: r["dateutc"]), str(e)) from e
        if not batch:
            if end_ms is None:
                end_ms = int(time.time() * 1000)
            empty_days += 1
            if empty_days > max_empty_days or (stop_before_ms is not None and end_ms <= stop_before_ms):
                break
            end_ms -= HOP_MS
            continue
        if empty_days:
            print(f"  readings resumed after {empty_days} empty day(s)", flush=True)
            empty_days = 0
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
    if rows:
        first = datetime.fromtimestamp(min(rows) / 1000, timezone.utc).strftime("%Y-%m-%d")
        print(f"  history starts {first}; stopped after {empty_days} empty day(s) in a row", flush=True)
    return sorted(rows.values(), key=lambda r: r["dateutc"])
