"""Write the config file electric-usage-downloader expects, from environment variables.

Credentials come from GitHub secrets and are written outside the repo (see
.github/workflows/utility-pull.yml), so they are never committed.
JSON is valid YAML, so json.dump handles quoting of odd characters in passwords.
"""
import json
import os
import sys

API_URL = "https://blachlylane.smarthub.coop"
TIMEZONE = "America/Los_Angeles"


def _num(v: str):
    """Account/location numbers are digits; keep leading zeros by leaving those as text."""
    return int(v) if v.isdigit() and str(int(v)) == v else v


def main(out_path: str) -> None:
    cfg = {
        "extract_days": 10,  # unused: the workflow always passes --start/--end
        "smarthub": {
            "api_url": API_URL,
            "username": os.environ["SMARTHUB_USERNAME"],
            "password": os.environ["SMARTHUB_PASSWORD"],
            "account": _num(os.environ["SMARTHUB_ACCOUNT"].strip()),
            "service_location": _num(os.environ["SMARTHUB_SERVICE_LOCATION"].strip()),
            "timezone": TIMEZONE,
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


if __name__ == "__main__":
    main(sys.argv[1])
