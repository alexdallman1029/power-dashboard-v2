"""Parse the text of a Blachly-Lane (SmartHub) bill PDF into per-meter charge lines.

Layout (one block per meter/service location, usually one per page):

    Service Address: ...                      <- ignored; addresses are never stored
    Description Meter No. Reading Dates ...   <- column header
    Residential Rural - 1 Phase R123 08/20/26 09/21/26 12345 13148 32 1 803
    Current Service Detail
    kWh Charge 803 kWh @ $0.11190 $89.86
    Distribution Capacity Charge $40.47
    Prompt Payment Credit -$3.00
    Total Current Charges for this Location $145.86

Every meter's lines are checked against its printed total (lines_ok).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

_MONEY = r"-?\$[\d,]+\.\d\d"

READING_ROW = re.compile(
    r"^(?P<desc>.+?)\s+(?P<meter>R\d+)\s+(?P<from>\d\d/\d\d/\d\d)\s+(?P<to>\d\d/\d\d/\d\d)"
    r"\s+(?P<prev>\d+)\s+(?P<pres>\d+)\s+(?P<days>\d+)\s+(?P<mult>[\d.]+)\s+(?P<usage>[\d,]+)$"
)
CHARGE_LINE = re.compile(
    rf"^(?P<desc>.+?)(?:\s+(?P<kwh>[\d,]+(?:\.\d+)?)\s+kWh\s+@\s+\$(?P<rate>\d*\.\d+))?\s+(?P<amt>{_MONEY})$"
)
TOTAL_LINE = re.compile(rf"^Total Current Charges for this Location\s+(?P<amt>{_MONEY})$")


@dataclass
class ChargeLine:
    description: str
    amount: float
    kwh: float | None = None
    rate: float | None = None


@dataclass
class MeterBlock:
    meter: str
    read_from: dt.date
    read_to: dt.date
    prev_reading: int
    present_reading: int
    days: int
    multiplier: float
    usage_kwh: float
    lines: list[ChargeLine] = field(default_factory=list)
    total: float | None = None

    @property
    def lines_ok(self) -> bool:
        """True when the charge lines add up to the printed total for this location."""
        return self.total is not None and abs(sum(l.amount for l in self.lines) - self.total) < 0.015


def _money(s: str) -> float:
    return float(s.replace("$", "").replace(",", ""))


def _date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%m/%d/%y").date()


def parse_bill_text(text: str) -> list[MeterBlock]:
    """Parse the full extracted text (all pages joined) into one MeterBlock per meter."""
    blocks: list[MeterBlock] = []
    current: MeterBlock | None = None
    in_detail = False
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue

        m = READING_ROW.match(line)
        if m:
            current = MeterBlock(
                meter=m["meter"],
                read_from=_date(m["from"]),
                read_to=_date(m["to"]),
                prev_reading=int(m["prev"]),
                present_reading=int(m["pres"]),
                days=int(m["days"]),
                multiplier=float(m["mult"]),
                usage_kwh=float(m["usage"].replace(",", "")),
            )
            blocks.append(current)
            in_detail = False
            continue

        if current is None:
            continue
        if line.startswith("Current Service Detail"):
            in_detail = True
            continue
        if not in_detail:
            continue

        t = TOTAL_LINE.match(line)
        if t:
            current.total = _money(t["amt"])
            in_detail = False
            continue
        c = CHARGE_LINE.match(line)
        if c:
            current.lines.append(
                ChargeLine(
                    description=c["desc"].strip(),
                    amount=_money(c["amt"]),
                    kwh=float(c["kwh"].replace(",", "")) if c["kwh"] else None,
                    rate=float(c["rate"]) if c["rate"] else None,
                )
            )
    return blocks
