"""Tests for the ETL. Run from the repo root:  python -m pytest etl/tests -q"""
import csv
import json
import math
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_dataset as bd  # noqa: E402
import fetch_pricecatcher as fp  # noqa: E402
import make_synthetic as ms  # noqa: E402

# Build without any shop location files, so results do not depend on etl/geo/.
NO_GEO = {"geo_path": None, "postcodes_path": None, "district_geo_path": None}


def prices(rows):
    return pd.DataFrame(rows, columns=["date", "premise_code", "item_code", "price"])


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def district_file(out, slug):
    return read_json(out / "d" / f"{slug}.json")


def write_raw(folder, items, premises, months):
    """A small hand-made raw folder.

    items: (code, name, unit); premises: (code, state, district);
    months: {"YYYY-MM": [(date, premise_code, item_code, price), ...]}.
    """
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([(c, n, u, "G", "C") for c, n, u in items],
                 columns=["item_code", "item", "unit", "item_group", "item_category"]
                 ).to_csv(folder / "lookup_item.csv", index=False)
    pd.DataFrame([(c, f"Kedai Contoh {c} (SAMPLE)", f"No. {c}, Jalan Contoh", "Pasar Mini", st, dist)
                  for c, st, dist in premises],
                 columns=["premise_code", "premise", "address", "premise_type", "state", "district"]
                 ).to_csv(folder / "lookup_premise.csv", index=False)
    for ym, rows in months.items():
        prices(rows).to_csv(folder / f"pricecatcher_{ym}.csv", index=False)
    return folder


# ---------- cleaning ----------

def test_clean_prices_drops_bad_rows_and_merges_duplicates():
    df = prices([
        ("2026-10-01", 1, 10, 5.0),
        ("2026-10-01", 1, 10, 5.4),     # same day duplicate -> median 5.2
        ("2026-10-02", 1, 10, 0.0),     # zero
        ("2026-10-02", 1, 10, None),    # blank
        ("bad-date", 1, 10, 5.0),       # bad date
        ("2026-10-03", 2, 10, 500.0),   # outlier (100x)
        ("2026-10-03", 3, 10, 5.1),
        ("2026-10-03", 9, 10, 5.0),     # unknown premise
        ("2026-10-03", 1, 99, 5.0),     # unknown item
        ("2026-10-04", "2", "10", "5.3"),  # strings coerce fine
    ])
    out = bd.clean_prices(df, item_codes=[10], premise_codes=[1, 2, 3])
    assert len(out) == 3
    assert out["price"].max() < 10
    dup = out[(out["premise_code"] == 1) & (out["date"] == "2026-10-01")]
    assert dup["price"].iloc[0] == pytest.approx(5.2)
    assert str(out["item_code"].dtype) == "int64"


def test_clean_premises_drops_rows_missing_location():
    df = pd.DataFrame({
        "premise_code": [1, 2, None, 4],
        "premise": ["A", "B", "C", None],
        "address": ["x", None, "z", "w"],
        "premise_type": ["Kedai Runcit", "Hypermarket", "x", "y"],
        "state": ["Selangor", "Selangor", "Johor", "Johor"],
        "district": ["Petaling", None, "Johor Bahru", "Johor Bahru"],
    })
    out = bd.clean_premises(df)
    assert out["premise_code"].tolist() == [1]


def test_clean_items_drops_invalid_codes():
    df = pd.DataFrame({"item_code": [1, -1, None, 2, 2], "item": ["A", "U", "X", "B", "B"],
                       "unit": ["1kg", "", "", "500g", "500g"], "item_group": ["G"] * 5,
                       "item_category": [None] * 5})
    out = bd.clean_items(df)
    assert out["item_code"].tolist() == [1, 2]
    assert out["item_category"].tolist() == ["", ""]


# ---------- metrics ----------

def test_latest_prices_picks_newest_date():
    df = bd.clean_prices(prices([
        ("2026-09-20", 1, 10, 4.0),
        ("2026-10-05", 1, 10, 4.5),
        ("2026-10-05", 1, 10, 4.7),
        ("2026-10-02", 2, 10, 4.2),
    ]))
    out = bd.latest_prices(df).set_index("premise_code")
    assert out.loc[1, "price"] == pytest.approx(4.6)
    assert out.loc[1, "date"] == pd.Timestamp("2026-10-05")
    assert out.loc[2, "price"] == pytest.approx(4.2)


def test_monthly_median_and_like_for_like_mom():
    df = bd.clean_prices(prices([
        # shop 1: 10 -> 11, shop 2: 10 -> 12, shop 3: only in Oct
        ("2026-09-03", 1, 10, 10.0), ("2026-09-20", 1, 10, 10.0),
        ("2026-10-03", 1, 10, 11.0),
        ("2026-09-03", 2, 10, 10.0), ("2026-10-03", 2, 10, 12.0),
        ("2026-10-03", 3, 10, 30.0),
    ]))
    pm = bd.monthly_premise_median(df)
    assert set(pm["month"]) == {"2026-09", "2026-10"}
    ch = bd.mom_changes(pm, "2026-10", "2026-09", min_n=2)
    row = ch.iloc[0]
    assert row["n"] == 2                      # shop 3 is not counted
    assert row["pct"] == pytest.approx(15.0)  # median 10.00 -> 11.50
    assert bd.mom_changes(pm, "2026-10", "2026-09", min_n=3).empty


def test_mom_pct_is_the_change_of_the_medians_shown():
    pm = pd.DataFrame({"month": ["2026-09"] * 3 + ["2026-10"] * 3, "premise_code": [1, 2, 3] * 2,
                       "item_code": [5] * 6, "price": [10.0, 20.0, 30.0, 30.0, 21.0, 31.0]})
    row = bd.mom_changes(pm, "2026-10", "2026-09").iloc[0]
    # Per-shop changes are +200%, +5% and +3.3% (median +5%), but the medians shown go RM20 -> RM30.
    assert (row["prev_price"], row["cur_price"], row["pct"], row["n"]) == (20.0, 30.0, 50.0, 3)
    # The % comes from the medians to the sen, as shown: RM1.005 shows as RM1.00, so
    # RM1.00 -> RM1.10 is +10.0% (the unrounded median would give +9.5%).
    pm2 = pd.DataFrame({"month": ["2026-09"] * 2 + ["2026-10"] * 2, "premise_code": [1, 2] * 2,
                        "item_code": [6] * 4, "price": [1.0, 1.01, 1.1, 1.1]})
    row = bd.mom_changes(pm2, "2026-10", "2026-09", min_n=2).iloc[0]
    assert (row["prev_price"], row["cur_price"], row["pct"]) == (1.0, 1.1, 10.0)


def test_feed_rows_sorted_by_pct():
    pm = pd.DataFrame({"month": ["2026-09"] * 9 + ["2026-10"] * 9,
                       "premise_code": [1, 2, 3] * 6,
                       "item_code": [7, 7, 7, 8, 8, 8, 9, 9, 9] * 2,
                       "price": [10.0] * 9 + [11.0] * 3 + [13.0] * 3 + [9.0] * 3})
    feed = bd.feed_rows(bd.mom_changes(pm, "2026-10", "2026-09"), 25)
    assert feed["cols"] == ["item_code", "pct", "n_shops", "prev_median", "cur_median"]
    assert feed["rises"] == [[8, 30.0, 3, 10.0, 13.0], [7, 10.0, 3, 10.0, 11.0]]
    assert feed["falls"] == [[9, -10.0, 3, 10.0, 9.0]]


def test_months_with_data_skips_nearly_empty_months():
    months = ["2026-07", "2026-08", "2026-09", "2026-10"]
    rows = {"2026-07": 1000, "2026-08": 0, "2026-09": 40, "2026-10": 1000}
    assert bd.months_with_data(rows, months) == ["2026-07", "2026-10"]
    rows["2026-09"] = 50   # exactly 5% of the median of the other months (1000)
    assert bd.months_with_data(rows, months) == ["2026-07", "2026-09", "2026-10"]
    assert bd.months_with_data({"2026-10": 3}, ["2026-10"]) == ["2026-10"]
    assert bd.months_with_data({"2026-10": 0}, ["2026-10"]) == []


def test_fresh_only_keeps_prices_up_to_30_days_old():
    latest = pd.DataFrame({"premise_code": [1, 2, 3], "item_code": [5, 5, 5], "price": [1.0, 1.0, 1.0],
                           "date": pd.to_datetime(["2026-10-20", "2026-09-20", "2026-09-19"])})
    out = bd.fresh_only(latest, pd.Timestamp("2026-10-20"))
    assert bd.MAX_PRICE_AGE_DAYS == 30
    assert out["premise_code"].tolist() == [1, 2]
    assert out["days_old"].tolist() == [0, 30]


def test_set_aside_pack_errors_against_the_item_median():
    latest = pd.DataFrame({"premise_code": range(1, 10), "item_code": [7] * 9,
                           "price": [30.0, 30.5, 29.5, 31.0, 30.0, 75.0, 12.0, 80.0, 11.0]})
    kept, aside = bd.set_aside_pack_errors(latest)
    # Median RM30: 75 (2.5x) and 12 (0.4x) stay; 80 and 11 are set aside.
    assert sorted(aside["price"]) == [11.0, 80.0]
    assert len(kept) == 7 and kept["price"].max() == 75.0


def test_pack_check_keeps_prices_exactly_on_the_limits():
    # In floating point 1.20 / 3.00 is 0.39999999999999997 and 2.35 / 0.94 is 2.5000000000000004,
    # but both prices are exactly on a limit, so they stay. One sen further out is set aside.
    for median, on_limit, outside in ((3.0, 1.2, 1.19), (0.94, 2.35, 2.36)):
        latest = pd.DataFrame({"premise_code": range(1, 7), "item_code": [7] * 6,
                               "price": [median] * 4 + [on_limit, outside]})
        kept, aside = bd.set_aside_pack_errors(latest)
        assert aside["price"].tolist() == [outside]
        assert sorted(kept["price"]) == sorted([median] * 4 + [on_limit])
    assert bd.pack_ok(1.2, 3.0) and bd.pack_ok(2.35, 0.94) and bd.pack_ok(2.8, 7.0)
    assert not bd.pack_ok(1.19, 3.0) and not bd.pack_ok(2.36, 0.94)


def test_history_table_aligns_months_and_fills_gaps():
    pm = pd.DataFrame({"month": ["2026-08", "2026-10"], "premise_code": [1, 1],
                       "item_code": [5, 5], "price": [2.0, 2.5]})
    h = bd.history_table(pm, ["2026-08", "2026-09", "2026-10"])
    assert h == {5: [2.0, None, 2.5]}


def test_slugify():
    assert bd.slugify("W.P. Kuala Lumpur", "Kuala Lumpur") == "w-p-kuala-lumpur--kuala-lumpur"
    assert bd.slugify("Pulau Pinang", "Seberang Perai Utara") == "pulau-pinang--seberang-perai-utara"


# ---------- proof figure ----------

# Names and units as in the real lookup table, with near misses the patterns must skip.
REAL_ITEMS = pd.DataFrame([
    (1, "AYAM BERSIH - STANDARD", "1kg"),
    (1109, "TELUR AYAM GRED A", "30 biji"),
    (118, "TELUR AYAM GRED A (BERAT 65.0 GM HINGGA 69.9 GM SEBIJI)", "10 biji"),
    (1445, "BERAS CAP FAIZA EMAS (SST5%)", "10 kg"),
    (1492, "BERAS PULUT THAILAND (BIASA) PELBAGAI JENAMA", "10 kg"),
    (1589, "GULA PUTIH BERTAPIS KASAR (PELBAGAI JENAMA)", "1kg"),
    (1939, "MINYAK MASAK TULEN CAP SAJI", "5 kg"),
    (1937, "MINYAK MASAK TULEN CAP SAJI", "1kg"),
    (1440, "BAWANG BESAR IMPORT (INDIA)", "1kg"),
    (1441, "BAWANG BESAR IMPORT (CHINA)", "1kg"),
    (1559, "KANGKUNG", "1kg"),
    (114, "TOMATO", "1kg"),
    (1476, "IKAN KEMBUNG (ANTARA 8 HINGGA 12 EKOR SEKILOGRAM)", "1kg"),
    (55, "IKAN KEMBUNG KECIL/PELALING (ANTARA 10 HINGGA 18 EKOR SEKILOGRAM)", "1kg"),
    (884, "KRIMER MANIS CAP GOLD COIN", "500 g"),
], columns=["item_code", "item", "unit"])
PROOF_CODES = [1, 1109, 1445, 1589, 1939, 1441, 1559, 114, 1476, 884]


def test_proof_items_pick_the_most_priced_match():
    shops = pd.Series({c: 50 for c in REAL_ITEMS["item_code"]})
    shops[[118, 1492, 1937, 55]] = 900   # more shops, but the wrong pack or name
    shops[1441] = 60                      # China onions beat India onions
    codes, labels = bd.proof_items(REAL_ITEMS, shops)
    assert codes == PROOF_CODES
    assert labels == [g[0] for g in bd.PROOF_GROUPS]
    shops[1441] = 50                      # a tie: the lower code
    assert bd.proof_items(REAL_ITEMS, shops)[0][5] == 1440


def _proof_rows(slug_state, slug_district, totals, start):
    """Shops pricing the whole proof list; each item costs total/10."""
    rows = []
    for k, total in enumerate(totals):
        rows += [(start + k, code, total / 10, slug_state, slug_district) for code in PROOF_CODES]
    return rows


def test_proof_figure_per_district():
    rows = (_proof_rows("Selangor", "Petaling", [10.0, 15.0, 20.0], 100)
            + _proof_rows("Kedah", "Sik", [10.0, 18.0, 19.0], 200)
            + _proof_rows("Johor", "Muar", [12.0, 13.0], 300)                   # only 2 full shops
            + [(400 + k, c, 1.0, "Johor", "Muar") for k, c in enumerate(PROOF_CODES[:9])])  # partial
    latest_d = pd.DataFrame(rows, columns=["premise_code", "item_code", "price", "state", "district"])
    shops = latest_d.groupby("item_code")["premise_code"].nunique()
    proof = bd.proof_figure(latest_d, REAL_ITEMS, shops)
    assert proof["items"] == PROOF_CODES
    assert proof["by_district"] == {"kedah--sik": [10.0, 18.0, 3], "selangor--petaling": [10.0, 15.0, 3]}
    assert proof["example"] == "kedah--sik"     # same number of shops, larger saving
    # Fewer than 8 items found: no figure.
    assert bd.proof_figure(latest_d, REAL_ITEMS[~REAL_ITEMS["item_code"].isin([1, 114, 884])], shops) is None
    # No district with 3 shops that price the whole list: no figure.
    assert bd.proof_figure(latest_d[latest_d["state"] == "Johor"], REAL_ITEMS, shops) is None


def test_median_sen_rounds_half_up_like_the_app():
    assert bd.median_sen([3250, 3251]) == 3251            # (3250 + 3251) / 2 = 3250.5 sen: half up
    assert bd.median_sen([3500, 3000, 3251]) == 3251      # an odd count: the middle total
    assert bd.median_sen([3250, 3252]) == 3251 and bd.median_sen([100, 301, 150, 201]) == 176
    assert bd.to_sen(0.1 + 0.2) == 30 and bd.to_sen(0.29) == 29 and bd.to_sen(32.51) == 3251


def _proof_shops(state, district, shop_prices, start):
    """latest_d rows: one shop per entry of shop_prices, each the prices of the 10 proof items."""
    return [(start + k, code, p, state, district) for k, ps in enumerate(shop_prices)
            for code, p in zip(PROOF_CODES, ps)]


def proof_of(rows):
    latest_d = pd.DataFrame(rows, columns=["premise_code", "item_code", "price", "state", "district"])
    return bd.proof_figure(latest_d, REAL_ITEMS, latest_d.groupby("item_code")["premise_code"].nunique())


def test_proof_figure_adds_up_in_whole_sen_like_the_app():
    # The app adds up a list in whole sen (each price to the sen) and gives the typical total of an even
    # count as the mean of the two middle totals, rounded half up. The home screen must show the same.
    proof = proof_of(
        _proof_shops("Johor", "Muar", [[3.0] * 10, [3.25] * 10, [3.25] * 9 + [3.26], [3.5] * 10], 100)
        + _proof_shops("Kedah", "Sik", [[3.0] * 10, [3.25] * 9 + [3.26], [3.5] * 10], 200)
        + _proof_shops("Perlis", "Kangar", [[0.1] * 10, [0.1, 0.2] * 5, [0.1, 0.2, 0.3] * 3 + [0.21],
                                            [0.3] * 9 + [0.31]], 300))
    # Muar: RM30.00, 32.50, 32.51 and 35.00, so (32.50 + 32.51) / 2 = 32.505, shown as 32.51 (rounding
    # 32.505 in floating point gives 32.50). Sik: the middle shop. Kangar: prices like 0.1 and 0.2 are not
    # exact in floating point (0.1 + 0.2 is 0.30000000000000004), but in whole sen the totals are exactly
    # 1.00, 1.50, 2.01 and 3.01, and (150 + 201) / 2 = 175.5 sen is shown as 1.76.
    assert proof["by_district"] == {"johor--muar": [30.0, 32.51, 4], "kedah--sik": [30.0, 32.51, 3],
                                    "perlis--kangar": [1.0, 1.76, 4]}
    assert proof["example"] == "johor--muar"
    # The example district's tie on shops goes to the larger saving, also in whole sen: both save 251 sen
    # (in floating point 32.51 - 30.00 is 2.509999999999998 and 12.51 - 10.00 is 2.51), so the first by name.
    proof = proof_of(_proof_shops("Kedah", "Sik", [[1.0] * 10, [1.25] * 9 + [1.26], [1.5] * 10], 200)
                     + _proof_shops("Johor", "Muar", [[3.0] * 10, [3.25] * 9 + [3.26], [3.5] * 10], 100))
    assert proof["by_district"] == {"johor--muar": [30.0, 32.51, 3], "kedah--sik": [10.0, 12.51, 3]}
    assert proof["example"] == "johor--muar"


# ---------- shop locations ----------

def test_district_centre_and_radius_rules():
    a, b, c = (3.0, 101.5), (3.02, 101.52), (3.04, 101.5)
    assert bd.district_centre([a, b, c], fallback=(9.0, 9.0)) == (3.02, 101.5)       # median of shops
    assert bd.district_centre([a, b]) == pytest.approx((3.01, 101.51))
    assert bd.district_centre([a], fallback=(3.1, 101.6)) == (3.1, 101.6)            # the district's point
    assert bd.district_centre([], fallback=(3.1, 101.6)) == (3.1, 101.6)
    assert bd.district_centre([a]) == a                    # no usable district point: the one located shop
    assert bd.district_centre([]) is None and bd.district_centre([], fallback=None) is None
    assert bd.district_radius((3.0, 101.5), []) == 3.0
    assert bd.district_radius((3.0, 101.5), [(3.0, 101.5)]) == 1.0
    assert bd.district_radius((3.0, 101.5), [(3.1, 101.5)]) == pytest.approx(11.6)    # 11.1 km + 0.5


def test_load_district_points_drops_points_shared_in_a_state(tmp_path):
    p = tmp_path / "district_geo.csv"
    p.write_text("key,lat,lon,note\n"
                 "Selangor|Petaling,3.1,101.6,x\n"
                 "W.P. Kuala Lumpur|Cheras,3.1456,101.6974,\n"
                 "W.P. Kuala Lumpur|Kepong,3.1458,101.6970,\n"   # 50 m from Cheras: a state-centre guess
                 "Johor|Muar,,,\n"
                 "Johor|Segamat,3.1,101.6,\n", encoding="utf-8")  # same point as Petaling, other state
    assert bd.load_district_points(p) == {"selangor|petaling": (3.1, 101.6), "johor|segamat": (3.1, 101.6)}
    assert bd.load_district_points(tmp_path / "missing.csv") == {}
    assert bd.load_district_points(None) == {}


def test_load_district_points_reads_the_shared_column(tmp_path):
    p = tmp_path / "district_geo.csv"
    p.write_text("key,lat,lon,tries,shared\n"
                 "W.P. Kuala Lumpur|Cheras,3.1456,101.6974,1,1\n"   # left alone on the old shared point
                 "W.P. Kuala Lumpur|Kepong,3.2100,101.6400,1,\n"    # moved to its own point: mark cleared
                 "W.P. Kuala Lumpur|Batu,3.1900,101.6800,1,0\n"
                 "Selangor|Petaling,3.1,101.6,0,\n"
                 "Selangor|Klang,3.04,101.45,0,\n"
                 "Selangor|Sepang,3.0401,101.4501,0,\n"             # 15 m from Klang, not marked: still shared
                 "Johor|Muar,3.0,102.5,1, 1 \n"
                 "Johor|Segamat,,,1,1\n", encoding="utf-8")
    assert bd.load_district_points(p) == {"w.p. kuala lumpur|kepong": (3.21, 101.64),
                                          "w.p. kuala lumpur|batu": (3.19, 101.68),
                                          "selangor|petaling": (3.1, 101.6)}
    # Without the column (an older file) Cheras, alone on its point, would be used.
    p.write_text("key,lat,lon\nW.P. Kuala Lumpur|Cheras,3.1456,101.6974\n", encoding="utf-8")
    assert bd.load_district_points(p) == {"w.p. kuala lumpur|cheras": (3.1456, 101.6974)}


def test_load_district_points_reads_shared_cells_with_spaces(tmp_path):
    # A hand-edited or spreadsheet-saved file can pad the cell. Each district has its own point, so
    # only the mark decides: ' 1 ' is marked, ' 0 ' and a cell of spaces are not.
    p = tmp_path / "district_geo.csv"
    p.write_text("key,lat,lon,tries,shared\n"
                 "Kedah|Sik,5.92,100.82,0, 1 \n"
                 "Kedah|Yan,5.85,100.42,0, 0 \n"
                 "Kedah|Baling,5.68,100.92,0,  \n", encoding="utf-8")
    assert bd.load_district_points(p) == {"kedah|yan": (5.85, 100.42), "kedah|baling": (5.68, 100.92)}


def test_load_postcodes_averages_places_and_keeps_leading_zero(tmp_path):
    p = tmp_path / "postcodes_my.csv"
    p.write_text("postcode,place,state,lat,lon,accuracy\n"
                 "01000,Kangar,Perlis,6.4414,100.1986,4\n"
                 "40100,Shah Alam,Selangor,3.07,101.52,4\n"
                 "40100,Seksyen 1,Selangor,3.09,101.54,4\n"
                 "1000,Kangar,Perlis,6.4414,100.1986,4\n"     # leading 0 lost in a spreadsheet
                 "40200,Nowhere,Selangor,,,\n"
                 "ABCDE,Bad,Selangor,3.0,101.0,1\n", encoding="utf-8")
    assert bd.load_postcodes(p) == {"01000": [6.4414, 100.1986], "40100": [3.08, 101.53]}
    assert bd.load_postcodes(tmp_path / "missing.csv") == {}


def test_load_geo_tolerates_new_and_unknown_columns(tmp_path):
    p = tmp_path / "premise_geo.csv"
    p.write_text("premise_code,lat,lon,precision,query,district,state,postcode,tries,last_tried,extra\n"
                 "1,3.123456,101.5,Address,q,D,S,40100,1,2026-11-01,x\n"
                 "2,,,postcode,q,D,S,46000,3,2026-11-01,\n"
                 "3,3.2,101.6,state,q,D,S,,2,,\n"
                 "4,nan,101.6,name,q,D,S,,1,,\n"
                 "x,3.2,101.6,name,q,D,S,,1,,\n", encoding="utf-8")
    assert bd.load_geo(p) == {1: (3.12346, 101.5, "address", "40100"), 3: (3.2, 101.6, "district", "")}
    assert bd.load_geo(None) == {} and bd.load_geo(tmp_path / "missing.csv") == {}


# ---------- fetch helpers (no network) ----------

def test_last_n_months_crosses_year():
    assert fp.last_n_months(3, date(2026, 2, 10)) == ["2025-12", "2026-01", "2026-02"]


def test_file_url_pattern():
    assert fp.file_url("pricecatcher_2026-10") == \
        "https://storage.data.gov.my/pricecatcher/pricecatcher_2026-10.parquet"
    assert fp.file_url("lookup_item", "csv") == "https://storage.data.gov.my/pricecatcher/lookup_item.csv"


def test_fetch_refuses_synthetic_folder(tmp_path):
    (tmp_path / "SYNTHETIC.txt").write_text("x")
    assert fp.main(["--out", str(tmp_path), "--months", "1"]) == 2


# ---------- end to end ----------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    out = tmp_path_factory.mktemp("out")
    ms.make(raw, months=4, end="2026-10", last_day=27, fmt="csv")
    res = bd.build(raw, out, months_keep=12, **NO_GEO)
    return raw, out, res


def test_synthetic_schema_matches_docs(built):
    raw, _, _ = built
    assert list(pd.read_csv(raw / "lookup_item.csv").columns) == \
        ["item_code", "item", "unit", "item_group", "item_category"]
    assert list(pd.read_csv(raw / "lookup_premise.csv").columns) == \
        ["premise_code", "premise", "address", "premise_type", "state", "district"]
    assert list(pd.read_csv(raw / "pricecatcher_2026-10.csv").columns) == \
        ["date", "premise_code", "item_code", "price"]


def test_build_outputs(built):
    _, out, res = built
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["synthetic"] is True
    assert meta["data_date"] == "2026-10-27"
    assert meta["months"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert meta["months_with_data"] == meta["months"]
    assert meta["licence"] == "CC BY 4.0"
    assert "OpenDOSM" in meta["source"]
    assert len(meta["areas"]) == 5
    assert meta["geo"] is None
    assert meta["proof"] is None        # the sample has only 7 of the 10 proof items
    assert isinstance(meta["stats"]["prices_set_aside"], int)
    slug = meta["areas"]["Selangor"][0]["slug"]
    d = json.loads((out / "d" / f"{slug}.json").read_text(encoding="utf-8"))
    assert d["premises"] and d["prices"] and d["history"]
    # No location files: every shop is "location unknown" and there is no centre.
    assert d["centre"] is None and "geo" not in meta["areas"]["Selangor"][0]
    assert all(p[4:] == [None, None, "district"] for p in d["premises"])
    # Every price row points at a real premise index, has a positive price, and is fresh enough.
    for rows in d["prices"].values():
        for idx, price, days in rows:
            assert 0 <= idx < len(d["premises"])
            assert price > 0
            assert 0 <= days <= bd.MAX_PRICE_AGE_DAYS
    # Every listed shop has at least one price.
    assert {r[0] for rows in d["prices"].values() for r in rows} == set(range(len(d["premises"])))
    # The injected 40x outlier must not survive.
    nat = meta["national_history"]
    for series in nat.values():
        vals = [v for v in series if v is not None]
        assert max(vals) < min(vals) * 3
    assert d["feed"]["rises"] and d["feed"]["rises"][0][1] > 0
    # The % in each feed matches the RM medians shown, and the feed is sorted by it.
    for feed in (meta["national_feed"], d["feed"]):
        for _code, pct, n, prev, cur in feed["rises"] + feed["falls"]:
            assert pct == round((cur / prev - 1) * 100, 1)
        assert [r[1] for r in feed["rises"]] == sorted((r[1] for r in feed["rises"]), reverse=True)
        assert [r[1] for r in feed["falls"]] == sorted(r[1] for r in feed["falls"])
    # Sizes stay small, and every JSON has a .js twin.
    assert max(res["sizes"].values()) < 200 * 1024
    for p in out.rglob("*.json"):
        js = p.with_suffix(".js").read_text(encoding="utf-8")
        assert js.startswith("(window.PCDATA=window.PCDATA||{})[")


def test_items_carry_national_shop_counts(built):
    _, out, _ = built
    meta = read_json(out / "meta.json")
    assert meta["item_cols"] == ["item_code", "item", "unit", "item_group", "item_category", "shops"]
    assert all(len(r) == 6 for r in meta["items"])
    counted = {}
    for areas in meta["areas"].values():
        for a in areas:
            for code, rows in district_file(out, a["slug"])["prices"].items():
                counted[int(code)] = counted.get(int(code), 0) + len(rows)
    assert {r[0]: r[5] for r in meta["items"]} == {r[0]: counted.get(r[0], 0) for r in meta["items"]}
    assert sum(r[5] for r in meta["items"]) > 0


def test_shop_months_table_shape():
    sub = pd.DataFrame([
        ("2026-08", 11, 1, 5.0), ("2026-09", 11, 1, 5.5), ("2026-10", 11, 1, 6.0),
        ("2026-10", 12, 1, 4.0), ("2026-09", 12, 2, 3.0),
        ("2026-10", 99, 1, 1.0),  # shop without a recent price: left out
        ("2026-07", 11, 1, 9.9),  # outside the 3 months: left out
    ], columns=["month", "premise_code", "item_code", "price"])
    out = bd.shop_months_table(sub, {11: 0, 12: 1}, ["2026-08", "2026-09", "2026-10"])
    assert out[1] == [[0, 5.0, 5.5, 6.0], [1, None, None, 4.0]]
    assert out[2] == [[1, None, 3.0, None]]


def test_same_pack_months_leaves_out_other_pack_sizes():
    pm = pd.DataFrame([
        ("2026-09", 1, 7, 30.0), ("2026-09", 2, 7, 30.0), ("2026-09", 3, 7, 11.0), ("2026-09", 4, 7, 29.0),
        ("2026-10", 1, 7, 30.0), ("2026-10", 2, 7, 12.0), ("2026-10", 3, 7, 30.0), ("2026-10", 4, 7, 80.0),
        ("2026-10", 1, 8, 3.0), ("2026-10", 2, 8, 3.0), ("2026-10", 3, 8, 1.2),      # 1.2: exactly 0.4x
        ("2026-09", 1, 9, 1.0), ("2026-09", 2, 9, 1.0), ("2026-09", 3, 9, 1.0),
        ("2026-10", 1, 9, 3.0), ("2026-10", 2, 9, 3.0), ("2026-10", 3, 9, 3.0), ("2026-10", 4, 9, 3.0),  # x3
        ("2026-09", 1, 10, 5.0), ("2026-09", 2, 10, 5.0), ("2026-09", 4, 10, 5.0),
        ("2026-10", 1, 10, 5.0), ("2026-10", 2, 10, 5.0), ("2026-10", 4, 10, 5.0),  # RM5.00 twice, then RM1.90
        ("2026-08", 1, 7, 30.0),                                                    # not a month asked for
    ], columns=["month", "premise_code", "item_code", "price"])
    # Latest prices set aside: shop 4's item 7 and item 10 in October, shop 1's item 9 in November.
    aside = pd.DataFrame({"premise_code": [4, 4, 1], "item_code": [7, 10, 9], "price": [80.0, 1.9, 9.9],
                          "date": pd.to_datetime(["2026-10-20", "2026-10-25", "2026-11-02"])})
    out = bd.same_pack_months(pm, aside, ["2026-09", "2026-10"])
    got = sorted(map(tuple, out[["item_code", "month", "premise_code", "price"]].values.tolist()))
    # Item 7: shop 3's September price is below 0.4x that month's median and shop 4's October price is
    # above 2.5x; shop 2's RM12 is exactly 0.4x the October median. Shop 4's latest price was set aside,
    # but only its October is left out: its September price is the right pack.
    # Item 9: each month has its own median, so a price that triples for everyone is kept; shop 1's
    # set-aside price is from November, which is not asked for, so its months stay.
    # Item 10: shop 4's October median (RM5.00) is right on the month's median, but that month
    # holds the set-aside RM1.90, so it may mix in the other pack: left out.
    assert got == [(7, "2026-09", 1, 30.0), (7, "2026-09", 2, 30.0), (7, "2026-09", 4, 29.0),
                   (7, "2026-10", 1, 30.0), (7, "2026-10", 2, 12.0), (7, "2026-10", 3, 30.0),
                   (8, "2026-10", 1, 3.0), (8, "2026-10", 2, 3.0), (8, "2026-10", 3, 1.2),
                   (9, "2026-09", 1, 1.0), (9, "2026-09", 2, 1.0), (9, "2026-09", 3, 1.0),
                   (9, "2026-10", 1, 3.0), (9, "2026-10", 2, 3.0), (9, "2026-10", 3, 3.0), (9, "2026-10", 4, 3.0),
                   (10, "2026-09", 1, 5.0), (10, "2026-09", 2, 5.0), (10, "2026-09", 4, 5.0),
                   (10, "2026-10", 1, 5.0), (10, "2026-10", 2, 5.0)]
    # Nothing set aside: only the limits apply, so shop 4's October price for item 10 comes back.
    out = bd.same_pack_months(pm, aside.iloc[:0], ["2026-09", "2026-10"])
    assert sorted(map(tuple, out[["item_code", "month", "premise_code", "price"]].values.tolist())) == \
        sorted(got + [(10, "2026-10", 4, 5.0)])


def test_same_pack_months_holds_a_set_aside_shop_to_0_8x_1_25x():
    # Shops 1-7 set each month's median: RM0.40 for item 11 in September, RM1.88 for item 12 in October.
    pm = pd.DataFrame([("2026-09", s, 11, 0.40) for s in range(1, 8)] + [
        ("2026-09", 8, 11, 0.32), ("2026-09", 9, 11, 0.31), ("2026-09", 10, 11, 0.31)] + [
        ("2026-10", s, 12, 1.88) for s in range(1, 8)] + [
        ("2026-10", 8, 12, 2.35), ("2026-10", 9, 12, 2.36), ("2026-10", 10, 12, 2.36),
    ], columns=["month", "premise_code", "item_code", "price"])
    # Latest prices from November (not a month asked for) set aside: shops 8 and 9 for both items,
    # shop 10 for item 11 only.
    aside = pd.DataFrame({"premise_code": [8, 9, 10, 8, 9], "item_code": [11, 11, 11, 12, 12],
                          "price": [0.1] * 3 + [9.9] * 2, "date": pd.to_datetime(["2026-11-02"] * 5)})
    out = bd.same_pack_months(pm, aside, ["2026-09", "2026-10"])
    got = {(r.item_code, r.premise_code): r.price for r in out.itertuples()}
    # Their other months must be within 0.8x-1.25x of the month's median, limits included (in floating
    # point 0.32 / 0.40 is 0.7999999999999999 and 2.35 / 1.88 is 1.2500000000000002): shop 8 stays at
    # 0.32 and 2.35, shop 9's 0.31 and 2.36 and shop 10's 0.31 go. Shop 10's item 12 was not set aside,
    # so its 2.36 (1.26x) only needs the usual 0.4x-2.5x.
    assert got == {**{(11, s): 0.40 for s in range(1, 8)}, (11, 8): 0.32,
                   **{(12, s): 1.88 for s in range(1, 8)}, (12, 8): 2.35, (12, 10): 2.36}
    # Nothing set aside: every price is within 0.4x-2.5x, so all come back.
    assert len(bd.same_pack_months(pm, aside.iloc[:0], ["2026-09", "2026-10"])) == len(pm)


def test_build_writes_shop_months(built):
    _, out, _ = built
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    slug = meta["areas"]["Selangor"][0]["slug"]
    d = json.loads((out / "d" / f"{slug}.json").read_text(encoding="utf-8"))
    assert d["avg_months"] == ["2026-08", "2026-09", "2026-10"]
    assert d["shop_months"]
    for rows in d["shop_months"].values():
        for r in rows:
            assert 0 <= r[0] < len(d["premises"])
            assert len(r) == 4
            assert any(v is not None and v > 0 for v in r[1:])


def test_build_lists_only_shops_with_fresh_prices(tmp_path):
    raw = write_raw(tmp_path / "raw", [(1, "BERAS CONTOH", "10kg"), (2, "GULA CONTOH", "1kg")],
                    [(c, "Selangor", "Petaling") for c in (11, 12, 13, 14, 15)],
                    {"2026-09": [("2026-09-10", 12, 1, 10.0),    # shop 12: only a 40-day-old price
                                 ("2026-09-15", 11, 1, 10.2),
                                 ("2026-09-25", 13, 2, 3.0),
                                 ("2026-09-20", 14, 2, 3.1),     # exactly 30 days old: kept
                                 ("2026-09-19", 15, 2, 3.2)],    # 31 days old: shop 15 drops out
                     "2026-10": [("2026-10-20", 11, 1, 10.4), ("2026-10-05", 13, 1, 10.6)]})
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    d = district_file(tmp_path / "out", "selangor--petaling")
    assert d["data_date"] == "2026-10-20"
    assert [p[0] for p in d["premises"]] == [11, 13, 14]
    assert d["prices"] == {"1": [[0, 10.4, 0], [1, 10.6, 15]], "2": [[1, 3.0, 25], [2, 3.1, 30]]}
    assert meta["stats"]["premises"] == 3
    # History and the 3-month averages still use every price, old ones included.
    assert d["history"] == {"1": [10.1, 10.5], "2": [3.1, None]}
    assert d["shop_months"]["1"] == [[0, 10.2, 10.4], [1, None, 10.6]]


def test_build_needs_at_least_3_months(tmp_path):
    # Data date 2027-03-01: shop 1's price from 2027-01-30 is 30 days old, so the January file is needed.
    raw = write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")], [(c, "Kedah", "Sik") for c in (1, 2, 3)],
                    {"2027-01": [("2027-01-30", 1, 1, 3.0), ("2027-01-29", 2, 1, 3.0)],
                     "2027-02": [("2027-02-10", 3, 1, 3.1)],
                     "2027-03": [("2027-03-01", 3, 1, 3.2)]})
    bd.build(raw, tmp_path / "out", months_keep=3, **NO_GEO)
    d = district_file(tmp_path / "out", "kedah--sik")
    assert d["data_date"] == "2027-03-01" and [p[0] for p in d["premises"]] == [1, 3]
    assert d["prices"] == {"1": [[0, 3.0, 30], [1, 3.2, 0]]}
    # Fewer months would never read the January file: refused with a clear message, nothing written.
    for n in (2, 1, 0):
        with pytest.raises(SystemExit, match=f"at least 3 \\(got {n}\\)"):
            bd.build(raw, tmp_path / f"out{n}", months_keep=n, **NO_GEO)
        assert not (tmp_path / f"out{n}").exists()
    with pytest.raises(SystemExit, match="--months must be at least 3"):
        bd.main(["--raw", str(raw), "--out", str(tmp_path / "cli"), "--months", "2", "--geo", ""])
    assert not (tmp_path / "cli").exists()


def test_build_sets_aside_pack_size_errors(tmp_path):
    shops = list(range(21, 30))
    oil = [30.0, 30.5, 29.5, 31.0, 30.0, 75.0, 12.0, 80.0, 11.0]   # 80 and 11: another pack size
    rows = [("2026-10-10", s, 1, p) for s, p in zip(shops, oil)] + [("2026-10-10", s, 2, 3.0) for s in shops]
    raw = write_raw(tmp_path / "raw", [(1, "MINYAK CONTOH", "5kg"), (2, "GULA CONTOH", "1kg")],
                    [(c, "Johor", "Muar") for c in shops], {"2026-10": rows})
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    d = district_file(tmp_path / "out", "johor--muar")
    assert meta["stats"]["prices_set_aside"] == 2
    codes = [p[0] for p in d["premises"]]
    assert codes == shops                  # still listed: they have another fresh price
    shown = {codes[i]: price for i, price, _ in d["prices"]["1"]}
    assert set(shown) == set(shops) - {28, 29}
    assert shown[26] == 75.0 and shown[27] == 12.0
    assert {r[0]: r[5] for r in meta["items"]} == {1: 7, 2: 9}
    # History still uses every price; "cheapest on average" leaves out the two set-aside prices.
    assert meta["national_history"]["1"] == [30.0]
    assert [(codes[r[0]], r[1]) for r in d["shop_months"]["1"]] == list(zip(shops, oil))[:7]


def test_cheapest_on_average_compares_the_same_pack_only(tmp_path):
    shops = list(range(21, 30))
    oil = {s: [p] * 3 for s, p in zip(shops, [30.0, 30.5, 29.5, 31.0, 30.0, 30.2])}
    oil[27] = [29.8, 29.8, 11.0]   # now a 1 kg pack under the 5 kg code: its latest price is set aside
    oil[28] = [11.0, 30.4, 30.4]   # the 1 kg pack in August only
    oil[29] = [11.0, None, None]   # the 1 kg pack, and no fresh price, so the latest-price check never sees it
    sugar = {s: [3.0, 3.0, 3.0] for s in shops}
    sugar[25] = [3.0, 1.2, 1.2]    # exactly 0.4x: kept
    months = {}
    for k, ym in enumerate(["2026-08", "2026-09", "2026-10"]):
        months[ym] = ([(f"{ym}-10", s, 1, v[k]) for s, v in oil.items() if v[k] is not None]
                      + [(f"{ym}-10", s, 2, v[k]) for s, v in sugar.items()])
    raw = write_raw(tmp_path / "raw", [(1, "MINYAK CONTOH", "5kg"), (2, "GULA CONTOH", "1kg")],
                    [(c, "Johor", "Muar") for c in shops], months)
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    d = district_file(tmp_path / "out", "johor--muar")
    assert meta["stats"]["prices_set_aside"] == 1
    codes = [p[0] for p in d["premises"]]
    assert codes == shops
    assert {codes[i] for i, _, _ in d["prices"]["1"]} == set(shops) - {27, 29}
    assert {codes[i]: p for i, p, _ in d["prices"]["2"]}[25] == 1.2
    avg = {code: {codes[r[0]]: r[1:] for r in rows} for code, rows in d["shop_months"].items()}
    assert d["avg_months"] == ["2026-08", "2026-09", "2026-10"]
    # Shop 27 keeps August and September but not October, the month of its set-aside latest price;
    # shop 28's August price is another pack, and shop 29 has only another pack's price. Without
    # these checks, these three would look cheapest on average.
    assert avg["1"] == {s: v for s, v in oil.items() if s < 27} | {27: [29.8, 29.8, None], 28: [None, 30.4, 30.4]}
    assert avg["2"] == sugar
    # History still uses every price, the other pack sizes included.
    assert d["history"]["1"] == meta["national_history"]["1"] == [30.0, 30.1, 30.1]


def test_cheapest_on_average_keeps_right_pack_months_of_a_set_aside_shop(tmp_path):
    # 20 shops sell 5 kg oil at RM30.00-31.00 from July to October. November has one row only, so it
    # is not a month with data: the averages use August to October.
    shops = list(range(1, 21))
    oil = {s: [30.0 + (s % 3) * 0.5] * 4 for s in shops}
    oil[18] = [11.0, 11.0, 11.0, 30.0]   # a 1 kg pack under the 5 kg code until September
    oil[19] = [11.0] * 4                 # always the 1 kg pack: its October latest price is set aside
    months = {ym: [(f"{ym}-12", s, 1, v[k]) for s, v in oil.items()] + [(f"{ym}-12", s, 2, 3.0) for s in shops]
              for k, ym in enumerate(["2026-07", "2026-08", "2026-09", "2026-10"])}
    months["2026-11"] = [("2026-11-01", 20, 1, 11.0)]   # shop 20's latest oil price: the 1 kg pack
    raw = write_raw(tmp_path / "raw", [(1, "MINYAK CONTOH", "5kg"), (2, "GULA CONTOH", "1kg")],
                    [(c, "Johor", "Muar") for c in shops], months)
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    d = district_file(tmp_path / "out", "johor--muar")
    assert meta["months_with_data"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert d["data_date"] == "2026-11-01" and d["avg_months"] == ["2026-08", "2026-09", "2026-10"]
    assert meta["stats"]["prices_set_aside"] == 2
    codes = [p[0] for p in d["premises"]]
    assert codes == shops and {codes[i] for i, _, _ in d["prices"]["1"]} == set(shops) - {19, 20}
    avg = {codes[r[0]]: r[1:] for r in d["shop_months"]["1"]}
    # Shop 20's latest price is another pack, but its August-October prices are the right pack: kept.
    assert avg[20] == [31.0, 31.0, 31.0]
    # Months of another pack are still left out by the monthly limits, latest price set aside or not.
    assert avg == {s: v[1:] for s, v in oil.items() if s not in (18, 19)} | {18: [None, None, 30.0]}


def cheapest_on_average(d, codes):
    """Shops as the app ranks them for "cheapest on average" (Basket.averageRank), one of each item:
    each item at the mean of the months the shop priced it; most items first, then the lowest total.
    Returns [(premise_code, total, items)]."""
    rows = {}
    for code in codes:
        for r in d["shop_months"].get(str(code), []):
            vals = [v for v in r[1:] if v is not None]
            if vals:
                total, have = rows.get(r[0], (0, 0))
                rows[r[0]] = (total + math.floor(sum(vals) / len(vals) * 100 + 0.5), have + 1)
    ranked = sorted(rows.items(), key=lambda kv: (-kv[1][1], kv[1][0], kv[0]))
    return [(d["premises"][i][0], total / 100, have) for i, (total, have) in ranked]


def test_cheapest_on_average_holds_a_set_aside_shop_to_its_usual_price(tmp_path):
    # 20 shops sell 5 kg oil at RM30 and sugar at RM3 from July to October. Shop 20 sells a 2 kg pack
    # under the 5 kg code: RM12.50 (0.417x, within the usual 0.4x-2.5x) until September, and RM11.80
    # (0.393x) in October, its latest price, which is set aside.
    shops = list(range(1, 21))
    months = {ym: [(f"{ym}-05", s, 1, 30.0) for s in shops[:-1]] + [(f"{ym}-05", 20, 1, p20)]
              + [(f"{ym}-05", s, 2, 3.0) for s in shops]
              for ym, p20 in zip(["2026-07", "2026-08", "2026-09", "2026-10"], [12.5, 12.5, 12.5, 11.8])}
    raw = write_raw(tmp_path / "raw", [(1, "MINYAK CONTOH", "5kg"), (2, "GULA CONTOH", "1kg")],
                    [(c, "Johor", "Muar") for c in shops], months)
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    d = district_file(tmp_path / "out", "johor--muar")
    assert meta["stats"]["prices_set_aside"] == 1 and d["avg_months"] == ["2026-08", "2026-09", "2026-10"]
    codes = [p[0] for p in d["premises"]]
    assert codes == shops and {codes[i] for i, _, _ in d["prices"]["1"]} == set(shops) - {20}
    # A shop whose latest price was set aside keeps its other months only within 0.8x-1.25x of the
    # month's median, so shop 20's RM12.50 months are left out too.
    assert {codes[r[0]]: r[1:] for r in d["shop_months"]["1"]} == {s: [30.0] * 3 for s in shops[:-1]}
    # So "cheapest on average" names shop 1 at RM33.00 (the tie goes to the first shop), not shop 20
    # at RM15.50 (RM12.50 oil and RM3.00 sugar); shop 20 has sugar only.
    ranked = cheapest_on_average(d, [1, 2])
    assert ranked[0] == (1, 33.0, 2) and ranked[-1] == (20, 3.0, 1)


def test_build_ignores_a_nearly_empty_new_month(tmp_path):
    raw = tmp_path / "raw"
    ms.make(raw, months=4, end="2026-10", last_day=27, fmt="csv")
    bd.build(raw, tmp_path / "a", **NO_GEO)
    # A new month file with just 10 rows on its first day.
    codes = pd.read_csv(raw / "lookup_premise.csv")["premise_code"].head(10)
    prices([("2026-11-01", int(c), 1, 10.0) for c in codes]).to_csv(raw / "pricecatcher_2026-11.csv", index=False)
    bd.build(raw, tmp_path / "b", **NO_GEO)
    ma, mb = read_json(tmp_path / "a" / "meta.json"), read_json(tmp_path / "b" / "meta.json")
    assert mb["months"] == ma["months"] + ["2026-11"]
    assert mb["months_with_data"] == ma["months_with_data"] == ma["months"]
    assert mb["data_date"] == "2026-11-01"
    assert mb["national_feed"] == ma["national_feed"]        # still October against September
    assert all(len(v) == 5 for v in mb["national_history"].values())
    slug = mb["areas"]["Selangor"][0]["slug"]
    db = district_file(tmp_path / "b", slug)
    assert db["avg_months"] == ["2026-08", "2026-09", "2026-10"]
    assert db["feed"] == district_file(tmp_path / "a", slug)["feed"]


def test_build_skips_an_empty_month_in_the_middle(tmp_path):
    shops = [31, 32, 33]
    raw = write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")], [(c, "Kedah", "Sik") for c in shops],
                    {"2026-08": [("2026-08-20", s, 1, 3.0) for s in shops],
                     "2026-09": [],
                     "2026-10": [("2026-10-20", s, 1, 3.3) for s in shops]})
    bd.build(raw, tmp_path / "out", **NO_GEO)
    meta = read_json(tmp_path / "out" / "meta.json")
    assert meta["months"] == ["2026-08", "2026-09", "2026-10"]
    assert meta["months_with_data"] == ["2026-08", "2026-10"]
    assert meta["national_history"] == {"1": [3.0, None, 3.3]}
    assert meta["national_feed"]["rises"] == [[1, 10.0, 3, 3.0, 3.3]]    # October against August
    d = district_file(tmp_path / "out", "kedah--sik")
    assert d["avg_months"] == ["2026-08", "2026-10"]
    assert d["shop_months"]["1"] == [[0, 3.0, 3.3], [1, 3.0, 3.3], [2, 3.0, 3.3]]


def test_build_state_and_top_items_filters(tmp_path):
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="csv")
    res = bd.build(raw, tmp_path / "out", state="Selangor", top_items=20)
    meta = json.loads((tmp_path / "out" / "meta.json").read_text(encoding="utf-8"))
    assert list(meta["areas"]) == ["Selangor"]
    assert len(meta["items"]) == 20
    assert res["stats"]["districts"] == 3


def test_filtered_build_keeps_other_district_files(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    ms.make(raw, months=2, fmt="csv")
    bd.build(raw, out, **NO_GEO)
    full = sorted(p.name for p in (out / "d").iterdir())
    assert len(full) == 2 * 7
    stale = [out / "d" / "old--gone.json", out / "d" / "old--gone.js"]
    for p in stale:
        p.write_text("{}", encoding="utf-8")
    bd.build(raw, out, state="Selangor", **NO_GEO)
    bd.build(raw, out, district="Klang", **NO_GEO)
    assert sorted(p.name for p in (out / "d").iterdir()) == sorted(full + [p.name for p in stale])
    # A full build removes files of districts it did not build.
    bd.build(raw, out, **NO_GEO)
    assert sorted(p.name for p in (out / "d").iterdir()) == full


def test_parquet_and_csv_give_same_result(tmp_path):
    pytest.importorskip("pyarrow")
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="both")
    a = bd.build(raw, tmp_path / "a")
    for p in raw.glob("*.parquet"):
        p.rename(p.with_suffix(".parquet.off"))
    b = bd.build(raw, tmp_path / "b")
    assert a["stats"] == b["stats"]


@pytest.fixture(scope="module")
def proof_build(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw_proof")
    out = tmp_path_factory.mktemp("out_proof")
    ms.make(raw, months=3, end="2026-10", last_day=27, fmt="csv")
    # Give sample items the real names the proof list looks for, plus a second onion to choose from.
    items = pd.read_csv(raw / "lookup_item.csv")
    rename = {105: "GULA PUTIH BERTAPIS KASAR (PELBAGAI JENAMA)", 113: "MINYAK MASAK TULEN CAP CONTOH",
              201: "KRIMER MANIS CAP CONTOH", 23: "BAWANG BESAR IMPORT (CHINA)"}
    items["item"] = [rename.get(c, n) for c, n in zip(items["item_code"], items["item"])]
    items.to_csv(raw / "lookup_item.csv", index=False)
    bd.build(raw, out, **NO_GEO)
    return out


def test_build_proof_figure(proof_build):
    out = proof_build
    meta = read_json(out / "meta.json")
    proof = meta["proof"]
    assert proof["labels"] == [g[0] for g in bd.PROOF_GROUPS]
    shops = {r[0]: r[5] for r in meta["items"]}
    onion = 22 if shops[22] >= shops[23] else 23
    assert proof["items"] == [1, 21, 102, 105, 113, onion, 30, 28, 9, 201]
    assert proof["by_district"] and proof["example"] in proof["by_district"]
    n_areas = sum(len(v) for v in meta["areas"].values())
    seen = 0
    for areas in meta["areas"].values():
        for a in areas:
            d = district_file(out, a["slug"])
            totals = {}
            for code in proof["items"]:
                for idx, price, _ in d["prices"].get(str(code), []):
                    totals.setdefault(idx, []).append(round(price * 100))
            # As the app adds up the list: whole sen; the median of an even count rounded half up.
            full = sorted(sum(v) for v in totals.values() if len(v) == len(proof["items"]))
            if len(full) < 3:
                assert a["slug"] not in proof["by_district"]
                continue
            seen += 1
            cheapest, typical, n = proof["by_district"][a["slug"]]
            h = len(full) // 2
            assert n == len(full)
            assert cheapest == full[0] / 100
            assert typical == (full[h] if n % 2 else math.floor((full[h - 1] + full[h]) / 2 + 0.5)) / 100
    assert seen == len(proof["by_district"]) and seen <= n_areas
    best = max(v[2] for v in proof["by_district"].values())
    assert proof["by_district"][proof["example"]][2] == best


def test_build_adds_locations(tmp_path, built):
    raw, _, _ = built
    out = tmp_path / "out"
    bd.build(raw, out, months_keep=12, **NO_GEO)
    sel = {a["district"]: a for a in read_json(out / "meta.json")["areas"]["Selangor"]}
    pet = [p[0] for p in district_file(out, sel["Petaling"]["slug"])["premises"]]
    klang = [p[0] for p in district_file(out, sel["Klang"]["slug"])["premises"]]

    geo = tmp_path / "premise_geo.csv"
    with geo.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["premise_code", "lat", "lon", "precision", "query", "district", "state", "postcode",
                    "tries", "last_tried", "extra"])
        w.writerow([pet[0], 3.0, 101.5, "address", "x", "Petaling", "Selangor", "40100", 1, "2026-11-01", "?"])
        w.writerow([pet[1], 3.02, 101.52, "name", "x", "Petaling", "Selangor", "40100", 1, "2026-11-01", ""])
        w.writerow([pet[2], 3.04, 101.5, "town", "x", "Petaling", "Selangor", "40150", 2, "2026-11-01", ""])
        w.writerow([pet[3], 3.3, 101.7, "district", "x", "Petaling", "Selangor", "", 3, "2026-11-01", ""])
        w.writerow([pet[4], "", "", "postcode", "x", "Petaling", "Selangor", "46000", 3, "2026-11-01", ""])
        w.writerow([pet[5], 3.2, 101.2, "state", "x", "Petaling", "Selangor", "", 3, "", ""])
        w.writerow([klang[0], 3.5, 101.9, "district", "x", "Klang", "Selangor", "", 3, "", ""])
    dgeo = tmp_path / "district_geo.csv"
    dgeo.write_text("key,lat,lon\nSelangor|Petaling,3.6,101.1\nSelangor|Klang,3.04,101.45\n"
                    "Selangor|Hulu Langat,3.1,101.8\nSelangor|Sepang,3.1001,101.8001\n", encoding="utf-8")
    pcs = tmp_path / "postcodes_my.csv"
    pcs.write_text("postcode,place,state,lat,lon,accuracy\n40100,Shah Alam,Selangor,3.07,101.52,4\n"
                   "01000,Kangar,Perlis,6.44,100.2,4\n", encoding="utf-8")
    bd.build(raw, out, months_keep=12, geo_path=geo, postcodes_path=pcs, district_geo_path=dgeo)
    meta = read_json(out / "meta.json")
    sel = {a["district"]: a for a in meta["areas"]["Selangor"]}

    # Petaling: 3 located shops, so the centre is their median (not the district_geo.csv point).
    d = district_file(out, sel["Petaling"]["slug"])
    assert d["premise_cols"][4:] == ["lat", "lon", "geo_precision"]
    assert d["centre"] == [3.02, 101.5]
    by_code = {p[0]: p[4:] for p in d["premises"]}
    assert by_code[pet[0]] == [3.0, 101.5, "address"]
    assert by_code[pet[1]] == [3.02, 101.52, "name"]
    assert by_code[pet[2]] == [3.04, 101.5, "town"]
    for c in pet[3:]:   # district level, blank point, unknown precision or no row: location unknown
        assert by_code[c] == [3.02, 101.5, "district"]
    furthest = max(bd.km(3.02, 101.5, *p) for p in [(3.0, 101.5), (3.02, 101.52), (3.04, 101.5)])
    assert sel["Petaling"]["geo"] == [3.02, 101.5, round(furthest + 0.5, 1)]

    # Klang: no located shop, so its own district_geo.csv point and a 3 km radius.
    dk = district_file(out, sel["Klang"]["slug"])
    assert dk["centre"] == [3.04, 101.45] and sel["Klang"]["geo"] == [3.04, 101.45, 3.0]
    assert {tuple(p[4:]) for p in dk["premises"]} == {(3.04, 101.45, "district")}

    # Hulu Langat: its point is shared with Sepang (a fallback guess) and no shop has a point.
    dh = district_file(out, sel["Hulu Langat"]["slug"])
    assert dh["centre"] is None and "geo" not in sel["Hulu Langat"]
    assert {tuple(p[4:]) for p in dh["premises"]} == {(None, None, "district")}

    g = meta["geo"]
    assert g["shops_located"] == 3
    assert g["precision"] == {"name": 1, "address": 1, "postcode": 0, "town": 1,
                              "district": meta["stats"]["premises"] - 3}
    # 40100 has 2 shops found by address or name that agree (pet[0], pet[1]): it is placed at their
    # median, 6.8 km from its GeoNames point. 01000 keeps its GeoNames point.
    assert g["postcodes"] == {"01000": [6.44, 100.2], "40100": [3.01, 101.51]}
    assert g["postcode_checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0}
    assert g["postcode_source"] == "GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap"
    assert g["postcode_source_url"] == "https://www.geonames.org/"
    assert "OpenStreetMap" in g["source"] and g["source_url"].startswith("https://www.openstreetmap.org/")

    # Without the GeoNames file: postcodes from shops found by name or address (median), not by town.
    bd.build(raw, out, months_keep=12, geo_path=geo, postcodes_path=tmp_path / "missing.csv", district_geo_path=dgeo)
    g = read_json(out / "meta.json")["geo"]
    assert g["postcodes"] == {"40100": [3.01, 101.51]}
    assert g["postcode_source"] == "shop addresses" and "postcode_source_url" not in g
    assert "postcode_checks" not in g


def test_build_never_uses_a_shared_district_point(tmp_path):
    kl, old = "W.P. Kuala Lumpur", (3.1456, 101.6974)   # one point for several KL districts: a fallback guess
    shops = {"Cheras": [1, 2, 3, 4], "Kepong": [11, 12], "Batu": [21, 22], "Titiwangsa": [31],
             "Setiawangsa": [41]}
    raw = write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")],
                    [(c, kl, dist) for dist, cs in shops.items() for c in cs],
                    {"2026-10": [("2026-10-10", c, 1, 3.0) for cs in shops.values() for c in cs]})
    geo = tmp_path / "premise_geo.csv"
    geo.write_text("premise_code,lat,lon,precision,query,district,state,postcode,tries,last_tried\n"
                   "1,3.10,101.70,name,q,Cheras,KL,,1,\n2,3.12,101.72,address,q,Cheras,KL,,1,\n"
                   "3,3.14,101.71,town,q,Cheras,KL,,1,\n11,3.21,101.64,postcode,q,Kepong,KL,52100,1,\n"
                   + "".join(f"{c},{old[0]},{old[1]},district,q,D,KL,,1,\n" for c in (4, 12, 21, 22)),
                   encoding="utf-8")
    # The geocoder marks shared points shared=1, and the mark stays on a district left alone on an
    # old shared point (Titiwangsa); Setiawangsa moved to its own point.
    new_dgeo, old_dgeo = tmp_path / "district_geo.csv", tmp_path / "district_geo_old.csv"
    rows = [(d, *old, "1") for d in ("Cheras", "Kepong", "Batu")] + [("Titiwangsa", 3.17, 101.70, "1"),
                                                                      ("Setiawangsa", 3.20, 101.73, "")]
    new_dgeo.write_text("key,lat,lon,tries,shared\n" + "".join(f"{kl}|{d},{la},{lo},1,{s}\n" for d, la, lo, s in rows),
                        encoding="utf-8")
    old_dgeo.write_text("key,lat,lon\n" + "".join(f"{kl}|{d},{la},{lo}\n" for d, la, lo, _ in rows), encoding="utf-8")

    def build_with(dgeo):
        out = tmp_path / dgeo.stem
        bd.build(raw, out, geo_path=geo, postcodes_path=None, district_geo_path=dgeo)
        areas = {a["district"]: a for a in read_json(out / "meta.json")["areas"][kl]}
        files = {dist: district_file(out, a["slug"]) for dist, a in areas.items()}
        for dist, d in files.items():   # the area's geo always matches the file's centre
            assert areas[dist].get("geo", [None, None])[:2] == (d["centre"] or [None, None])
        return areas, files

    cheras = [(3.10, 101.70), (3.12, 101.72), (3.14, 101.71)]
    r_cheras = round(max(bd.km(3.12, 101.71, *p) for p in cheras) + 0.5, 1)
    for dgeo in (new_dgeo, old_dgeo):
        areas, files = build_with(dgeo)
        rows_of = {dist: {p[0]: p[4:] for p in d["premises"]} for dist, d in files.items()}
        # 3 located shops: their median; the unlocated shop 4 gets that centre, not the shared point.
        assert files["Cheras"]["centre"] == [3.12, 101.71] and areas["Cheras"]["geo"] == [3.12, 101.71, r_cheras]
        assert rows_of["Cheras"][4] == [3.12, 101.71, "district"]
        # 1 located shop: that shop, not a mix with the shared point that shop 12 carries.
        assert files["Kepong"]["centre"] == [3.21, 101.64] and areas["Kepong"]["geo"] == [3.21, 101.64, 1.0]
        assert rows_of["Kepong"] == {11: [3.21, 101.64, "postcode"], 12: [3.21, 101.64, "district"]}
        # No located shop: no centre and no area geo, although both shops carry the shared point.
        assert files["Batu"]["centre"] is None and "geo" not in areas["Batu"]
        assert rows_of["Batu"] == {21: [None, None, "district"], 22: [None, None, "district"]}
        assert files["Setiawangsa"]["centre"] == [3.2, 101.73] and areas["Setiawangsa"]["geo"] == [3.2, 101.73, 3.0]
    # Titiwangsa's point is shared by its mark; an older file without the column cannot tell, so it is used.
    areas, files = build_with(new_dgeo)
    assert files["Titiwangsa"]["centre"] is None and "geo" not in areas["Titiwangsa"]
    assert files["Titiwangsa"]["premises"][0][4:] == [None, None, "district"]
    areas, files = build_with(old_dgeo)
    assert files["Titiwangsa"]["centre"] == [3.17, 101.7] and areas["Titiwangsa"]["geo"] == [3.17, 101.7, 3.0]


def test_main_runs_with_location_files_switched_off(tmp_path, capsys):
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="csv")
    assert bd.main(["--raw", str(raw), "--out", str(tmp_path / "out"),
                    "--geo", "", "--postcodes", "", "--district-geo", ""]) == 0
    meta = read_json(tmp_path / "out" / "meta.json")
    assert meta["geo"] is None and meta["proof"] is None
    assert all(a.get("geo") is None for areas in meta["areas"].values() for a in areas)
    text = capsys.readouterr().out
    assert "prices set aside" in text and "proof figure: none" in text


def test_synthetic_build_skips_the_default_location_files(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="csv")
    prem = pd.read_csv(raw / "lookup_premise.csv").dropna()
    pet = [int(c) for c in prem.loc[prem["district"] == "Petaling", "premise_code"].head(3)]
    # These stand in for etl/geo/: real shops whose codes are the same as made-up shops.
    geo, dgeo, pcs = tmp_path / "premise_geo.csv", tmp_path / "district_geo.csv", tmp_path / "postcodes_my.csv"
    geo.write_text("premise_code,lat,lon,precision,query,district,state,postcode\n" + "".join(
        f"{c},3.0{i},101.5,address,x,Petaling,Selangor,40100\n" for i, c in enumerate(pet)), encoding="utf-8")
    dgeo.write_text("key,lat,lon\nSelangor|Petaling,3.1,101.6\nSelangor|Klang,3.04,101.45\n", encoding="utf-8")
    pcs.write_text("postcode,place,state,lat,lon,accuracy\n40100,Shah Alam,Selangor,3.07,101.52,4\n",
                   encoding="utf-8")
    monkeypatch.setattr(bd, "GEO_DEFAULT", geo)
    monkeypatch.setattr(bd, "DISTRICT_GEO_DEFAULT", dgeo)
    monkeypatch.setattr(bd, "POSTCODES_DEFAULT", pcs)

    def geo_of(out):
        meta = read_json(out / "meta.json")
        return meta["geo"], [a.get("geo") for areas in meta["areas"].values() for a in areas]

    # No paths given: the default files are not read, and a note says so.
    bd.build(raw, tmp_path / "a")
    g, areas = geo_of(tmp_path / "a")
    assert g is None and areas == [None] * 7
    assert "synthetic sample" in capsys.readouterr().out
    # Paths given (even the default ones) are used.
    bd.build(raw, tmp_path / "b", geo_path=bd.GEO_DEFAULT, postcodes_path=pcs, district_geo_path=dgeo)
    g, areas = geo_of(tmp_path / "b")
    assert g["shops_located"] == 3
    assert g["postcode_source"] == "GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap"
    assert sum(a is not None for a in areas) == 2      # Petaling (its shops) and Klang (district point)
    assert "synthetic sample" not in capsys.readouterr().out
    assert bd.main(["--raw", str(raw), "--out", str(tmp_path / "c"), "--geo", str(geo)]) == 0
    g, areas = geo_of(tmp_path / "c")
    assert g["shops_located"] == 3 and g["postcode_source"] == "shop addresses"   # no --postcodes
    assert sum(a is not None for a in areas) == 1      # no --district-geo: Petaling only
    assert "synthetic sample" in capsys.readouterr().out
    # Real data (no SYNTHETIC.txt) reads the default files as before.
    real = tmp_path / "real"
    shutil.copytree(raw, real, ignore=shutil.ignore_patterns("SYNTHETIC.txt"))
    bd.build(real, tmp_path / "d")
    assert geo_of(tmp_path / "d") == geo_of(tmp_path / "b")
    assert "synthetic sample" not in capsys.readouterr().out
