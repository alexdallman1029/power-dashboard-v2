"""Rent, internet and storage for the tenant text.

THE DOLLAR AMOUNTS ARE PRIVATE. This repo is public, so none of them live here. They come from the
RENT_CONFIG environment variable (a GitHub secret). For a dry run on your own computer you can instead
put the same JSON in rent_config.local.json, which git ignores. This file holds only the rules.

RENT_CONFIG is JSON shaped like this (numbers here are made up):

    {
      "greeting": "Greetings. I am Alex's billing bot.",
      "payment": "You can pay via Zelle, Venmo, or check. ...",
      "sites": {
        "East Site": {"base_rent": 1000, "extra_person": 100, "internet_per_person": 10, "storage": 0},
        "Yurt":      {"base_rent": 900,  "internet_per_person": 10,
                      "storage": [{"from": "2026-11", "amount": 40}, {"from": "2027-01", "amount": 0}]},
        "Airstream": {"idle": true}
      }
    }

Rules:
  * rent = base_rent + extra_person for every person beyond the first (extra_person defaults to 0)
  * internet = internet_per_person x people
  * storage is a number, or a list of {"from": "YYYY-MM", "amount": n}; the newest entry on or before the
    rent month applies. A storage line is left out of the text when it is 0.
  * "idle": true means the site is not billed or texted at all until that is removed.
  * The text is sent on the 1st, so "this month" is the month AFTER the power cycle's label
    (the cycle that ends Oct 22 is "2026-10"; its text goes out in November, "2026-11").
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

LOCAL_FILE = Path("rent_config.local.json")
_YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def next_month(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def parse_rent_config(raw: str) -> dict:
    """Parse and check the JSON, with clear messages (it is typed by hand into a secret)."""
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"RENT_CONFIG is not valid JSON ({e.msg} at character {e.pos})") from None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("sites"), dict) or not cfg["sites"]:
        raise ValueError('RENT_CONFIG needs a "sites" object')
    for name, s in cfg["sites"].items():
        if s.get("idle"):
            continue
        for key in ("base_rent", "internet_per_person"):
            if not isinstance(s.get(key), (int, float)) or isinstance(s.get(key), bool) or s[key] < 0:
                raise ValueError(f'RENT_CONFIG: "{name}" needs a number for "{key}"')
        extra = s.get("extra_person", 0)
        if not isinstance(extra, (int, float)) or extra < 0:
            raise ValueError(f'RENT_CONFIG: "{name}" extra_person must be a number')
        storage = s.get("storage", 0)
        if isinstance(storage, list):
            for e in storage:
                if not (isinstance(e, dict) and _YM.match(str(e.get("from", ""))) and isinstance(e.get("amount"), (int, float))):
                    raise ValueError(f'RENT_CONFIG: "{name}" storage entries look like {{"from": "2026-11", "amount": 50}}')
        elif not isinstance(storage, (int, float)) or storage < 0:
            raise ValueError(f'RENT_CONFIG: "{name}" storage must be a number or a list of from/amount entries')
    return cfg


def load_rent_config() -> dict | None:
    """From the RENT_CONFIG secret, else a local, git-ignored file; None when neither exists."""
    raw = os.environ.get("RENT_CONFIG", "").strip()
    if not raw and LOCAL_FILE.exists():
        raw = LOCAL_FILE.read_text(encoding="utf-8")
    return parse_rent_config(raw) if raw else None


def storage_for(site_cfg: dict, rent_ym: str) -> float:
    storage = site_cfg.get("storage", 0)
    if isinstance(storage, list):
        applicable = sorted((e for e in storage if e["from"] <= rent_ym), key=lambda e: e["from"])
        return float(applicable[-1]["amount"]) if applicable else 0.0
    return float(storage)


def charges_for(site_cfg: dict, people: int, rent_ym: str) -> dict:
    """Rent, internet and storage for a site this month (all zero if nobody lives there)."""
    if people <= 0:
        return {"rent": 0.0, "internet": 0.0, "storage": 0.0}
    return {
        "rent": float(site_cfg["base_rent"]) + float(site_cfg.get("extra_person", 0)) * (people - 1),
        "internet": float(site_cfg["internet_per_person"]) * people,
        "storage": storage_for(site_cfg, rent_ym),
    }


def money(x: float) -> str:
    return f"${x:,.2f}"


def compose_text(cfg: dict, charges: dict, power: float | None) -> tuple[str, float]:
    """The tenant's text and their total. Lines with nothing to charge are left out."""
    lines = [cfg.get("greeting", "Greetings.")]
    if charges["rent"] > 0:
        lines.append(f"Base rent for this month is {money(charges['rent'])}.")
        lines.append(f"Internet is {money(charges['internet'])}.")
    if power is not None:
        lines.append(f"Power is {money(power)}.")
    if charges["storage"] > 0:
        lines.append(f"Storage is {money(charges['storage'])}.")
    total = charges["rent"] + charges["internet"] + charges["storage"] + (power or 0.0)
    lines.append(f"Total: {money(total)}.")
    lines.append(cfg.get("payment", "You can pay via Zelle, Venmo, or check."))
    return "\n".join(lines), total


# ---- how many text segments a message uses (Twilio bills per segment)
_GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM7_EXT = set(r"^{}\[~]|€") | {"\f"}


def sms_segments(text: str) -> tuple[str, int]:
    """(encoding, segments). One character outside the basic GSM-7 set, such as an emoji, switches the whole
    message to UCS-2: 70 characters per single text, 67 per part of a longer one (GSM-7: 160 and 153)."""
    if all(c in _GSM7 or c in _GSM7_EXT for c in text):
        units, single, multi, enc = sum(2 if c in _GSM7_EXT else 1 for c in text), 160, 153, "GSM-7"
    else:
        units, single, multi, enc = len(text.encode("utf-16-le")) // 2, 70, 67, "UCS-2"
    return enc, (1 if units <= single else math.ceil(units / multi))
