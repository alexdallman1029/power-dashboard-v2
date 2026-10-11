"""Diagnostic: show the SHAPE of SmartHub's billing-history response (structure only).

Prints key names and types, with digits in keys masked. Dollar amounts, dates and
account numbers are NOT printed.
"""
import json
import re

from shared.smarthub import billing_overview, login


def mask(s: str) -> str:
    return re.sub(r"\d", "#", s)


def shape(obj, depth=0):
    if depth > 6:
        return "..."
    if isinstance(obj, dict):
        return {mask(str(k)): shape(v, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [f"{len(obj)} items", shape(obj[0], depth + 1)] if obj else ["empty list"]
    return type(obj).__name__


def main() -> None:
    data = billing_overview(login())
    print(json.dumps(shape(data), indent=1)[:6000])


if __name__ == "__main__":
    main()
