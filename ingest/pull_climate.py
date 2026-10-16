"""Refresh the ENSO (ONI) and PDO climate indices from NOAA into data/weather.sqlite.

    python -m ingest.pull_climate

Both indices are monthly, so running this weekly is plenty.
"""
from shared.climate import ONI_URL, PDO_URL, fetch, parse_oni, parse_pdo, save


def main() -> None:
    oni = parse_oni(fetch(ONI_URL))
    pdo = parse_pdo(fetch(PDO_URL))
    if len(oni) < 500 or len(pdo) < 1000:
        raise RuntimeError(f"NOAA files look incomplete (ONI {len(oni)} months, PDO {len(pdo)} months)")
    n = save(oni, pdo)
    last_oni = max(oni)
    print(f"Saved {n:,} months. Newest ONI: {last_oni} = {oni[last_oni][0]:+.2f} ({oni[last_oni][1]}); "
          f"newest PDO: {max(pdo)} = {pdo[max(pdo)]:+.2f}")


if __name__ == "__main__":
    main()
