"""Plotly chart builders for the dashboard, in one consistent style.

Colors come from a validated categorical palette (dark steps, since the app runs on a dark
theme). Each chart uses a fixed hue per entity, thin marks, a 2px gap between stacked
segments, hairline grids, direct labels where there are few series, and hover tooltips.
Gray means "not a measured category" (the fee, the part Emporia misses).
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

SURFACE = "#0e1117"      # the app's dark background; used for gaps between segments
INK = "#f0efec"
INK2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

# Bill components, left -> right (order validated for adjacent separation)
COMPONENT_ORDER = ["Monthly fee", "Own usage", "Kitchen / lounge share", "Shop share", "Base load share"]
COMPONENT_COLORS = {
    "Monthly fee": "#898781",
    "Own usage": "#9085e9",
    "Kitchen / lounge share": "#008300",
    "Shop share": "#d55181",
    "Base load share": "#c98500",
}
# Where the farm's measured power goes
LEDGER_ORDER = ["Tenants", "Grow barn", "Owners"]
LEDGER_COLORS = {"Tenants": "#3987e5", "Grow barn": "#d95926", "Owners": "#199e70"}
# Two-series comparisons (farm vs house)
ROLE_COLORS = {"Farm": "#3987e5", "House": "#d95926"}
ACCENT = "#3987e5"
# One-hue sequential ramp for magnitude (dark surface: brighter = more)
SEQUENTIAL = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]


def _base(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=28 if legend else 8, b=8, autoexpand=True),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=INK2, size=13),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text="", font=dict(color=INK2), traceorder="normal"),
        hoverlabel=dict(bgcolor="#1a1a19", bordercolor=AXIS, font=dict(family=FONT, color=INK)),
    )
    # automargin makes room for tick labels and axis titles so the browser never clips them
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED), title_font=dict(color=MUTED), zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, tickfont=dict(color=MUTED), title_font=dict(color=MUTED), automargin=True)
    return fig


def money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------- tenants

def bill_breakdown_chart(long_df: pd.DataFrame) -> go.Figure:
    """What each site's charge is made of: one horizontal stacked bar per site, total labeled."""
    fig = go.Figure()
    sites = list(dict.fromkeys(long_df["Tenant"]))
    for part in COMPONENT_ORDER:
        d = long_df[long_df["Part"] == part].set_index("Tenant").reindex(sites)
        fig.add_bar(
            y=sites,
            x=d["$"].fillna(0),
            name=part,
            orientation="h",
            marker=dict(color=COMPONENT_COLORS[part], line=dict(color=SURFACE, width=2)),
            customdata=d["kWh"].fillna(0),
            hovertemplate="<b>%{y}</b><br>" + part + ": $%{x:,.2f}"
            + ("<br>%{customdata:,.0f} kWh" if part != "Monthly fee" else "") + "<extra></extra>",
        )
    totals = long_df.groupby("Tenant")["$"].sum().reindex(sites)
    for site, tot in totals.items():
        fig.add_annotation(x=tot, y=site, text=f"<b>{money(tot)}</b>", showarrow=False, xanchor="left", xshift=8,
                           font=dict(color=INK, size=13))
    fig.update_layout(barmode="stack", bargap=0.45)
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=INK2, size=13))
    fig.update_xaxes(tickprefix="$", gridcolor=GRID, showgrid=True, range=[0, float(totals.max()) * 1.18])
    return _base(fig, height=max(200, 70 + 58 * len(sites)))


def tenant_trend_chart(df: pd.DataFrame, tenant: str, view: str, vacant_months: list[str]) -> go.Figure:
    """One tenant over time (emphasis form: a single series). Vacant months are labeled."""
    d = df[df["Tenant"] == tenant].sort_values("ym")
    col = "Charge ($)" if view == "$" else "kWh (calc)"
    labels = pd.to_datetime(d["ym"] + "-01").dt.strftime("%b %Y")
    fig = go.Figure(
        go.Bar(
            x=labels, y=d[col], marker=dict(color=ACCENT, line=dict(width=0)),
            text=[money(v) if view == "$" else f"{v:,.0f}" for v in d[col]],
            textposition="outside", cliponaxis=False, textfont=dict(color=INK2, size=12),
            customdata=d[["kWh (calc)", "Charge ($)"]],
            hovertemplate="<b>%{x}</b><br>%{customdata[0]:,.0f} kWh<br>$%{customdata[1]:,.2f}<extra></extra>",
        )
    )
    for ym in vacant_months:
        fig.add_annotation(x=pd.to_datetime(ym + "-01").strftime("%b %Y"), y=0, text="vacant", showarrow=False,
                           yshift=10, font=dict(color=MUTED, size=11))
    fig.update_layout(bargap=0.55, uniformtext_minsize=11, uniformtext_mode="show")
    fig.update_yaxes(title_text="Charge ($)" if view == "$" else "kWh")
    return _base(fig, height=320, legend=False)


def occupancy_chart(grid: pd.DataFrame) -> go.Figure:
    """Who lived where, by month: people per site (0 shown as a dash)."""
    sites = list(dict.fromkeys(grid["Site"]))
    months = list(dict.fromkeys(grid["ym"]))
    z = grid.pivot(index="Site", columns="ym", values="People").reindex(index=sites, columns=months)
    labels = pd.to_datetime(pd.Series(months) + "-01").dt.strftime("%b %y").tolist()
    text = z.map(lambda v: "–" if v == 0 else str(int(v)))
    fig = go.Figure(
        go.Heatmap(
            z=z.values, x=labels, y=sites, text=text.values, texttemplate="%{text}",
            textfont=dict(color=INK, size=13),
            colorscale=[[0, "#1a1a19"], [0.001, SEQUENTIAL[2]], [1, SEQUENTIAL[3]]],
            zmin=0, zmax=max(2, float(z.max().max())), showscale=False, xgap=3, ygap=3,
            hovertemplate="<b>%{y}</b><br>%{x}: %{z} people<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=INK2))
    fig.update_xaxes(side="top", tickfont=dict(color=MUTED))
    return _base(fig, height=60 + 38 * len(sites), legend=False)


# -------------------------------------------------------------- farm power

def ledger_chart(fl: pd.DataFrame) -> go.Figure:
    """Where the farm's measured kWh went each cycle, with the utility meter's total as a marker."""
    labels = pd.to_datetime(fl["ym"] + "-01").dt.strftime("%b %Y")
    fig = go.Figure()
    for name in LEDGER_ORDER:
        fig.add_bar(
            x=labels, y=fl[name], name=name if name != "Owners" else "Owners (house share)",
            marker=dict(color=LEDGER_COLORS[name], line=dict(color=SURFACE, width=2)),
            hovertemplate="<b>%{x}</b><br>" + name + ": %{y:,.0f} kWh<extra></extra>",
        )
    has_util = fl["Utility kWh"].notna()
    if has_util.any():
        full = fl["Coverage"].fillna(0) >= 0.97
        fig.add_trace(go.Scatter(
            x=labels[has_util & full], y=fl.loc[has_util & full, "Utility kWh"], mode="markers", name="Utility farm meter",
            marker=dict(symbol="diamond", size=11, color=INK, line=dict(color=SURFACE, width=2)),
            hovertemplate="<b>%{x}</b><br>Utility meter: %{y:,.0f} kWh<extra></extra>",
        ))
    fig.update_layout(barmode="stack", bargap=0.4)
    fig.update_yaxes(title_text="kWh per billing cycle")
    return _base(fig, height=360)


def cycle_compare_chart(cv: pd.DataFrame, title_unit: str = "kWh") -> go.Figure:
    """This cycle against the one before, by day of cycle (emphasis: previous is the quiet gray)."""
    fig = go.Figure()
    prev = cv[cv["Cycle"] == "Previous cycle"]
    cur = cv[cv["Cycle"] == "This cycle"]
    fig.add_trace(go.Scatter(
        x=prev["Day of cycle"], y=prev["kWh"], mode="lines", name="Previous cycle",
        line=dict(color=MUTED, width=2, dash="dot"),
        hovertemplate="Day %{x} (previous): %{y:,.1f} kWh<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=cur["Day of cycle"], y=cur["kWh"], mode="lines", name="This cycle",
        line=dict(color=ACCENT, width=2),
        hovertemplate="Day %{x}: %{y:,.1f} kWh<extra></extra>",
    ))
    fig.update_xaxes(title_text="Day of cycle (starts the 23rd)", dtick=5)
    fig.update_yaxes(title_text=f"{title_unit} per day", rangemode="tozero")
    fig.update_layout(hovermode="x unified")
    return _base(fig, height=320)


def calendar_heatmap(daily: pd.DataFrame, start: pd.Timestamp) -> go.Figure:
    """Daily usage as a weekday-by-week grid, to spot unusual days."""
    d = daily.copy()
    d["week"] = ((d["day"] - start).dt.days // 7)
    d["dow"] = d["day"].dt.dayofweek
    z = d.pivot(index="dow", columns="week", values="kWh").reindex(range(7))
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    week_start = [start + pd.Timedelta(days=7 * int(w)) for w in z.columns]
    fig = go.Figure(
        go.Heatmap(
            z=z.values, x=[f"{w:%b} {w.day}" for w in week_start], y=names,
            colorscale=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
            xgap=3, ygap=3, colorbar=dict(title="kWh", thickness=10, tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
            hovertemplate="Week of %{x}, %{y}: %{z:,.1f} kWh<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(side="top")
    return _base(fig, height=250, legend=False)


# ------------------------------------------------------------ utility bills

def usage_history_chart(uh: pd.DataFrame, role: str) -> go.Figure:
    """Like the bill's Usage History: kWh per day for each bill, last year's marked for comparison."""
    d = uh[uh["Role"] == role].tail(13)
    labels = pd.to_datetime(d["read_to"]).dt.strftime("%b %y")
    fig = go.Figure()
    fig.add_bar(
        x=labels, y=d["kWh/day"], name="kWh per day", marker=dict(color=ROLE_COLORS[role], line=dict(width=0)),
        customdata=d[["usage_kwh", "days", "total_charges"]],
        hovertemplate="<b>%{x}</b><br>%{y:,.1f} kWh/day<br>%{customdata[0]:,.0f} kWh over %{customdata[1]} days<br>$%{customdata[2]:,.2f}<extra></extra>",
    )
    ly = d["Last year kWh/day"]
    if ly.notna().any():
        fig.add_trace(go.Scatter(
            x=labels[ly.notna()], y=ly[ly.notna()], mode="markers", name="Same time last year",
            marker=dict(symbol="line-ew", size=16, line=dict(color=INK, width=2)),
            hovertemplate="<b>%{x}</b><br>Last year: %{y:,.1f} kWh/day<extra></extra>",
        ))
    fig.update_layout(bargap=0.5)
    fig.update_yaxes(title_text="kWh per day")
    return _base(fig, height=300)


def cost_per_kwh_chart(uh: pd.DataFrame) -> go.Figure:
    """All-in cost per kWh over time: it rises when usage falls because of fixed charges."""
    fig = go.Figure()
    for role in ["Farm", "House"]:
        d = uh[(uh["Role"] == role) & uh["$/kWh"].notna() & (uh["days"] >= 20)]  # a short first bill is mostly fixed charges
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(d["read_to"]), y=d["$/kWh"], mode="lines+markers", name=role,
            line=dict(color=ROLE_COLORS[role], width=2), marker=dict(size=8, line=dict(color=SURFACE, width=2)),
            hovertemplate="<b>" + role + "</b> %{x|%b %Y}<br>$%{y:,.3f} per kWh<extra></extra>",
        ))
    fig.update_yaxes(title_text="All-in $ per kWh", tickprefix="$", rangemode="tozero")
    return _base(fig, height=300)


def coverage_chart(rec: pd.DataFrame) -> go.Figure:
    """How much of the farm meter's kWh Emporia saw, per bill, against a +/-2% band."""
    d = rec.sort_values("Bill date").tail(15)
    seen = d["Emporia kWh"] / d["Farm kWh"] * 100.0
    fig = go.Figure()
    fig.add_hrect(y0=98, y1=102, fillcolor="#3987e5", opacity=0.12, line_width=0)
    fig.add_trace(go.Scatter(
        x=pd.to_datetime(d["Bill date"]), y=seen, mode="lines+markers", name="Emporia saw",
        line=dict(color=ACCENT, width=2), marker=dict(size=8, line=dict(color=SURFACE, width=2)),
        hovertemplate="Bill %{x|%b %-d, %Y}<br>Emporia saw %{y:.1f}% of farm kWh<extra></extra>",
    ))
    fig.add_annotation(xref="paper", x=1, y=100, yref="y", text="within 2%", showarrow=False, xanchor="right",
                       yshift=14, font=dict(color=MUTED, size=11))
    fig.update_yaxes(title_text="% of farm-meter kWh", ticksuffix="%", range=[95, 105])
    return _base(fig, height=280, legend=False)


def monthly_bar_chart(months: list[str], values: list[float], unit: str = "kWh") -> go.Figure:
    """A simple one-series monthly column chart (one series: no legend, value labels on top)."""
    labels = pd.to_datetime(pd.Series(months) + "-01").dt.strftime("%b %y")
    fig = go.Figure(
        go.Bar(
            x=labels, y=values, marker=dict(color=ACCENT, line=dict(width=0)),
            text=[f"{v:,.0f}" for v in values], textposition="outside", cliponaxis=False,
            textfont=dict(color=INK2, size=11),
            hovertemplate="<b>%{x}</b><br>%{y:,.1f} " + unit + "<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.5, uniformtext_minsize=10, uniformtext_mode="show")
    fig.update_yaxes(title_text=unit)
    return _base(fig, height=260, legend=False)


# ------------------------------------------------------------------ hubs
REST_LABEL = "Everything else on this hub"
OTHER_LABEL = "Other circuits"
PIE_SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]  # canonical order, dark steps
REST_COLOR = "#898781"
OTHER_COLOR = "#5d5b57"


def channel_chart(df: pd.DataFrame, main_kwh: float, kind: str = "Bar") -> go.Figure:
    """Circuits on one Emporia hub. The part of the hub's Main reading that isn't on a monitored
    circuit is shown as 'Everything else', so shares add up to the hub's real total."""
    d = df[["Channel", "kWh"]].copy()
    total_circuits = float(d["kWh"].sum())
    rest = max(0.0, main_kwh - total_circuits) if main_kwh > 0 else 0.0
    if rest > 0.5:
        d = pd.concat([d, pd.DataFrame([{"Channel": REST_LABEL, "kWh": rest}])], ignore_index=True)
    grand = float(d["kWh"].sum()) or 1.0
    d["pct"] = d["kWh"] / grand * 100.0

    if kind == "Pie":
        circuits = d[d["Channel"] != REST_LABEL].sort_values("kWh", ascending=False)
        top, tail = circuits.head(5), circuits.iloc[5:]
        labels = top["Channel"].tolist()
        values = top["kWh"].tolist()
        colors = PIE_SLOTS[: len(labels)]
        if len(tail):
            labels.append(OTHER_LABEL)
            values.append(float(tail["kWh"].sum()))
            colors.append(OTHER_COLOR)
        if rest > 0.5:
            labels.append(REST_LABEL)
            values.append(rest)
            colors.append(REST_COLOR)
        grand_pie = float(sum(values)) or 1.0
        pct_text = [f"{v / grand_pie * 100:.0f}%" if v / grand_pie >= 0.04 else "" for v in values]  # label only readable slices
        fig = go.Figure(
            go.Pie(
                labels=labels, values=values, hole=0.55, sort=False, direction="clockwise",
                marker=dict(colors=colors, line=dict(color=SURFACE, width=2)),
                text=pct_text, textinfo="text", textposition="outside", textfont=dict(color=INK2, size=12),
                domain=dict(x=[0.0, 0.58]), automargin=True,
                hovertemplate="<b>%{label}</b><br>%{value:,.0f} kWh · %{percent:.1%}<extra></extra>",
            )
        )
        _base(fig, height=300)  # then the pie-specific legend, so the shared one doesn't override it
        fig.update_layout(legend=dict(orientation="v", x=0.62, y=0.5, xanchor="left", yanchor="middle", font=dict(color=INK2, size=12)))
        return fig

    d = d.sort_values("kWh", ascending=False)
    colors = [REST_COLOR if c == REST_LABEL else ACCENT for c in d["Channel"]]
    fig = go.Figure(
        go.Bar(
            y=d["Channel"], x=d["kWh"], orientation="h", marker=dict(color=colors, line=dict(width=0)),
            text=[f"{k:,.0f} kWh · {p:.0f}%" for k, p in zip(d["kWh"], d["pct"])],
            textposition="outside", cliponaxis=False, textfont=dict(color=INK2, size=12),
            hovertemplate="<b>%{y}</b><br>%{x:,.1f} kWh<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=INK2, size=12))
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, float(d["kWh"].max()) * 1.45])
    fig.update_layout(bargap=0.4)
    return _base(fig, height=max(130, 36 + 27 * len(d)), legend=False)


# ------------------------------------------------------------------ weather
def temp_range_chart(d: pd.DataFrame) -> go.Figure:
    """Each day's low-to-high temperature as a band, with the day's average as a line."""
    fig = go.Figure()
    edge = "rgba(57,135,229,0.6)"
    fig.add_trace(go.Scatter(x=d["day"], y=d["Low °F"], mode="lines", line=dict(width=1, color=edge), showlegend=False,
                             hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=d["day"], y=d["High °F"], mode="lines", line=dict(width=1, color=edge), fill="tonexty", fillcolor="rgba(57,135,229,0.28)",
        name="Daily low to high", customdata=d[["Low °F"]],
        hovertemplate="%{x|%b %-d}<br>High %{y:.0f}°F · Low %{customdata[0]:.0f}°F<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=d["day"], y=d["Avg °F"], mode="lines", name="Daily average", line=dict(color=ACCENT, width=2),
        hovertemplate="%{x|%b %-d}<br>Average %{y:.0f}°F<extra></extra>",
    ))
    fig.update_yaxes(title_text="°F")
    fig.update_xaxes(tickformat="%b %-d")
    return _base(fig, height=300)


def rain_chart(d: pd.DataFrame) -> go.Figure:
    """Rain per day (a single series: no legend)."""
    fig = go.Figure(
        go.Bar(x=d["day"], y=d["Rain in"], marker=dict(color=ACCENT, line=dict(width=0)),
               hovertemplate="%{x|%b %-d}: %{y:.2f} in<extra></extra>")
    )
    fig.update_layout(bargap=0.35)
    fig.update_yaxes(title_text="inches per day", rangemode="tozero")
    fig.update_xaxes(tickformat="%b %-d")
    return _base(fig, height=240, legend=False)


def energy_temp_scatter(m: pd.DataFrame, label: str) -> tuple[go.Figure, float, float]:
    """Daily kWh against the day's average temperature, with a straight-line fit.

    Returns the figure, the correlation r, and kWh change per +10°F (the line's slope x 10).
    """
    import numpy as np

    x, y = m["Avg °F"].to_numpy(), m["kWh"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    r = float(np.corrcoef(x, y)[0, 1])
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers", name="One day",
        marker=dict(size=7, color=ACCENT, opacity=0.6, line=dict(color=SURFACE, width=1)),
        customdata=m["day"].dt.strftime("%b %-d, %Y"),
        hovertemplate="%{customdata}<br>%{x:.0f}°F average<br>%{y:,.1f} kWh<extra></extra>",
    ))
    xs = np.array([x.min(), x.max()])
    fig.add_trace(go.Scatter(x=xs, y=slope * xs + intercept, mode="lines", name="Trend",
                             line=dict(color=INK2, width=2, dash="dash"), hoverinfo="skip"))
    fig.update_xaxes(title_text="Average outdoor temperature that day (°F)")
    fig.update_yaxes(title_text=f"{label}, kWh per day", rangemode="tozero")
    return _base(fig, height=340), r, float(slope * 10)
