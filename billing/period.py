import datetime as dt

BILLING_DAY = 23  # Emporia billing cycle day


def last_day_of_month(d: dt.date) -> int:
    next_month = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return (next_month - dt.timedelta(days=1)).day


def clamp_day(year: int, month: int, day: int) -> dt.date:
    dmax = last_day_of_month(dt.date(year, month, 1))
    return dt.date(year, month, min(day, dmax))


def billing_period_for_ym(billing_ym: str, billing_day: int = BILLING_DAY) -> tuple[dt.date, dt.date]:
    """
    billing_ym is the Emporia "billing month" label in YYYY-MM.
    For billing_day=23: billing_ym=2026-01 means 2025-12-23 .. 2026-01-22
    """
    y, m = map(int, billing_ym.split("-"))
    period_start = dt.date(y, m, 1)

    prev_month = (period_start - dt.timedelta(days=1)).replace(day=1)
    start = clamp_day(prev_month.year, prev_month.month, billing_day)

    this_billing_day = clamp_day(period_start.year, period_start.month, billing_day)
    end = this_billing_day - dt.timedelta(days=1)
    return start, end


def billing_ym_for_day(day: dt.date, billing_day: int = BILLING_DAY) -> str:
    """Billing-month label (YYYY-MM) a given calendar day counts toward."""
    if day.day >= billing_day:
        day = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return day.strftime("%Y-%m")


def last_closed_billing_ym(today: dt.date, billing_day: int = BILLING_DAY) -> str:
    """Billing-month label (YYYY-MM) of the most recent cycle that has fully ended.

    With a 23rd-22nd cycle: on Oct 23 or any day through Nov 22 the last closed cycle
    is the one that ended Oct 22, labeled "2026-10". So a job running on the 1st
    always reports the cycle that ended on the 22nd of the previous month.
    """
    if today.day >= billing_day:
        return today.strftime("%Y-%m")
    prev = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    return prev.strftime("%Y-%m")
