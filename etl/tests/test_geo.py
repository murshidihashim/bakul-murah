"""Tests for shop locations, the GeoNames postcode table and download limits. No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import csv
import io
import json
import os
import shutil
import subprocess
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
# Kelantan, for road names: shop 21684 (Kota Bharu district) is on JALAN KUALA KRAI, in Ketereh.
KB_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "18000", "Kuala Krai", "Kelantan", "03", "", "", "", "", "5.5306", "102.1990", "4"],
    ["MY", "16450", "Ketereh", "Kelantan", "03", "", "", "", "", "5.9561", "102.2432", "4"],
    ["MY", "15000", "Kota Bharu", "Kelantan", "03", "", "", "", "", "6.1254", "102.2381", "4"],
    ["MY", "17000", "Pasir Mas", "Kelantan", "03", "", "", "", "", "6.0493", "102.1399", "4"],
    ["MY", "18200", "Dabong", "Kelantan", "03", "", "", "", "", "5.3613", "101.9950", "4"],
    ["MY", "81100", "Johor Bahru", "Johor", "01", "", "", "", "", "1.5000", "103.7600", "4"],
    ["MY", "98700", "Limbang", "Sarawak", "13", "", "", "", "", "4.7500", "115.0000", "4"],
])
KB_CENTRE, JB_CENTRE = (6.124791, 102.237807), (1.4927, 103.7414)   # from district_geo.csv


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


def test_geonames_file_with_a_byte_order_mark_keeps_its_first_row():
    assert len(fpc.parse("\ufeff" + MY_TXT)) == 6
    assert len(fpc.parse(fpc.read_zip(zip_of("\ufeff" + MY_TXT)))) == 6


def test_postcode_table_is_due_without_a_readable_info_file(tmp_path):
    out, info = tmp_path / "postcodes_my.csv", tmp_path / "postcodes_my.json"
    calls = []

    def fetch():
        calls.append(1)
        return zip_of(MY_TXT)

    assert fpc.update(out, fetch=fetch, today=date(2026, 1, 1), min_rows=1) == "updated"
    # A checkout makes every file look new (as in GitHub Actions): only the info file knows the
    # table's age, so without it the table is fetched again.
    for broken in ("{broken", "[]", '{"fetched": 20260101}', '{"fetched": "2099-01-01"}', ""):
        info.write_text(broken, encoding="utf-8")
        assert fpc.update(out, fetch=fetch, today=date(2026, 1, 2), min_rows=1) == "updated", broken
    assert len(calls) == 6 and json.loads(info.read_text(encoding="utf-8"))["fetched"] == "2026-01-02"
    copy = tmp_path / "copy.csv"   # a table with no info file beside it
    copy.write_bytes(out.read_bytes())
    assert fpc.update(copy, fetch=fetch, today=date(2026, 1, 2), min_rows=1) == "updated"
    out.write_text("not,our,table\n", encoding="utf-8")   # damaged table, good info file
    assert fpc.update(out, fetch=fetch, today=date(2026, 1, 3), min_rows=1) == "updated"
    assert fpc.update(out, fetch=fetch, today=date(2026, 1, 4), min_rows=1) == "fresh"
    assert len(calls) == 8


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
    # Two rows 35 km apart are two places too: their midpoint is 18 km from both (it used to be
    # kept, as no row was more than 20 km from the mean). Rows 15 km apart are one town.
    two = gp.Places([{"postcode": "02000", "place": "Simpang Ampat", "state": "Perlis", "lat": "6.40", "lon": "100.2"},
                     {"postcode": "02100", "place": "Simpang Ampat", "state": "Perlis", "lat": "6.72", "lon": "100.2"},
                     {"postcode": "01000", "place": "Kangar", "state": "Perlis", "lat": "6.40", "lon": "100.2"},
                     {"postcode": "01007", "place": "Kangar", "state": "Perlis", "lat": "6.535", "lon": "100.2"}])
    assert gp.km(6.40, 100.2, 6.72, 100.2) == pytest.approx(35.6, abs=0.1)
    assert two.town("SIMPANG AMPAT", "Perlis") is None
    assert two.town("KANGAR", "Perlis") == ("Kangar", pytest.approx((6.4675, 100.2)))

    districts = gp.Districts({f"{KL}|Kepong": {"key": f"{KL}|Kepong", "lat": "3.2", "lon": "101.63"}},
                             geocoder(Fake()), {})
    shop = {"premise_code": "1", "premise": "AEON BIG ( KEPONG )", "district": "Kepong", "state": KL}
    a = gp.anchor_for({**shop, "address": "NO.2, JALAN METRO PERDANA, 52100 KUALA LUMPUR"}, places, districts)
    # "KUALA LUMPUR" is the state here, not the town: the search text uses the postcode's place.
    assert a["kind"] == "postcode" and a["label"] == "52100" and a["context"] == "Kepong"
    a = gp.anchor_for({**shop, "address": "NO.1, JALAN METRO PRIMA,KEPONG,"}, places, districts)
    assert a["kind"] == "town" and a["context"] == "Kepong"
    a = gp.anchor_for({**shop, "address": "JALAN 1"}, places, districts)
    assert a["kind"] == "district" and a["point"] == (3.2, 101.63) and a["context"] == "Kepong"


# GeoNames rows for postcodes caught in a run of lot or house numbers.
RUN_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "81100", "Johor Bahru", "Johor", "01", "", "", "", "", "1.5000", "103.7600", "4"],
    ["MY", "88000", "Kota Kinabalu", "Sabah", "12", "", "", "", "", "5.9750", "116.0725", "4"],
    ["MY", "91100", "Lahad Datu", "Sabah", "12", "", "", "", "", "5.0300", "118.3300", "4"],
    ["MY", "47500", "Subang Jaya", "Selangor", "10", "", "", "", "", "3.0790", "101.5880", "4"],
    ["MY", "40150", "Subang", "Selangor", "10", "", "", "", "", "3.1200", "101.5500", "4"],
    ["MY", "34567", "Kampung Baru", "Perak", "08", "", "", "", "", "4.8000", "100.7000", "4"],
    ["MY", "12345", "Butterworth", "Pulau Pinang", "07", "", "", "", "", "5.4000", "100.3700", "4"],
    ["MY", "12000", "Kampung Baru", "Pulau Pinang", "07", "", "", "", "", "5.3900", "100.3800", "4"],
])


def test_lot_title_and_box_numbers_are_not_postcodes():
    for address in ["PTD 52100, JALAN ENAU 14,TAMAN TERATAI,",    # a real Johor Bahru shop; 52100 is Kepong, KL
                    "PTD 92368, JALAN RINTING,TAMAN RINTING, MASAI,",
                    "LOT 12081-12085,JALAN PULAU MELAKA, SERING,KOTA BHARU",
                    "NO:PT 35345 & PT 35346 (G),DATARAN AUSTIN, WAKAF TEMBESU",
                    "LOT 16794 & 16895, JALAN SETIAWANGSA,TAMAN SETIAWANGSA,",
                    "Ground Floor, Survey Lot 11211 of Parent Lot 10816,,Section 64,,",
                    "PASAR RAKYAT LAHAD DATU, P.O BOX 60249, LAHAD DATU, SABAH,",
                    "JALAN KLINIK,PETI SURAT 61758,",
                    "TB.14999,JALAN DATUK CHONG THIEN VUN,TAWAU",
                    "NO-15817,LORONG 1,TAMAN MENTIGA JAYA,",
                    "HS(D) 12345, PLOT 23456, GRN 34567, KAMPUNG BARU"]:
        assert gp.postcodes_in(address) == [], address
    assert gp.postcode_of("PTD 52100, JALAN ENAU 14,TAMAN TERATAI,") == "52100"   # the old helper is unchanged
    # Real postcodes stay, also right after a list of house numbers or a box number.
    assert gp.postcodes_in("NO 3,47500 SUBANG JAYA, SELANGOR") == ["47500"]
    assert gp.postcodes_in("LOT 4,5,6,PERSIARAN PERPADUAN USJ 1,47600 SUBANG JAYA") == ["47600"]
    assert gp.postcodes_in("JALAN TUN MUSTAPHA, P.O. BOX 80838, 87018 WP LABUAN") == ["87018"]
    # A group followed by a place name first; then later groups first.
    assert gp.postcodes_in("11697 JALAN KELANTAN, 20050 KUALA TERENGGANU") == ["20050", "11697"]
    assert gp.postcodes_in("40000 SHAH ALAM, 40100 SHAH ALAM") == ["40100", "40000"]
    assert gp.best_postcode("PTD 52100, JALAN ENAU 14,") == "" and gp.best_postcode("52100 KEPONG") == "52100"
    # A real postcode after a house or lot number ('NO 10 - 81100 JOHOR BAHRU') is not part of the
    # run when GeoNames has it with a place right after it. Without the table nothing in a run counts.
    run = gp.Places(fpc.parse(RUN_TXT))
    cases = [("NO 10 - 81100 JOHOR BAHRU", "Johor", "81100"),
             ("KEDAI NO 7 - 88000 KOTA KINABALU", "Sabah", "88000"),
             ("LOT 5 & 47500 SUBANG JAYA", "Selangor", "47500"),
             ("NO 47500 SUBANG", "Selangor", "47500"),
             ("BOX 123 - 88000 KOTA KINABALU", "Sabah", "88000"),
             ("LOT 5 & 47500SUBANG JAYA", "Selangor", "47500")]
    for address, state, pc in cases:
        assert gp.postcodes_in(address, run) == [pc], address
        assert gp.postcodes_in(address, run, state) == [pc] and gp.best_postcode(address, run, state) == pc
        assert gp.postcodes_in(address) == [], address
    # Still lot, title or box numbers with the table: not a GeoNames postcode (60249), another
    # state's postcode (12345 is Penang's, for a Perak shop), or no place right after it.
    for address, state in [("PASAR RAKYAT LAHAD DATU, P.O BOX 60249, LAHAD DATU, SABAH,", "Sabah"),
                           ("HS(D) 12345, PLOT 23456, GRN 34567, KAMPUNG BARU", "Pulau Pinang"),
                           ("LOT 12345, KAMPUNG BARU", "Perak"),
                           ("PTD 52100, JALAN ENAU 14,TAMAN TERATAI,", "Johor"),
                           ("LOT 16794 & 16895, JALAN SETIAWANGSA,TAMAN SETIAWANGSA,", "W.P. Kuala Lumpur")]:
        assert gp.postcodes_in(address, run, state) == [], address
    assert gp.postcodes_in("GRN 34567, KAMPUNG BARU", run, "Perak") == ["34567"]   # Perak's own
    assert gp.postcodes_in("LOT 12345, KAMPUNG BARU", run, "Pulau Pinang") == ["12345"]
    # A "place" named like a road (a table made from old addresses has "11570, Jalan Abdul Rahman
    # Talib") never rescues a number, gives strong evidence, or is a town.
    road = gp.Places(fpc.parse(RUN_TXT + "\nMY\t11570\tJalan Abdul Rahman Talib\tPahang\t06\t\t\t\t\t3.75\t103.21\t4"))
    address = "LOT 11570 JALAN ABDUL RAHMAN TALIB,KG JAYA GADING,KUANTAN, PAHANG"
    assert gp.postcodes_in(address, road, "Pahang") == [] and road.towns_in(address, "Pahang") == []
    assert road.named_after("11570", ["JALAN", "ABDUL", "RAHMAN", "TALIB"]) is False
    # The cache's postcode column prefers a postcode of the shop's own state.
    two = gp.Places(fpc.parse(MY_TXT + "\n" + KB_TXT))
    assert gp.best_postcode("JALAN ENAU, 81100 JOHOR BAHRU, 52100 KEPONG") == "52100"
    assert gp.best_postcode("JALAN ENAU, 81100 JOHOR BAHRU, 52100 KEPONG", two, "Johor") == "81100"



def test_town_in_a_road_name_is_not_an_anchor():
    kb = gp.Places(fpc.parse(KB_TXT))

    def names(address):
        return [n for n, _ in kb.towns_in(address, "Kelantan")]

    shop_21684 = "PT 5055-5059 KOMPLEKS PERNIAGAAN SAIDINA ALI,JALAN KUALA KRAI,KETEREH"
    assert names(shop_21684) == ["Ketereh"] and kb.town(shop_21684, "Kelantan")[0] == "Ketereh"
    for road in ("JALAN", "JLN.", "LORONG", "LRG", "PERSIARAN", "LEBUH", "LEBUHRAYA"):
        assert names(f"NO 1, {road} KUALA KRAI, TAMAN X") == [], road
    # A route "A-B" names two places the shop is not in.
    assert names("KM 5, BULATAN KOTA BHARU-KUALA KRAI, KETEREH") == ["Ketereh"]
    assert names("JALAN PASIR MAS - KOTA BHARU, KETEREH") == ["Ketereh"]
    assert names("KM 3 KOTA BHARU - PASIR MAS, KETEREH") == ["Ketereh"]
    # The same name standing alone still counts; so does a name after other words or a number.
    assert names("JALAN KUALA KRAI, KUALA KRAI") == ["Kuala Krai"]
    assert names("JALAN BESAR KETEREH,") == ["Ketereh"] and names("SEKSYEN 13-KETEREH") == ["Ketereh"]
    # A dash makes a route only between two GeoNames places: "SAIDINA - KETEREH" is a shop name
    # and a town (it used to drop the town).
    assert names("KOMPLEKS SAIDINA - KETEREH,") == ["Ketereh"] and names("SAIDINA-KETEREH") == ["Ketereh"]
    # ... or when it follows a street word: the whole is the road's name, whatever the other end
    # is (Jeli is not in this table; "KCH" is short for Kuching).
    assert names("BANDAR BARU JELI JALAN JELI-DABONG, KETEREH") == ["Ketereh"]
    assert names("BATU 22, JALAN KCH - KUALA KRAI") == [] and names("LEBUHRAYA X Y-DABONG") == []
    # A shorter place name inside a road name does not count either.
    krai = gp.Places(fpc.parse(KB_TXT + "\nMY\t18050\tKrai\tKelantan\t03\t\t\t\t\t5.60\t102.20\t4"))
    assert [n for n, _ in krai.towns_in("JALAN KUALA KRAI, KETEREH", "Kelantan")] == ["Ketereh"]


def test_anchor_must_be_in_the_shop_state_and_near_its_district():
    places = gp.Places(fpc.parse(MY_TXT + "\n" + KB_TXT))

    def districts(rows, cache=None):
        return gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in rows.items()},
                            geocoder(Fake()), cache or {})

    d = districts({"Johor|Johor Bahru": JB_CENTRE, "Kelantan|Kota Bharu": KB_CENTRE})
    jb = {"premise_code": "1", "premise": "KEDAI", "district": "Johor Bahru", "state": "Johor"}
    # 52100 is a KL postcode: in a Johor address it is a typo, not the anchor; the town is.
    a = gp.anchor_for({**jb, "address": "NO 5, JALAN ENAU 14, 52100 JOHOR BAHRU"}, places, d)
    assert (a["kind"], a["label"]) == ("town", "Johor Bahru")
    # The next postcode is tried: the KL one fails, the Johor one counts.
    a = gp.anchor_for({**jb, "address": "JALAN ENAU, 81100 JOHOR BAHRU, 52100 KEPONG"}, places, d)
    assert (a["kind"], a["label"], a["postcode"]) == ("postcode", "81100", "81100")
    a = gp.anchor_for({**jb, "address": "PTD 52100, JALAN ENAU 14,TAMAN TERATAI,"}, places, d)
    assert (a["kind"], a["point"], a["postcode"]) == ("district", JB_CENTRE, "")
    # Same state but 66 km from a trusted centre: a town alone is weak evidence, not the anchor.
    kb = {"premise_code": "2", "premise": "KEDAI", "district": "Kota Bharu", "state": "Kelantan"}
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, KUALA KRAI"}, places, d)
    assert (a["kind"], a["point"]) == ("district", KB_CENTRE)
    # "18000 KUALA KRAI" (the postcode's own GeoNames place right after it) is strong: it may lie
    # up to 80 km away, but only when nothing nearer is in the address (Ketereh, 19 km).
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, 18000 KUALA KRAI"}, places, d)
    assert (a["kind"], a["label"]) == ("postcode", "18000") and a["strong"] is True
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, 18000 KUALA KRAI, KETEREH"}, places, d)
    assert (a["kind"], a["label"]) == ("town", "Ketereh") and a["strong"] is False
    # A shared centre is not trusted: the median of the district's located shops is the reference.
    shared = districts({"Kelantan|Kota Bharu": KB_CENTRE, "Kelantan|Bachok": KB_CENTRE},
                       {c: cache_row(c, 6.13, 102.24 + c / 1000, "name", district="Kota Bharu", state="Kelantan")
                        for c in (7, 8)})
    assert shared.trusted("Kelantan", "Kota Bharu") is None
    a = gp.anchor_for({**kb, "address": "NO 1, KUALA KRAI"}, places, shared)
    assert a["kind"] == "district" and a["point"] == pytest.approx((6.13, 102.2475))
    # No trusted centre and no located shops: only the state is checked, and it still keeps
    # other states' postcodes out.
    alone = districts({"Kelantan|Kota Bharu": KB_CENTRE, "Kelantan|Bachok": KB_CENTRE})
    a = gp.anchor_for({**kb, "address": "NO 1, 18000 KUALA KRAI"}, places, alone)
    assert (a["kind"], a["label"]) == ("postcode", "18000")
    a = gp.anchor_for({**kb, "address": "NO 1, 81100 JOHOR BAHRU"}, places, alone)
    assert (a["kind"], a["point"], a["postcode"]) == ("district", KB_CENTRE, "81100")
    # A state spelt in a way the table does not know: the distance check alone decides, and
    # with nothing to measure against no postcode counts.
    odd = {**kb, "state": "Kelantan Darul Naim"}
    assert places.knows("W.P. Kuala Lumpur") and places.knows("Kelantan") and not places.knows(odd["state"])
    near_kb = districts({"Kelantan Darul Naim|Kota Bharu": KB_CENTRE})
    assert gp.anchor_for({**odd, "address": "15000 KOTA BHARU"}, places, near_kb)["label"] == "15000"
    assert gp.anchor_for({**odd, "address": "81100 JOHOR BAHRU"}, places, near_kb)["kind"] == "district"
    assert gp.anchor_for({**odd, "address": "15000 KOTA BHARU"}, places, districts({})) is None
    # Limbang's centre is in the middle of a large district, 44 km from its town. "98700 LIMBANG"
    # and "LIMBANG" (the district's name) are strong evidence, so they still count.
    lb = {"premise_code": "3", "premise": "KEDAI", "district": "Limbang", "state": "Sarawak", "address": "98700 LIMBANG"}
    centre = (4.352, 115.060)
    plain = districts({"Sarawak|Limbang": centre})
    assert gp.km(*centre, 4.75, 115.0) == pytest.approx(44.8, abs=0.1)
    assert (gp.anchor_for(lb, places, plain)["label"], gp.anchor_for(lb, places, plain)["strong"]) == ("98700", True)
    assert gp.anchor_for({**lb, "address": "PASAR LIMBANG"}, places, plain)["kind"] == "town"
    # Postcode or town points from an earlier run do not vouch for the district (they may be the
    # wrong ones, as at Kalabakan): the centre is still the reference ...
    near_town = {c: cache_row(c, 4.69, 114.98 + c / 1000, "postcode", district="Limbang", state="Sarawak")
                 for c in (9, 10)}
    limbang = districts({"Sarawak|Limbang": centre}, near_town)
    assert limbang.trusted("Sarawak", "Limbang") == centre and limbang.references("Sarawak", "Limbang") == [centre]
    assert gp.anchor_for(lb, places, limbang)["label"] == "98700"
    # ... but a shop with no postcode or town is searched for around them (38 km from the centre),
    # not in the middle of the district.
    a = gp.anchor_for({**lb, "address": "KAMPUNG X"}, places, limbang)
    assert a["kind"] == "district" and a["point"] == pytest.approx((4.69, 114.9895))
    # Shops OpenStreetMap found there are a reference, and then the centre is not trusted.
    found = {c: cache_row(c, 4.69, 114.98 + c / 1000, "address", district="Limbang", state="Sarawak")
             for c in (9, 10)}
    limbang = districts({"Sarawak|Limbang": centre}, found)
    assert limbang.trusted("Sarawak", "Limbang") is None
    assert limbang.references("Sarawak", "Limbang") == [pytest.approx((4.69, 114.9895))]


def test_road_towns_and_lot_numbers_do_not_move_shops(tmp_path):
    """The two real shops from the review: one was moved 66 km to Kuala Krai, one 302 km to KL."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", MY_TXT + "\n" + KB_TXT)
    data = write_build(tmp_path / "data", {
        ("Kelantan", "Kota Bharu"): [(21684, "PASARAYA SAIDINA ALI",
                                      "PT 5055-5059 KOMPLEKS PERNIAGAAN SAIDINA ALI,JALAN KUALA KRAI,KETEREH")],
        ("Johor", "Johor Bahru"): [(20860, "KEDAI TERATAI", "PTD 52100, JALAN ENAU 14,TAMAN TERATAI,")]})
    old = cache_row(21684, *KB_CENTRE, "district", district="Kota Bharu", state="Kelantan", query="Kota Bharu, Kelantan")
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], [old])
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Kelantan|Kota Bharu", "lat": str(KB_CENTRE[0]), "lon": str(KB_CENTRE[1])},
                        {"key": "Johor|Johor Bahru", "lat": str(JB_CENTRE[0]), "lon": str(JB_CENTRE[1])}])
    fake = Fake()
    gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    kb, jb = rows["21684"], rows["20860"]
    assert (kb["precision"], kb["query"]) == ("town", "Ketereh")
    assert gp.km(float(kb["lat"]), float(kb["lon"]), 5.9561, 102.2432) < 0.1
    assert (jb["precision"], jb["postcode"]) == ("district", "")
    assert gp.km(float(jb["lat"]), float(jb["lon"]), *JB_CENTRE) < 0.1
    boxes = {c["q"]: c["viewbox"] for c in fake.calls}
    assert boxes["PASARAYA SAIDINA ALI, Ketereh"] == gp.viewbox(5.9561, 102.2432, 0.15)
    assert boxes["KEDAI TERATAI, Johor Bahru"] == gp.viewbox(*JB_CENTRE, 0.3)


def test_a_wrong_postcode_point_from_an_earlier_run_is_replaced(tmp_path):
    """A row written before these checks (a lot number read as KL's 52100) is not kept by the
    'never swap for a worse point' rule: a retry puts the shop back in its district."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", MY_TXT + "\n" + KB_TXT)
    data = write_build(tmp_path / "data", {("Johor", "Johor Bahru"): [
        (20860, "KEDAI TERATAI", "PTD 52100, JALAN ENAU 14,TAMAN TERATAI,")]})
    bad = cache_row(20860, 3.212, 101.643, "postcode", district="Johor Bahru", state="Johor", query="52100",
                    postcode="52100", tries=1, last="2026-10-01")
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [bad])
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Johor|Johor Bahru", "lat": str(JB_CENTRE[0]), "lon": str(JB_CENTRE[1])}])
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    row = read_rows(cache)["20860"]
    assert (row["precision"], row["postcode"], row["tries"]) == ("district", "", "2")
    assert gp.km(float(row["lat"]), float(row["lon"]), *JB_CENTRE) < 0.1


# Rompin (Pahang): the centre is inland, the shops OpenStreetMap found are in Muadzam Shah, and
# Kuala Rompin is 43 km from the centre. GeoNames-like rows (approximate real points); Selendang is
# a made-up weak town at the same distance.
ROMPIN_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "26700", "Muadzam Shah", "Pahang", "06", "", "", "", "", "3.0547", "103.0801", "4"],
    ["MY", "26800", "Kuala Rompin", "Pahang", "06", "", "", "", "", "2.8061", "103.4851", "4"],
    ["MY", "26810", "Selendang", "Pahang", "06", "", "", "", "", "2.5200", "103.3450", "4"],
])
ROMPIN_CENTRE, KUALA_ROMPIN, MUADZAM = (2.824577, 103.099356), (2.8061, 103.4851), (3.05698, 103.084566)
# The 11 real shops whose address says KUALA ROMPIN.
KUALA_ROMPIN_SHOPS = [
    (1152, "PT 2269 & PT 2270,DEPAN BANDAR BARU,KUALA ROMPIN,PAHANG DARUL MAKMUR."),
    (14332, "BH16, 17, 18 & 19,JALAN SEHALA TAMAN WAWASAN 01,TANJUNG GEMOK,KUALA ROMPIN"),
    (15118, "JALAN PASAR ROMPIN,KUALA ROMPIN,"),
    (15664, "NO.75, 77, 78, 79 & 80, JALAN PASAR,LOT 5002 & LOT 5003, SEK. 1,KUALA ROMPIN,PAHANG DARUL MAKMUR."),
    (20747, "NO. 13,14, 15, JLN. ROMPIN JAYA UTAMA,KUALA ROMPIN,"),
    (20995, "NO. 25 & 26 (GF),JALAN WAWASAN B.BARU,KUALA ROMPIN"),
    (20997, "NO. 07 & 08(GF),TAMAN PUTERI INDAH UTAMA,KUALA ROMPIN"),
    (20998, "NO. 13,14, JALAN IDAMAN JAYA 1,TAMAN IDAMAN JAYA,KUALA ROMPIN"),
    (21500, "NO.2, KEDAI JALAN SELESA JAYA 1,,TAMAN SELESA JAYA,KUALA ROMPIN"),
    (21512, "E2-7089, KAMPUNG BAHAGIA,,KUALA ROMPIN,"),
    (21807, "NO.70 & 71 JALAN WAWASAN,,BANDAR BARU,,KUALA ROMPIN,"),
]


def test_a_town_named_after_the_district_may_be_further_away(tmp_path):
    """Kuala Rompin is 43 km from Rompin's centre and 27 km beyond the shops OpenStreetMap found:
    it is in the district's name, so it counts (up to 80 km). A weak town that far does not."""
    assert gp.km(*ROMPIN_CENTRE, *KUALA_ROMPIN) == pytest.approx(42.9, abs=0.1)
    pc = write_postcodes(tmp_path / "postcodes_my.csv", ROMPIN_TXT)
    found = [(14840 + i, "BS FRESHMART", "JALAN MEDAN MEWAH 12,BANDAR MUADZAM SHAH,MUADZAM SHAH") for i in range(4)]
    data = write_build(tmp_path / "data", {("Pahang", "Rompin"): [
        (c, f"KEDAI {c}", a) for c, a in KUALA_ROMPIN_SHOPS] + found + [
        (10214, "TUNAS MANJA (MUADZAM SHAH)", "LOT. PT232,MUKIM BEBAR,MUADZAM SHAH"),
        (30000, "KEDAI SELENDANG", "NO 1, KAMPUNG SELENDANG,SELENDANG")]})
    v1 = [cache_row(c, *ROMPIN_CENTRE, "district", district="Rompin", state="Pahang", query="Rompin, Pahang")
          for c, _ in KUALA_ROMPIN_SHOPS + [(10214, "")]]
    v1 += [cache_row(c, *MUADZAM, "address", district="Rompin", state="Pahang", tries=1, last="2026-11-01")
           for c, *_ in found]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, v1)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Pahang|Rompin", "lat": str(ROMPIN_CENTRE[0]), "lon": str(ROMPIN_CENTRE[1])}])
    fake = Fake()
    gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    for c, _ in KUALA_ROMPIN_SHOPS:
        r = rows[str(c)]
        assert (r["precision"], r["query"], r["tries"]) == ("town", "Kuala Rompin", "1"), c
        assert gp.km(float(r["lat"]), float(r["lon"]), *KUALA_ROMPIN) < 0.1
    boxes = {c["q"].split(",")[0]: c["viewbox"] for c in fake.calls}
    assert boxes["KEDAI 1152"] == gp.viewbox(*KUALA_ROMPIN, 0.15)
    assert (rows["10214"]["precision"], rows["10214"]["query"]) == ("town", "Muadzam Shah")   # 26 km: weak is fine
    # A weak town 43 km from the centre (and 66 km from the found shops) is still not used.
    assert gp.km(*ROMPIN_CENTRE, 2.52, 103.345) == pytest.approx(43.5, abs=0.2)
    assert rows["30000"]["precision"] == "district"
    assert gp.km(float(rows["30000"]["lat"]), float(rows["30000"]["lon"]), *ROMPIN_CENTRE) < 0.1


TAWAU_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "91000", "Tawau", "Sabah", "12", "", "", "", "", "4.2448", "117.8912", "4"],
    ["MY", "91007", "Tawau", "Sabah", "12", "", "", "", "", "4.2448", "117.8912", "4"],
    ["MY", "91308", "Semporna", "Sabah", "12", "", "", "", "", "4.4800", "118.6100", "4"],
])
KALABAKAN, TAWAU, OLD_91000 = (4.480894, 117.407133), (4.2448, 117.8912), (4.724542, 117.389679)


def test_a_postcode_followed_by_its_own_place_is_strong():
    tw = gp.Places(fpc.parse(TAWAU_TXT))
    near = [KALABAKAN]
    assert gp.km(*KALABAKAN, *TAWAU) == pytest.approx(59.7, abs=0.1)
    for address in ("TAMAN SEMARAK, 91000 TAWAU, SABAH,", "PUSAT KOMERSIAL GAYA, 91000TAWAU, SABAH,",
                    "PUSAT KOMERSIAL GAYA, 91000\rTAWAU, SABAH,", "MILE 5, 91000, TAWAU"):
        assert gp.postcodes_in(address, tw, "Sabah") == ["91000"], address
        assert tw.postcode_anchor(["91000"], "Sabah", near, address) == ("91000", TAWAU, True), address
    # Another place after it, or none: weak, so 60 km is too far.
    for address in ("91000 SEMPORNA", "TAMAN SEMARAK, 91000", "91000 JALAN APAS"):
        assert tw.postcode_anchor(["91000"], "Sabah", near, address) is None, address
        assert tw.postcode_anchor(["91000"], "Sabah", [TAWAU], address)[2] is False
    assert tw.postcode_anchor(["91000"], "Sabah", near, "91000 TAWAU", gp.CHECK_KM) is None
    assert tw.postcode_anchor(["91000"], "Sabah", [(5.98, 116.07)], "91000 TAWAU") is None   # 220 km


# The real Kalabakan shops and their rows from the first run: four '91000 TAWAU' shops were put
# at 4.72,117.39 (77 km from Tawau), and those rows made up the district's "located" median.
KALABAKAN_SHOPS = [
    (2637, "ERA SUPERSTORE",
     "KAMPUNG MEROTAI BESAR, P/S 21218,LOT A 25/1522, KAMPUNG MEROTAI BESAR,91007 TAWAU, SABAH.", "district"),
    (2641, "PASAR SEMARAK", "TAMAN SEMARAK,MILE 5, 91000 TAWAU, SABAH,", "postcode"),
    (17280, "D'UTAMA", "MEROTAI BESAR, TAWAU,", "district"),
    (17634, "CITY FRESH MART SEMARAK", "TAMAN SEMARAK, 91000 TAWAU,", "postcode"),
    (17849, "INSTAR SUPERFRESH S/B (SIN ONN)",
     "CL 105329008, CL 105329217, TB 3579, MILE 2.5, JLN SIN ONN, 91000 TAWAU,", "postcode"),
    (17853, "INSMARK SUPERFRESH", "PUSAT KOMERSIAL GAYA, 91000\rTAWAU, SABAH,", "postcode"),
    (21963, "SERVAY JAYA SUPERMARKET SDN.BHD", "Tawau,Tawau,", "address"),
]


def test_old_postcode_points_do_not_vouch_for_themselves(tmp_path):
    """Kalabakan: only shops OpenStreetMap found (name or address) say where a district's shops
    are. The four wrong '91000 TAWAU' rows move to Tawau on the retry; '91007 TAWAU' too."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", TAWAU_TXT)
    data = write_build(tmp_path / "data", {("Sabah", "Kalabakan"): [(c, n, a) for c, n, a, _ in KALABAKAN_SHOPS]})
    v1 = [cache_row(c, *(OLD_91000 if p == "postcode" else KALABAKAN), p, district="Kalabakan", state="Sabah",
                    query="91000" if p == "postcode" else "Kalabakan, Sabah",
                    postcode="91000" if p == "postcode" else "")
          for c, _, _, p in KALABAKAN_SHOPS]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], v1)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Sabah|Kalabakan", "lat": str(KALABAKAN[0]), "lon": str(KALABAKAN[1])}])
    d = gp.Districts(read_rows(dcache), geocoder(Fake()), read_rows(cache))
    assert d.references("Sabah", "Kalabakan") == [KALABAKAN]   # not the median of the wrong rows
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    for code in ("2641", "17634", "17849", "17853", "2637"):
        r = rows[code]
        assert (r["precision"], r["tries"]) == ("postcode", "1"), code
        assert gp.km(float(r["lat"]), float(r["lon"]), *TAWAU) < 0.1, code
    assert rows["2637"]["postcode"] == "91007"
    # "MEROTAI BESAR, TAWAU": a town alone is weak, and Tawau is 60 km from the centre.
    assert rows["17280"]["precision"] == "district"
    assert gp.km(float(rows["17280"]["lat"]), float(rows["17280"]["lon"]), *KALABAKAN) < 0.1
    assert rows["21963"]["precision"] == "address"   # found by OpenStreetMap: not retried


CYBER_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "63000", "Cyberjaya", "Selangor", "10", "", "", "", "", "2.9213", "101.6559", "4"],
    ["MY", "62000", "Putrajaya", "Wilayah Persekutuan Putrajaya", "16", "", "", "", "", "2.9264", "101.6964", "4"],
    ["MY", "68000", "Ampang", "Selangor", "10", "", "", "", "", "3.1500", "101.7600", "4"],
    ["MY", "41000", "Klang", "Selangor", "10", "", "", "", "", "3.0449", "101.4456", "4"],
    ["MY", "50000", "Kuala Lumpur", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.1478", "101.6953", "4"],
    ["MY", "54200", "Setiawangsa", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.1800", "101.7300", "4"],
    ["MY", "93000", "Kuching", "Sarawak", "13", "", "", "", "", "1.5535", "110.3593", "4"],
    ["MY", "94700", "Serian", "Sarawak", "13", "", "", "", "", "1.1667", "110.5667", "4"],
])


def test_cyberjaya_and_kl_shops_may_use_selangor_places():
    """PriceCatcher files Cyberjaya under W.P. Putrajaya; GeoNames puts 63000 in Selangor."""
    places = gp.Places(fpc.parse(CYBER_TXT))
    d = gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in {
        "W.P. Putrajaya|Cyberjaya": (2.914883, 101.669659), f"{KL}|Setiawangsa": (3.19, 101.728),
        "Selangor|Petaling": (3.07, 101.59), "Sarawak|Kuching": (1.5535, 110.3593)}.items()}, geocoder(Fake()), {})
    cyber = {"premise_code": "20863", "premise": "PASAR MINI GOOD DAY", "district": "Cyberjaya",
             "state": "W.P. Putrajaya",
             "address": "A-G-10, CYBER SQUARE, JALAN TEKNOKRAT 6, 63000, CYBERJAYA, SELANGOR."}
    a = gp.anchor_for(cyber, places, d)
    assert (a["kind"], a["label"], a["postcode"]) == ("postcode", "63000", "63000") and a["strong"] is True
    # "CYBERSQUARE - CYBERJAYA" is not a route (CYBERSQUARE is no place): Cyberjaya is the town.
    star = "Star Grocer Cybersquare - Cyberjaya,,Jalan Teknokrat 6,Cyber 5,"
    a = gp.anchor_for({**cyber, "address": star}, places, d)
    assert (a["kind"], a["label"]) == ("town", "Cyberjaya")
    # W.P. Kuala Lumpur shops too, but a Selangor postcode or town counts only as strong evidence
    # (and the distance checks still apply): KL roads are named after Selangor towns, and three
    # real Setapak shops on "JALAN GENTING KLANG" would go 30-39 km to Klang. Selangor shops do
    # not get KL places this way.
    kl = {"premise_code": "1", "premise": "KEDAI", "district": "Setiawangsa", "state": KL,
          "address": "NO 1, 68000 AMPANG"}
    assert gp.anchor_for(kl, places, d)["label"] == "68000"
    for address in ("NO 1, AMPANG", "NO 1, JALAN 2, 68000",
                    "NO. 6,7,8 & 9 @ LOT 1617, BATU 4 1/2,JALAN GENTING KLANG, SETAPAK,"):
        assert gp.anchor_for({**kl, "address": address}, places, d)["kind"] == "district", address
    sel = {"premise_code": "2", "premise": "KEDAI", "district": "Petaling", "state": "Selangor",
           "address": "NO 1, 54200 SETIAWANGSA"}
    assert gp.anchor_for(sel, places, d)["kind"] == "district"
    # A route still never anchors at either end.
    kch = {"premise_code": "3", "premise": "KEDAI", "district": "Kuching", "state": "Sarawak"}
    for address in ("LOT 1, JALAN KUCHING-SERIAN, KAMPUNG X", "KM 10 KUCHING-SERIAN ROAD", "KUCHING - SERIAN",
                    "SHOPLOT NO. 9, BATU 22,,JALAN KCH-SERIAN"):
        assert places.towns_in(address, "Sarawak") == [], address
        assert gp.anchor_for({**kch, "address": address}, places, d)["kind"] == "district"


STATES_TXT = "\n".join("\t".join(r) for r in [
    ["MY", "75000", "Melaka", "Melaka", "04", "", "", "", "", "2.1960", "102.2480", "4"],
    ["MY", "77000", "Jasin", "Melaka", "04", "", "", "", "", "2.3096", "102.4281", "4"],
    ["MY", "10000", "Pulau Pinang", "Pulau Pinang", "07", "", "", "", "", "5.4141", "100.3288", "4"],
    ["MY", "10200", "Penang", "Pulau Pinang", "07", "", "", "", "", "5.4100", "100.3300", "4"],
    ["MY", "50000", "Kuala Lumpur", "Wilayah Persekutuan Kuala Lumpur", "14", "", "", "", "", "3.1478", "101.6953", "4"],
    ["MY", "87000", "Labuan", "Wilayah Persekutuan Labuan", "15", "", "", "", "", "5.2831", "115.2308", "4"],
])


def test_the_state_name_is_not_a_town():
    """'..., JASIN, MELAKA': MELAKA is the state here. It used to win as the longer name."""
    places = gp.Places(fpc.parse(STATES_TXT))
    no_jasin = gp.Places(fpc.parse("\n".join(x for x in STATES_TXT.splitlines() if "Jasin" not in x)))
    d = gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in {
        "Melaka|Jasin": (2.31, 102.43), "Melaka|Melaka Tengah": (2.20, 102.25), f"{KL}|Bukit Bintang": (3.147, 101.711),
        "Pulau Pinang|Timur Laut": (5.41, 100.33), "W.P. Labuan|W.P Labuan": (5.28, 115.23)}.items()},
        geocoder(Fake()), {})

    def anchor(state, district, address, table=places):
        a = gp.anchor_for({"premise_code": "1", "premise": "KEDAI", "state": state, "district": district,
                           "address": address}, table, d)
        return a["kind"], a["label"]

    assert anchor("Melaka", "Jasin", "NO 5, JALAN SETIA, JASIN, MELAKA") == ("town", "Jasin")
    assert anchor("Melaka", "Jasin", "NO 5, JALAN SETIA, JASIN, MELAKA", no_jasin)[0] == "district"
    assert anchor("Melaka", "Melaka Tengah", "NO 5, JALAN HANG TUAH, MELAKA") == ("town", "Melaka")
    assert anchor(KL, "Bukit Bintang", "NO 5, JALAN ALOR, KUALA LUMPUR")[0] == "district"
    assert anchor("Pulau Pinang", "Timur Laut", "NO 5, LEBUH CHULIA, PULAU PINANG")[0] == "district"
    assert anchor("Pulau Pinang", "Timur Laut", "NO 5, LEBUH CHULIA, PENANG")[0] == "district"
    assert anchor("W.P. Labuan", "W.P Labuan", "JALAN POHON BATU, LABUAN") == ("town", "Labuan")
    # A postcode is still a postcode: "75000 MELAKA" is Melaka city.
    assert anchor("Melaka", "Jasin", "NO 5, 75000 MELAKA") == ("postcode", "75000")


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
                                                (2, "PASAR MINI X", "NO 5, KEPONG")], [old])
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
    fake = Fake({"AEON BIG ( KEPONG ), Kepong": shop_pt})
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
    assert st["looked_up"] == 2 and st["skipped"] == 9      # only shops 1 and 2 reached Nominatim
    #                                                         (8 new ones and shop 99 skipped)
    rows = read_rows(cache)
    assert rows["1"]["tries"] == "1"                                # 3 calls, all answered
    assert all(rows[str(i)]["tries"] == "0" for i in range(2, 11))  # skipped: never marked as tried
    assert rows["99"] == {**old, "tries": "0", "last_tried": ""}


def test_stats_count_only_shops_that_reached_nominatim(tmp_path):
    shops = [(i, f"KEDAI {i}", f"JALAN {i}, 52100 KUALA LUMPUR") for i in range(1, 16)]
    old = cache_row(99, 3.2, 101.63, "district", tries=0, last="")
    data, cache, dcache, pc = kepong(tmp_path, shops + [(99, "KEDAI LAMA", "JALAN LAMA")], [old])
    st = gp.run(data, cache, dcache, geocoder(Fake(default=net_error())), postcodes_path=pc, today=TODAY)
    # 10 shops hit one error each, then the breaker stops all calls: 5 never reach Nominatim,
    # and the retry pass does not start (its one shop is skipped too).
    assert (st["looked_up"], st["skipped"], st["retried"]) == (10, 6, 0)
    assert (st["offline"], st["kept"], st["errors"], st["requests"]) == (15, 0, 10, 10)


def test_breaker_during_the_retry_pass_counts_the_shops_not_sent(tmp_path):
    rows = [cache_row(i, 3.2, 101.63, "district", tries=0, last="") for i in range(1, 16)]
    data, cache, dcache, _ = kepong(tmp_path, [(i, f"KEDAI {i}", f"JALAN {i}") for i in range(1, 16)], rows,
                                    postcodes=False)
    due = len(gp.retry_queue(gp.shops_from_build(data), gp.read_cache(cache), TODAY, 3000))
    st = gp.run(data, cache, dcache, geocoder(Fake(default=net_error())), today=TODAY)
    assert due == 15 and st["breaker_tripped"] is True
    assert (st["retried"], st["skipped"], st["kept"]) == (10, 5, 10)
    assert st["retried"] + st["skipped"] == due
    assert {r["tries"] for r in read_rows(cache).values()} == {"0"}   # errors and skips: nothing marked tried


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


def shared_kl(tmp_path):
    """Batu, Cheras and Kepong on the old KL answer; 3 Batu shops located away from it."""
    shared = {"lat": "3.145593", "lon": "101.697390"}
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": f"{KL}|{d}", **shared} for d in ("Batu", "Cheras", "Kepong")])
    located = [cache_row(10 + i, 3.22 + i * 0.002, 101.68, "name", district="Batu", tries=1, last="2026-11-01")
               for i in range(3)]
    return write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, located), dcache


MOVED = {"Kepong, Kuala Lumpur, Malaysia": (3.2132, 101.6389), "Cheras, Kuala Lumpur, Malaysia": (3.095, 101.74)}
BATU_MEDIAN = (3.222, 101.68)


def test_district_alone_on_the_old_shared_point_keeps_using_its_shop_median(tmp_path):
    cache, dcache = shared_kl(tmp_path)
    data = write_build(tmp_path / "data", {(KL, "Batu"): [(1, "KEDAI BATU", "JALAN 1")],
                                           (KL, "Cheras"): [(2, "KEDAI CHERAS", "JALAN 2")],
                                           (KL, "Kepong"): [(3, "KEDAI KEPONG", "JALAN 3")]})
    fake = Fake(MOVED)   # Kepong and Cheras get their own centres; Batu gets nothing
    gp.run(data, cache, dcache, geocoder(fake), today=TODAY)
    boxes = {c["q"].split(",")[0]: c["viewbox"] for c in fake.calls if "viewbox" in c}
    assert boxes["KEDAI BATU"] == gp.viewbox(*BATU_MEDIAN, 0.3)     # not the old shared point
    assert boxes["KEDAI KEPONG"] == gp.viewbox(3.2132, 101.6389, 0.3)
    row = read_rows(cache)["1"]
    assert row["precision"] == "district" and (float(row["lat"]), float(row["lon"])) == pytest.approx(BATU_MEDIAN)
    d = read_rows(dcache)
    assert [d[f"{KL}|{x}"]["shared"] for x in ("Batu", "Cheras", "Kepong")] == ["1", "", ""]
    # The next run still knows Batu's point is the old shared one, though nobody shares it now.
    later = gp.Districts(read_rows(dcache), geocoder(Fake()), read_rows(cache))
    assert later.trusted(KL, "Batu") is None and later.centre(KL, "Batu") == pytest.approx(BATU_MEDIAN)
    assert later.trusted(KL, "Kepong") == (3.2132, 101.6389)


def test_district_left_alone_on_a_shared_point_is_asked_again(tmp_path):
    """Run 1 has work only in Cheras and Kepong. Batu, now alone on the old point, is still
    asked once when it has work, and is not trusted while nothing better is found."""
    cache, dcache = shared_kl(tmp_path)
    run1 = write_build(tmp_path / "run1", {(KL, "Cheras"): [(2, "KEDAI CHERAS", "JALAN 2")],
                                           (KL, "Kepong"): [(3, "KEDAI KEPONG", "JALAN 3")]})
    gp.run(run1, cache, dcache, geocoder(Fake(MOVED)), today=TODAY)
    d = read_rows(dcache)
    assert (d[f"{KL}|Batu"]["shared"], d[f"{KL}|Batu"]["tries"]) == ("1", "0")
    run2 = write_build(tmp_path / "run2", {(KL, "Batu"): [(1, "KEDAI BATU", "JALAN 1")]})
    fake = Fake({"Batu, Kuala Lumpur, Malaysia": None})
    st = gp.run(run2, cache, dcache, geocoder(fake), today=TODAY)
    assert [q for q in fake.queries() if q.endswith("Malaysia")] == ["Batu, Kuala Lumpur, Malaysia"]
    assert st["district_requeried"] == 1
    d = read_rows(dcache)
    assert (d[f"{KL}|Batu"]["shared"], d[f"{KL}|Batu"]["tries"]) == ("1", "1")
    row = read_rows(cache)["1"]
    assert (float(row["lat"]), float(row["lon"])) == pytest.approx(BATU_MEDIAN)
    # Asked once only; an answer that moves the point makes it trusted.
    fake = Fake()
    gp.run(write_build(tmp_path / "run3", {(KL, "Batu"): [(4, "KEDAI 4", "JALAN 4")]}), cache, dcache,
           geocoder(fake), today=TODAY)
    assert not [q for q in fake.queries() if q.endswith("Malaysia")]
    dc = {k: dict(v) for k, v in read_rows(dcache).items()}
    dc[f"{KL}|Batu"]["tries"] = "0"
    districts = gp.Districts(dc, geocoder(Fake({"Batu, Kuala Lumpur, Malaysia": (3.2300, 101.6800)})), {})
    districts.prepare([(KL, "Batu")])
    assert districts.trusted(KL, "Batu") == (3.23, 101.68) and dc[f"{KL}|Batu"]["shared"] == ""


def test_new_district_centre_error_is_not_cached(tmp_path):
    data = write_build(tmp_path / "data", {("Perlis", "Kangar"): [(1, "KEDAI 1", "JALAN 1")]})
    cache, dcache = tmp_path / "p.csv", tmp_path / "d.csv"
    st = gp.run(data, cache, dcache, geocoder(Fake(default=net_error())), today=TODAY)
    assert read_rows(dcache) == {} and read_rows(cache) == {} and st["missed"] == 1
    gp.run(data, cache, dcache, geocoder(Fake({"Kangar, Perlis, Malaysia": (6.44, 100.2)})), today=TODAY)
    assert read_rows(dcache)["Perlis|Kangar"]["tries"] == "1"
    assert read_rows(cache)["1"]["precision"] == "district"


LABUAN = (5.275699, 115.232815)   # the old answer for all three Labuan districts


def test_a_district_answer_outside_its_state_is_not_used(tmp_path):
    """W.P Labuan, re-asked, answered with a point near KL (1,525 km away): with the GeoNames
    table, an answer more than 50 km from every place of the district's state is not used."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", MY_TXT + "\n" + STATES_TXT)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": f"W.P. Labuan|{d}", "lat": str(LABUAN[0]), "lon": str(LABUAN[1])}
                        for d in ("Kawasan Bandar", "Kawasan Luar Bandar", "W.P Labuan")])
    data = write_build(tmp_path / "data", {
        ("W.P. Labuan", "W.P Labuan"): [(21275, "SUNDRY MART", "SP01-SP04, GANGGARAK PERMAI,,JALAN POHON BATU,")],
        ("W.P. Labuan", "Kawasan Bandar"): [(17365, "KEDAI EKONOMI", "BA. JLN UJONG PASIR,W.P LABUAN,")],
        ("Perlis", "Kangar"): [(1, "KEDAI 1", "JALAN 1")]})
    cache = tmp_path / "premise_geo.csv"
    fake = Fake({"Labuan, Labuan, Malaysia": (3.1, 101.6), "Kawasan Bandar, Labuan, Malaysia": (5.2900, 115.2500),
                 "Kawasan Luar Bandar, Labuan, Malaysia": None, "Kangar, Perlis, Malaysia": (3.2, 101.6)})
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    assert gp.km(3.1, 101.6, *LABUAN) > 1500
    d = read_rows(dcache)
    lb = d["W.P. Labuan|W.P Labuan"]
    assert ((float(lb["lat"]), float(lb["lon"])), lb["tries"], lb["shared"]) == (LABUAN, "1", "1")   # old point kept
    assert st["district_rejected"] == 2 and st["district_lookups"] == 3
    assert (float(d["W.P. Labuan|Kawasan Bandar"]["lat"]), d["W.P. Labuan|Kawasan Bandar"]["shared"]) == (5.29, "")
    assert (d["Perlis|Kangar"]["lat"], d["Perlis|Kangar"]["tries"]) == ("", "1")   # a new district: no point
    rows = read_rows(cache)
    assert gp.km(float(rows["21275"]["lat"]), float(rows["21275"]["lon"]), *LABUAN) < 1
    assert "1" not in rows   # nothing to place it by: not placed near KL either
    # A correct answer is used (0.2 km from Kangar's GeoNames row) ...
    good = gp.Districts({}, geocoder(Fake({"Kangar, Perlis, Malaysia": (6.44, 100.2)})), {},
                        gp.Places(fpc.parse(MY_TXT)))
    good.prepare([("Perlis", "Kangar")])
    assert good.trusted("Perlis", "Kangar") == (6.44, 100.2) and good.rejected == 0
    # ... and without the table there is nothing to check against (as before).
    bare = gp.Districts({}, geocoder(Fake({"Kangar, Perlis, Malaysia": (3.2, 101.6)})), {})
    bare.prepare([("Perlis", "Kangar")])
    assert bare.trusted("Perlis", "Kangar") == (3.2, 101.6) and bare.rejected == 0


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
    assert list(read_rows(dcache)[f"{KL}|Kepong"]) == ["key", "lat", "lon", "tries", "shared"]


def test_postcode_column_is_worked_out_again_on_every_write(tmp_path):
    """Rows kept from the first run (found by name, so never retried) held lot or box numbers."""
    shops = {("Sarawak", "Kuching"): [
                 (2248, "MEDAN NIAGA SATOK", "LOT 13636, SEKSYEN 65 KTLD,JALAN MATANG,PETRA JAYA, KUCHING")],
             ("W.P. Labuan", "Kawasan Bandar"): [
                 (3531, "MILIMEWAH SUPERSTORE", "LOT 22-27 LAZENDA COMMERCIAL CENTRE, PHASE LL,JLN TUN MUSTAPHA, "
                  "P.O. BOX 80838, 87018 WP LABUAN,PHASE LL,JLN TUN MUSTAPHA, P.O. BOX 80838,"),
                 (11587, "LABUAN SUPERMARKET", "LOT 35-38 JALAN MUHIBBAH,P.O. BOX 80883,")]}
    data = write_build(tmp_path / "data", shops)
    old = [cache_row(2248, 1.555147, 110.321564, "name", district="Kuching", state="Sarawak", postcode="13636"),
           cache_row(3531, 5.280379, 115.245528, "name", district="Kawasan Bandar", state="W.P. Labuan",
                     postcode="80838"),
           cache_row(11587, 5.278655, 115.242394, "name", district="Kawasan Bandar", state="W.P. Labuan",
                     postcode="80883"),
           # Not in this build (no address to read): kept only when GeoNames has it in the shop's state.
           cache_row(1302, 2.70, 113.78, "district", district="Belaga", state="Sarawak", postcode="96900"),
           cache_row(21219, 5.53, 102.20, "district", district="Kuala Krai", state="Kelantan", postcode="14934"),
           cache_row(18501, 1.55, 110.34, "name", district="Kuching", state="Sarawak", postcode="1000")]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], old)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"], [])
    fake = Fake()
    gp.run(data, cache, dcache, geocoder(fake), today=TODAY)    # no table, no lookups due
    rows = read_rows(cache)
    assert fake.calls == [] and {c: rows[c]["postcode"] for c in ("2248", "3531", "11587")} == {
        "2248": "", "3531": "87018", "11587": ""}
    assert {c: rows[c]["postcode"] for c in ("1302", "21219", "18501")} == {"1302": "96900", "21219": "14934",
                                                                            "18501": "1000"}
    belaga = "MY\t96900\tBelaga\tSarawak\t13\t\t\t\t\t2.70\t113.78\t4"
    pc = write_postcodes(tmp_path / "postcodes_my.csv", KB_TXT + "\n" + belaga)
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    assert {c: rows[c]["postcode"] for c in ("1302", "21219", "18501", "3531")} == {
        "1302": "96900", "21219": "", "18501": "", "3531": "87018"}
    assert rows["2248"]["lat"] == old[0]["lat"] and rows["2248"]["precision"] == "name"   # the point is untouched


def test_files_with_a_byte_order_mark_still_load(tmp_path):
    data, cache, dcache, pc = kepong(tmp_path, [(1, "KEDAI 1", "NO 5, KEPONG")], [cache_row(2, 3.21, 101.64, "name")])
    for path in (cache, dcache, pc):
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())   # as a spreadsheet saves it
    assert gp.read_cache(cache)["2"]["premise_code"] == "2"
    assert gp.Places.load(pc).postcode("52100") == pytest.approx((3.212, 101.643))
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    assert set(rows) == {"1", "2"} and rows["2"]["premise_code"] == "2" and rows["1"]["precision"] == "town"
    assert list(read_rows(dcache)) == [f"{KL}|Kepong"]


REAL_GEO = Path(__file__).resolve().parents[1] / "geo"


@pytest.mark.skipif(not (REAL_GEO / "premise_geo.csv").exists(), reason="no committed shop locations")
def test_the_committed_caches_still_load(tmp_path):
    cache, dcache = tmp_path / "premise_geo.csv", tmp_path / "district_geo.csv"
    shutil.copy(REAL_GEO / "premise_geo.csv", cache)
    shutil.copy(REAL_GEO / "district_geo.csv", dcache)
    before, dbefore = read_rows(cache), read_rows(dcache)
    assert len(before) > 1000 and {r["precision"] for r in before.values()} <= set(gp.RANK)
    (tmp_path / "data" / "d").mkdir(parents=True)   # a build with no shops: nothing to look up
    fake = Fake()
    st = gp.run(tmp_path / "data", cache, dcache, geocoder(fake), today=TODAY)
    assert fake.calls == [] and st["requests"] == 0
    after, dafter = read_rows(cache), read_rows(dcache)
    cols = [c for c in gp.FIELDS if c in next(iter(before.values()))]
    assert set(after) == set(before)
    assert all({c: after[k][c] for c in cols} == {c: before[k][c] for c in cols} for k in before)
    assert {k: (r["lat"], r["lon"]) for k, r in dafter.items()} == {k: (r["lat"], r["lon"]) for k, r in dbefore.items()}
    # Centres shared by districts of one state (the old KL answer) are now remembered as shared.
    assert {k for k, r in dafter.items() if r["shared"] == "1"} == gp.shared_keys(dbefore)


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


# ---------- workflow ----------

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "refresh-and-deploy.yml"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_workflow_checks_the_geo_minutes_input():
    """Run the workflow's own shell lines that turn the geo_minutes input into --max-minutes."""
    lines = [x.strip() for x in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    start = lines.index('m="$GEO_MINUTES"')
    end = next(i for i in range(start, len(lines)) if "--max-minutes" in lines[i])
    script = "\n".join(lines[start:end] + ['echo "$m"'])
    for given, want in (("99999999999999999999", "40"), ("1000", "40"), ("30", "30"), ("100", "100"),
                        ("250", "100"), ("0", "0"), ("", "40"), ("abc", "40"), ("1e3", "40"), ("-5", "40")):
        out = subprocess.run(["bash", "-c", script], env={"GEO_MINUTES": given, "PATH": os.environ.get("PATH", "")},
                             capture_output=True, text=True)
        assert (out.stdout.strip(), out.stderr) == (want, ""), given
