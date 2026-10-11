"""Diagnostic: download one SmartHub bill PDF with our login and print its LAYOUT.

    python -m ingest.probe_smarthub_pdf 2026_10_5

Every digit is masked as '#', so amounts, dates and numbers are not printed. Names or
street names on the bill may still appear: read the output before sharing it.
"""
import io
import os
import re
import sys
import time
import uuid

from shared.smarthub import API, HOST, _get, login


def masked_lines(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line or re.search(r"\S+@\S+\.\S+", line):  # skip email addresses
            continue
        yield re.sub(r"\d", "#", line)


def main(bill_date: str) -> None:
    token = login()
    acct = os.environ["SMARTHUB_ACCOUNT"].strip()
    url = (
        f"{API}/services/secured/billPdfService/{bill_date}_{acct}.pdf"
        f"?account={acct}&timestamp={int(time.time() * 1000)}&uuid={uuid.uuid4()}&systemOfRecord=UTILITY"
    )
    status, body = _get(
        url,
        {
            "authority": HOST,
            "authorization": "Bearer " + token,
            "x-nisc-smarthub-username": os.environ["SMARTHUB_USERNAME"],
        },
    )
    print(f"PDF request: HTTP {status}, {len(body)} bytes, starts with {body[:5]!r}")
    if status != 200 or not body.startswith(b"%PDF"):
        print("Not a PDF; body (digits masked):", re.sub(r"\d", "#", body[:300].decode("utf-8", "replace")))
        return

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    print(f"pages: {len(reader.pages)}")
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        print(f"\n===== PAGE {i}: {len(text)} characters of text =====")
        for line in list(masked_lines(text))[:120]:
            print(line)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026_10_5")
