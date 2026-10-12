import pandas as pd

COLUMNS = [
    "Tenant", "kWh (calc)", "Charge ($)",
    "Own kWh", "Kitchen kWh", "Shop kWh", "Base kWh", "Fee ($)",
]


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
        return pd.DataFrame(columns=COLUMNS)

    rows = []
    for name, people in occupied.items():
        own = site_kwh[name]
        kitchen = kitchen_lounge_kwh * people / billed_people
        shop = shared_shop_kwh * people / all_people
        base = base_kwh * people / all_people
        kwh = own + kitchen + shop + base
        rows.append(
            {
                "Tenant": name,
                "kWh (calc)": kwh,
                "Charge ($)": round(kwh * rate + fee, 2),
                # The pieces of the bill, for breakdown charts (kWh; dollars = kWh x rate)
                "Own kWh": own,
                "Kitchen kWh": kitchen,
                "Shop kWh": shop,
                "Base kWh": base,
                "Fee ($)": fee,
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)
