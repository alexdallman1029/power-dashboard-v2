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


# ------------------------------------------------------- weather explorer
RESOLUTIONS = ["Hour", "Day", "Month", "Year"]
SUNSHINE_WM2 = 120.0   # an hour counts as sunny when its average solar radiation reaches this
RAINY_DAY_IN = 0.01    # a day counts as rainy at 0.01 in or more (the usual "wet day" cutoff)
DEGREE_DAY_BASE_F = 65.0
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass_point(deg: float) -> str | None:
    """Compass sector (N, NE, ...) for a direction in degrees; each sector is 45 degrees wide."""
    if deg is None or pd.isna(deg):
        return None
    return COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def _period_start(day: pd.Series, resolution: str) -> pd.Series:
    if resolution == "Month":
        return day.dt.to_period("M").dt.to_timestamp()
    if resolution == "Year":
        return day.dt.to_period("Y").dt.to_timestamp()
    return day


def weather_series(
    wconn: sqlite3.Connection, start: dt.date, end: dt.date, resolution: str = "Day",
    base_f: float = DEGREE_DAY_BASE_F, tz: str = PACIFIC_TZ,
) -> pd.DataFrame:
    """Weather at hour / day / month / year resolution, bucketed in local (Pacific) time.

    One row per bucket. Columns combine the hourly values correctly: temperature average and the
    highest high / lowest low, rain summed, gust and UV as peaks, wind direction as the speed-weighted
    prevailing direction. Degree days, sunny hours and rainy days are counted from complete local days
    (20+ hours of data). `Hours` shows how much data each bucket holds, so a partial bucket is visible.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    lo = (pd.Timestamp(start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d 00:00")
    hi = (pd.Timestamp(end) + pd.Timedelta(days=2)).strftime("%Y-%m-%d 00:00")
    h = pd.read_sql_query("select * from weather_hourly where hour_utc >= ? and hour_utc < ?", wconn, params=(lo, hi))
    if h.empty:
        return pd.DataFrame()
    h["local"] = pd.to_datetime(h["hour_utc"], utc=True).dt.tz_convert(tz).dt.tz_localize(None)
    h = h[(h["local"] >= pd.Timestamp(start)) & (h["local"] < pd.Timestamp(end) + pd.Timedelta(days=1))].copy()
    if h.empty:
        return pd.DataFrame()
    h["day"] = h["local"].dt.normalize()
    h["Period"] = h["local"] if resolution == "Hour" else _period_start(h["day"], resolution)

    # wind as vectors, weighted by speed: the prevailing direction, not a plain average of angles
    import math

    rad = h["wind_dir_deg"] * math.pi / 180.0
    h["_u"] = h["wind_mph_avg"] * rad.map(math.sin)
    h["_v"] = h["wind_mph_avg"] * rad.map(math.cos)
    h["_sunny"] = (h["solar_wm2_avg"] >= SUNSHINE_WM2).astype(float).where(h["solar_wm2_avg"].notna())

    g = h.groupby("Period")
    out = pd.DataFrame(
        {
            "Hours": g["hour_utc"].count(),
            "Temp avg °F": g["temp_f_avg"].mean(), "Temp high °F": g["temp_f_max"].max(), "Temp low °F": g["temp_f_min"].min(),
            "Feels like °F": g["feels_like_f"].mean(), "Dew point °F": g["dew_point_f"].mean(),
            "Humidity %": g["humidity_avg"].mean(),
            "Wind mph": g["wind_mph_avg"].mean(), "Gust mph": g["gust_mph_max"].max(),
            "Rain in": g["rain_in"].sum(),
            "Solar avg W/m²": g["solar_wm2_avg"].mean(), "Solar peak W/m²": g["solar_wm2_max"].max(),
            "UV peak": g["uv_max"].max(),
            "Sunny hours": g["_sunny"].sum(min_count=1),
        }
    )
    u, v = g["_u"].sum(min_count=1), g["_v"].sum(min_count=1)
    out["Wind dir °"] = [
        (math.degrees(math.atan2(a, b)) % 360) if pd.notna(a) and pd.notna(b) and (abs(a) + abs(b)) > 1e-9 else float("nan")
        for a, b in zip(u, v)
    ]

    # degree days and rainy days come from complete local days, then add up into the bucket
    days = h.groupby("day").agg(n=("hour_utc", "count"), avg=("temp_f_avg", "mean"), rain=("rain_in", "sum"))
    full = days["n"] >= 20
    days["HDD"] = (base_f - days["avg"]).clip(lower=0).where(full)
    days["CDD"] = (days["avg"] - base_f).clip(lower=0).where(full)
    # A day with rain is rainy however many hours were recorded; only a DRY day needs a full day of data
    # to be trusted (a partial day with no rain yet could still have been wet). The tiny allowance keeps
    # 0.0099999 from hiding a 0.01 in day.
    wet = days["rain"] >= RAINY_DAY_IN - 1e-6
    days["Rainy"] = wet.astype(float).where(wet | full)
    days["Period"] = days.index if resolution == "Hour" else _period_start(pd.Series(days.index, index=days.index), resolution).values
    if resolution == "Hour":
        # degree days and rainy days are per-day ideas, so they are blank at hourly resolution
        out["Heating degree days"] = out["Cooling degree days"] = out["Rainy days"] = float("nan")
        out["Wind dir °"] = g["wind_dir_deg"].first()  # an hour has its own direction, even when calm
    else:
        dg = days.groupby("Period")
        out["Heating degree days"] = dg["HDD"].sum(min_count=1)
        out["Cooling degree days"] = dg["CDD"].sum(min_count=1)
        out["Rainy days"] = dg["Rainy"].sum(min_count=1)
    out["Compass"] = out["Wind dir °"].map(compass_point)
    return out.round(2).reset_index()


def latest_weather_time(wconn: sqlite3.Connection) -> pd.Timestamp | None:
    r = wconn.execute("select max(hour_utc) from weather_hourly").fetchone()[0]
    return None if r is None else pd.Timestamp(r, tz="UTC").tz_convert(PACIFIC_TZ).tz_localize(None)


def first_weather_time(wconn: sqlite3.Connection) -> pd.Timestamp | None:
    r = wconn.execute("select min(hour_utc) from weather_hourly").fetchone()[0]
    return None if r is None else pd.Timestamp(r, tz="UTC").tz_convert(PACIFIC_TZ).tz_localize(None)


def weather_month_of_year(wconn: sqlite3.Connection, base_f: float = DEGREE_DAY_BASE_F) -> pd.DataFrame:
    """Every complete month on record with its Year and month number, for year-over-year overlays."""
    first, last = first_weather_time(wconn), latest_weather_time(wconn)
    if first is None:
        return pd.DataFrame()
    m = weather_series(wconn, first.date(), last.date(), "Month", base_f)
    if m.empty:
        return m
    m["Year"], m["MonthNum"] = m["Period"].dt.year, m["Period"].dt.month
    expected = m["Period"].dt.days_in_month * 24
    return m[m["Hours"] >= 0.85 * expected].reset_index(drop=True)  # drop partial months (the first and the current)


# ------------------------------------------------------- climate patterns
def climate_monthly(wconn: sqlite3.Connection) -> pd.DataFrame:
    """ENSO (ONI) and PDO by month, or an empty frame if the climate feed has not run yet."""
    names = {r[0] for r in wconn.execute("select name from sqlite_master where type = 'table'")}
    if "climate_monthly" not in names:
        return pd.DataFrame()
    df = pd.read_sql_query("select month, oni, oni_season, pdo from climate_monthly order by month", wconn)
    df["Period"] = pd.to_datetime(df["month"] + "-01")
    return df


def climate_now(df: pd.DataFrame) -> dict | None:
    """Where ENSO and the PDO stand now, and how fast ENSO has moved."""
    from shared.climate import phase, strength

    df = df.sort_values("Period")
    oni = df.dropna(subset=["oni"])
    if oni.empty:
        return None
    last = oni.iloc[-1]
    earlier = oni[oni["Period"] <= last["Period"] - pd.DateOffset(months=3)]
    pdo = df.dropna(subset=["pdo"])
    return {
        "oni": float(last["oni"]), "season": last["oni_season"], "month": last["Period"],
        "phase": phase(float(last["oni"])), "strength": strength(float(last["oni"])),
        "oni_3mo_ago": float(earlier.iloc[-1]["oni"]) if not earlier.empty else None,
        "pdo": float(pdo.iloc[-1]["pdo"]) if not pdo.empty else None,
        "pdo_month": pdo.iloc[-1]["Period"] if not pdo.empty else None,
    }


def winter_summary(wconn: sqlite3.Connection, clim: pd.DataFrame) -> pd.DataFrame:
    """Each winter (December to February) on record: local temperature and rain next to ENSO and the PDO."""
    first, last = first_weather_time(wconn), latest_weather_time(wconn)
    if first is None:
        return pd.DataFrame()
    m = weather_series(wconn, first.date(), last.date(), "Month")
    if m.empty:
        return m
    m["wy"] = m["Period"].map(lambda p: p.year if p.month == 12 else p.year - 1 if p.month <= 2 else None)
    w = m[m["wy"].notna() & m["Period"].dt.month.isin([12, 1, 2])]
    rows = []
    for wy, g in w.groupby("wy"):
        months = len(g)
        full = (g["Hours"] >= 0.85 * g["Period"].dt.days_in_month * 24).all() and months == 3
        centre = pd.Timestamp(int(wy) + 1, 1, 1)
        c = clim[clim["Period"] == centre] if not clim.empty else pd.DataFrame()
        pdo = clim[(clim["Period"] >= pd.Timestamp(int(wy), 12, 1)) & (clim["Period"] <= pd.Timestamp(int(wy) + 1, 2, 1))]["pdo"].mean() if not clim.empty else float("nan")
        rows.append({
            "Winter": f"{int(wy)}–{str(int(wy) + 1)[2:]}", "Months of data": months, "Complete": bool(full),
            "Avg temp °F": round(float(g["Temp avg °F"].mean()), 1), "Rain in": round(float(g["Rain in"].sum()), 1),
            "ENSO (ONI)": float(c.iloc[0]["oni"]) if not c.empty and pd.notna(c.iloc[0]["oni"]) else float("nan"),
            "PDO": round(float(pdo), 2) if pd.notna(pdo) else float("nan"),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------ wind by season
SEASONS = {"Winter": [12, 1, 2], "Spring": [3, 4, 5], "Summer": [6, 7, 8], "Fall": [9, 10, 11]}
WIND_BINS = [(0.0, 1.0, "under 1 mph"), (1.0, 3.0, "1 to 3 mph"), (3.0, 6.0, "3 to 6 mph"), (6.0, 1e9, "6 mph and up")]


def wind_by_season(wconn: sqlite3.Connection) -> dict:
    """Wind rose data and plain statistics for each season, from every hour on record (Pacific time).

    `rose[season]` is a table of % of hours by compass sector and speed band. Statistics use the
    speed-weighted prevailing direction, so a few strong hours count more than many calm ones.
    """
    import math

    h = pd.read_sql_query(
        "select hour_utc, wind_mph_avg, gust_mph_max, wind_dir_deg from weather_hourly "
        "where wind_mph_avg is not null", wconn)
    if h.empty:
        return {}
    local = pd.to_datetime(h["hour_utc"], utc=True).dt.tz_convert(PACIFIC_TZ).dt.tz_localize(None)
    h["month"] = local.dt.month
    h["when"] = local
    h["sector"] = h["wind_dir_deg"].map(compass_point)
    out = {"rose": {}, "stats": {}}
    for season, months in SEASONS.items():
        s = h[h["month"].isin(months)]
        if s.empty:
            continue
        has_dir = s.dropna(subset=["sector"])
        table = pd.DataFrame(0.0, index=COMPASS, columns=[b[2] for b in WIND_BINS])
        for lo, hi, label in WIND_BINS:
            sel = has_dir[(has_dir["wind_mph_avg"] >= lo) & (has_dir["wind_mph_avg"] < hi)]
            for sector, n in sel["sector"].value_counts().items():
                table.loc[sector, label] = n / len(has_dir) * 100.0 if len(has_dir) else 0.0
        rad = has_dir["wind_dir_deg"] * math.pi / 180.0
        u, v = (has_dir["wind_mph_avg"] * rad.map(math.sin)).sum(), (has_dir["wind_mph_avg"] * rad.map(math.cos)).sum()
        prevailing = compass_point(math.degrees(math.atan2(u, v)) % 360) if (abs(u) + abs(v)) > 1e-9 else None
        weight = has_dir.groupby("sector")["wind_mph_avg"].sum()
        by_hours = has_dir["sector"].value_counts(normalize=True)
        gust_row = s.loc[s["gust_mph_max"].idxmax()]
        out["rose"][season] = table
        out["stats"][season] = {
            "hours": len(s), "mean_mph": float(s["wind_mph_avg"].mean()), "prevailing": prevailing,
            "prevailing_share": float(weight.max() / weight.sum()) if weight.sum() else None,
            "most_frequent": by_hours.index[0] if len(by_hours) else None,
            "most_frequent_share": float(by_hours.iloc[0]) if len(by_hours) else None,
            "top_two": list(weight.sort_values(ascending=False).index[:2]),
            "gust_mph": float(gust_row["gust_mph_max"]), "gust_when": gust_row["when"],
            "gust_from": compass_point(gust_row["wind_dir_deg"]),
            "light_share": float((s["wind_mph_avg"] < 1.0).mean()),
        }
    return out


# What the region's winds usually do, in general terms (not from your station), to set beside the local record
TYPICAL_WIND = {
    "Winter": ({"S", "SW", "SE"}, "winter storms usually push strong south and southwest winds ahead of each front, with calmer, cold nights in between"),
    "Spring": ({"W", "SW", "NW"}, "spring is a gusty transition, with westerly and southwesterly winds behind passing fronts"),
    "Summer": ({"NW", "N", "W"}, "summer usually brings a steady northwest to west flow, strongest in the afternoon as marine air is drawn inland"),
    "Fall": ({"S", "SW", "SE", "E"}, "fall switches back toward southerly storm winds, with the occasional dry east wind off the land"),
}


def wind_insights(stats: dict) -> list[str]:
    """One plain-language paragraph per season: what the local record shows, next to the regional norm."""
    out = []
    for season, st in stats.items():
        if not st.get("prevailing"):
            continue
        a, b = (st["top_two"] + [None, None])[:2]
        dirs = f"{a} or {b}" if b else a
        gust = f"{st['gust_mph']:.0f} mph"
        gust_dir = f", from the {st['gust_from']}" if st.get("gust_from") else ""
        text = (
            f"**{season}.** Winds here come most from the {dirs}; the speed-weighted prevailing direction is {st['prevailing']}. "
            f"The average is only {st['mean_mph']:.1f} mph, and {st['light_share']:.0%} of hours are lighter than 1 mph. "
            f"The strongest gust on record was {gust}{gust_dir}, on {st['gust_when']:%b} {st['gust_when'].day}, {st['gust_when'].year}."
        )
        typical, note = TYPICAL_WIND.get(season, (set(), ""))
        if typical:
            fit = "fits" if st["prevailing"] in typical else "differs from"
            text += f" Regionally, {note}. Your prevailing direction {fit} that pattern."
        out.append(text)
    return out
