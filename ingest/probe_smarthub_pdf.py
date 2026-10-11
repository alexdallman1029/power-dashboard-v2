"""Diagnostic: download one SmartHub bill PDF with our login and print its LAYOUT.

    python -m ingest.probe_smarthub_pdf 2026_10_5 [2026_10_5_ACCOUNT]

The optional second argument is the exact file-name stem from the "view bill" link; when
given, it is used instead of building the name from the SMARTHUB_ACCOUNT secret, which
also tells us whether that secret matches the account in the link.

Every digit in printed text is masked as '#', so amounts and numbers are not printed.
Names or street names on the bill may still appear: read the output before sharing it.
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


def main(bill_date: str, stem: str = "") -> None:
    token = login()
    secret_acct = os.environ["SMARTHUB_ACCOUNT"].strip()
    if stem:
        acct = stem.rsplit("_", 1)[-1]
        print(f"SMARTHUB_ACCOUNT secret matches the account in the link: {secret_acct == acct} "
              f"(secret has {len(secret_acct)} characters, link has {len(acct)})")
    else:
        acct, stem = secret_acct, f"{bill_date}_{secret_acct}"

    headers = {
        "authority": HOST,
        "authorization": "Bearer " + token,
        "x-nisc-smarthub-username": os.environ["SMARTHUB_USERNAME"],
    }
    base = f"{API}/services/secured/billPdfService/{stem}.pdf"
    full = f"?account={acct}&timestamp={int(time.time() * 1000)}&uuid={uuid.uuid4()}&systemOfRecord=UTILITY"
    variants = {"full query": base + full, "account only": base + f"?account={acct}", "no query": base}

    body = b""
    for name, url in variants.items():
        status, b = _get(url, headers)
        print(f"PDF request ({name}): HTTP {status}, {len(b)} bytes, starts with {b[:5]!r}")
        if len(b) < 500 and not b.lstrip().startswith(b"<"):
            print("  short message from server (digits masked):", re.sub(r"\d", "#", b.decode("utf-8", "replace")))
        if status == 200 and b.startswith(b"%PDF"):
            body = b
            break
    if not body:
        print("No variant returned a PDF.")
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
    main(sys.argv[1] if len(sys.argv) > 1 else "2026_10_5", sys.argv[2] if len(sys.argv) > 2 else "")
