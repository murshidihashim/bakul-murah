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
        assert res["fails"] == [] and "No live build" in res["notes"][0] and "missing, empty or unreadable" in res["notes"][0]
    nan = tmp_path / "nan.json"   # Python can write this; it is not JSON
    nan.write_text('{"data_date": "2026-10-29", "stats": {"premises": NaN, "rows_kept": 1000000}}', encoding="utf-8")
    res = cd.check(good, nan)
    assert res["fails"] == [] and res["notes"] == ["No live build to compare with (previous meta.json holds NaN or Infinity)."]
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


def test_wrong_types_fail_with_a_message_not_a_crash(tmp_path, monkeypatch, capsys):
    def broken(name, meta=None, district=None):
        root = make_build(tmp_path / name)
        if meta:
            edit_json(root / "meta.json", meta)
        if district:
            edit_json(root / "d" / "state-1--district-1.json", district)
        return root

    fails = cd.check(broken("areas", lambda m: m.update(areas=[1, 2])))["fails"]
    assert any("no areas object" in f for f in fails) and any("Only 0 districts" in f for f in fails)
    fails = cd.check(broken("stats", lambda m: m.update(stats=[1])))["fails"]
    assert any("stats is not an object" in f for f in fails) and any("no shop count" in f for f in fails)
    assert cd.check(broken("state", lambda m: m["areas"].update({"State 0": 5})))["fails"] == [
        "Only 140 districts (expected at least 150)."]
    assert cd.check(broken("slug", lambda m: m["areas"]["State 1"][0].update(slug=["x"])))["fails"] == [
        "1 district file(s) missing: State 1 / District 1"]
    assert cd.check(broken("months", district=lambda d: d.update(shop_months=[[0, 4.4, 4.5, 4.6]])))["fails"] == [
        "1 district file(s) unreadable: state-1--district-1"]
    res = cd.check(broken("geo", lambda m: m["geo"].update(postcodes=7)))
    assert res["fails"] == [] and any("has 0 postcodes" in w for w in res["warns"])
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"data_date": "2026-11-01", "stats": [1]}), encoding="utf-8")
    assert cd.check(make_build(tmp_path / "good"), prev)["fails"] == []
    # From the command line the report still reaches the summary, with exit 1.
    assert cd.main(["--data", str(tmp_path / "areas")]) == 1
    assert capsys.readouterr().out.startswith("## Data check: FAIL")
    # Even a shape nobody foresaw is a FAIL with a report, never a traceback.
    monkeypatch.setattr(cd, "_col", lambda *a: 1 / 0)
    assert cd.main(["--data", str(tmp_path / "good")]) == 1
    out = capsys.readouterr().out
    assert out.startswith("## Data check: FAIL") and "ZeroDivisionError" in out


def test_odd_precision_values_warn_and_never_crash(tmp_path, monkeypatch, capsys):
    """A number (5) or null as geo_precision: a report with a warning, never a traceback."""
    root = make_build(tmp_path / "odd")

    def odd(d):
        d["premises"][0][6] = 5
        d["premises"][1][6] = None

    edit_json(root / "d" / "state-1--district-1.json", odd)
    res = cd.check(root)
    assert res["fails"] == [] and res["locations"]["5"] == 1 and res["locations"]["none"] == 1
    assert any("not name, address, postcode, town or district: 5, None" in w for w in res["warns"])
    assert cd.main(["--data", str(root)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("## Data check: PASS (1 warning)") and "| 5 | 1 | 0% |" in out and err == ""
    # Mixed key types from any other caller still print.
    assert "| 5 | 1 |" in cd.report({"fails": [], "warns": [], "notes": [], "facts": {}, "towns": [],
                                     "locations": cd.Counter({"exact": 3, 5: 1, None: 1})})
    # A crash while writing the report is still a FAIL with a report and exit 1.
    real = cd.report

    def flaky(res):
        if not res["fails"]:
            raise TypeError("boom")
        return real(res)

    monkeypatch.setattr(cd, "report", flaky)
    assert cd.main(["--data", str(make_build(tmp_path / "good"))]) == 1
    out, err = capsys.readouterr()
    assert out.startswith("## Data check: FAIL") and "TypeError: boom" in out and err == ""


def test_nan_infinity_and_impossible_points_are_named(tmp_path, capsys):
    """Python writes NaN and Infinity into JSON, but browsers cannot read such a file; a latitude
    of 999 is no place. These fail, naming the file (and the shop). A point outside Malaysia warns."""
    def build(name, change=None, meta=None):
        root = make_build(tmp_path / name)
        if change:
            edit_json(root / "d" / "state-1--district-1.json", change)
        if meta:
            edit_json(root / "meta.json", meta)
        return root

    cases = {"lat NaN": lambda d: d["premises"][0].__setitem__(4, float("nan")),
             "lat Infinity": lambda d: d["premises"][0].__setitem__(4, float("inf")),
             "lon -Infinity": lambda d: d["premises"][0].__setitem__(5, float("-inf")),
             "price NaN": lambda d: d["prices"]["1"][0].__setitem__(1, float("nan"))}
    for name, change in cases.items():
        res = cd.check(build(name.replace(" ", "_"), change))
        assert any("NaN or Infinity" in f and "d/state-1--district-1.json" in f for f in res["fails"]), name
    res = cd.check(build("meta_nan", meta=lambda m: m["stats"].update(rows_kept=float("nan"))))
    assert len(res["fails"]) == 1 and "meta.json" in res["fails"][0] and "NaN or Infinity" in res["fails"][0]
    for name, lat, lon in (("lat_999", 999, 101.6), ("lon_200", 3.1, 200.0), ("lat_huge", 1e400, 101.6)):
        root = build(name, lambda d, lat=lat, lon=lon: d["premises"][1].__setitem__(4, lat) or d["premises"][1].__setitem__(5, lon))
        if name == "lat_huge":   # written as a number too big for a float: it reads as infinity
            f = root / "d" / "state-1--district-1.json"
            f.write_text(f.read_text(encoding="utf-8").replace("Infinity", "1e400"), encoding="utf-8")
        res = cd.check(root)
        assert any("outside -90..90" in f and "d/state-1--district-1.json shop 101" in f for f in res["fails"]), name
        assert cd.main(["--data", str(root)]) == 1 and capsys.readouterr().out.startswith("## Data check: FAIL")
    # A real point outside Malaysia (Bangkok) is a warning naming the shop, not a failure.
    root = build("thailand", lambda d: d["premises"][2].__setitem__(4, 13.75) or d["premises"][2].__setitem__(5, 100.5))
    res = cd.check(root)
    assert res["fails"] == [] and any("outside Malaysia" in w and "d/state-1--district-1.json shop 102" in w
                                      for w in res["warns"])
    assert cd.main(["--data", str(root)]) == 0
    # A price written as a 401-digit integer: Python reads it, browsers read it as Infinity.
    res = cd.check(build("price_huge", lambda d: d["prices"]["1"][0].__setitem__(1, 10 ** 400)))
    assert any("1 price row(s)" in f for f in res["fails"])
    assert cd._num(float("nan")) is None and cd._num(float("inf")) is None and cd._num(10 ** 400) is None
    assert cd._num(10 ** 300) == 10 ** 300 and cd._num(4.5) == 4.5


def test_workflow_live_meta_download_is_optional_and_time_limited():
    """A stalled GitHub Pages reply must not hold the Check data step until its timeout."""
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "refresh-and-deploy.yml"
    curl = [x.strip() for x in workflow.read_text(encoding="utf-8").splitlines() if x.strip().startswith("curl ")]
    assert len(curl) == 1 and "prev_meta.json" in curl[0]
    assert "--max-time 30" in curl[0] and "--retry 2" in curl[0] and curl[0].endswith("|| true")
