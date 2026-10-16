import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.climate import parse_oni, parse_pdo, phase, save, strength

ONI = """ SEAS  YR   TOTAL   ANOM
  DJF 1950  25.01  -1.32
  NDJ 1950  24.80  -0.90
  DJF 2026  26.15  -0.39
  JAS 2026  29.12   2.16
"""
PDO = """ ERSST PDO Index:
Year  Jan   Feb   Mar   Apr   May   Jun   Jul   Aug   Sep   Oct   Nov   Dec
2025 -1.29 -1.40 -1.12 -1.15 -1.66 -2.64 -4.21 -3.23 -2.31 -2.37 -1.51 -0.96
2026 -1.24 -1.00 -1.42 -1.62 -1.68 -1.72 -2.16 -1.89 -1.55 99.99 99.99 99.99
"""


def test_oni_seasons_are_labeled_by_their_middle_month():
    o = parse_oni(ONI)
    assert o["1950-01"] == (-1.32, "DJF")      # Dec-Jan-Feb is centered on January
    assert o["1950-12"] == (-0.90, "NDJ")      # Nov-Dec-Jan is centered on December
    assert o["2026-08"] == (2.16, "JAS")       # the newest season in the file
    assert len(o) == 4


def test_pdo_skips_months_that_have_not_happened():
    p = parse_pdo(PDO)
    assert p["2026-09"] == -1.55 and "2026-10" not in p and len(p) == 21


def test_phase_and_strength_follow_noaas_bands():
    assert [phase(x) for x in (0.5, 0.49, -0.5, -0.4, 2.16)] == ["El Niño", "Neutral", "La Niña", "Neutral", "El Niño"]
    assert [strength(x) for x in (0.7, 1.0, 1.7, 2.0, -1.2, 0.3)] == ["weak", "moderate", "strong", "very strong", "moderate", None]


def test_save_merges_both_series_and_is_repeatable():
    path = os.path.join(tempfile.mkdtemp(), "w.sqlite")
    assert save(parse_oni(ONI), parse_pdo(PDO), path) > 20
    save(parse_oni(ONI), parse_pdo(PDO), path)
    c = sqlite3.connect(path)
    assert c.execute("select oni, oni_season, pdo from climate_monthly where month = '2026-08'").fetchone() == (2.16, "JAS", -1.89)
    assert c.execute("select oni from climate_monthly where month = '2025-12'").fetchone() == (None,)  # PDO-only month


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
