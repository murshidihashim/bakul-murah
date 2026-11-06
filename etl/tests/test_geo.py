"""Tests for shop locations, the GeoNames postcode table and download limits. No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import csv
import io
import json
import sys
import types
import urllib.error
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_dataset as bd  # noqa: E402
import fetch_postcodes as fpc  # noqa: E402
import fetch_pricecatcher as fp  # noqa: E402
import geocode_premises as gp  # noqa: E402
import make_synthetic as ms  # noqa: E402

TODAY = "2026-11-06"
KL = "W.P. Kuala Lumpur"
UA = "BakulMurah/1.1 (+https://github.com/murshidihashim/bakul-murah)"

# GeoNames MY.txt style: country, postcode, place, admin1 name, admin1 code, admin2 name, admin2 code,
# admin3 name, admin3 code, lat, lon, accuracy.
MY_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "52100", "Kepong", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.2100", "101.6400", "4"],
    ["MY", "52100", "Kepong Baru", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.2140", "101.6460", "4"],
    ["MY", "50000", "Kuala Lumpur", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.1478", "101.6953", "4"],
    ["MY", "43650", "Bandar Baru Bangi", "Selangor", "10", "", "", "", "", "2.9640", "101.7620", "4"],
    ["MY", "43600", "Bangi", "Selangor", "10", "", "", "", "", "2.9000", "101.7800", "4"],
    ["MY", "1000", "Kangar", "Perlis", "09", "", "", "", "", "6.4414", "100.1986", "4"],
])


def write_postcodes(path: Path, text: str = MY_TXT) -> Path:
    rows = fpc.parse(text)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fpc.FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def write_build(root: Path, shops: dict) -> Path:
    """shops: {(state, district): [(code, name, address), ...]} -> root/d/<slug>.json"""
    (root / "d").mkdir(parents=True, exist_ok=True)
    for (st, dist), rows in shops.items():
        d = {"state": st, "district": dist,
             "premise_cols": ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision"],
             "premises": [[c, n, "Pasar Mini", a, None, None, None] for c, n, a in rows], "prices": {}}
        slug = f"{st}--{dist}".lower().replace(" ", "-").replace(".", "")
        (root / "d" / f"{slug}.json").write_text(json.dumps(d), encoding="utf-8")
    return root


def write_csv(path: Path, fields: list, rows: list) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def read_rows(path: Path) -> dict:
    with path.open(encoding="utf-8", newline="") as f:
        return {r[next(iter(r))]: r for r in csv.DictReader(f)}


def cache_row(code, lat, lon, prec, district="Kepong", state=KL, tries=None, last=None, query="x", postcode=""):
    r = {"premise_code": str(code), "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "precision": prec, "query": query,
         "district": district, "state": state, "postcode": postcode}
    if tries is not None:
        r["tries"] = str(tries)
    if last is not None:
        r["last_tried"] = last
    return r


class Fake:
    """Fake Nominatim. answers: query text -> (lat, lon), None or an exception (or a function)."""

    def __init__(self, answers=None, default=None):
        self.answers, self.default, self.calls = answers or {}, default, []

    def __call__(self, params):
        self.calls.append(dict(params))
        a = self.answers.get(params.get("q"), self.default)
        if callable(a):
            a = a(params)
        if isinstance(a, Exception):
            raise a
        return [{"lat": str(a[0]), "lon": str(a[1])}] if a else []

    def queries(self):
        return [c.get("q") for c in self.calls]


def net_error():
    return urllib.error.URLError("network is down")


def geocoder(fake, **kw):
    return gp.Geocoder(fetch=fake, delay=0, **kw)


@pytest.fixture
def places():
    return gp.Places(fpc.parse(MY_TXT))


# ---------- GeoNames postcode table ----------

def test_parse_geonames_rows():
    text = MY_TXT + "\n" + "\n".join("\t".join(r) for r in [
        ["SG", "01000", "Singapore", "x", "", "", "", "", "", "1.3", "103.8", "1"],     # other country
        ["MY", "ABCDE", "Bad", "Perlis", "", "", "", "", "", "6.4", "100.2", "1"],      # not a postcode
        ["MY", "01000", "Kangar", "Perlis", "", "", "", "", "", "north", "100.2", "1"],  # bad latitude
        ["MY", "01000", "Far", "Perlis", "", "", "", "", "", "51.5", "0.12", "1"],       # not in Malaysia
        ["MY", "52100", "Kepong", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "",
         "3.2100", "101.6400", "4"],                                                     # duplicate
        [""],
    ])
    rows = fpc.parse(text)
    assert [r["postcode"] for r in rows] == ["01000", "43600", "43650", "50000", "52100", "52100"]
    assert [r["place"] for r in rows[-2:]] == ["Kepong", "Kepong Baru"]   # then sorted by place
    assert rows[0] == {"postcode": "01000", "place": "Kangar", "state": "Perlis",
                       "lat": "6.44140", "lon": "100.19860", "accuracy": "4"}
    assert list(rows[0]) == ["postcode", "place", "state", "lat", "lon", "accuracy"]


def zip_of(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "GeoNames")
        z.writestr("MY.txt", text)
    return buf.getvalue()


def test_fetch_postcodes_writes_table_then_skips_while_fresh(tmp_path):
    out = tmp_path / "postcodes_my.csv"
    calls = []

    def fetch():
        calls.append(1)
        return zip_of(MY_TXT)

    assert fpc.update(out, fetch=fetch, today=date(2026, 11, 6), min_rows=1) == "updated"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "postcode,place,state,lat,lon,accuracy"
    assert len(lines) == 1 + 6 and lines[1].startswith("01000,Kangar,Perlis,6.44140,100.19860")
    info = json.loads((tmp_path / "postcodes_my.json").read_text(encoding="utf-8"))
    assert info["fetched"] == "2026-11-06" and info["rows"] == 6 and info["licence"] == "CC BY 4.0"
    # Young table: no download (the age comes from the .json, not the file time a checkout resets).
    assert fpc.update(out, fetch=fetch, today=date(2027, 1, 30), min_rows=1) == "fresh"
    assert len(calls) == 1
    assert fpc.update(out, fetch=fetch, today=date(2026, 11, 7), force=True, min_rows=1) == "updated"
    assert fpc.update(out, fetch=fetch, today=date(2027, 2, 6), min_rows=1) == "updated"   # 91 days
    assert len(calls) == 3


def test_fetch_postcodes_failure_keeps_old_table_and_exits_0(tmp_path, monkeypatch, capsys):
    out = tmp_path / "postcodes_my.csv"
    assert fpc.update(out, fetch=lambda: zip_of(MY_TXT), today=date(2026, 1, 1), min_rows=1) == "updated"
    before = out.read_bytes()

    def down():
        raise urllib.error.URLError("blocked")

    for fetch in (down, lambda: b"not a zip", lambda: zip_of(MY_TXT)):  # error, bad zip, too few rows
        assert fpc.update(out, fetch=fetch, today=date(2026, 11, 6)) == "failed"
        assert out.read_bytes() == before
        assert not (tmp_path / "postcodes_my.csv.tmp").exists()
    assert "WARNING" in capsys.readouterr().out
    monkeypatch.setattr(fpc, "download", down)
    assert fpc.main(["--out", str(out), "--force"]) == 0
    assert out.read_bytes() == before
    # No table at all and no network: still exit 0.
    assert fpc.main(["--out", str(tmp_path / "none.csv")]) == 0
    assert not (tmp_path / "none.csv").exists()


def test_postcode_download_sends_user_agent(monkeypatch):
    seen = {}

    class Resp:
        def __init__(self):
            self.parts = [zip_of(MY_TXT), b""]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read1(self, n):
            return self.parts.pop(0)

    def fake_urlopen(req, timeout):
        seen["ua"], seen["url"] = req.get_header("User-agent"), req.full_url
        return Resp()

    monkeypatch.setattr(fpc.urllib.request, "urlopen", fake_urlopen)
    assert fpc.parse(fpc.read_zip(fpc.download()))
    assert seen == {"ua": UA, "url": "https://download.geonames.org/export/zip/MY.zip"}
    assert gp.UA == UA and "@" not in UA


# ---------- names and anchors ----------

def test_state_names_compare_across_sources():
    for s in ["W.P. Kuala Lumpur", "WP Kuala Lumpur", "Wilayah Persekutuan Kuala Lumpur", "Kuala Lumpur",
              "w.p kuala lumpur"]:
        assert gp.state_key(s) == "kuala lumpur"
    assert gp.state_key("W.P Labuan") == "labuan" and gp.state_key("Wp Putrajaya") == "putrajaya"
    assert gp.state_key("Penang") == gp.state_key("Pulau Pinang") == "pulau pinang"
    assert gp.plain_name("W.P. Kuala Lumpur") == "Kuala Lumpur" and gp.plain_name("Selangor") == "Selangor"
    assert gp.district_query(KL, "Kepong") == "Kepong, Kuala Lumpur, Malaysia"


def test_postcode_and_town_anchors(places):
    # Postcode point: the average of that postcode's rows.
    lat, lon = places.postcode("52100")
    assert lat == pytest.approx(3.212) and lon == pytest.approx(101.643)
    assert places.postcode("99999") is None and places.postcode("") is None
    # Town: a GeoNames place written as whole words in the address, same state ('W.P.' or not).
    assert places.town("NO.1, JALAN METRO PRIMA,KEPONG,", KL)[0] == "Kepong"
    assert places.town("NO.1, JALAN METRO PRIMA,KEPONG,", "Selangor") is None
    assert places.town("JALAN KEPONGAN 3", KL) is None
    # The longest name wins; names shorter than 4 letters are ignored.
    assert places.town("JALAN 9/1, SEKSYEN 9, BANDAR BARU BANGI", "Selangor")[0] == "Bandar Baru Bangi"
    short = gp.Places([{"postcode": "02000", "place": "Ulu", "state": "Perlis", "lat": "6.4", "lon": "100.2"}])
    assert short.town("KAMPUNG ULU", "Perlis") is None
    # One name for places far apart is not a town point.
    far = gp.Places([{"postcode": "02000", "place": "Simpang Ampat", "state": "Perlis", "lat": "6.4", "lon": "100.2"},
                     {"postcode": "02100", "place": "Simpang Ampat", "state": "Perlis", "lat": "6.8", "lon": "100.2"}])
    assert far.town("SIMPANG AMPAT", "Perlis") is None

    districts = gp.Districts({f"{KL}|Kepong": {"key": f"{KL}|Kepong", "lat": "3.2", "lon": "101.63"}},
                             geocoder(Fake()), {})
    shop = {"premise_code": "1", "premise": "AEON BIG ( KEPONG )", "district": "Kepong", "state": KL}
    a = gp.anchor_for({**shop, "address": "NO.2, JALAN METRO PERDANA, 52100 KUALA LUMPUR"}, places, districts)
    assert a["kind"] == "postcode" and a["label"] == "52100" and a["context"] == "Kuala Lumpur"
    a = gp.anchor_for({**shop, "address": "NO.1, JALAN METRO PRIMA,KEPONG,"}, places, districts)
    assert a["kind"] == "town" and a["context"] == "Kepong"
    a = gp.anchor_for({**shop, "address": "JALAN 1"}, places, districts)
    assert a["kind"] == "district" and a["point"] == (3.2, 101.63) and a["context"] == "Kepong"


def test_acceptance_radius_and_search_box(places):
    shop = {"premise_code": "1", "premise": "MYDIN", "district": "Kepong", "state": KL,
            "address": "LOT 5, JALAN KEPONG, 52100 KUALA LUMPUR"}
    pc = {"kind": "postcode", "point": places.postcode("52100"), "label": "52100", "context": "Kepong"}
    far, near = (3.212 + 0.11, 101.643), (3.212 + 0.045, 101.643)    # about 12 km and 5 km away
    fake = Fake({"MYDIN, Kepong": far, "JALAN KEPONG, KUALA LUMPUR": near})
    assert gp.lookup(geocoder(fake), shop, pc) == ("address", near, "JALAN KEPONG, KUALA LUMPUR")
    assert fake.calls[0]["bounded"] == 1
    assert fake.calls[0]["viewbox"] == gp.viewbox(*pc["point"], 0.15)
    # Around a district centre: a wider box, and hits up to 25 km count.
    dc = {"kind": "district", "point": (3.2, 101.63), "label": "Kepong, KL", "context": "Kepong"}
    fake = Fake({"MYDIN, Kepong": (3.2 + 0.18, 101.63)})                # about 20 km
    assert gp.lookup(geocoder(fake), shop, dc)[0] == "name"
    assert fake.calls[0]["viewbox"] == gp.viewbox(3.2, 101.63, 0.3)
    fake = Fake(default=(3.2 + 0.27, 101.63))                           # about 30 km: never accepted
    assert gp.lookup(geocoder(fake), shop, dc) is None
    assert fake.queries() == ["MYDIN, Kepong", "JALAN KEPONG, KUALA LUMPUR", "MYDIN"]   # at most 3 calls


# ---------- errors, breaker, time budget ----------

def test_geocoder_tells_no_result_from_errors():
    assert geocoder(Fake()).search(q="x") is None
    assert geocoder(Fake(default=(3.1, 101.6))).search(q="x") == (3.1, 101.6)
    for bad in ({"error": "Unable to geocode"}, [{"lat": "north", "lon": "1"}], ValueError("bad JSON"),
                net_error(), TimeoutError("timed out"),
                urllib.error.HTTPError("u", 500, "Server Error", {}, None)):
        g = geocoder(lambda p, bad=bad: (_ for _ in ()).throw(bad) if isinstance(bad, Exception) else bad)
        with pytest.raises(gp.GeocodeError):
            g.search(q="x")
        assert g.errors == 1


def test_breaker_trips_after_10_errors_in_a_row():
    g = geocoder(Fake(default=net_error()))
    for _ in range(10):
        with pytest.raises(gp.GeocodeError):
            g.search(q="x")
    assert g.breaker_tripped and g.calls == 10
    with pytest.raises(gp.GeocodeStopped):
        g.search(q="x")
    assert g.calls == 10
    # A good answer in between resets the count.
    answers = iter([net_error()] * 9 + [None] + [net_error()] * 9)
    g = geocoder(Fake(default=lambda p: next(answers)))
    for _ in range(19):
        try:
            g.search(q="x")
        except gp.GeocodeError:
            pass
    assert not g.breaker_tripped and g.calls == 19


def test_429_retry_after_is_honoured(monkeypatch):
    waits = []
    monkeypatch.setattr(gp.time, "sleep", waits.append)
    for headers, want in (({"Retry-After": "7"}, 7), ({"Retry-After": "500"}, 120), ({}, 30)):
        g = gp.Geocoder(fetch=Fake(default=urllib.error.HTTPError("u", 429, "Too Many", headers, None)), delay=0)
        with pytest.raises(gp.GeocodeError):
            g.search(q="x")
        assert waits[-1] == want
    g = gp.Geocoder(fetch=Fake(default=urllib.error.HTTPError("u", 503, "Busy", {"Retry-After": "12"}, None)), delay=0)
    with pytest.raises(gp.GeocodeError):
        g.search(q="x")
    assert waits[-1] == 12
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert 50 <= gp.retry_after({"Retry-After": when}) <= 60
    assert gp.retry_after({"Retry-After": "soon"}) == 30
    # Never wait past the time budget.
    t = [0.0]
    g = gp.Geocoder(fetch=Fake(default=urllib.error.HTTPError("u", 429, "x", {"Retry-After": "100"}, None)),
                    delay=0, max_minutes=1, clock=lambda: t[0], sleep=waits.append)
    t[0] = 50
    with pytest.raises(gp.GeocodeError):
        g.search(q="x")
    assert waits[-1] == 10


def kepong(tmp_path, shops, cached=(), postcodes=True):
    """A one-district build (KL / Kepong), its caches and the postcode table."""
    data = write_build(tmp_path / "data", {(KL, "Kepong"): shops})
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, list(cached))
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": f"{KL}|Kepong", "lat": "3.200000", "lon": "101.630000"}])
    pc = write_postcodes(tmp_path / "postcodes_my.csv") if postcodes else None
    return data, cache, dcache, pc


def test_errors_are_never_cached_as_results(tmp_path):
    old = cache_row(2, 3.2, 101.63, "district", tries=0, last="", query="Kepong, W.P. Kuala Lumpur")
    data, cache, dcache, pc = kepong(tmp_path, [(1, "AEON BIG ( KEPONG )", "NO.2, JALAN METRO PERDANA, 52100 KUALA LUMPUR"),
                                                (2, "PASAR MINI X", "JALAN KEPONG")], [old])
    st = gp.run(data, cache, dcache, geocoder(Fake(default=net_error())), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    # New shop: the offline postcode point, tries 0, so the next run looks it up again.
    assert rows["1"]["precision"] == "postcode" and rows["1"]["tries"] == "0" and rows["1"]["last_tried"] == ""
    assert float(rows["1"]["lat"]) == pytest.approx(3.212)
    # Cached shop: exactly what it had.
    assert rows["2"] == old
    assert st["offline"] == 1 and st["kept"] == 1 and st["errors"] == 2
    # Next run, Nominatim works: both are looked up properly.
    shop_pt = (3.2147, 101.6439)
    fake = Fake({"AEON BIG ( KEPONG ), Kuala Lumpur": shop_pt})
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    assert rows["1"]["precision"] == "name" and rows["1"]["tries"] == "1" and rows["1"]["last_tried"] == TODAY
    assert rows["2"]["precision"] == "town" and rows["2"]["tries"] == "1"
    assert st["retried"] == 2 and st["errors"] == 0


def test_breaker_stops_the_run_and_new_shops_get_offline_points(tmp_path):
    shops = [(i, f"KEDAI {i}", f"JALAN {i}, 52100 KUALA LUMPUR") for i in range(1, 16)]
    data, cache, dcache, pc = kepong(tmp_path, shops)
    fake = Fake(default=net_error())
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    assert len(fake.calls) == 10 and st["requests"] == 10 and st["breaker_tripped"] is True
    rows = read_rows(cache)
    assert len(rows) == 15
    assert {r["precision"] for r in rows.values()} == {"postcode"}
    assert {r["tries"] for r in rows.values()} == {"0"}


def test_time_budget_stops_calls(tmp_path):
    shops = [(i, f"KEDAI {i}", f"JALAN {i}, 52100 KUALA LUMPUR") for i in range(1, 11)]
    old = cache_row(99, 3.2, 101.63, "district", tries=0, last="")
    data, cache, dcache, pc = kepong(tmp_path, shops + [(99, "KEDAI LAMA", "JALAN LAMA")], [old])
    t = [0.0]

    def slow(params):
        t[0] += 30   # every call takes 30 s
        return []

    g = gp.Geocoder(fetch=slow, delay=0, max_minutes=2, clock=lambda: t[0], sleep=lambda s: None)
    st = gp.run(data, cache, dcache, g, postcodes_path=pc, today=TODAY)
    assert st["requests"] == 4 and st["out_of_time"] is True and st["retried"] == 0
    rows = read_rows(cache)
    assert rows["1"]["tries"] == "1"                                # 3 calls, all answered
    assert all(rows[str(i)]["tries"] == "0" for i in range(2, 11))  # skipped: never marked as tried
    assert rows["99"] == {**old, "tries": "0", "last_tried": ""}


def shops_asked(fake):
    """Shop names in the order they were searched."""
    return list(dict.fromkeys(q.split(",")[0] for q in fake.queries() if q.startswith("KEDAI")))


def test_retry_pass_order_and_limits(tmp_path):
    long_ago, lately = "2026-10-01", "2026-11-01"
    rows = [cache_row(1, 3.2, 101.63, "district", tries=0, last=""),
            cache_row(2, 3.21, 101.64, "town", tries=0, last=""),
            cache_row(3, 3.21, 101.64, "postcode", tries=0, last=""),
            cache_row(4, 3.2, 101.63, "district", tries=1, last=long_ago),
            cache_row(5, 3.2, 101.63, "district", tries=1, last=lately),
            cache_row(6, 3.2, 101.63, "district", tries=2, last=long_ago),
            cache_row(7, 3.21, 101.64, "name", tries=0, last="")]
    data, cache, dcache, pc = kepong(tmp_path, [(r["premise_code"], f"KEDAI {r['premise_code']}", "JALAN 1")
                                                for r in rows], rows, postcodes=False)
    fake = Fake()
    st = gp.run(data, cache, dcache, geocoder(fake), retry=3, today=TODAY)
    assert shops_asked(fake) == ["KEDAI 1", "KEDAI 4", "KEDAI 2"]   # district rows first, then town; capped at 3
    after = read_rows(cache)
    assert after["1"]["tries"] == "1" and after["4"]["tries"] == "2" and after["1"]["last_tried"] == TODAY
    for code in "3567":
        assert after[code] == {**rows[int(code) - 1], **({"tries": "0", "last_tried": ""} if code in "37" else {})}
    assert st["retried"] == 3
    # Later the same day: only the postcode row is still due.
    fake = Fake()
    gp.run(data, cache, dcache, geocoder(fake), today=TODAY)
    assert shops_asked(fake) == ["KEDAI 3"]


def test_retry_never_swaps_a_point_for_a_worse_one(tmp_path):
    old = cache_row(1, 3.205, 101.635, "postcode", query="52109", postcode="52109")   # an old file: no tries
    data, cache, dcache, pc = kepong(tmp_path, [(1, "KEDAI 1", "JALAN 1, 52109 WILAYAH")], [old])
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)   # 52109 not in the table
    row = read_rows(cache)["1"]
    assert (row["lat"], row["lon"], row["precision"]) == (old["lat"], old["lon"], "postcode")
    assert row["tries"] == "1" and row["last_tried"] == TODAY


# ---------- district centres ----------

def test_shared_district_centres_are_asked_again_once(tmp_path):
    shared = {"lat": "3.145593", "lon": "101.697390"}   # the old answer for every KL district
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": f"{KL}|{d}", **shared} for d in ("Batu", "Cheras", "Kepong")])
    located = [cache_row(10, 3.20, 101.68, "name", district="Batu"),
               cache_row(11, 3.22, 101.70, "address", district="Batu"),
               cache_row(12, 3.21, 101.69, "district", district="Batu")]   # not a real location
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], located)
    data = write_build(tmp_path / "data", {(KL, d): [(i, f"KEDAI {d}", "JALAN 1")]
                                           for i, d in enumerate(("Batu", "Cheras", "Kepong"), 1)})
    kepong_pt = (3.2132, 101.6389)
    fake = Fake({"Kepong, Kuala Lumpur, Malaysia": kepong_pt, "Batu, Kuala Lumpur, Malaysia": None,
                 "Cheras, Kuala Lumpur, Malaysia": net_error()})
    st = gp.run(data, cache, dcache, geocoder(fake), today=TODAY)
    asked = [q for q in fake.queries() if q.endswith("Malaysia")]
    assert sorted(asked) == ["Batu, Kuala Lumpur, Malaysia", "Cheras, Kuala Lumpur, Malaysia",
                             "Kepong, Kuala Lumpur, Malaysia"]
    d = read_rows(dcache)
    assert (d[f"{KL}|Kepong"]["lat"], d[f"{KL}|Kepong"]["tries"]) == ("3.213200", "1")
    assert (d[f"{KL}|Batu"]["lat"], d[f"{KL}|Batu"]["tries"]) == ("3.145593", "1")    # nothing better: kept
    assert d[f"{KL}|Cheras"]["tries"] in ("", "0")                                     # error: ask next run
    boxes = {c["q"].split(",")[0]: c["viewbox"] for c in fake.calls if "viewbox" in c}
    assert boxes["KEDAI Kepong"] == gp.viewbox(*kepong_pt, 0.3)
    assert boxes["KEDAI Batu"] == gp.viewbox(3.21, 101.69, 0.3)   # still shared: median of its 2 located shops
    assert boxes["KEDAI Cheras"] == gp.viewbox(3.145593, 101.69739, 0.3)   # no shops: the shared point
    rows = read_rows(cache)
    assert rows["1"]["precision"] == "district" and float(rows["1"]["lat"]) == pytest.approx(3.21)
    assert st["district_requeried"] == 2
    # Next run: only the centre that failed is asked again.
    fake = Fake()
    gp.run(write_build(tmp_path / "data2", {(KL, "Cheras"): [(5, "KEDAI 5", "JALAN 5")],
                                            (KL, "Kepong"): [(6, "KEDAI 6", "JALAN 6")]}),
           cache, dcache, geocoder(fake), today=TODAY)
    assert [q for q in fake.queries() if q.endswith("Malaysia")] == ["Cheras, Kuala Lumpur, Malaysia"]


def test_new_district_centre_error_is_not_cached(tmp_path):
    data = write_build(tmp_path / "data", {("Perlis", "Kangar"): [(1, "KEDAI 1", "JALAN 1")]})
    cache, dcache = tmp_path / "p.csv", tmp_path / "d.csv"
    st = gp.run(data, cache, dcache, geocoder(Fake(default=net_error())), today=TODAY)
    assert read_rows(dcache) == {} and read_rows(cache) == {} and st["missed"] == 1
    gp.run(data, cache, dcache, geocoder(Fake({"Kangar, Perlis, Malaysia": (6.44, 100.2)})), today=TODAY)
    assert read_rows(dcache)["Perlis|Kangar"]["tries"] == "1"
    assert read_rows(cache)["1"]["precision"] == "district"


# ---------- cache files ----------

def test_old_cache_files_without_tries_still_load(tmp_path):
    old = cache_row(1, 3.2, 101.63, "district", query="Kepong, W.P. Kuala Lumpur")   # 8 columns, as in v1
    data, cache, dcache, pc = kepong(tmp_path, [(1, "KEDAI 1", "JALAN 1")], [])
    write_csv(cache, gp.FIELDS[:8], [old, cache_row(2, 3.21, 101.64, "name")])
    assert "tries" not in cache.read_text(encoding="utf-8")
    fake = Fake()
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    assert st["retried"] == 1 and len(fake.calls) == 3     # missing tries = 0, never tried: due now
    rows = read_rows(cache)
    assert list(rows["2"]) == gp.FIELDS
    assert rows["2"]["tries"] == "0" and rows["2"]["last_tried"] == ""   # not in the build: left alone
    assert rows["1"]["tries"] == "1"
    assert list(read_rows(dcache)[f"{KL}|Kepong"]) == ["key", "lat", "lon", "tries"]


def test_clean_address_keeps_street_drops_unit_numbers():
    assert gp.clean_address("NO 3 JALAN SS16/1,47500 SUBANG JAYA, SELANGOR DARUL EHSAN,") == \
        "JALAN SS16/1, SUBANG JAYA, SELANGOR DARUL EHSAN"
    assert gp.clean_address("LOT 4,5,6,PERSIARAN PERPADUAN USJ 1,47600 SUBANG JAYA, SELANGOR") == \
        "PERSIARAN PERPADUAN USJ 1, SUBANG JAYA, SELANGOR"
    assert gp.postcode_of("NO. 3,JALAN AEROBIK 13/43, SEKSYEN 13,40100 SHAH ALAM") == "40100"
    assert gp.postcode_of("TANJUNG MANIS BAZAAR,") == ""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    out = tmp_path_factory.mktemp("out")
    ms.make(raw, months=4, end="2026-10", last_day=27, fmt="csv")
    bd.build(raw, out, months_keep=12, geo_path=None)
    return out


def test_geocode_run_uses_cache_and_falls_back(tmp_path, built):
    centre = (3.07, 101.52)
    calls = []

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
    st = gp.run(built, cache, dcache, gp.Geocoder(fetch=fake, delay=0), max_new=10_000, today=TODAY)
    n_shops = st["shops"]
    assert st["looked_up"] == n_shops and st["missed"] == 0
    assert st["name"] + st["address"] + st["postcode"] + st["town"] + st["district"] == n_shops
    rows = list(csv.DictReader(cache.open(encoding="utf-8")))
    assert len(rows) == n_shops and {r["tries"] for r in rows} == {"1"}
    for r in rows:
        assert gp.km(float(r["lat"]), float(r["lon"]), *centre) <= gp.DISTRICT_KM
    for key in ("looked_up", "retried", "errors", "breaker_tripped", "requests", "minutes"):
        assert key in st
    # Second run: everything cached and recently tried, so no requests at all.
    calls.clear()
    st2 = gp.run(built, cache, dcache, gp.Geocoder(fetch=fake, delay=0), max_new=10_000, today=TODAY)
    assert st2["looked_up"] == 0 and st2["retried"] == 0 and calls == []


# ---------- PriceCatcher download deadline ----------

class SlowResponse:
    """A response that takes `secs` of fake time per read and, without parts, never ends."""

    def __init__(self, clock, secs, parts=None):
        self.clock, self.secs, self.parts, self.reads = clock, secs, parts, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read1(self, n):
        self.clock[0] += self.secs
        self.reads += 1
        if self.reads > 1000:  # the deadline did not stop it: fail instead of looping for ever
            raise AssertionError("download never gave up")
        if self.parts is not None:
            return self.parts.pop(0) if self.parts else b""
        return b"x" * 100


def test_pricecatcher_download_stops_at_the_deadline_and_keeps_old_data(tmp_path, monkeypatch):
    dest = tmp_path / "pricecatcher_2026-10.parquet"
    dest.write_bytes(b"old month")
    t, opened = [0.0], []

    def fake_urlopen(req, timeout):
        opened.append(timeout)
        return SlowResponse(t, 60)   # a minute per read: a trickle that never finishes

    monkeypatch.setattr(fp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fp, "time", types.SimpleNamespace(monotonic=lambda: t[0],
                                                          sleep=lambda s: t.__setitem__(0, t[0] + s)))
    with pytest.raises(RuntimeError, match="longer than 10 minutes"):
        fp.download("https://storage.data.gov.my/pricecatcher/x.parquet", dest)
    assert dest.read_bytes() == b"old month"
    assert not (tmp_path / "pricecatcher_2026-10.parquet.part").exists()
    assert opened == [120] and t[0] <= 11 * 60   # no retry once the time is up
    # A normal download still works, and --deadline reaches it.
    t[0] = 0.0
    monkeypatch.setattr(fp.urllib.request, "urlopen", lambda req, timeout: SlowResponse(t, 1, [b"ab", b"cd"]))
    assert fp.download("https://x/y.parquet", dest, deadline=5) == 4 and dest.read_bytes() == b"abcd"
    out = tmp_path / "raw"
    monkeypatch.setattr(fp.urllib.request, "urlopen", lambda req, timeout: SlowResponse(t, 1, [b"pq"]))
    assert fp.main(["--out", str(out), "--months", "1", "--deadline", "0.5"]) == 0
    assert (out / "lookup_item.parquet").read_bytes() == b"pq"
