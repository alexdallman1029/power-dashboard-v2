"""Data for the dashboard's charts and tables (pure pandas/sqlite, no Streamlit).

Kept separate from the page so it can be tested against the real database.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

from billing.compute import (
    POLE_BARN_MAIN_GID,
    _device_main_kwh,
    _device_total_pref_main,
    _pool_inputs,
    tenant_charges_for_ym,
    vacant_site_usage,
)
from billing.config import display_channel_name, load_config
from billing.period import billing_period_for_ym
from billing.utility import has_bill_tables, reconcile


def month_range(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def shift_month(ym: str, delta: int) -> str:
    y, m = map(int, ym.split("-"))
    n = y * 12 + (m - 1) + delta
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


# ---------------------------------------------------------------- tenants

def tenant_history(conn: sqlite3.Connection, months: list[str]) -> pd.DataFrame:
    """One row per (month, occupied site) with the bill and its components."""
    frames = []
    for ym in months:
        df = tenant_charges_for_ym(conn, ym).copy()
        if df.empty:
            continue
        df.insert(0, "ym", ym)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def site_cards(conn: sqlite3.Connection, ym: str, people_override: dict | None = None) -> pd.DataFrame:
    """One row per site (vacant ones included) for the tenant cards.

    `People` is the headcount used (the what-if choice, else the real one from sites.toml),
    `Configured` is the real one. Vacant sites have no charge but still show their own meter.
    """
    cfg = load_config()
    p = _pool_inputs(conn, ym, people_override)
    charges = tenant_charges_for_ym(conn, ym, people_override)
    charges = charges.set_index("Tenant") if not charges.empty else charges
    rows = []
    for site in cfg.sites:
        occupied = site.name in charges.index
        rows.append(
            {
                "Site": site.name,
                "People": p["site_people"][site.name],
                "Configured": cfg.people(site, ym),
                "Own meter kWh": p["site_kwh"][site.name],
                "metered": site.device_gid is not None and bool(site.channel),
                "Charge ($)": float(charges.loc[site.name, "Charge ($)"]) if occupied else None,
                "kWh (calc)": float(charges.loc[site.name, "kWh (calc)"]) if occupied else None,
            }
        )
    return pd.DataFrame(rows)


def channel_breakdown(conn: sqlite3.Connection, gid: int, start: dt.date, end: dt.date) -> tuple[pd.DataFrame, float, float]:
    """Circuits on one Emporia hub: (table of Channel / kWh / Share %, Main kWh, circuits total).

    Tenant channels are shown under their site names, never the people's names from Emporia.
    """
    cfg = load_config()
    df = pd.read_sql_query(
        "select channel_num, coalesce(nullif(trim(channel_name), ''), 'Unnamed channel ' || channel_num) as Channel, "
        "sum(kwh) as kWh from usage_daily where device_gid = ? and day >= ? and day <= ? and channel_num != 0 "
        "group by channel_num, Channel",
        conn,
        params=(int(gid), start.isoformat(), end.isoformat()),
    )
    main = conn.execute(
        "select sum(kwh) from usage_daily where device_gid = ? and channel_num = 0 and day >= ? and day <= ?",
        (int(gid), start.isoformat(), end.isoformat()),
    ).fetchone()[0] or 0.0
    df = df[df["kWh"] > 0.05].copy()
    df["Channel"] = df["Channel"].map(lambda n: display_channel_name(cfg, int(gid), n))
    df = df.sort_values("kWh", ascending=False)[["Channel", "kWh"]].reset_index(drop=True)
    total = float(df["kWh"].sum())
    df["Share %"] = df["kWh"] / total * 100.0 if total else 0.0
    return df, float(main), total


def bill_breakdown_dollars(df: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Long format for a stacked bar: what each site's dollars are made of."""
    rows = []
    for _, r in df.iterrows():
        for label, kwh_col in [
            ("Own usage", "Own kWh"),
            ("Kitchen / lounge share", "Kitchen kWh"),
            ("Shop share", "Shop kWh"),
            ("Base load share", "Base kWh"),
        ]:
            rows.append({"Tenant": r["Tenant"], "Part": label, "$": r[kwh_col] * rate, "kWh": r[kwh_col]})
        rows.append({"Tenant": r["Tenant"], "Part": "Monthly fee", "$": r["Fee ($)"], "kWh": 0.0})
    return pd.DataFrame(rows)


def occupancy_grid(months: list[str]) -> pd.DataFrame:
    """People per site per month, from sites.toml (0 = vacant)."""
    cfg = load_config()
    rows = []
    for site in cfg.sites:
        for ym in months:
            rows.append({"Site": site.name, "ym": ym, "People": cfg.people(site, ym)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------- farm power

def farm_ledger(conn: sqlite3.Connection, months: list[str]) -> pd.DataFrame:
    """Where the farm's measured kWh went each billing month.

    tenants + owners = Emporia pole barn main; + grow barn = everything Emporia sees on the
    farm meter. The fixed 'base load' added to bills is not measured power, so it is left out.
    """
    cfg = load_config()
    rows = []
    for ym in months:
        start, end = billing_period_for_ym(ym)
        s, e = start.isoformat(), end.isoformat()
        p = _pool_inputs(conn, ym)
        t = tenant_charges_for_ym(conn, ym)
        billed = sum(n for n in p["site_people"].values() if n > 0)
        everyone = billed + p["unbilled_people"]
        tenants = float(t[["Own kWh", "Kitchen kWh", "Shop kWh"]].sum().sum()) if not t.empty else 0.0
        owners = p["shared_shop_kwh"] * p["unbilled_people"] / everyone if everyone else 0.0
        if t.empty:  # nobody billed: the shared shop is all the owners'
            owners = p["shared_shop_kwh"]
        owners += p["vacant_kwh"]  # power metered at vacant sites is the owners', not the tenants'
        grow = (
            _device_total_pref_main(conn, cfg.grow_barn_device, s, e)
            if cfg.grow_barn_device is not None
            else 0.0
        )
        rows.append(
            {
                "ym": ym,
                "Tenants": tenants,
                "Owners": owners,
                "Grow barn": grow,
                "Emporia total": _device_main_kwh(conn, POLE_BARN_MAIN_GID, s, e) + grow,
            }
        )
    out = pd.DataFrame(rows)
    util = utility_kwh_by_cycle(conn, months)
    return out.merge(util, on="ym", how="left")


def utility_kwh_by_cycle(conn: sqlite3.Connection, months: list[str]) -> pd.DataFrame:
    """Farm-meter kWh/$ spread evenly across the days of each tenant cycle (approximate).

    `Coverage` is the share of the cycle's days that the utility bills cover; a cycle that
    runs past the newest bill is partial.
    """
    cfg = load_config()
    if not has_bill_tables(conn):
        return pd.DataFrame({"ym": months, "Utility kWh": None, "Utility $": None, "Coverage": None})
    farm = conn.execute(
        "select read_from, read_to, days, usage_kwh, total_charges from smarthub_bill_meters where meter != ?",
        (cfg.house_meter,),
    ).fetchall()
    rows = []
    for ym in months:
        start, end = billing_period_for_ym(ym)
        kwh = cost = covered = 0.0
        for rf, rt, days, k, c in farm:
            if not days:
                continue
            a = max(start, dt.date.fromisoformat(rf))
            b = min(end, dt.date.fromisoformat(rt) - dt.timedelta(days=1))
            n = (b - a).days + 1
            if n > 0:
                kwh += k / days * n
                cost += (c or 0.0) / days * n
                covered += n
        total_days = (end - start).days + 1
        rows.append(
            {
                "ym": ym,
                "Utility kWh": kwh if covered else None,
                "Utility $": cost if covered else None,
                "Coverage": covered / total_days,
            }
        )
    return pd.DataFrame(rows)


def daily_device(conn: sqlite3.Connection, gid: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily kWh for a device: its Main channel when it has one, else the sum of subchannels."""
    df = pd.read_sql_query(
        """
        select day,
               sum(case when channel_num = 0 then kwh else 0 end) as main,
               sum(case when channel_num != 0 then kwh else 0 end) as subs
        from usage_daily where device_gid = ? and day >= ? and day <= ? group by day order by day
        """,
        conn,
        params=(int(gid), start.isoformat(), end.isoformat()),
    )
    df["kWh"] = df["main"].where(df["main"] > 0, df["subs"])
    df["day"] = pd.to_datetime(df["day"])
    return df[["day", "kWh"]]


def cycle_vs_previous(conn: sqlite3.Connection, gid: int, ym: str) -> pd.DataFrame:
    """Daily kWh for a cycle and the cycle before it, aligned by day-of-cycle."""
    cur_s, cur_e = billing_period_for_ym(ym)
    prev_s, prev_e = billing_period_for_ym(shift_month(ym, -1))
    cur = daily_device(conn, gid, cur_s, cur_e)
    prev = daily_device(conn, gid, prev_s, prev_e)
    cur["Day of cycle"] = (cur["day"].dt.date - cur_s).map(lambda d: d.days + 1)
    prev["Day of cycle"] = (prev["day"].dt.date - prev_s).map(lambda d: d.days + 1)
    cur["Cycle"] = "This cycle"
    prev["Cycle"] = "Previous cycle"
    return pd.concat([prev, cur], ignore_index=True)


def device_monthly(conn: sqlite3.Connection, gid: int, months: list[str]) -> pd.DataFrame:
    """kWh per billing month for one device (its Main channel, else the sum of subchannels)."""
    if not months:
        return pd.DataFrame(columns=["ym", "kwh"])
    ym = (
        "case when cast(strftime('%d', day) as integer) >= 23 then strftime('%Y-%m', date(day, '+1 month')) "
        "else strftime('%Y-%m', day) end"
    )
    df = pd.read_sql_query(
        f"select {ym} as ym, sum(case when channel_num = 0 then kwh else 0 end) as main, "
        f"sum(case when channel_num != 0 then kwh else 0 end) as subs "
        f"from usage_daily where device_gid = ? group by ym",
        conn,
        params=(int(gid),),
    )
    df["kwh"] = df["main"].where(df["main"] > 0, df["subs"])
    return pd.DataFrame({"ym": months}).merge(df[["ym", "kwh"]], on="ym", how="left").fillna({"kwh": 0.0})


# ----------------------------------------------------------- utility bills

def bill_list(conn: sqlite3.Connection) -> pd.DataFrame:
    if not has_bill_tables(conn):
        return pd.DataFrame()
    return pd.read_sql_query(
        "select * from smarthub_bills where bill_ts in (select bill_ts from smarthub_bill_meters) "
        "order by bill_date desc",
        conn,
    )


def bill_detail(conn: sqlite3.Connection, bill_ts: int) -> list[dict]:
    """The bill, laid out like the paper one: per meter, the reading row then charge lines."""
    cfg = load_config()
    meters = conn.execute(
        "select meter, read_from, read_to, prev_reading, present_reading, days, multiplier, usage_kwh, "
        "total_charges, lines_ok from smarthub_bill_meters where bill_ts = ? order by meter = ? desc, meter",
        (bill_ts, cfg.house_meter),
    ).fetchall()
    out = []
    for m in meters:
        lines = pd.read_sql_query(
            "select description as Description, kwh as kWh, rate as Rate, amount as Amount "
            "from smarthub_bill_lines where bill_ts = ? and meter = ? order by seq",
            conn,
            params=(bill_ts, m[0]),
        )
        out.append(
            {
                "meter": m[0],
                "role": "House" if m[0] == cfg.house_meter else "Farm (pole barn, RV sites, grow barn)",
                "read_from": m[1], "read_to": m[2], "prev": m[3], "present": m[4],
                "days": m[5], "multiplier": m[6], "usage_kwh": m[7], "total": m[8], "ok": bool(m[9]),
                "lines": lines,
            }
        )
    return out


def usage_history(conn: sqlite3.Connection, n: int = 24) -> pd.DataFrame:
    """Per bill and meter: kWh, days, kWh/day, dollars, $/kWh, plus the same-period-last-year kWh/day."""
    cfg = load_config()
    if not has_bill_tables(conn):
        return pd.DataFrame()
    df = pd.read_sql_query(
        "select b.bill_date, m.meter, m.read_from, m.read_to, m.days, m.usage_kwh, m.total_charges, m.lines_ok "
        "from smarthub_bill_meters m join smarthub_bills b using (bill_ts) order by b.bill_date",
        conn,
    )
    if df.empty:
        return df
    df["Role"] = df["meter"].map(lambda m: "House" if m == cfg.house_meter else "Farm")
    # Dollar figures are only trusted when the bill's charge lines added up to its printed total
    df.loc[df["lines_ok"] != 1, "total_charges"] = float("nan")
    df["kWh/day"] = df["usage_kwh"] / df["days"]
    df["$/day"] = df["total_charges"] / df["days"]
    df["$/kWh"] = df["total_charges"] / df["usage_kwh"]
    df["Month"] = pd.to_datetime(df["read_to"]).dt.to_period("M").astype(str)
    # same meter role, roughly one year earlier (nearest bill 330-400 days before)
    last_year = []
    for _, r in df.iterrows():
        end = pd.to_datetime(r["read_to"])
        cand = df[(df["Role"] == r["Role"])]
        gap = (end - pd.to_datetime(cand["read_to"])).dt.days
        match = cand[(gap > 330) & (gap < 400)]
        last_year.append(float(match["kWh/day"].iloc[0]) if not match.empty else None)
    df["Last year kWh/day"] = last_year
    return df.tail(n * 2)


# --------------------------------------------------------- needs attention

def needs_attention(conn: sqlite3.Connection, ym: str, today: dt.date | None = None) -> list[tuple[str, str]]:
    """(severity, message) for things worth a look. severity: 'warning' or 'info'."""
    today = today or dt.date.today()
    notes: list[tuple[str, str]] = []

    for site, kwh in vacant_site_usage(conn, ym).items():
        notes.append(("warning", f"{site} is marked vacant but its meter used {kwh:,.0f} kWh this cycle. It counts toward your share, not any tenant's bill."))

    row = conn.execute("select value from kv_store where key = 'last_ingested_at_utc'").fetchone()
    if row:
        last = dt.datetime.fromisoformat(row[0]).astimezone(dt.timezone.utc)
        age_h = (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 3600
        if age_h > 36:
            notes.append(("warning", f"Emporia data was last updated {age_h / 24:.0f} days ago."))

    if has_bill_tables(conn):
        rec = reconcile(conn)
        if not rec.empty:
            latest = rec.sort_values("Bill date").iloc[-1]
            if not bool(latest["Checked"]):
                notes.append(("warning", f"The {latest['Bill date']} utility bill did not add up when read; its dollar figures may be off."))
            if latest["Unaccounted %"] is not None and abs(latest["Unaccounted %"]) > 5:
                notes.append(("info", f"Emporia differed from the farm meter by {latest['Unaccounted %']:+.1f}% on the {latest['Bill date']} bill."))
            age = (today - dt.date.fromisoformat(latest["Bill date"])).days
            if age > 45:
                notes.append(("info", f"The newest utility bill on file is {age} days old."))
    return notes


# ------------------------------------------------------------------ weather
WEATHER_DB = "data/weather.sqlite"
PACIFIC_TZ = "America/Los_Angeles"


def weather_available(path: str = WEATHER_DB) -> bool:
    import os

    return os.path.exists(path)


def weather_daily(wconn: sqlite3.Connection, start: dt.date, end: dt.date, tz: str = PACIFIC_TZ) -> pd.DataFrame:
    """Daily weather from the hourly table, for days start..end inclusive.

    tz="America/Los_Angeles" gives local days for weather charts; tz="UTC" matches the day
    boundaries of the energy data (usage_daily), so the two can be lined up exactly.
    """
    lo = (pd.Timestamp(start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d 00:00")
    hi = (pd.Timestamp(end) + pd.Timedelta(days=2)).strftime("%Y-%m-%d 00:00")
    h = pd.read_sql_query(
        "select * from weather_hourly where hour_utc >= ? and hour_utc < ?", wconn, params=(lo, hi)
    )
    if h.empty:
        return pd.DataFrame()
    t = pd.to_datetime(h["hour_utc"], utc=True).dt.tz_convert(tz)
    h["day"] = t.dt.tz_localize(None).dt.normalize()
    h = h[(h["day"] >= pd.Timestamp(start)) & (h["day"] <= pd.Timestamp(end))]
    g = h.groupby("day")
    out = pd.DataFrame(
        {
            "Hours": g["hour_utc"].count(),
            "High °F": g["temp_f_max"].max(),
            "Low °F": g["temp_f_min"].min(),
            "Avg °F": g["temp_f_avg"].mean(),
            "Rain in": g["rain_in"].sum(),
            "Wind mph": g["wind_mph_avg"].mean(),
            "Gust mph": g["gust_mph_max"].max(),
            "Humidity %": g["humidity_avg"].mean(),
            "Solar W/m²": g["solar_wm2_avg"].mean(),
        }
    ).reset_index()
    return out


def cycle_weather_summary(wconn: sqlite3.Connection, ym: str) -> dict:
    """Weather for a billing cycle, and for the same cycle a year earlier."""

    def one(label_ym: str) -> dict | None:
        s, e = billing_period_for_ym(label_ym)
        d = weather_daily(wconn, s, e)
        d = d[d["Hours"] >= 20] if not d.empty else d
        if d.empty:
            return None
        return {
            "days": len(d), "avg": float(d["Avg °F"].mean()), "high": float(d["High °F"].max()),
            "low": float(d["Low °F"].min()), "rain": float(d["Rain in"].sum()),
            "solar": float(d["Solar W/m²"].mean()),
        }

    return {"now": one(ym), "last_year": one(shift_month(ym, -12))}


def latest_conditions(wconn: sqlite3.Connection) -> dict | None:
    r = wconn.execute(
        "select hour_utc, temp_f_avg, feels_like_f, humidity_avg, wind_mph_avg, gust_mph_max, rain_in, solar_wm2_avg "
        "from weather_hourly where temp_f_avg is not null order by hour_utc desc limit 1"
    ).fetchone()
    if not r:
        return None
    local = pd.Timestamp(r[0], tz="UTC").tz_convert(PACIFIC_TZ)
    rain24 = wconn.execute(
        "select coalesce(sum(rain_in), 0) from weather_hourly where hour_utc > ?",
        ((pd.Timestamp(r[0]) - pd.Timedelta(hours=24)).strftime("%Y-%m-%d %H:00"),),
    ).fetchone()[0]
    return {"when": local, "temp": r[1], "feels": r[2], "humidity": r[3], "wind": r[4], "gust": r[5],
            "rain24": float(rain24), "solar": r[7]}


def energy_vs_temp(conn: sqlite3.Connection, wconn: sqlite3.Connection, gid: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily kWh for a device against that day's average outdoor temperature (UTC days, so both match)."""
    e = daily_device(conn, gid, start, end)
    w = weather_daily(wconn, start, end, tz="UTC")
    if e.empty or w.empty:
        return pd.DataFrame()
    m = e.merge(w[["day", "Avg °F", "Hours", "Rain in"]], on="day")
    return m[(m["Hours"] >= 20) & (m["kWh"] > 0)].reset_index(drop=True)
