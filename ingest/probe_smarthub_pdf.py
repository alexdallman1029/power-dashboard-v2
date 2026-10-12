"""Diagnostic: download one SmartHub bill PDF with our login and print its LAYOUT.

    python -m ingest.probe_smarthub_pdf [index]      # 0 = newest bill (default)

Picks the bill from the billing-history list (the list supplies the timestamp and uuid
SmartHub requires). Every digit in printed text is masked as '#', so amounts and numbers
are not printed. Names or street names on the bill may still appear: read the output
before sharing it.
"""
import io
import re
import sys

from shared.smarthub import billing_overview, fetch_bill_pdf, login


def masked_lines(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line or re.search(r"\S+@\S+\.\S+", line):  # skip email addresses
            continue
        yield re.sub(r"\d", "#", line)


def main(index: int = 0) -> None:
    token = login()
    bills = sorted(
        (b for b in billing_overview(token) if b.get("showViewBillLink")),
        key=lambda b: b["billingDateTimestamp"],
        reverse=True,
    )
    print(f"{len(bills)} bills have a view-bill link; trying number {index} (0 = newest)")
    status, body = fetch_bill_pdf(token, bills[index])
    print(f"PDF request: HTTP {status}, {len(body)} bytes, starts with {body[:5]!r}")
    if status != 200 or not body.startswith(b"%PDF"):
        if len(body) < 500 and not body.lstrip().startswith(b"<"):
            print("  short message from server (digits masked):", re.sub(r"\d", "#", body.decode("utf-8", "replace")))
        return

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    print(f"pages: {len(reader.pages)}")
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        print(f"\n===== PAGE {i}: {len(text)} characters of text =====")
        for line in list(masked_lines(text))[:150]:
            print(line)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
