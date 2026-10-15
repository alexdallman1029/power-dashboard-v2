import datetime as dt
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from billing import views as V
from billing.compute import tenant_charges_for_ym
from billing.config import display_channel_name, load_config
from billing.period import BILLING_DAY, billing_period_for_ym
from billing.utility import has_bill_tables, reconcile
from ui import charts as C

DB_PATH = "data/energy.sqlite"

RV_SITES = 417380
GROW_BARN = 432079
POLE_BARN_MAIN = 422491
POLE_BARN_SUB = 432058
FEATURED_GIDS = [RV_SITES, POLE_BARN_MAIN, POLE_BARN_SUB, GROW_BARN]

st.set_page_config(page_title="FishDog Farm Dashboard", layout="wide")

# ---------------------------------------------------------------- header
st.markdown(
    "<h1 style='text-align:center;margin:0;padding:0.2rem 0 0'>FishDog Farm</h1>"
    "<p style='text-align:center;margin:0 0 0.8rem;opacity:0.7;font-size:1.15rem'>Data Dashboard</p>",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ data
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


@st.cache_data(ttl=300)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    with db() as conn:
        return pd.read_sql_query(sql, conn, params=params)


@st.cache_data(ttl=300)
def c_tenants(months: tuple) -> pd.DataFrame:
    with db() as conn:
        return V.tenant_history(conn, list(months))


@st.cache_data(ttl=300)
def c_cycle_charges(ym: str, override: tuple) -> pd.DataFrame:
    with db() as conn:
        return tenant_charges_for_ym(conn, ym, dict(override) or None)


@st.cache_data(ttl=300)
def c_site_cards(ym: str, override: tuple) -> pd.DataFrame:
    with db() as conn:
        return V.site_cards(conn, ym, dict(override) or None)


@st.cache_data(ttl=300)
def c_channels(gid: int, start: dt.date, end: dt.date):
    with db() as conn:
        return V.channel_breakdown(conn, gid, start, end)


@st.cache_data(ttl=300)
def c_ledger(months: tuple) -> pd.DataFrame:
    with db() as conn:
        return V.farm_ledger(conn, list(months))


@st.cache_data(ttl=300)
def c_attention(ym: str):
    with db() as conn:
        return V.needs_attention(conn, ym)


@st.cache_data(ttl=300)
def c_cycle_compare(gid: int, ym: str) -> pd.DataFrame:
    with db() as conn:
        return V.cycle_vs_previous(conn, gid, ym)


@st.cache_data(ttl=300)
def c_daily(gid: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    with db() as conn:
        return V.daily_device(conn, gid, start, end)


@st.cache_data(ttl=300)
def c_device_monthly(gid: int, months: tuple) -> pd.DataFrame:
    with db() as conn:
        return V.device_monthly(conn, gid, list(months))


@contextmanager
def wdb():
    conn = sqlite3.connect(V.WEATHER_DB)
    try:
        yield conn
    finally:
        conn.close()


@st.cache_data(ttl=300)
def c_wx_summary(ym: str) -> dict:
    with wdb() as w:
        return V.cycle_weather_summary(w, ym)


@st.cache_data(ttl=300)
def c_wx_daily(start: dt.date, end: dt.date) -> pd.DataFrame:
    with wdb() as w:
        return V.weather_daily(w, start, end)


@st.cache_data(ttl=300)
def c_wx_latest():
    with wdb() as w:
        return V.latest_conditions(w)


@st.cache_data(ttl=300)
def c_energy_vs_temp(gid: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    with db() as conn, wdb() as w:
        return V.energy_vs_temp(conn, w, gid, start, end)


@st.cache_data(ttl=300)
def c_reconcile() -> pd.DataFrame:
    with db() as conn:
        return reconcile(conn) if has_bill_tables(conn) else pd.DataFrame()


@st.cache_data(ttl=300)
def c_bills() -> pd.DataFrame:
    with db() as conn:
        return V.bill_list(conn)


@st.cache_data(ttl=300)
def c_bill_detail(bill_ts: int) -> list:
    with db() as conn:
        return V.bill_detail(conn, bill_ts)


@st.cache_data(ttl=300)
def c_usage_history() -> pd.DataFrame:
    with db() as conn:
        return V.usage_history(conn)


cfg = load_config()

aliases = q("select device_gid, display_name from device_aliases")
device_name_map = dict(zip(aliases["device_gid"], aliases["display_name"]))

# ----------------------------------------------------- month + freshness
available = q(
    """
    select distinct
      case when cast(strftime('%d', day) as integer) >= ?
           then strftime('%Y-%m', date(day, '+1 month')) else strftime('%Y-%m', day) end as ym
    from usage_daily order by ym desc
    """,
    params=(BILLING_DAY,),
)
raw_months = sorted(available["ym"].tolist(), reverse=True)


def ym_label(ym: str) -> str:
    return dt.date.fromisoformat(ym + "-01").strftime("%B %Y")


month_labels = {ym_label(m): m for m in raw_months}

last_ingest = q("select value from kv_store where key = 'last_ingested_at_utc' limit 1")
if not last_ingest.empty:
    stamp = datetime.fromisoformat(last_ingest["value"].iloc[0]).replace(tzinfo=ZoneInfo("UTC"))
    t_local = stamp.astimezone(ZoneInfo("America/Los_Angeles"))
    updated = f"{t_local:%b} {t_local.day}, {t_local.hour % 12 or 12}:{t_local:%M %p}"
else:
    updated = "unknown"

top_a, _top_b = st.columns([3, 9])
with top_a:
    month_label = st.selectbox("Billing month", options=list(month_labels.keys()), index=0)
ym = month_labels[month_label]
prev_ym = V.shift_month(ym, -1)
period_start, period_end = billing_period_for_ym(ym, BILLING_DAY)

last_day = q(
    "select max(day) as d from usage_daily where day >= ? and day <= ?",
    params=(period_start.isoformat(), period_end.isoformat()),
)["d"].iloc[0]
last_date = dt.date.fromisoformat(last_day) if last_day else None
in_progress = last_date is not None and last_date < period_end
st.caption(
    f"Cycle {period_start:%b} {period_start.day} – {period_end:%b} {period_end.day}, {period_end.year}"
    + (f" · in progress, data through {last_date:%b} {last_date.day}" if in_progress else "")
    + f" · updated {updated} PT"
)

# ------------------------------------------------------ needs attention
notes = c_attention(ym)
if notes:
    with st.container(border=True):
        st.markdown("**Needs attention**")
        for severity, text in notes:
            st.markdown(("⚠️ " if severity == "warning" else "ℹ️ ") + text)

# --------------------------------------------------------------- shared
chart_months = [m for m in raw_months if m <= ym][:12][::-1]
if not chart_months:
    st.warning("No data found for that month.")
    st.stop()
all_tenants = c_tenants(tuple(chart_months))
prev = all_tenants[all_tenants["ym"] == prev_ym] if not all_tenants.empty else pd.DataFrame()

# What-if headcounts: the dropdowns on the site cards set these. They change only what is shown
# here (cards, breakdown, CSV); real billing and the texts always use billing/sites.toml.
PEOPLE_LABELS = {0: "Vacant", 1: "1 person", 2: "2 people"}


def people_label(n: int) -> str:
    return PEOPLE_LABELS.get(n, f"{n} people")


def ppl_key(site_name: str) -> str:
    return f"ppl_{ym}_{site_name}"


override = {}
for _site in cfg.sites:
    if ppl_key(_site.name) in st.session_state:
        picked = next((n for n in range(0, 9) if people_label(n) == st.session_state[ppl_key(_site.name)]), None)
        if picked is not None and picked != cfg.people(_site, ym):
            override[_site.name] = picked
whatif = bool(override)
override_t = tuple(sorted(override.items()))
cur = c_cycle_charges(ym, override_t)
if not cur.empty:
    cur = cur.assign(ym=ym)
ledger = c_ledger(tuple(chart_months))
rec = c_reconcile()


def signed_money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def pct_change(now: float, before: float):
    return None if not before else (now - before) / before * 100.0


tab_overview, tab_tenants, tab_farm, tab_weather, tab_utility = st.tabs(["Overview", "Tenants", "Farm power", "Weather", "Utility bills"])

# ================================================================ OVERVIEW
with tab_overview:
    prev_ledger = c_ledger((prev_ym,))
    lr = ledger[ledger["ym"] == ym].iloc[0]
    emp_now, emp_prev = float(lr["Emporia total"]), float(prev_ledger["Emporia total"].iloc[0])
    billed_now = float(cur["Charge ($)"].sum()) if not cur.empty else 0.0
    billed_prev = float(prev["Charge ($)"].sum()) if not prev.empty else 0.0
    unbilled_kwh = float(lr["Owners"] + lr["Grow barn"])

    k1, k2, k3, k4 = st.columns(4)
    d = pct_change(emp_now, emp_prev)
    k1.metric("Farm power (Emporia)", f"{emp_now:,.0f} kWh", delta=None if d is None else f"{d:+.0f}% vs last cycle", delta_color="off")
    k2.metric("Billed to tenants", f"${billed_now:,.2f}", delta=f"{signed_money(billed_now - billed_prev)} vs last cycle", delta_color="off")
    k3.metric("Not billed to tenants", f"{unbilled_kwh:,.0f} kWh", help="Grow barn, power at vacant sites, and the owners' share of shared power")
    if not rec.empty:
        latest = rec.sort_values("Bill date").iloc[-1]
        k4.metric("Latest utility bill (farm meter)", f"${latest['Farm $']:,.2f}", help=f"Bill dated {latest['Bill date']}: {latest['Farm kWh']:,.0f} kWh")
    else:
        k4.metric("Latest utility bill (farm meter)", "–")

    st.markdown("#### What each tenant owes")
    if whatif:
        wi_a, wi_b = st.columns([9, 3])
        wi_a.info(
            "What-if view: the charges below use the headcounts you picked, not the real occupancy. "
            "Your texts and past bills are unchanged."
        )

        def _reset():
            for _s in cfg.sites:
                st.session_state.pop(ppl_key(_s.name), None)

        wi_b.button("Reset to real occupancy", on_click=_reset)

    cards = c_site_cards(ym, override_t)
    prev_by_site = prev.set_index("Tenant")["Charge ($)"] if not prev.empty else pd.Series(dtype=float)
    card_cols = st.columns(len(cards))
    for col, (_, row) in zip(card_cols, cards.iterrows()):
        with col:
            # Fixed-height top section so the People dropdowns line up across cards
            with st.container(height=150, border=False):
                if row["People"] == 0:
                    # Same layout as an occupied card: dollars on top, kWh underneath. For a vacant site
                    # that is its own meter's actual usage at the tenant rate (no fee, nobody is billed).
                    own = float(row["Own meter kWh"])
                    st.metric(row["Site"], f"${own * cfg.rate_per_kwh:,.2f}")
                    if not row["metered"]:
                        st.caption(f"{own:,.1f} kWh · vacant, no meter yet")
                    elif own > cfg.vacant_alert_kwh:
                        st.caption(f"⚠️ {own:,.1f} kWh · vacant, no bill; counted in the owners' share")
                    elif own < 0.05:
                        st.caption(f"{own:,.1f} kWh · vacant, nothing used")
                    else:
                        st.caption(f"{own:,.1f} kWh · vacant, no bill; counted in the owners' share")
                else:
                    delta = None
                    if row["Site"] in prev_by_site.index and not whatif:
                        delta = f"{signed_money(row['Charge ($)'] - float(prev_by_site[row['Site']]))} vs last cycle"
                    st.metric(row["Site"], f"${row['Charge ($)']:,.2f}", delta=delta, delta_color="off")
                    st.caption(f"{row['kWh (calc)']:,.0f} kWh")
            real = int(row["Configured"])
            options = [people_label(n) for n in sorted({0, 1, 2, real})]
            shown = people_label(int(row["People"]))
            st.selectbox("People", options, index=options.index(shown) if shown in options else options.index(people_label(real)), key=ppl_key(row["Site"]))

    st.markdown("#### Where the farm's power goes")
    st.caption(
        "Bars: power Emporia measured, split by who it belongs to. Diamond: what the utility's farm meter recorded "
        "(shown once the bill covers the whole cycle). The base-load amount added to bills isn't measured power, so it isn't here."
    )
    st.plotly_chart(C.ledger_chart(ledger), theme=None, width="stretch")

# ================================================================ TENANTS
with tab_tenants:
    st.markdown("#### What makes up each bill" + (" (what-if headcounts)" if whatif else ""))
    if cur.empty:
        st.info("No occupied sites this cycle.")
    else:
        st.plotly_chart(
            C.bill_breakdown_chart(V.bill_breakdown_dollars(cur, cfg.rate_per_kwh)), theme=None, width="stretch"
        )
        st.caption(
            f"Each bill = your own metered kWh + a share of the kitchen/lounge, shop and base load, at \\${cfg.rate_per_kwh:.4f}/kWh, "
            f"plus a \\${cfg.monthly_fee:.2f} monthly fee. Shares are split by people, and vacant sites aren't counted."
        )
        table = cur[["Tenant", "Own kWh", "Kitchen kWh", "Shop kWh", "Base kWh", "kWh (calc)", "Fee ($)", "Charge ($)"]].copy()
        kwh_cols = ["Own kWh", "Kitchen kWh", "Shop kWh", "Base kWh", "kWh (calc)"]
        st.dataframe(
            table, hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.1f") for c in kwh_cols}
            | {"Fee ($)": st.column_config.NumberColumn(format="$%.2f"), "Charge ($)": st.column_config.NumberColumn(format="$%.2f")},
        )
        st.download_button(
            "Download this cycle (CSV)", table.to_csv(index=False).encode("utf-8"),
            file_name=f"tenant-charges-{ym}{'-whatif' if whatif else ''}.csv", mime="text/csv",
        )

    st.markdown("#### One tenant over time")
    sites_in_range = [s.name for s in cfg.sites if (all_tenants["Tenant"] == s.name).any()] if not all_tenants.empty else []
    if sites_in_range:
        pick_a, pick_b = st.columns([3, 9])
        tenant_choice = pick_a.selectbox("Site", sites_in_range)
        view = pick_b.segmented_control("View", options=["$", "kWh"], default="$")
        site_obj = next(s for s in cfg.sites if s.name == tenant_choice)
        vacant = [m for m in chart_months if cfg.people(site_obj, m) == 0]
        st.plotly_chart(C.tenant_trend_chart(all_tenants, tenant_choice, view or "$", vacant), theme=None, width="stretch")

    st.markdown("#### Who lived where")
    st.plotly_chart(C.occupancy_chart(V.occupancy_grid(chart_months)), theme=None, width="stretch")
    st.caption("Number of people per site each billing month (– means vacant). Edit this in billing/sites.toml.")

# ============================================================== FARM POWER
with tab_farm:
    device_choices = {
        "Pole barn (main)": POLE_BARN_MAIN,
        "RV sites": RV_SITES,
        "Grow barn": GROW_BARN,
        "Pole barn (sub-panel)": POLE_BARN_SUB,
    }
    pick = st.selectbox("Device", list(device_choices.keys()))
    gid = device_choices[pick]

    st.markdown("#### Day by day, this cycle vs last")
    st.plotly_chart(C.cycle_compare_chart(c_cycle_compare(gid, ym)), theme=None, width="stretch")

    st.markdown("#### Daily usage, last 13 weeks")
    end_day = min(period_end, last_date) if last_date else period_end
    start_day = end_day - dt.timedelta(days=13 * 7 - 1)
    start_day -= dt.timedelta(days=start_day.weekday())  # start on a Monday
    daily = c_daily(gid, start_day, end_day)
    if daily.empty:
        st.info("No daily data for this device.")
    else:
        st.plotly_chart(C.calendar_heatmap(daily, pd.Timestamp(start_day)), theme=None, width="stretch")
        st.caption("Each square is one day; brighter means more kWh. Unusual bright days stand out.")

    st.markdown("#### What's on each hub")
    kind = st.segmented_control("Chart", options=["Bar", "Pie"], default="Bar", key="hub_chart_kind") or "Bar"
    st.caption(
        "Each circuit on the hub's panel for this cycle. 'Everything else' is power on the hub that isn't on a monitored "
        "circuit, such as a sub-panel. A pie only shows the top five circuits; the bar shows them all."
    )
    HUB_NOTES = {
        POLE_BARN_MAIN: "Most of the 'everything else' here is the Pole Barn Sub panel, which has its own hub below, plus shop loads without their own circuit.",
        POLE_BARN_SUB: "Most of the 'everything else' here is the RV sites, which have their own hub above.",
    }
    hub_defs = [(RV_SITES, "RV Sites"), (GROW_BARN, "Grow Barn"), (POLE_BARN_MAIN, "Pole Barn Main"), (POLE_BARN_SUB, "Pole Barn Sub")]
    for pair in (hub_defs[:2], hub_defs[2:]):
        hub_cols = st.columns(2)
        for hub_col, (hub_gid, fallback) in zip(hub_cols, pair):
            with hub_col:
                hub_name = device_name_map.get(hub_gid, fallback)
                st.subheader(hub_name)
                hub_df, hub_main, hub_total = c_channels(hub_gid, period_start, period_end)
                if hub_df.empty:
                    st.info("No circuit data this cycle.")
                    continue
                st.plotly_chart(C.channel_chart(hub_df, hub_main, kind), theme=None, width="stretch")
                st.caption(f"{(hub_main if hub_main > 0 else hub_total):,.0f} kWh on this hub · circuits add up to {hub_total:,.0f} kWh")
                if hub_gid in HUB_NOTES and hub_main > hub_total + 0.5:
                    st.caption(HUB_NOTES[hub_gid])

    st.markdown("#### All devices")
    device_totals = q(
        f"""
        select device_gid,
          round(sum(case when channel_num = 0 then kwh else 0 end), 3) as kwh_main,
          round(sum(case when channel_num != 0 then kwh else 0 end), 3) as kwh_sub
        from usage_daily
        where day >= ? and day <= ? and device_gid in ({",".join(["?"] * len(FEATURED_GIDS))})
        group by device_gid
        """,
        params=(period_start.isoformat(), period_end.isoformat(), *FEATURED_GIDS),
    ).set_index("device_gid")

    def device_section(device_gid: int):
        name = device_name_map.get(device_gid, f"Device {device_gid}")
        st.subheader(name)
        if device_gid not in device_totals.index:
            st.info("No data for this device in the selected billing period.")
            return
        row = device_totals.loc[device_gid]
        main_kwh, sub_kwh = float(row["kwh_main"] or 0), float(row["kwh_sub"] or 0)
        st.metric("Device kWh", f"{(main_kwh if main_kwh > 0 else sub_kwh):,.1f}")
        mdf = c_device_monthly(device_gid, tuple(chart_months))
        st.plotly_chart(C.monthly_bar_chart(mdf["ym"].tolist(), mdf["kwh"].tolist()), theme=None, width="stretch")
        if main_kwh > 0:
            st.caption(f"Subchannels: {sub_kwh:,.1f} kWh · Balance: {main_kwh - sub_kwh:,.1f} kWh")
        ch = q(
            """
            select coalesce(nullif(trim(channel_name), ''), 'Unnamed') as channel_name, round(sum(kwh), 3) as kwh
            from usage_daily where device_gid = ? and day >= ? and day <= ? and channel_num != 0
            group by channel_num, channel_name having kwh is not null order by kwh desc
            """,
            params=(int(device_gid), period_start.isoformat(), period_end.isoformat()),
        )
        ch = ch[ch["channel_name"] != "Unnamed"].rename(columns={"channel_name": "Channel", "kwh": "kWh (billing period)"})
        # Show site names instead of the people's names Emporia uses for tenant channels
        ch["Channel"] = ch["Channel"].map(lambda n: display_channel_name(cfg, int(device_gid), n))
        with st.expander("Channel details", expanded=False):
            st.dataframe(ch, width="stretch", hide_index=True)

    row1a, row1b = st.columns(2)
    with row1a:
        device_section(RV_SITES)
    with row1b:
        device_section(GROW_BARN)
    row2a, row2b = st.columns(2)
    with row2a:
        device_section(POLE_BARN_MAIN)
    with row2b:
        device_section(POLE_BARN_SUB)

# ================================================================ WEATHER
with tab_weather:
    if not V.weather_available():
        st.info("No weather data yet. Run the 'Ambient Weather backfill' workflow on GitHub.")
    else:
        now_wx = c_wx_latest()
        if now_wx:
            st.markdown("#### Latest reading")
            w1, w2, w3, w4, w5 = st.columns(5)
            w1.metric("Temperature", f"{now_wx['temp']:.0f}°F", help=f"Feels like {now_wx['feels']:.0f}°F")
            w2.metric("Humidity", f"{now_wx['humidity']:.0f}%")
            w3.metric("Wind", f"{now_wx['wind']:.0f} mph", help=f"Gusts to {now_wx['gust']:.0f} mph")
            w4.metric("Rain, last 24 h", f"{now_wx['rain24']:.2f} in")
            w5.metric("Sunlight", f"{now_wx['solar']:.0f} W/m²")
            t_ = now_wx["when"]
            st.caption(f"Hour starting {t_:%b} {t_.day}, {t_.hour % 12 or 12} {t_:%p} PT. Updated once a day with the rest of the data.")

        st.markdown("#### This cycle vs the same cycle last year")
        wsum = c_wx_summary(ym)
        cur_w, ly_w = wsum["now"], wsum["last_year"]
        if not cur_w:
            st.info("No weather recorded for this cycle.")
        else:
            def vs(key, unit, digits=0):
                if not ly_w:
                    return None
                diff = cur_w[key] - ly_w[key]
                return f"{'+' if diff >= 0 else '-'}{abs(diff):.{digits}f}{unit} vs last year"

            x1, x2, x3, x4, x5 = st.columns(5)
            x1.metric("Average temp", f"{cur_w['avg']:.1f}°F", delta=vs("avg", "°F", 1), delta_color="off")
            x2.metric("Hottest", f"{cur_w['high']:.0f}°F", delta=vs("high", "°F"), delta_color="off")
            x3.metric("Coldest", f"{cur_w['low']:.0f}°F", delta=vs("low", "°F"), delta_color="off")
            x4.metric("Rain", f"{cur_w['rain']:.1f} in", delta=vs("rain", " in", 1), delta_color="off")
            x5.metric("Sunlight", f"{cur_w['solar']:.0f} W/m²", delta=vs("solar", "", 0), delta_color="off")
            if in_progress:
                st.caption(f"This cycle is still in progress ({cur_w['days']} days so far), so it isn't a fair comparison with a full cycle last year.")

        wdaily = c_wx_daily(period_start, period_end)
        wdaily = wdaily[wdaily["Hours"] >= 12] if not wdaily.empty else wdaily
        if not wdaily.empty:
            st.markdown("#### Temperature and rain, day by day")
            st.plotly_chart(C.temp_range_chart(wdaily), theme=None, width="stretch")
            st.plotly_chart(C.rain_chart(wdaily), theme=None, width="stretch")

        st.markdown("#### Does the weather drive your power use?")
        st.caption(
            "Each dot is one day over the past year: the day's average outdoor temperature across, kWh used up. "
            "A downward slope means colder days use more power."
        )
        wx_devices = {"Pole barn (main)": POLE_BARN_MAIN, "RV sites": RV_SITES, "Grow barn": GROW_BARN}
        wx_pick = st.selectbox("Power use to compare", list(wx_devices.keys()), key="wx_device")
        scatter_end = min(period_end, last_date) if last_date else period_end
        scatter_df = c_energy_vs_temp(wx_devices[wx_pick], scatter_end - dt.timedelta(days=364), scatter_end)
        if len(scatter_df) < 20:
            st.info("Not enough days with both power and weather data yet.")
        else:
            fig_sc, r_val, per10 = C.energy_temp_scatter(scatter_df, wx_pick)
            st.plotly_chart(fig_sc, theme=None, width="stretch")
            strength = "strong" if abs(r_val) >= 0.7 else "moderate" if abs(r_val) >= 0.4 else "weak"
            direction = "cooler" if r_val < 0 else "warmer"
            st.caption(
                f"{wx_pick}: a {strength} link (correlation {r_val:+.2f}, from {len(scatter_df)} days). "
                f"On average, each 10°F {'drop' if per10 < 0 else 'rise'} in temperature goes with about {abs(per10):,.0f} kWh "
                f"{'more' if per10 < 0 else 'more'} per day; {direction} days use more." if abs(r_val) >= 0.4 else
                f"{wx_pick}: only a {strength} link with temperature (correlation {r_val:+.2f}, from {len(scatter_df)} days), "
                "so weather isn't what mainly drives it."
            )

# ============================================================ UTILITY BILLS
with tab_utility:
    bills = c_bills()
    if bills.empty:
        st.info("No utility bills loaded yet. Run the 'Utility (SmartHub) pull' workflow on GitHub.")
    else:
        labels = {f"{r['bill_date']}  ·  ${r['original_amount']:,.2f}": int(r["bill_ts"]) for _, r in bills.iterrows()}
        sel_a, _sp = st.columns([4, 8])
        chosen = sel_a.selectbox("Bill", list(labels.keys()), index=0)
        ts = labels[chosen]
        b = bills[bills["bill_ts"] == ts].iloc[0]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total current charges", f"${b['total_current_charge']:,.2f}" if pd.notna(b["total_current_charge"]) else "–")
        m2.metric("Adjustments", f"${b['total_adjustments']:,.2f}" if pd.notna(b["total_adjustments"]) else "–")
        m3.metric("Usage billed", f"{b['total_usage_kwh']:,.0f} kWh" if pd.notna(b["total_usage_kwh"]) else "–")
        m4.metric(
            "Usage charges", f"${b['total_usage_charge']:,.2f}" if pd.notna(b["total_usage_charge"]) else "–",
            help="Energy and distribution charges as SmartHub reports them, before credits and the Energy Share line.",
        )
        if int(b["usage_estimated"] or 0):
            st.warning("The utility marked usage on this bill as estimated.")

        st.markdown("#### The bill, meter by meter")
        uh = c_usage_history()
        for m in c_bill_detail(ts):
            with st.container(border=True):
                st.markdown(f"**{m['role']}** · meter {m['meter']}")
                reading = pd.DataFrame(
                    [{
                        "From": m["read_from"], "To": m["read_to"], "Previous": m["prev"], "Present": m["present"],
                        "Days": m["days"], "Multiplier": m["multiplier"], "Usage (kWh)": m["usage_kwh"],
                    }]
                )
                st.caption("Reading dates and usage")
                st.dataframe(reading, hide_index=True, width="stretch",
                             column_config={"Usage (kWh)": st.column_config.NumberColumn(format="%.0f")})
                st.caption("Current service detail")
                lines = m["lines"].copy()
                total_row = pd.DataFrame([{"Description": "Total current charges for this location", "kWh": None, "Rate": None, "Amount": m["total"]}])
                shown = pd.concat([lines, total_row], ignore_index=True)
                # Text for the optional columns so blanks stay blank (a number column would print "None")
                shown["kWh"] = shown["kWh"].map(lambda v: "" if pd.isna(v) else f"{v:,.0f}")
                shown["Rate"] = shown["Rate"].map(lambda v: "" if pd.isna(v) else f"${v:.5f}")
                shown["Amount"] = shown["Amount"].map(lambda v: f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}")
                st.dataframe(shown, hide_index=True, width="stretch")
                if not m["ok"]:
                    st.warning("These lines did not add up to the printed total when the bill was read; treat the dollars with care.")
                if not uh.empty:
                    mine = uh[(uh["meter"] == m["meter"]) & (uh["read_to"] == m["read_to"])]
                    if not mine.empty:
                        r = mine.iloc[0]
                        a, bb, c = st.columns(3)
                        a.metric("Avg per day", f"{r['kWh/day']:,.1f} kWh")
                        bb.metric("Same time last year", f"{r['Last year kWh/day']:,.1f} kWh/day" if pd.notna(r["Last year kWh/day"]) else "–")
                        c.metric("Avg cost per day", f"${r['$/day']:,.2f}" if pd.notna(r["$/day"]) else "–")

        st.markdown("#### Usage trends")
        if not uh.empty:
            t1, t2 = st.columns(2)
            with t1:
                st.markdown("**Farm meter, kWh per day**")
                st.plotly_chart(C.usage_history_chart(uh, "Farm"), theme=None, width="stretch")
            with t2:
                st.markdown("**House meter, kWh per day**")
                st.plotly_chart(C.usage_history_chart(uh, "House"), theme=None, width="stretch")
            st.markdown("**All-in cost per kWh**")
            st.caption("Fixed charges are spread over the kWh used, so the cost per kWh climbs when usage drops.")
            st.plotly_chart(C.cost_per_kwh_chart(uh), theme=None, width="stretch")

        if not rec.empty:
            st.markdown("#### Does Emporia agree with the farm meter?")
            st.caption("The share of the utility farm meter's kWh that Emporia (pole barn main + grow barn) recorded over the same reading dates.")
            st.plotly_chart(C.coverage_chart(rec), theme=None, width="stretch")
            with st.expander("All bills, side by side"):
                show = rec.sort_values("Bill date", ascending=False).copy()
                show["Checked"] = show["Checked"].map({True: "yes", False: "no"})
                st.dataframe(
                    show, hide_index=True, width="stretch",
                    column_config={
                        "Farm kWh": st.column_config.NumberColumn(format="%.0f"),
                        "House kWh": st.column_config.NumberColumn(format="%.0f"),
                        "Emporia kWh": st.column_config.NumberColumn(format="%.0f"),
                        "Unaccounted kWh": st.column_config.NumberColumn(format="%.0f"),
                        "Unaccounted %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Farm $": st.column_config.NumberColumn(format="$%.2f"),
                        "House $": st.column_config.NumberColumn(format="$%.2f"),
                        "Farm $/kWh": st.column_config.NumberColumn(format="$%.3f"),
                    },
                )
