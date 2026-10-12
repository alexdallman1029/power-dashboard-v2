"""Loads and validates billing/sites.toml (the file you edit to change sites/people)."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("sites.toml")
_YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

History = tuple[tuple[str, int], ...]  # (from_ym, people), sorted by from_ym


@dataclass(frozen=True)
class Site:
    name: str
    greeting: str
    device_gid: int | None  # None = no Emporia meter (yet)
    channel: str | None
    history: History


@dataclass(frozen=True)
class BillingConfig:
    rate_per_kwh: float
    monthly_fee: float
    base_kwh: float
    vacant_alert_kwh: float
    house_meter: str
    farm_emporia_devices: tuple[int, ...]
    unbilled_history: History
    sites: tuple[Site, ...]

    def people(self, site: Site, ym: str) -> int:
        return _people_for(site.history, ym)

    def unbilled(self, ym: str) -> int:
        return _people_for(self.unbilled_history, ym)


def _people_for(history: History, ym: str) -> int:
    people = 0
    for from_ym, n in history:
        if from_ym <= ym:
            people = n
    return people


def _parse_history(raw, where: str) -> History:
    if not raw:
        raise ValueError(f"sites.toml: {where} needs at least one history entry")
    out = []
    for e in raw:
        from_ym, people = e.get("from"), e.get("people")
        if not isinstance(from_ym, str) or not _YM.match(from_ym):
            raise ValueError(f'sites.toml: {where}: "from" must look like "2026-11", got {from_ym!r}')
        if not isinstance(people, int) or isinstance(people, bool) or people < 0:
            raise ValueError(f"sites.toml: {where}: people must be a whole number >= 0, got {people!r}")
        out.append((from_ym, people))
    return tuple(sorted(out))


@lru_cache(maxsize=1)
def load_config(path: Path = CONFIG_PATH) -> BillingConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    rates = raw["rates"]
    sites = [
        Site(
            name=s["name"],
            greeting=s.get("greeting") or s["name"],
            device_gid=int(s["device_gid"]) if "device_gid" in s else None,
            channel=s.get("channel"),
            history=_parse_history(s.get("history"), f'site "{s["name"]}"'),
        )
        for s in raw["sites"]
    ]
    names = [s.name for s in sites]
    if len(set(names)) != len(names):
        raise ValueError(f"sites.toml: duplicate site names: {names}")

    return BillingConfig(
        rate_per_kwh=float(rates["rate_per_kwh"]),
        monthly_fee=float(rates["monthly_fee"]),
        base_kwh=float(rates["base_kwh"]),
        vacant_alert_kwh=float(rates.get("vacant_alert_kwh", 5.0)),
        house_meter=str(raw.get("utility", {}).get("house_meter", "")),
        farm_emporia_devices=tuple(int(g) for g in raw.get("utility", {}).get("farm_emporia_devices", [])),
        unbilled_history=_parse_history(
            raw.get("unbilled_residents", {}).get("history", [{"from": "2000-01", "people": 0}]),
            "unbilled_residents",
        ),
        sites=tuple(sites),
    )
