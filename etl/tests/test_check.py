"""Tests for etl/check_data.py, on small made-up web/data folders. No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_data as cd  # noqa: E402

COLS = ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision"]


def make_build(root: Path, districts: int = 150, shops: int = 13, items: int = 300, data_date: str = "2026-11-05",
               generated_at: str = "2026-11-06T01:00:00Z", postcodes: int = 2000, rows_kept: int = 1_000_000,
               precision=lambda i, k: "name", days_old: int = 1) -> Path:
    """A web/data folder that passes every check: shops sit close to the towns in cd.TOWNS."""
    (root / "d").mkdir(parents=True, exist_ok=True)
    areas = {}
    for i in range(districts):
        state = f"State {i % 16}"
        slug = f"state-{i % 16}--district-{i}"
        _, lat, lon = cd.TOWNS[i % len(cd.TOWNS)]
        premises = []
        for k in range(shops):
            p = precision(i, k)
            premises.append([i * 100 + k, f"Kedai {k}", "Pasar Mini", "Jalan 1", lat + 0.001 * k, lon, p])
        d = {"state": state, "district": f"District {i}", "premise_cols": COLS, "premises": premises,
             "price_cols": ["premise_index", "price", "days_old"],
             "prices": {"1": [[k, 4.5, days_old] for k in range(shops)]},
             "shop_months": {"1": [[k, 4.4, 4.5, 4.6] for k in range(shops)]}}
        (root / "d" / f"{slug}.json").write_text(json.dumps(d), encoding="utf-8")
        areas.setdefault(state, []).append({"district": f"District {i}", "slug": slug, "premises": shops, "items": 1})
    meta = {"generated_at": generated_at, "data_date": data_date, "synthetic": False,
            "items": [[c, f"Item {c}", "1kg", "G", "C"] for c in range(items)],
            "areas": areas,
            "geo": {"shops_located": districts * shops, "postcodes": {f"{n:05d}": [3.1, 101.6] for n in range(postcodes)}},
            "stats": {"rows_in": rows_kept + 10, "rows_kept": rows_kept, "premises": districts * shops,
                      "items": items, "districts": districts}}
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return root


def edit_json(path: Path, change) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    change(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    return make_build(tmp_path_factory.mktemp("good"))


def test_good_build_passes_without_warnings(good, capsys):
    res = cd.check(good)
    assert res["fails"] == [] and res["warns"] == []
    assert res["facts"]["districts"] == 150 and res["facts"]["premises"] == 1950
    assert all(n >= 10 for _, n in res["towns"]) and len(res["towns"]) == 16
    assert cd.main(["--data", str(good)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## Data check: PASS\n") and "1,950 shops" in out and "| Kuching |" in out


def test_missing_or_unreadable_meta_fails(tmp_path, capsys):
    assert cd.check(tmp_path)["fails"]
    (tmp_path / "meta.json").write_text("{not json", encoding="utf-8")
    assert cd.main(["--data", str(tmp_path)]) == 1
    assert "## Data check: FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("kw, words", [({"districts": 149, "shops": 14}, "149 districts"),
                                       ({"shops": 12}, "1800 shops"),
                                       ({"items": 299}, "299 items")])
def test_too_few_districts_shops_or_items_fail(tmp_path, kw, words):
    res = cd.check(make_build(tmp_path, **kw))
    assert len(res["fails"]) == 1 and words in res["fails"][0]


def test_meta_without_counts_fails(tmp_path):
    root = make_build(tmp_path)
    edit_json(root / "meta.json", lambda m: (m["stats"].pop("premises"), m["stats"].pop("items"), m.pop("items")))
    fails = " | ".join(cd.check(root)["fails"])
    assert "no shop count" in fails and "no item list" in fails


def test_synthetic_or_undated_build_fails(tmp_path):
    root = make_build(tmp_path)
    edit_json(root / "meta.json", lambda m: m.update(synthetic=True, data_date=None))
    fails = cd.check(root)["fails"]
    assert any("synthetic" in f for f in fails) and any("data_date" in f for f in fails)


def test_missing_district_file_and_bad_price_rows_fail(tmp_path):
    root = make_build(tmp_path)
    edit_json(root / "meta.json", lambda m: m["areas"]["State 0"].append({"district": "Gone", "slug": "state-0--gone"}))
    res = cd.check(root)
    assert len(res["fails"]) == 1 and "missing: state-0--gone" in res["fails"][0]

    root = make_build(tmp_path / "b")
    f = root / "d" / "state-1--district-1.json"
    edit_json(f, lambda d: d["prices"]["1"].append([13, 4.5, 1]))           # 13 shops: index 13 is out of range
    edit_json(root / "d" / "state-2--district-2.json", lambda d: d["shop_months"]["1"].append([-1, 4.0, 4.0, 4.0]))
    (root / "d" / "state-3--district-3.json").write_text("{broken", encoding="utf-8")
    fails = cd.check(root)["fails"]
    assert any("2 price row(s)" in x for x in fails)
    assert any("unreadable: state-3--district-3" in x for x in fails)


def test_comparison_with_the_live_build(tmp_path, good):
    def prev(**kw):
        p = tmp_path / "prev.json"
        p.write_text(json.dumps({"data_date": kw.get("data_date", "2026-10-29"),
                                 "stats": {"premises": kw.get("premises", 1950),
                                           "rows_kept": kw.get("rows_kept", 1_000_000)}}), encoding="utf-8")
        return p

    assert cd.check(good, prev())["fails"] == []
    assert cd.check(good, prev(premises=2200))["fails"] == []                       # down 11%: fine
    assert "Shops fell from 2,300 to 1,950" in cd.check(good, prev(premises=2300))["fails"][0]   # down 15.2%
    assert "price rows fell" in cd.check(good, prev(rows_kept=1_400_000))["fails"][0]           # down 29%
    assert cd.check(good, prev(rows_kept=1_300_000))["fails"] == []                  # down 23%: fine
    assert "older than the live" in cd.check(good, prev(data_date="2026-11-06"))["fails"][0]
    # No live build yet (first deploy, or the download failed): nothing to compare, no failure.
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    for p in (tmp_path / "none.json", empty):
        res = cd.check(good, p)
        assert res["fails"] == [] and "No live build" in res["notes"][0]
    html = tmp_path / "page.json"
    html.write_text("<html>not found</html>", encoding="utf-8")
    assert cd.check(good, html)["fails"] == []
    assert cd.main(["--data", str(good), "--previous", str(prev(premises=3000))]) == 1


def test_warnings_do_not_fail(tmp_path, capsys):
    # 40% of shops at district precision; the Kuching shops (town 7) only at district level too.
    def precision(i, k):
        return "district" if (k < 5 or i % 16 == 7) else "postcode" if k < 7 else "address"

    root = make_build(tmp_path, precision=precision, postcodes=1999, data_date="2026-10-26", days_old=31)
    res = cd.check(root)
    assert res["fails"] == []
    warns = " | ".join(res["warns"])
    assert "have no real location" in warns
    assert "1,999 postcodes" in warns
    assert "11 days before the build" in warns
    assert "older than 30 days" in warns
    assert "Kuching (0)" in warns and "Ipoh" not in warns
    assert res["locations"]["district"] > 0 and res["locations"]["approximate"] > 0
    assert cd.main(["--data", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## Data check: PASS (5 warnings)") and "**Warnings:**" in out
    assert "| district only |" in out


def test_shops_without_any_location_count_as_unlocated(tmp_path):
    root = make_build(tmp_path, precision=lambda i, k: None if k < 6 else "name")
    res = cd.check(root)
    assert res["locations"]["none"] == 150 * 6
    assert any("no real location" in w for w in res["warns"])


def test_report_lists_problems(tmp_path):
    root = make_build(tmp_path, districts=10)
    text = cd.report(cd.check(root, tmp_path / "missing.json"))
    assert text.startswith("## Data check: FAIL")
    assert "**Problems (deploy stopped):**" in text and "- Only 10 districts" in text
    assert "No live build to compare with" in text
