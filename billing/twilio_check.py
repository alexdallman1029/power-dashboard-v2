"""Diagnose the Twilio secrets without sending anything.

    python -m billing.twilio_check

Logs in with TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN, lists the phone numbers on that
account, and says whether TWILIO_FROM_NUMBER is one of them. This repo's Actions logs are
public, so only the last four digits of any number are ever printed.
"""
import os
import re
import sys

import requests

BASE = "https://api.twilio.com/2010-04-01"


def last4(num: str) -> str:
    digits = re.sub(r"\D", "", num)
    return f"number ending {digits[-4:]}" if len(digits) >= 4 else "(not a phone number)"


def main() -> int:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    frm = os.environ.get("TWILIO_FROM_NUMBER", "")

    print("== What the secrets look like (no values shown)")
    print(f"  TWILIO_ACCOUNT_SID: {'starts with AC' if sid.startswith('AC') else 'does NOT start with AC'}, {len(sid)} characters (should be 34)")
    print(f"  TWILIO_AUTH_TOKEN: {len(token)} characters{' (has a space or line break at the end)' if token != token.strip() else ''} (should be 32)")
    print(f"  TWILIO_FROM_NUMBER: {len(frm)} characters; "
          + ("looks like +1XXXXXXXXXX" if re.fullmatch(r"\+1\d{10}", frm) else
             "does NOT look like +1XXXXXXXXXX" + (" (it starts with PN: that is a Phone Number SID, not a number)" if frm.startswith("PN") else "")))
    if frm != frm.strip():
        print("  -> the From number has a space or line break at the start or end")

    r = requests.get(f"{BASE}/Accounts/{sid}.json", auth=(sid, token.strip()), timeout=30)
    print("\n== Login")
    if r.status_code == 401:
        print("  FAILED: Twilio rejected the SID/token pair. Re-copy both from the account's console home page.")
        return 1
    if not r.ok:
        print(f"  Unexpected answer: HTTP {r.status_code}")
        return 1
    acct = r.json()
    print(f"  OK. This SID is a {acct.get('type', '?')} account, status {acct.get('status', '?')}.")
    if acct.get("type") == "Trial":
        print("  NOTE: this is a TRIAL account. The approved campaign is on a full (paid) account, so these are probably the OLD account's keys.")

    r = requests.get(f"{BASE}/Accounts/{sid}/IncomingPhoneNumbers.json", params={"PageSize": 50}, auth=(sid, token.strip()), timeout=30)
    numbers = r.json().get("incoming_phone_numbers", []) if r.ok else []
    print(f"\n== Phone numbers on this account: {len(numbers)}")
    for n in numbers:
        caps = n.get("capabilities", {})
        print(f"  {last4(n['phone_number'])}, SMS {'yes' if caps.get('sms') else 'NO'}")
    match = any(n["phone_number"] == frm.strip() for n in numbers)
    print("\n== Result")
    if match:
        print(f"  The From number ({last4(frm)}) IS on this account. The secrets agree.")
    elif not numbers:
        print("  This account has NO phone numbers. The SID/token are from a different account than the one that owns your number.")
    else:
        print(f"  The From number ({last4(frm)}) is NOT on this account. Compare its last four digits with the list above:")
        print("   - if one of the listed numbers is the right one, copy it (with +1) into TWILIO_FROM_NUMBER;")
        print("   - if none is, the SID/token are from a different account than your number.")
    return 0 if match else 1


if __name__ == "__main__":
    sys.exit(main())
