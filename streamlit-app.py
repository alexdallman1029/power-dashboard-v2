import streamlit as st
import pandas as pd
import plotly.express as px
import datetime as dt
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image

from billing.compute import tenant_charges_for_ym, vacant_site_usage
from billing.period import BILLING_DAY, billing_period_for_ym


#---------------------------
# Title and Header
#---------------------------
logo = Image.open("assets/fishdog-24.png")
st.set_page_config(page_title="FishDog Farm Dashboard", layout="wide")

#Center logo and title
left, mid, right = st.columns([3, 2, 3])

with mid:
    st.image(logo, width=350)
    st.title("Data Dashboard")

#---------------------------
# Data
#---------------------------

DB_PATH = "data/energy.sqlite"

FEATURED_GIDS = [417380, 422491, 432058, 432079]

RV_SITES_GID = 417380
POLE_BARN_MAIN_GID = 422491
POLE_BARN_SUB_GID = 432058

#---------------------------
# Color Palette
#---------------------------
PALETTE = {
    "color1": "#4c5424",
    "color2": "#ceb82c",
    "color3": "#81c566",
    "color4": "#6e73b6",
    "color5": "#d39f76",
    "color6": "#3d4187",
}

#For plotly charts specifically
px.defaults.color_discrete_sequence = [
    PALETTE["color1"],
    PALETTE["color2"],
    PALETTE["color3"],
    PALETTE["color4"],
    PALETTE["color5"],
    PALETTE["color6"],
]

# -------------------------
# Helpers
# -------------------------

@st.cache_data(ttl=60)
def load_table(sql: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(sql, conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def first_of_month(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)





# -------- Get device names from database --------
aliases = query_df(
    "select device_gid, display_name from device_aliases"
)

device_name_map = dict(
    zip(aliases["device_gid"], aliases["display_name"])
)

# -------- Get last time data were updated --------
last_ingest = query_df(
    "select value from kv_store where key = 'last_ingested_at_utc' limit 1"
)

if not last_ingest.empty:
    utc_ts = last_ingest["value"].iloc[0]

    # Parse ISO string as UTC
    dt_utc = datetime.fromisoformat(utc_ts).replace(tzinfo=ZoneInfo("UTC"))

    # Convert to Pacific Time
    dt_pst = dt_utc.astimezone(ZoneInfo("America/Los_Angeles"))

    # Format: Month Day, Year, Time
    last_updated_str = dt_pst.strftime("%B %d, %Y · %I:%M %p")

else:
    last_updated_str = "Unknown"

st.caption(f"Last updated: {last_updated_str} (PT)")


# -------- Month selector (defaults to current month) --------
today = dt.date.today()
default_month = first_of_month(today)

available_months = query_df(
    """
    select distinct
      case
        when cast(strftime('%d', day) as integer) >= ?
        then strftime('%Y-%m', date(day, '+1 month'))
        else strftime('%Y-%m', day)
      end as ym
    from usage_daily
    order by ym desc
    """,
    params=(BILLING_DAY,),
)

raw_months = sorted(available_months["ym"].tolist(), reverse=True)

def ym_to_label(ym: str) -> str:
    d = dt.date.fromisoformat(ym + "-01")
    return d.strftime("%B %Y")   # e.g. "January 2026"

# Build mapping
month_labels = {ym_to_label(ym): ym for ym in raw_months}

# Ensure current month is first
default_ym = default_month.strftime("%Y-%m")
if default_ym not in month_labels.values():
    month_labels = {ym_to_label(default_ym): default_ym, **month_labels}

col_month, col_spacer = st.columns([3, 9])

with col_month:
    month_label = st.selectbox(
        "Month",
        options=list(month_labels.keys()),
        index=0,
    )

month_ym = month_labels[month_label]

period_start, period_end = billing_period_for_ym(month_ym, BILLING_DAY)

# Show the actual billing period for clarity/auditing
st.caption(f"Billing period: {period_start.strftime('%b %d, %Y')} – {period_end.strftime('%b %d, %Y')}")

# -------- Global “as of” / summary --------
global_range = query_df(
    """
    select
      min(day) as min_day,
      max(day) as max_day
    from usage_daily
    where day >= ?
      and day <= ?
    """,
    params=(period_start.isoformat(), period_end.isoformat()),
).iloc[0]

as_of = global_range["max_day"]

summary = query_df(
    """
    select
      count(*) as rows,
      1 as devices,
      count(distinct channel_num) as device_channels,
      round(sum(kwh), 3) as kwh_total
    from usage_daily
    where device_gid = ?
      and channel_num = 0
      and day >= ?
      and day <= ?
    """,
    params=(POLE_BARN_MAIN_GID, period_start.isoformat(), period_end.isoformat()),
).iloc[0]

st.header("Total Farm Power Usage")

c1, c2, c3 = st.columns(3)
c1.metric("Total kWh", f"{float(summary['kwh_total'] or 0.0):,.2f}")
c2.metric("Start day", str(global_range["min_day"]))
c3.metric("End day", str(global_range["max_day"]))

st.divider()

# -------- Tenant calculations --------

st.header("Tenant Power")

def get_device_main_kwh(device_gid: int) -> float:
    df = query_df(
        """
        select sum(kwh) as kwh
        from usage_daily
        where device_gid = ?
          and channel_num = 0
          and day >= ?
          and day <= ?
        """,
        params=(device_gid, period_start.isoformat(), period_end.isoformat()),
    )
    v = df["kwh"].iloc[0]
    return float(v) if v is not None else 0.0


def get_device_total_kwh_pref_main(device_gid: int) -> float:
    df = query_df(
        """
        select
          sum(case when channel_num = 0 then kwh else 0 end) as kwh_main,
          sum(case when channel_num != 0 then kwh else 0 end) as kwh_sub
        from usage_daily
        where device_gid = ?
          and day >= ?
          and day <= ?
        """,
        params=(device_gid, period_start.isoformat(), period_end.isoformat()),
    ).iloc[0]

    main = float(df["kwh_main"] or 0.0)
    sub  = float(df["kwh_sub"] or 0.0)
    return main if main > 0 else sub



def get_channel_kwh(device_gid: int, channel_name: str) -> float:
    df = query_df(
        """
        select sum(kwh) as kwh
        from usage_daily
        where device_gid = ?
          and day >= ?
          and day <= ?
          and lower(trim(coalesce(channel_name,''))) = lower(trim(?))
        """,
        params=(device_gid, period_start.isoformat(), period_end.isoformat(), channel_name),
    )
    v = df["kwh"].iloc[0]
    return float(v) if v is not None else 0.0

def billing_ym_expr(billing_day: int = BILLING_DAY) -> str:
    # Labels each row with Emporia-style billing month (YYYY-MM)
    # If day-of-month >= billing_day => counts toward next month label
    return f"""
    case
      when cast(strftime('%d', day) as integer) >= {billing_day}
      then strftime('%Y-%m', date(day, '+1 month'))
      else strftime('%Y-%m', day)
    end
    """

def monthly_channel_kwh(device_gid: int, channel_name: str, months: list[str]) -> pd.DataFrame:
    placeholders = ",".join(["?"] * len(months))
    ym_expr = billing_ym_expr()

    df = query_df(
        f"""
        select
          {ym_expr} as ym,
          sum(kwh) as kwh
        from usage_daily
        where device_gid = ?
          and lower(trim(coalesce(channel_name,''))) = lower(trim(?))
          and {ym_expr} in ({placeholders})
        group by ym
        """,
        params=(device_gid, channel_name, *months),
    )

    base = pd.DataFrame({"ym": months})
    out = base.merge(df, on="ym", how="left").fillna({"kwh": 0.0})
    out["kwh"] = out["kwh"].astype(float)
    return out

def monthly_device_total_pref_main(device_gid: int, months: list[str]) -> pd.DataFrame:
    placeholders = ",".join(["?"] * len(months))
    ym_expr = billing_ym_expr()

    df = query_df(
        f"""
        select
          {ym_expr} as ym,
          sum(case when channel_num = 0 then kwh else 0 end) as kwh_main,
          sum(case when channel_num != 0 then kwh else 0 end) as kwh_sub
        from usage_daily
        where device_gid = ?
          and {ym_expr} in ({placeholders})
        group by ym
        """,
        params=(device_gid, *months),
    )

    base = pd.DataFrame({"ym": months})
    out = base.merge(df, on="ym", how="left").fillna({"kwh_main": 0.0, "kwh_sub": 0.0})
    out["kwh_main"] = out["kwh_main"].astype(float)
    out["kwh_sub"]  = out["kwh_sub"].astype(float)
    out["kwh"] = out.apply(lambda r: r["kwh_main"] if r["kwh_main"] > 0 else r["kwh_sub"], axis=1)
    return out[["ym", "kwh"]]

def monthly_device_main_kwh(device_gid: int, months: list[str]) -> pd.DataFrame:
    placeholders = ",".join(["?"] * len(months))
    ym_expr = billing_ym_expr()

    df = query_df(
        f"""
        select
          {ym_expr} as ym,
          sum(case when channel_num = 0 then kwh else 0 end) as kwh
        from usage_daily
        where device_gid = ?
          and {ym_expr} in ({placeholders})
        group by ym
        """,
        params=(device_gid, *months),
    )

    base = pd.DataFrame({"ym": months})
    out = base.merge(df, on="ym", how="left").fillna({"kwh": 0.0})
    out["kwh"] = out["kwh"].astype(float)
    return out

def monthly_device_total_for_chart(device_gid: int, months: list[str]) -> pd.DataFrame:
    """
    Returns a 2-col DF: ym, kwh_total_for_device
    Uses main (channel 0) if it exists; otherwise uses sub total.
    """
    df = monthly_device_total_pref_main(device_gid, months).copy()
    df = df.rename(columns={"kwh": "kwh_total"})
    return df

# --- Calculate charges (math in billing/compute.py; people/sites in billing/sites.toml) ---
_conn = sqlite3.connect(DB_PATH)
results = tenant_charges_for_ym(_conn, month_ym)
_vacant_usage = vacant_site_usage(_conn, month_ym)
_conn.close()

for _site, _kwh in _vacant_usage.items():
    st.warning(
        f"{_site} is marked vacant but its meter used {_kwh:,.1f} kWh this billing period. "
        "That usage was added to the shared shop. Worth checking why."
    )

#Show results
cols = st.columns(len(results))

for col, (_, row) in zip(cols, results.iterrows()):
    with col:
        st.metric(
            label=row[0],
            value=f"${row[2]:,.2f}",
            delta=None
        )
        st.caption(f"{row[1]:,.1f} kWh")


st.subheader("Monthly Power Usage over Time")

# ---- choose the last 12 billing months up through the selected month ----
months_for_chart = [m for m in raw_months if m <= month_ym][:12]
months_for_chart = list(reversed(months_for_chart))  # oldest -> newest

# ---- monthly charges, same shared calculation as the header above ----
_conn = sqlite3.connect(DB_PATH)
rows = []
for _ym in months_for_chart:
    _df = tenant_charges_for_ym(_conn, _ym).copy()
    _df["ym"] = _ym
    rows.append(_df)
_conn.close()

monthly_all = pd.concat(rows, ignore_index=True)

monthly_all = monthly_all.rename(columns={
    monthly_all.columns[0]: "Tenant",
    monthly_all.columns[1]: "kWh",
    monthly_all.columns[2]: "$",
})

# ---- UI controls ----
tenant_col, tenant_col2 = st.columns([3,9])

with tenant_col:
    tenant_choice = st.selectbox(
        "Tenant", 
        options=sorted(monthly_all["Tenant"].unique().tolist()), 
        index=0
        )

view = st.segmented_control(
    "View",
    options=["kWh", "$"],
    default="kWh",
)

# ---- chart dataframe ----
chart_df = monthly_all.loc[monthly_all["Tenant"] == tenant_choice, ["ym", "kWh", "$"]].copy()
chart_df["Month"] = pd.to_datetime(chart_df["ym"] + "-01").dt.strftime("%b %Y")

y_col = "kWh" if view == "kWh" else "$"
y_title = "kWh (billing month)" if view == "kWh" else "Charge ($, billing month)"
text_fmt = "%{text:.0f}" if view == "kWh" else "$%{text:,.0f}"

if y_col == "$":
    bar_color = "#ceb82c"
else:
    bar_color = "#3d4187"


fig_tenant = px.bar(
    chart_df,
    x="Month",
    y=y_col,
    text=y_col,
)

fig_tenant.update_traces(
    marker_color=bar_color,
    texttemplate=text_fmt,
    textposition="outside",
    cliponaxis=False,
    hovertemplate=(
        "<b>%{x}</b><br>"
        "Usage: %{customdata[0]:,.1f} kWh<br>"
        "Charge: $%{customdata[1]:,.2f}<extra></extra>"
    ),
    customdata=chart_df[["kWh", "$"]].values,
)

fig_tenant.update_layout(
    xaxis_title="",
    yaxis_title=y_title,
    margin=dict(l=10, r=10, t=10, b=10),
)

st.plotly_chart(fig_tenant, use_container_width=True)

st.divider()

# -------- Per-device totals and per-channel tables --------
st.header("All Power Usage")

placeholders = ",".join(["?"] * len(FEATURED_GIDS))

device_totals = query_df(
    f"""
    select
      device_gid,

      round(sum(case when channel_num = 0 then kwh else 0 end), 3) as kwh_main,
      round(sum(case when channel_num != 0 then kwh else 0 end), 3) as kwh_sub,

      round(
        case
          when sum(case when channel_num = 0 then kwh else 0 end) > 0
          then sum(case when channel_num = 0 then kwh else 0 end)
          else sum(case when channel_num != 0 then kwh else 0 end)
        end
      , 3) as kwh_device,

      count(distinct case when channel_num != 0 then channel_num end) as channels,
      min(day) as start_day,
      max(day) as end_day
    from usage_daily
    where day >= ?
      and day <= ?
      and device_gid in ({placeholders})
    group by device_gid
    order by kwh_device desc
    """,
    params=(period_start.isoformat(), period_end.isoformat(), *FEATURED_GIDS),
)

# Reorder
device_totals["__order"] = device_totals["device_gid"].apply(
    lambda x: FEATURED_GIDS.index(int(x)) if int(x) in FEATURED_GIDS else 999
)
device_totals = device_totals.sort_values("__order").drop(columns="__order")


if device_totals.empty:
    st.warning("No data found for that month.")
    st.stop()


def render_device_section(device_gid: int):
    device_name = device_name_map.get(device_gid, f"Device {device_gid}")

    d = device_totals.loc[device_totals["device_gid"] == device_gid]
    if d.empty:
        st.subheader(device_name)
        st.info("No data for this device in the selected billing period.")
        return

    drow = d.iloc[0]

    st.subheader(device_name)
    st.metric("Device kWh", f"{float(drow['kwh_device'] or 0.0):,.2f}")

        # ---- Monthly totals chart (last 12 billing months) ----
    months_for_device_chart = raw_months[:12]
    months_for_device_chart = list(reversed(months_for_device_chart))  # oldest -> newest

    mdf = monthly_device_total_for_chart(int(device_gid), months_for_device_chart)

    # Label months nicely
    mdf["Month"] = pd.to_datetime(mdf["ym"] + "-01").dt.strftime("%b %Y")
    mdf["kWh"] = mdf["kwh_total"].astype(float)

    DEVICE_COLORS = {
        RV_SITES: "#3d4187",
        GROW_BARN: "#81c566",
        POLE_BARN_MAIN: "#ceb82c",
        POLE_BARN_SUB: "#6e73b6",
}

    fig_dev = px.bar(
        mdf,
        x="Month",
        y="kWh",
        text="kWh",
    )
    fig_dev.update_traces(
        marker_color=DEVICE_COLORS.get(int(device_gid), "#3d4187"),
        texttemplate="%{text:.0f}",
        textposition="outside",
        cliponaxis=False,
    )
    fig_dev.update_layout(
        xaxis_title="",
        yaxis_title="kWh (billing month)",
        margin=dict(l=10, r=10, t=10, b=10),
    )

    st.plotly_chart(fig_dev, use_container_width=True)


    # ---- Balance like Emporia  ----
    main_kwh = float(drow["kwh_main"] or 0.0)
    sub_kwh = float(drow["kwh_sub"] or 0.0)
    if main_kwh > 0:
        bal = main_kwh - sub_kwh
        st.caption(f"Subchannels: {sub_kwh:,.2f} kWh · Balance: {bal:,.2f} kWh")

    # ---- Channel details (exclude Main) ----
    try:
        channels_df = query_df(
            """
            select
              channel_num,
              coalesce(nullif(trim(channel_name), ''), 'Unnamed') as channel_name,
              round(sum(kwh), 3) as kwh
            from usage_daily
            where device_gid = ?
              and day >= ?
              and day <= ?
              and channel_num != 0
            group by channel_num, channel_name
            having kwh is not null
            order by kwh desc
            """,
            params=(int(device_gid), period_start.isoformat(), period_end.isoformat()),
        )
    except Exception as e:
        st.error(f"Channel query failed for {device_name}: {e}")
        return

    channels_df = channels_df[channels_df["channel_name"] != "Unnamed"].copy()
    channels_df = channels_df.rename(columns={"channel_name": "Channel", "kwh": "kWh (billing period)"})
    channels_df = channels_df[["Channel", "kWh (billing period)"]]

    with st.expander("Channel details", expanded=False):
        st.dataframe(channels_df, use_container_width=True, hide_index=True)


    channels_df = query_df(
        """
        select
        channel_num,
        coalesce(nullif(trim(channel_name), ''), 'Unnamed') as channel_name,
        round(sum(kwh), 3) as kwh
        from usage_daily
        where device_gid = ?
        and day >= ?
        and day <= ?
        and channel_num != 0
        group by channel_num, channel_name
        having kwh is not null
        order by kwh desc
        """,
        params=(int(device_gid), period_start.isoformat(), period_end.isoformat()),
    )

    channels_df = channels_df[channels_df["channel_name"] != "Unnamed"].copy()
    channels_df = channels_df.rename(columns={"channel_name": "Channel", "kwh": "kWh (billing period)"})
    channels_df = channels_df[["Channel", "kWh (billing period)"]]


RV_SITES = 417380
GROW_BARN = 432079
POLE_BARN_MAIN = 422491
POLE_BARN_SUB = 432058

# Row 1: RV Sites | Grow Barn
col_left, col_right = st.columns(2)
with col_left:
    render_device_section(RV_SITES)
with col_right:
    render_device_section(GROW_BARN)

st.divider()

# Row 2: Pole Barn Main | Pole Barn Sub
col_left, col_right = st.columns(2)
with col_left:
    render_device_section(POLE_BARN_MAIN)
with col_right:
    render_device_section(POLE_BARN_SUB)


