"""Tests for the ETL. Run from grocery-prototype/:  python -m pytest etl/tests -q"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_dataset as bd  # noqa: E402
import fetch_pricecatcher as fp  # noqa: E402
import make_synthetic as ms  # noqa: E402
import geocode_premises as gp  # noqa: E402


def prices(rows):
    return pd.DataFrame(rows, columns=["date", "premise_code", "item_code", "price"])


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
        # shop 1: 10 -> 11 (+10%), shop 2: 10 -> 12 (+20%), shop 3: only in Oct
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
    assert row["pct"] == pytest.approx(15.0)  # median of +10% and +20%
    assert bd.mom_changes(pm, "2026-10", "2026-09", min_n=3).empty


def test_history_table_aligns_months_and_fills_gaps():
    pm = pd.DataFrame({"month": ["2026-08", "2026-10"], "premise_code": [1, 1],
                       "item_code": [5, 5], "price": [2.0, 2.5]})
    h = bd.history_table(pm, ["2026-08", "2026-09", "2026-10"])
    assert h == {5: [2.0, None, 2.5]}


def test_slugify():
    assert bd.slugify("W.P. Kuala Lumpur", "Kuala Lumpur") == "w-p-kuala-lumpur--kuala-lumpur"
    assert bd.slugify("Pulau Pinang", "Seberang Perai Utara") == "pulau-pinang--seberang-perai-utara"


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
    res = bd.build(raw, out, months_keep=12, geo_path=None)
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
    assert meta["licence"] == "CC BY 4.0"
    assert "OpenDOSM" in meta["source"]
    assert len(meta["areas"]) == 5
    slug = meta["areas"]["Selangor"][0]["slug"]
    d = json.loads((out / "d" / f"{slug}.json").read_text(encoding="utf-8"))
    assert d["premises"] and d["prices"] and d["history"]
    # Every price row points at a real premise index, has a positive price, and is fresh enough.
    for rows in d["prices"].values():
        for idx, price, days in rows:
            assert 0 <= idx < len(d["premises"])
            assert price > 0
            assert 0 <= days <= 62
    # The injected 40x outlier must not survive.
    nat = meta["national_history"]
    for series in nat.values():
        vals = [v for v in series if v is not None]
        assert max(vals) < min(vals) * 3
    assert d["feed"]["rises"] and d["feed"]["rises"][0][1] > 0
    # Sizes stay small, and every JSON has a .js twin.
    assert max(res["sizes"].values()) < 200 * 1024
    for p in out.rglob("*.json"):
        js = p.with_suffix(".js").read_text(encoding="utf-8")
        assert js.startswith("(window.PCDATA=window.PCDATA||{})[")


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


def test_build_state_and_top_items_filters(tmp_path):
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="csv")
    res = bd.build(raw, tmp_path / "out", state="Selangor", top_items=20)
    meta = json.loads((tmp_path / "out" / "meta.json").read_text(encoding="utf-8"))
    assert list(meta["areas"]) == ["Selangor"]
    assert len(meta["items"]) == 20
    assert res["stats"]["districts"] == 3


def test_parquet_and_csv_give_same_result(tmp_path):
    pytest.importorskip("pyarrow")
    raw = tmp_path / "raw"
    ms.make(raw, months=2, fmt="both")
    a = bd.build(raw, tmp_path / "a")
    for p in raw.glob("*.parquet"):
        p.rename(p.with_suffix(".parquet.off"))
    b = bd.build(raw, tmp_path / "b")
    assert a["stats"] == b["stats"]


# ---------- shop locations ("near me") ----------

def test_clean_address_keeps_street_drops_unit_numbers():
    assert gp.clean_address("NO 3 JALAN SS16/1,47500 SUBANG JAYA, SELANGOR DARUL EHSAN,") == \
        "JALAN SS16/1, SUBANG JAYA, SELANGOR DARUL EHSAN"
    assert gp.clean_address("LOT 4,5,6,PERSIARAN PERPADUAN USJ 1,47600 SUBANG JAYA, SELANGOR") == \
        "PERSIARAN PERPADUAN USJ 1, SUBANG JAYA, SELANGOR"
    assert gp.postcode_of("NO. 3,JALAN AEROBIK 13/43, SEKSYEN 13,40100 SHAH ALAM") == "40100"
    assert gp.postcode_of("TANJUNG MANIS BAZAAR,") == ""


def test_geocode_run_uses_cache_and_falls_back(tmp_path, built):
    _, out, _ = built
    calls = []
    centre = (3.07, 101.52)

    def fake(params):
        calls.append(params)
        q = params.get("q", "")
        if q.endswith(", Malaysia"):                 # district centre
            return [{"lat": centre[0], "lon": centre[1]}]
        if "far" in q.lower():
            return [{"lat": 6.4, "lon": 100.2}]       # 400 km away: must be ignored
        if len(calls) % 3 == 0:
            return [{"lat": centre[0] + 0.01, "lon": centre[1] + 0.01}]
        return []

    cache, dcache = tmp_path / "p.csv", tmp_path / "d.csv"
    st = gp.run(out, cache, dcache, gp.Geocoder(fetch=fake, delay=0), max_new=10_000)
    n_shops = st["shops"]
    assert st["looked_up"] == n_shops and st["missed"] == 0
    assert st["name"] + st["address"] + st["postcode"] + st["district"] == n_shops
    rows = list(csv.DictReader(cache.open(encoding="utf-8")))
    assert len(rows) == n_shops
    for r in rows:
        assert gp.km(float(r["lat"]), float(r["lon"]), *centre) <= gp.MAX_KM
    # Second run: everything cached, no requests.
    calls.clear()
    st2 = gp.run(out, cache, dcache, gp.Geocoder(fetch=fake, delay=0), max_new=10_000)
    assert st2["looked_up"] == 0 and calls == []


def test_build_adds_locations_from_cache(tmp_path, built):
    raw, _, _ = built
    out = tmp_path / "out"
    bd.build(raw, out, months_keep=12, geo_path=None)
    meta0 = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta0["geo"] is None
    slug = meta0["areas"]["Selangor"][0]["slug"]
    d0 = json.loads((out / "d" / f"{slug}.json").read_text(encoding="utf-8"))
    codes = [p[0] for p in d0["premises"]]
    geo = tmp_path / "geo.csv"
    with geo.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["premise_code", "lat", "lon", "precision", "query", "district", "state", "postcode"])
        w.writerow([codes[0], 3.0, 101.5, "address", "x", "", "", "40100"])
        w.writerow([codes[1], 3.02, 101.5, "district", "x", "", "", ""])
    bd.build(raw, out, months_keep=12, geo_path=geo)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    d = json.loads((out / "d" / f"{slug}.json").read_text(encoding="utf-8"))
    assert d["premise_cols"][4:] == ["lat", "lon", "geo_precision"]
    assert d["premises"][0][4:] == [3.0, 101.5, "address"]
    assert d["premises"][2][4] is None
    area = [a for a in meta["areas"]["Selangor"] if a["slug"] == slug][0]
    assert area["geo"][0] == pytest.approx(3.01) and area["geo"][2] >= 1.1
    assert meta["geo"]["postcodes"] == {"40100": [3.0, 101.5]}
    assert "OpenStreetMap" in meta["geo"]["source"]
