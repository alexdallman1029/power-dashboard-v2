# FishDog Farm power dashboard

Gathers the farm's power and weather data, works out what each tenant owes, shows it all on a dashboard, and texts the monthly amounts. It replaces an older paid Render app and runs for free on GitHub Actions and Streamlit Community Cloud.

This repo is public, so **no private information lives in it**: no keys, phone numbers, rent amounts or tenant names. Those are GitHub secrets (below), and the workflow logs leave out dollar amounts.

## What it does

- **Power:** pulls daily usage from Emporia (per site and per circuit) and the Blachly-Lane co-op's monthly usage and bill PDFs from SmartHub, then reconciles them.
- **Tenant billing:** splits each 23rd-to-22nd billing cycle by site. Shared power is split among the occupied sites. Vacant sites, the grow barn and the owners' share fall to the owners and are never added to a tenant's bill.
- **Weather:** archives the Ambient Weather station (hourly), with heating and cooling degree days, sunshine hours, rainy days, a wind rose and wind-speed explorer, year-over-year comparisons, and ENSO/PDO climate context.
- **Texts:**
  - the day before the 1st, an owner preview with every tenant's total
  - on the 1st, each tenant's text with rent, internet, power, storage (only if above $0), total and how to pay
- **Dashboard:** `streamlit-app.py`, with Power, Weather and Billing tabs. It runs invite-only on Streamlit Community Cloud.

## How it runs

| Workflow | When | What it does |
|---|---|---|
| `daily-pull` | daily | Emporia usage into `data/energy.sqlite` |
| `utility-pull` | daily | SmartHub monthly usage and bill PDFs |
| `ambient-pull` | daily | weather readings into `data/weather.sqlite` |
| `climate-pull` | Mondays | ENSO and PDO indexes |
| `monthly-bills` | 28th-31st (acts only the day before the 1st) and the 1st | owner preview and tenant texts |
| `ambient-backfill`, `*-probe`, `twilio-check` | manual | one-off history load and diagnostics |

Each pull commits the updated SQLite files back to the repo, and the dashboard reads them. GitHub often starts scheduled runs a few hours late.

Texts are a **dry run unless sending is enabled.** Scheduled runs send only when the repository variable `SEND_ENABLED` is `true`. Manual runs send only if you tick "send". A record of what was sent (`data/sent_log.json`) prevents duplicates; the "force" option overrides it.

## Setup

Settings → Secrets and variables → Actions.

**Secrets**

| Name | What it is |
|---|---|
| `EMPORIA_USERNAME`, `EMPORIA_PASSWORD` | Emporia account |
| `SMARTHUB_USERNAME`, `SMARTHUB_PASSWORD`, `SMARTHUB_ACCOUNT`, `SMARTHUB_SERVICE_LOCATION` | Blachly-Lane SmartHub login and IDs |
| `AMBIENT_API_KEY`, `AMBIENT_APPLICATION_KEY` | Ambient Weather keys |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Twilio (the From number in `+1...` format) |
| `TENANT_PHONES` | JSON mapping each site to a phone number (or a list of numbers), plus an `Owner` entry. Only add a tenant's number with their consent. |
| `RENT_CONFIG` | Rent, internet and storage rules and the payment line; see below |

**Variables:** `SEND_ENABLED` (`true` to let scheduled runs send) and `DASHBOARD_URL` (link added to the owner text).

### RENT_CONFIG

Shape (numbers here are made up):

```json
{
  "greeting": "Greetings. I am the billing bot.",
  "payment": "You can pay via Zelle, Venmo, or check.",
  "sites": {
    "East Site": {"base_rent": 1000, "extra_person": 100, "internet_per_person": 10, "storage": 0},
    "Yurt": {"base_rent": 900, "internet_per_person": 10,
             "storage": [{"from": "2026-11", "amount": 40}, {"from": "2027-01", "amount": 0}]},
    "Airstream": {"idle": true}
  }
}
```

- Rent = `base_rent` + `extra_person` for each person beyond the first. Internet = `internet_per_person` × people. People per site come from `billing/sites.toml`.
- `storage` is a number, or a dated list. The newest entry on or before the rent month applies, and the line is omitted at $0.
- `"idle": true` skips a site entirely.
- The text goes out on the 1st, so "this month" is the month after the power cycle's label (the cycle ending Oct 22 is "2026-10" and is texted in November, "2026-11").
- Sending is refused if the secret is missing. To try it on your own computer, put the same JSON in `rent_config.local.json` (git-ignored). Never commit it.
- Emoji, or even a curly apostrophe, switch a text to UCS-2 encoding (70 characters per segment instead of 160). The log shows the segment count.

## Configuration in the repo

`billing/sites.toml` holds the rates, who lives where over time, the utility meters, device IDs and the vacant-site alert level. It contains no rent or contact details.

## Run it locally

```
pip install -r requirements.txt
streamlit run streamlit-app.py
python -m pytest -q tests

# dry run of the texts (nothing is sent; add SHOW_MESSAGES=1 to print them)
python -m billing.send_bills --mode all
```

Useful options for `billing.send_bills`: `--send` (really send), `--mode preview|tenants|all`, `--only Owner`, `--ym YYYY-MM`, `--force`.

Pulls are run as modules, for example `python -m ingest.pull_emporia 3` (last three days).

## Layout

```
billing/   rates, billing math, rent rules, text sending
ingest/    data pulls (Emporia, SmartHub, Ambient, climate) and probes
shared/    API clients and the database helper
ui/        chart helpers
data/      SQLite databases and the sent-text log
tests/     pytest suite (uses made-up numbers only)
```

## Privacy notes

- Public logs show counts and statuses, not dollar amounts or text bodies, unless `SHOW_DETAILS` or `SHOW_MESSAGES` is set for a run.
- Tenant names are not stored; the dashboard uses site names.
- The dashboard is invite-only. Don't put rent amounts or tenant details in the app or the repo.
