import pandas as pd


def calculate_site_charges(
    *,
    site_kwh: dict[str, float],
    site_people: dict[str, int],
    unbilled_people: int,
    kitchen_lounge_kwh: float,
    shared_shop_kwh: float,
    rate: float,
    fee: float,
    base_kwh: float,
) -> pd.DataFrame:
    """
    One row per OCCUPIED site (people > 0): own metered kWh plus its people's
    share of each pool.

      base + shop : divided by all people (billed + unbilled)
      kitchen     : divided by billed people only

    Vacant sites are not billed and are not counted in any divisor.
    All kWh inputs are already filtered to the billing period.
    """
    occupied = {n: p for n, p in site_people.items() if p > 0}
    billed_people = sum(occupied.values())
    all_people = billed_people + unbilled_people
    if billed_people == 0:
        return pd.DataFrame(columns=["Tenant", "kWh (calc)", "Charge ($)"])

    rows = []
    for name, people in occupied.items():
        kwh = (
            site_kwh[name]
            + base_kwh * people / all_people
            + kitchen_lounge_kwh * people / billed_people
            + shared_shop_kwh * people / all_people
        )
        rows.append(
            {
                "Tenant": name,
                "kWh (calc)": kwh,
                "Charge ($)": round(kwh * rate + fee, 2),
            }
        )
    return pd.DataFrame(rows, columns=["Tenant", "kWh (calc)", "Charge ($)"])
