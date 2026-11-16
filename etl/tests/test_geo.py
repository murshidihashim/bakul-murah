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
                            geocoder(Fake()), cache or {}, places)

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
    # "18000 KUALA KRAI" (the postcode's own GeoNames place right after it) is strong evidence, but
    # 66 km away with only GeoNames' point to go on it is not used. When shops OpenStreetMap found
    # vouch for where 18000 is, it may lie up to 80 km away, but only when nothing nearer is in the
    # address (Ketereh, 19 km).
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, 18000 KUALA KRAI"}, places, d)
    assert (a["kind"], a["point"]) == ("district", KB_CENTRE)
    vouched = gp.Places(fpc.parse(MY_TXT + "\n" + KB_TXT))
    vouched.learn([({"state": "Kelantan", "district": "Kuala Krai", "address": f"NO {i}, 18000 KUALA KRAI"},
                    (5.53 + i / 1000, 102.20)) for i in (1, 2)])
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, 18000 KUALA KRAI"}, vouched, d)
    assert (a["kind"], a["label"], a["strong"], a["vouched"]) == ("postcode", "18000", True, True)
    assert a["point"] == pytest.approx((5.5315, 102.20))   # where the found shops are
    a = gp.anchor_for({**kb, "address": "NO 1, KAMPUNG X, 18000 KUALA KRAI, KETEREH"}, vouched, d)
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
    # and "LIMBANG" (the district's name) are strong evidence: with shops OpenStreetMap found there
    # to vouch for the point they count; with only GeoNames' point that far out they do not (the
    # real table puts 98700 at 81 km from the town).
    lb = {"premise_code": "3", "premise": "KEDAI", "district": "Limbang", "state": "Sarawak", "address": "98700 LIMBANG"}
    centre = (4.352, 115.060)
    plain = districts({"Sarawak|Limbang": centre})
    assert gp.km(*centre, 4.75, 115.0) == pytest.approx(44.8, abs=0.1)
    assert gp.anchor_for(lb, places, plain)["kind"] == "district"
    assert gp.anchor_for({**lb, "address": "PASAR LIMBANG"}, places, plain)["kind"] == "district"
    seen = gp.Places(fpc.parse(MY_TXT + "\n" + KB_TXT))
    seen.learn([({"state": "Sarawak", "district": "Limbang", "address": f"NO {i}, 98700 LIMBANG"},
                 (4.75, 115.0 + i / 1000)) for i in (1, 2)])
    assert (gp.anchor_for(lb, seen, plain)["label"], gp.anchor_for(lb, seen, plain)["strong"]) == ("98700", True)
    assert gp.anchor_for({**lb, "address": "PASAR LIMBANG"}, seen, plain)["kind"] == "town"
    # Postcode or town points from an earlier run do not vouch for the district (they may be the
    # wrong ones, as at Kalabakan): the centre is still the reference ...
    near_town = {c: cache_row(c, 4.69, 114.98 + c / 1000, "postcode", district="Limbang", state="Sarawak",
                              query="98700", postcode="98700") for c in (9, 10)}
    limbang = districts({"Sarawak|Limbang": centre}, near_town)
    assert limbang.trusted("Sarawak", "Limbang") == centre and limbang.references("Sarawak", "Limbang") == [centre]
    # ... but a shop is searched for around them (38 km from the centre: they are named after the
    # district), not in the middle of the district.
    for address in ("98700 LIMBANG", "KAMPUNG X"):
        a = gp.anchor_for({**lb, "address": address}, places, limbang)
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
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    kb, jb = rows["21684"], rows["20860"]
    assert (kb["precision"], kb["query"]) == ("town", "Ketereh")
    assert gp.km(float(kb["lat"]), float(kb["lon"]), 5.9561, 102.2432) < 0.1
    # Ketereh is 19 km from the centre, and no shop OpenStreetMap found vouches for its point:
    # fine for a town that is not the district's own, but counted for a look by hand.
    assert gp.km(*KB_CENTRE, 5.9561, 102.2432) > gp.GUESS_KM and st["unvouched_far"] == 1
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
    """Kuala Rompin is 43 km from Rompin's centre and 27 km beyond the shops OpenStreetMap found in
    Muadzam Shah: it is in the district's name, so it counts (up to 80 km) when shops OpenStreetMap
    found there vouch for its point. With only GeoNames' point it does not (the real table puts
    Kuala Rompin 18 km from the town). A weak town that far never counts."""
    assert gp.km(*ROMPIN_CENTRE, *KUALA_ROMPIN) == pytest.approx(42.9, abs=0.1)
    for vouched in (True, False):
        work = tmp_path / str(vouched)
        work.mkdir()
        pc = write_postcodes(work / "postcodes_my.csv", ROMPIN_TXT)
        found = [(14840 + i, "BS FRESHMART", "JALAN MEDAN MEWAH 12,BANDAR MUADZAM SHAH,MUADZAM SHAH") for i in range(4)]
        in_town = [(14850 + i, "PASARAYA", "JALAN BESAR, KUALA ROMPIN, PAHANG") for i in range(2)] if vouched else []
        data = write_build(work / "data", {("Pahang", "Rompin"): [
            (c, f"KEDAI {c}", a) for c, a in KUALA_ROMPIN_SHOPS] + found + in_town + [
            (10214, "TUNAS MANJA (MUADZAM SHAH)", "LOT. PT232,MUKIM BEBAR,MUADZAM SHAH"),
            (30000, "KEDAI SELENDANG", "NO 1, KAMPUNG SELENDANG,SELENDANG")]})
        v1 = [cache_row(c, *ROMPIN_CENTRE, "district", district="Rompin", state="Pahang", query="Rompin, Pahang")
              for c, _ in KUALA_ROMPIN_SHOPS + [(10214, "")]]
        v1 += [cache_row(c, *MUADZAM, "address", district="Rompin", state="Pahang", tries=1, last="2026-11-01")
               for c, *_ in found]
        v1 += [cache_row(c, KUALA_ROMPIN[0] + (c - 14850) / 1000, KUALA_ROMPIN[1], "name", district="Rompin",
                         state="Pahang", tries=1, last="2026-11-01") for c, *_ in in_town]
        cache = write_csv(work / "premise_geo.csv", gp.FIELDS, v1)
        dcache = write_csv(work / "district_geo.csv", ["key", "lat", "lon"],
                           [{"key": "Pahang|Rompin", "lat": str(ROMPIN_CENTRE[0]), "lon": str(ROMPIN_CENTRE[1])}])
        fake = Fake()
        gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
        rows = read_rows(cache)
        for c, _ in KUALA_ROMPIN_SHOPS:
            r = rows[str(c)]
            if vouched:
                assert (r["precision"], r["query"], r["tries"]) == ("town", "Kuala Rompin", "1"), c
                assert gp.km(float(r["lat"]), float(r["lon"]), *KUALA_ROMPIN) < 0.1
            else:
                assert (r["precision"], r["tries"]) == ("district", "1"), c
        boxes = {c["q"].split(",")[0]: c["viewbox"] for c in fake.calls}
        in_town_median = (KUALA_ROMPIN[0] + 0.0005, KUALA_ROMPIN[1])
        assert boxes["KEDAI 1152"] == (gp.viewbox(*in_town_median, 0.15) if vouched else gp.viewbox(*ROMPIN_CENTRE, 0.3))
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
    addresses = ("TAMAN SEMARAK, 91000 TAWAU, SABAH,", "PUSAT KOMERSIAL GAYA, 91000TAWAU, SABAH,",
                 "PUSAT KOMERSIAL GAYA, 91000\rTAWAU, SABAH,", "MILE 5, 91000, TAWAU")
    for address in addresses:
        assert gp.postcodes_in(address, tw, "Sabah") == ["91000"], address
        # Strong, but with only GeoNames' point to go on: within 15 km (GUESS_KM), not 60.
        assert tw.postcode_anchor(["91000"], "Sabah", [(4.30, 117.89)], address) == ("91000", TAWAU, True)
        assert tw.postcode_anchor(["91000"], "Sabah", near, address) is None, address
    # Shops OpenStreetMap found that name 91000 vouch for its point: it may then lie up to 80 km away.
    tw.learn([({"state": "Sabah", "district": "Tawau", "address": f"NO {i}, 91000 TAWAU"}, (TAWAU[0] + i / 1000, TAWAU[1]))
              for i in (1, 2)])
    for address in addresses:
        assert tw.postcode_anchor(["91000"], "Sabah", near, address) == (
            "91000", pytest.approx((TAWAU[0] + 0.0015, TAWAU[1])), True), address
    # Another place after it, or none: weak, so 60 km is too far.
    for address in ("91000 SEMPORNA", "TAMAN SEMARAK, 91000", "91000 JALAN APAS"):
        assert tw.postcode_anchor(["91000"], "Sabah", near, address) is None, address
        assert tw.postcode_anchor(["91000"], "Sabah", [TAWAU], address)[2] is False
    assert tw.postcode_anchor(["91000"], "Sabah", near, "91000 TAWAU", gp.CHECK_KM) is None
    assert tw.postcode_anchor(["91000"], "Sabah", [(5.98, 116.07)], "91000 TAWAU") is None   # 220 km


def test_a_spread_postcode_is_strong_only_through_its_named_place():
    """91000's rows in a table made from old addresses: Tawau town plus far Kalabakan points. Their
    average is nowhere; '91000 TAWAU' means the Tawau rows."""
    rows = TAWAU_TXT + "\n" + "\n".join("\t".join(r) for r in [
        ["MY", "91000", "Tawau", "Sabah", "12", "", "", "", "", "4.2500", "117.8900", "4"],
        ["MY", "91000", "Kalabakan", "Sabah", "12", "", "", "", "", "4.7245", "117.3897", "4"],
        ["MY", "91000", "Kalabakan", "Sabah", "12", "", "", "", "", "4.7300", "117.3800", "4"]])
    tw = gp.Places(fpc.parse(rows))
    tawau_rows = (4.2474, 117.8906)   # the median of the two Tawau rows
    near_tawau = (4.30, 117.89)       # 6 km from them: strong evidence counts without found shops
    for address in ("TAMAN SEMARAK, 91000 TAWAU, SABAH,", "PUSAT KOMERSIAL GAYA, 91000TAWAU, SABAH,"):
        assert tw.postcode_anchor(["91000"], "Sabah", [near_tawau], address) == ("91000", pytest.approx(tawau_rows), True)
    assert tw.spread_postcodes == {"91000"}
    pc, point, strong = tw.postcode_anchor(["91000"], "Sabah", [OLD_91000], "KAMPUNG X, 91000 KALABAKAN")
    assert strong and point == pytest.approx((4.72725, 117.38485))
    # A bare 91000 is weak evidence: within 35 km only (at the postcode's usual point, the average).
    east = (4.25, 118.40)   # 56 km from the Tawau rows, far from the others
    seen = gp.Places(fpc.parse(rows))   # found shops vouch for 91000 at Tawau: strong may reach 56 km
    seen.learn([({"state": "Sabah", "district": "Tawau", "address": f"NO {i}, 91000 TAWAU"}, tawau_rows)
                for i in (1, 2)])
    assert seen.postcode_anchor(["91000"], "Sabah", [east], "TAMAN SEMARAK, 91000 TAWAU")[2] is True
    assert seen.postcode_anchor(["91000"], "Sabah", [east], "TAMAN SEMARAK, 91000") is None
    assert tw.postcode_anchor(["91000"], "Sabah", [east], "TAMAN SEMARAK, 91000") is None
    pc, point, strong = tw.postcode_anchor(["91000"], "Sabah", [KALABAKAN], "TAMAN SEMARAK, 91000")
    assert not strong and point == pytest.approx(tw.postcode("91000"))   # 25 km away: weak is enough
    assert gp.km(*tw.postcode("91000"), *TAWAU) > gp.CHECK_KM
    assert tw.postcode_anchor(["91000"], "Sabah", [TAWAU], "TAMAN SEMARAK, 91000") is None
    # Its own place's rows spread too: weak.
    split = gp.Places(fpc.parse(rows.replace("4.2500\t117.8900", "4.5000\t117.6000")))
    assert split.postcode_anchor(["91000"], "Sabah", [KALABAKAN], "91000 TAWAU, SABAH")[2] is False
    assert split.postcode_anchor(["91000"], "Sabah", [east], "91000 TAWAU, SABAH") is None
    # Postcodes whose rows agree are unchanged (the average of all their rows, strong as before).
    assert tw.postcode_anchor(["91007"], "Sabah", [near_tawau], "MEROTAI, 91007 TAWAU") == ("91007", TAWAU, True)


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


# Shops OpenStreetMap found in Tawau town whose address has 91000 (the real build has four).
TAWAU_FOUND = [(30100 + i, f"KEDAI TAWAU {i}", f"NO {i}, JALAN BESAR, 91000 TAWAU, SABAH") for i in range(2)]


def kalabakan_files(tmp_path):
    """The Kalabakan shops with their first-run rows, and two shops OpenStreetMap found in Tawau."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", TAWAU_TXT)
    data = write_build(tmp_path / "data", {("Sabah", "Kalabakan"): [(c, n, a) for c, n, a, _ in KALABAKAN_SHOPS],
                                           ("Sabah", "Tawau"): TAWAU_FOUND})
    v1 = [cache_row(c, *(OLD_91000 if p == "postcode" else KALABAKAN), p, district="Kalabakan", state="Sabah",
                    query="91000" if p == "postcode" else "Kalabakan, Sabah",
                    postcode="91000" if p == "postcode" else "")
          for c, _, _, p in KALABAKAN_SHOPS]
    v1 += [cache_row(c, TAWAU[0] + (c - 30100) / 1000 - 0.0005, TAWAU[1], "name", district="Tawau", state="Sabah")
           for c, *_ in TAWAU_FOUND]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], v1)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Sabah|Kalabakan", "lat": str(KALABAKAN[0]), "lon": str(KALABAKAN[1])}])
    return pc, data, cache, dcache


def test_old_postcode_points_do_not_vouch_for_themselves(tmp_path):
    """Kalabakan: only shops OpenStreetMap found (name or address) say where a district's shops
    are. The four wrong '91000 TAWAU' rows are 77 km from where the shops OpenStreetMap found with
    91000 are: they move to Tawau before any search (and stay there after the retry)."""
    pc, data, cache, dcache = kalabakan_files(tmp_path)
    d = gp.Districts(read_rows(dcache), geocoder(Fake()), read_rows(cache))
    assert d.references("Sabah", "Kalabakan") == [KALABAKAN]   # not the median of the wrong rows
    st = gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    assert st["corrected_offline"] == 4
    rows = read_rows(cache)
    for code in ("2641", "17634", "17849", "17853"):
        r = rows[code]
        assert (r["precision"], r["tries"]) == ("postcode", "1"), code
        assert gp.km(float(r["lat"]), float(r["lon"]), *TAWAU) < 0.1, code
    # "91007 TAWAU" is strong evidence too, but no found shop vouches for 91007's GeoNames point,
    # and it is 60 km from the centre; "MEROTAI BESAR, TAWAU": a town alone is weak, and that far.
    for code in ("2637", "17280"):
        assert rows[code]["precision"] == "district"
        assert gp.km(float(rows[code]["lat"]), float(rows[code]["lon"]), *KALABAKAN) < 0.1
    assert rows["2637"]["postcode"] == "91007"
    assert rows["21963"]["precision"] == "address"   # found by OpenStreetMap: not retried


def test_far_rows_from_strong_evidence_do_not_move_the_search_box(tmp_path):
    """After the retry moved the Kalabakan rows to Tawau ('91000 TAWAU', strong, 60 km from the
    centre), a Kalabakan shop with no postcode or town is still searched around the district's own
    point, not in Tawau. Rows named after the district ('98700 LIMBANG') still move the search."""
    pc, data, cache, dcache = kalabakan_files(tmp_path)
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    assert sum(read_rows(cache)[str(c)]["precision"] == "postcode" for c, *_ in KALABAKAN_SHOPS) == 4
    later = gp.Districts(read_rows(dcache), geocoder(Fake()), read_rows(cache), gp.Places.load(pc))
    assert later.centre("Sabah", "Kalabakan") == KALABAKAN
    data2 = write_build(tmp_path / "data2", {("Sabah", "Kalabakan"): [(30001, "KEDAI LUASONG", "KAMPUNG LUASONG, KALABAKAN")]})
    fake = Fake()
    gp.run(data2, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    assert {c["viewbox"] for c in fake.calls} == {gp.viewbox(*KALABAKAN, 0.3)}
    row = read_rows(cache)["30001"]
    assert row["precision"] == "district" and gp.km(float(row["lat"]), float(row["lon"]), *KALABAKAN) < 0.1
    # Limbang: postcode rows at the town, 38 km from the middle point; GeoNames names 98700 LIMBANG.
    places = gp.Places(fpc.parse(KB_TXT))
    rows = {c: cache_row(c, 4.69, 114.98 + c / 1000, "postcode", district="Limbang", state="Sarawak", query="98700")
            for c in (9, 10)}
    rows[11] = cache_row(11, 4.70, 114.99, "town", district="Limbang", state="Sarawak", query="Limbang")
    lb = gp.Districts({"Sarawak|Limbang": {"key": "Sarawak|Limbang", "lat": "4.352", "lon": "115.060"}},
                      geocoder(Fake()), rows, places)
    assert lb.centre("Sarawak", "Limbang") == pytest.approx((4.69, 114.99))
    # A far postcode row whose place is another town does not count, nor one GeoNames does not know.
    rows = {c: {**r, "query": "81100"} for c, r in rows.items() if c != 11}
    lb = gp.Districts({"Sarawak|Limbang": {"key": "Sarawak|Limbang", "lat": "4.352", "lon": "115.060"}},
                      geocoder(Fake()), rows, places)
    assert lb.centre("Sarawak", "Limbang") == (4.352, 115.06)


LIMBANG_SHOPS = [(2831, "CHOON CHEONG", "LOT 2230-2231, LIMBANG TOWN DISTRICT JALAN PANDARUAN,98700 LIMBANG,"),
                 (2846, "LBG SOON SENG", "LOT 2117, 2118 & 2119, JALAN BANGKITA,98700 LIMBANG,SARAWAK.,"),
                 (21545, "CHOON CHEONG 2", "LOT NO 61-62,BANK STREET,")]
LIMBANG_CENTRE, LIMBANG_V1 = (4.351998, 115.060242), (4.689833, 114.977808)


def test_without_a_table_old_postcode_rows_are_never_judged_wrong(tmp_path):
    """Limbang: OpenStreetMap found none of its shops and its centre is in the middle of the
    district, 38 km from the first run's postcode rows at the town. Without the GeoNames table
    nothing new contradicts those rows, so a retry keeps them. With it they keep their point too:
    their place is named after the district, and GeoNames' own point is no reason to move them."""
    data = write_build(tmp_path / "data", {("Sarawak", "Limbang"): LIMBANG_SHOPS})
    v1 = [cache_row(c, *LIMBANG_V1, "postcode", district="Limbang", state="Sarawak", query="98700", postcode="98700")
          for c, *_ in LIMBANG_SHOPS[:2]]
    v1.append(cache_row(21545, *LIMBANG_CENTRE, "district", district="Limbang", state="Sarawak"))
    dcache_rows = [{"key": "Sarawak|Limbang", "lat": str(LIMBANG_CENTRE[0]), "lon": str(LIMBANG_CENTRE[1])}]
    assert gp.km(*LIMBANG_V1, *LIMBANG_CENTRE) > gp.CHECK_KM
    for table in (None, KB_TXT):   # KB_TXT has 98700 Limbang at the town
        work = tmp_path / ("no_table" if table is None else "table")
        work.mkdir()
        cache = write_csv(work / "premise_geo.csv", gp.FIELDS[:8], v1)
        dcache = write_csv(work / "district_geo.csv", ["key", "lat", "lon"], dcache_rows)
        pc = write_postcodes(work / "postcodes_my.csv", table) if table else None
        fake = Fake()
        gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
        rows = read_rows(cache)
        for c, *_ in LIMBANG_SHOPS[:2]:
            r = rows[str(c)]
            assert (r["precision"], r["tries"]) == ("postcode", "1"), (table is None, c)
            assert gp.km(float(r["lat"]), float(r["lon"]), *LIMBANG_V1) < 0.1, (table is None, c)
        # The shop with no postcode is searched at the town: the rows whose address names Limbang
        # right after the postcode ("98700 LIMBANG") place the search, with the table or without.
        r = rows["21545"]
        assert r["precision"] == "district" and gp.km(float(r["lat"]), float(r["lon"]), *LIMBANG_V1) < 0.1, table
        assert {c["viewbox"] for c in fake.calls if c["q"].startswith("CHOON CHEONG 2")} == {
            gp.viewbox(*LIMBANG_V1, 0.3)}, table


# Rows of the real GeoNames table (2026-11-16) far from their towns: 98700 is 81 km from Limbang
# town, 85300 60 km from Labis, the 'Seri Manjung' row 47 km from Seri Manjung, 97100 76 km from
# Sebauh. The first run's points for these shops came from OpenStreetMap.
REAL_FAR_TXT = "\n".join("\t".join(["MY", pc, place, st, "", "", "", "", "", lat, lon, "3"]) for pc, place, st, lat, lon in [
    ("98700", "Limbang", "Sarawak", "4.1371", "114.6005"), ("98707", "Limbang", "Sarawak", "4.3466", "115.1836"),
    ("85000", "Segamat", "Johor", "2.3134", "102.9786"), ("85300", "Labis", "Johor", "2.2604", "103.5458"),
    ("32040", "Seri Manjong", "Perak", "4.2200", "100.7541"), ("32040", "Seri Manjung", "Perak", "4.6020", "100.7979"),
    ("97100", "Sebauh", "Sarawak", "2.7811", "112.6702"), ("97007", "Bintulu", "Sarawak", "3.1906", "113.0901")])
LABIS_V1, SEGAMAT_CENTRE = (2.404107, 103.014033), (2.492210, 102.844922)


def test_a_retry_keeps_old_points_that_only_geonames_disputes(tmp_path):
    """Real Limbang and Labis shops, two runs 30 days apart, OpenStreetMap finds nothing. Their
    first-run postcode points are 7 km from Limbang town and 2 km from Labis; GeoNames puts 98700
    and 85300 81 and 60 km from those towns. The rows keep their points, and Limbang's search box
    stays at its town."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", REAL_FAR_TXT)
    limbang = [(2831, "CHOON CHEONG", "LOT 2230-2231, LIMBANG TOWN DISTRICT JALAN PANDARUAN, P.O BOX 162,98700 LIMBANG, SARAWAK.,"),
               (4666, "TAMU CINA LIMBANG", "MALAYAN BANKING STREET,98700, LIMBANG,SARAWAK."),
               (17426, "THE PLACE", "LOT NO 2186-B1-2, B1-3,ASEMENT LIMBANG PLAZA,98700 LIMBANG, SARAWAK.")]
    district_only = [(21544, "MK HYPERMART", "LOT 2266-2274, GROUND FLOOR LAKIL SHOPHOUSE,,JALAN MUHIBBAH,"),
                     (21547, "HAPPY MART", "LOT 17-18, GROUND FLOOR,JALAN MERDEKA,")]
    labis = [(1139, "PASAR AWAM LABIS", "PASAR AWAM LABIS,JLN TENANG,85300 LABIS, JOHOR."),
             (17162, "PASARAYA DENWAY", "NO. 1,3,5,7,9,11,13,15,17 JALAN HIJAU ,TAMAN BANDAR JAYA,85300 LABIS, JOHOR.")]
    data = write_build(tmp_path / "data", {("Sarawak", "Limbang"): limbang + district_only, ("Johor", "Segamat"): labis})
    v1 = [cache_row(c, *LIMBANG_V1, "postcode", district="Limbang", state="Sarawak", query="98700", postcode="98700")
          for c, *_ in limbang]
    v1 += [cache_row(c, *LIMBANG_CENTRE, "district", district="Limbang", state="Sarawak") for c, *_ in district_only]
    v1 += [cache_row(c, *LABIS_V1, "postcode", district="Segamat", state="Johor", query="85300", postcode="85300")
           for c, *_ in labis]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS[:8], v1)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": "Sarawak|Limbang", "lat": str(LIMBANG_CENTRE[0]), "lon": str(LIMBANG_CENTRE[1])},
                        {"key": "Johor|Segamat", "lat": str(SEGAMAT_CENTRE[0]), "lon": str(SEGAMAT_CENTRE[1])}])
    places = gp.Places.load(pc)
    assert gp.km(*places.postcode("98700"), *LIMBANG_V1) > 70 and gp.km(*places.postcode("85300"), *LABIS_V1) > 55
    for day, tries in (("2026-11-16", "1"), ("2026-12-16", "2")):
        fake = Fake()
        gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=day)
        rows = read_rows(cache)
        for c, *_ in limbang + labis:
            r = rows[str(c)]
            want = LIMBANG_V1 if r["district"] == "Limbang" else LABIS_V1
            assert (r["precision"], r["tries"]) == ("postcode", tries), (day, c)
            assert gp.km(float(r["lat"]), float(r["lon"]), *want) < 0.1, (day, c)
        for c, *_ in district_only:   # searched, and saved, at the town (the rows named after it)
            r = rows[str(c)]
            assert r["precision"] == "district" and gp.km(float(r["lat"]), float(r["lon"]), *LIMBANG_V1) < 0.1
        limbang_boxes = {x["viewbox"] for x in fake.calls if "viewbox" in x and "LIMBANG" not in x["q"]
                         and x["q"].split(",")[0] in {n for _, n, _ in limbang + district_only}}
        assert limbang_boxes == {gp.viewbox(*LIMBANG_V1, 0.3)}, day


def test_found_shops_place_towns_and_postcodes_before_geonames(tmp_path):
    """No time for searches (geo_minutes 0). Seri Manjung: GeoNames' 'Seri Manjung' row is 47 km
    from the town, where five shops OpenStreetMap found that name it are; a 'location unknown'
    shop of the town gets their median. Sebauh: GeoNames puts 97100 and Sebauh 76 km from the town,
    and nothing vouches for that point; Bintulu, written after Sebauh, is the division, not where
    the shop is. So the Sebauh shop stays 'location unknown' (at its district's centre, by the town)."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", REAL_FAR_TXT)
    manjung_found = [(5360, "PASAR AWAM SERI MANJUNG", "32040 SERI MANJUNG,PERAK,", (4.198096, 100.669201)),
                     (18123, "99 SPEEDMART SERI MANJUNG", "NO. 66 & 67 (G), PERSIARAN 2B, FASA 2B,32040 SERI MANJUNG,PERAK.",
                      (4.201667, 100.660036)),
                     (21375, "PASARAYA BSK", "NO 594-598, LORONG KURAU 3, TAMAN SAMUDERA,,SERI MANJUNG,", (4.203373, 100.660689)),
                     (21386, "PCK FRESHMART", "NO 385, TAMAN SAMUDERA,,SERI MANJUNG,", (4.203995, 100.662435))]
    bintulu_found = [(30200 + i, f"KEDAI BINTULU {i}", f"NO {i}, JALAN TUN AHMAD ZAIDI, BINTULU", (3.17 + i / 1000, 113.04))
                     for i in range(2)]
    data = write_build(tmp_path / "data", {
        ("Perak", "Manjung"): [(c, n, a) for c, n, a, _ in manjung_found] + [
            (21378, "SEGI CASH & CARRY", "NO 11 -13G, JALAN SM 1C/15, FASA 1C4,,BANDAR BARU, SERI MANJUNG,"),
            (16087, "AEON SERI MANJUNG", "PUSAT PERNIAGAAN MANJUNG POINT 3,32040 SERI MANJUNG, PERAK")],
        ("Sarawak", "Sebauh"): [(4169, "LAU LIONG KEE", "NO. 48-49,MAIN BAZAAR,97100 SEBAUH,BINTULU,SARAWAK,")],
        ("Sarawak", "Bintulu"): [(c, n, a) for c, n, a, _ in bintulu_found]})
    manjung_centre, sebauh_centre = (4.334761, 100.634266), (3.108935, 113.269243)
    rows = [cache_row(c, *p, "name", district="Manjung", state="Perak") for c, _, _, p in manjung_found]
    rows += [cache_row(21378, *manjung_centre, "district", district="Manjung", state="Perak", tries=2, last="2026-11-01"),
             cache_row(16087, 4.197879, 100.664847, "postcode", district="Manjung", state="Perak", query="32040"),
             cache_row(4169, *sebauh_centre, "district", district="Sebauh", state="Sarawak", tries=2, last="2026-11-01")]
    rows += [cache_row(c, *p, "name", district="Bintulu", state="Sarawak") for c, _, _, p in bintulu_found]
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, rows)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"], [
        {"key": "Perak|Manjung", "lat": str(manjung_centre[0]), "lon": str(manjung_centre[1])},
        {"key": "Sarawak|Sebauh", "lat": str(sebauh_centre[0]), "lon": str(sebauh_centre[1])},
        {"key": "Sarawak|Bintulu", "lat": "3.187426", "lon": "113.047280"}])
    fake = Fake()
    st = gp.run(data, cache, dcache, gp.Geocoder(fetch=fake, delay=0, max_minutes=0), postcodes_path=pc, today=TODAY)
    assert fake.calls == [] and st["upgraded_offline"] == 1
    after = read_rows(cache)
    found_median = (4.2025, 100.661563)   # of the four shops that name SERI MANJUNG
    r = after["21378"]
    assert (r["precision"], r["query"], r["tries"]) == ("town", "Seri Manjung", "2")
    assert gp.km(float(r["lat"]), float(r["lon"]), *found_median) < 0.1
    assert gp.km(4.602, 100.7979, *found_median) > 45   # GeoNames' point
    # The first run's postcode point agrees with the found shops: kept.
    assert (after["16087"]["lat"], after["16087"]["lon"]) == ("4.197879", "100.664847")
    r = after["4169"]
    assert (r["precision"], r["lat"], r["lon"]) == ("district", f"{sebauh_centre[0]:.6f}", f"{sebauh_centre[1]:.6f}")
    places = gp.Places.load(pc)
    assert gp.km(*places.postcode("97100"), *sebauh_centre) > 70


def test_a_district_s_own_town_needs_found_shops_or_to_be_near():
    """GeoNames often puts a district's own town (and its postcode) in the middle of the postcode's
    wide area: with only that point, it counts within GUESS_KM (15 km) of where the district's
    shops are. Another town, or a postcode whose place is another town, still counts within 35 km."""
    places = gp.Places(fpc.parse(REAL_FAR_TXT))
    assert gp.km(*places.postcode("85000"), *SEGAMAT_CENTRE) == pytest.approx(24.8, abs=0.2)
    for district in ("Segamat", "Labis"):
        own = district == "Segamat"
        assert (places.postcode_anchor(["85000"], "Johor", [SEGAMAT_CENTRE], "85000 SEGAMAT", district=district)
                is None) is own, district
        assert (places.town_anchors("NO 1, SEGAMAT", "Johor", [SEGAMAT_CENTRE], district) == []) is own, district
    # Within 15 km it counts; vouched by found shops, 35 km (and 80 km for strong evidence).
    near = (2.40, 102.92)
    assert gp.km(*places.postcode("85000"), *near) < gp.GUESS_KM
    assert places.postcode_anchor(["85000"], "Johor", [near], "85000 SEGAMAT", district="Segamat")[0] == "85000"
    places.learn([({"state": "Johor", "district": "Segamat", "address": f"NO {i}, 85000 SEGAMAT"}, (2.5148, 102.8158 + i / 1000))
                  for i in (1, 2)])
    assert places.postcode_anchor(["85000"], "Johor", [SEGAMAT_CENTRE], "85000 SEGAMAT", district="Segamat") == (
        "85000", pytest.approx((2.5148, 102.8173)), True)
    assert places.town_anchors("NO 1, SEGAMAT", "Johor", [SEGAMAT_CENTRE], "Segamat")[0][0] == "Segamat"


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


# Kuching's district (its shops and town) and Kota Samarahan, for route names.
SAMARAHAN_TXT = CYBER_TXT + "\n" + "MY\t94300\tKota Samarahan\tSarawak\t13\t\t\t\t\t1.4600\t110.4900\t4"


def test_abbreviated_routes_are_not_towns():
    """'KM 12 KUCHING-SAMARAHAN ROAD': SAMARAHAN is not a GeoNames place on its own and no street
    word comes first, but the road word, the milestone and the end of KOTA SAMARAHAN each make it a
    route, so Kuching is not where a Kota Samarahan shop is."""
    places = gp.Places(fpc.parse(SAMARAHAN_TXT))
    d = gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in {
        "Sarawak|Kota Samarahan": (1.4637, 110.4891), "W.P. Putrajaya|Cyberjaya": (2.914883, 101.669659)}.items()},
        geocoder(Fake()), {}, places)
    assert gp.km(1.5535, 110.3593, 1.4637, 110.4891) < gp.CHECK_KM   # near enough to pass the checks

    def names(address):
        return [n for n, _ in places.towns_in(address, "Sarawak")]

    samarahan = {"premise_code": "1", "premise": "KEDAI", "state": "Sarawak", "district": "Kota Samarahan"}
    for address in ("KM 12 KUCHING-SAMARAHAN ROAD", "BATU 10, KUCHING-SAMARAHAN EXPRESSWAY"):
        assert names(address) == [], address
        assert gp.anchor_for({**samarahan, "address": address}, places, d)["kind"] == "district", address
    # Each rule on its own (BAU and MERANTI are not in this table):
    for address in ("KUCHING-BAU ROAD", "SERIAN-BAU HIGHWAY", "KUCHING-BAU BY-PASS",        # a road word follows
                    "KM 12 KUCHING-BAU", "BATU 4 1/2, KUCHING - BAU", "MILE 7 KUCHING-BAU",  # a milestone before
                    "10 MILE KUCHING - BAU",
                    "KUCHING-KCH", "KCH-SERIAN, TAMAN X", "SERIAN - KL",                    # a town written short
                    "KUCHING-SAMARAHAN, TAMAN X", "SAMARAHAN-SERIAN, TAMAN X"):             # the end of a name
        assert names(address) == [], address
    # Not routes: a shop name, a number, a word that is none of these, or a dash with a town on
    # the right of a non-place (the town on the left of a dash is not always a road either).
    assert names("STAR GROCER CYBERSQUARE - KUCHING") == ["Kuching"]
    assert names("SEKSYEN 13-SERIAN") == ["Serian"] and names("KAMPUNG MERANTI-SERIAN") == ["Serian"]
    assert names("SERIAN - KOMPLEKS MERANTI, TAMAN X") == ["Serian"]
    assert names("KUCHING-MERANTI, TAMAN X") == ["Kuching"] and names("SRI-SERIAN") == ["Serian"]
    star = {"premise_code": "2", "premise": "STAR GROCER", "state": "W.P. Putrajaya", "district": "Cyberjaya",
            "address": "Star Grocer Cybersquare - Cyberjaya,,Jalan Teknokrat 6,Cyber 5,"}
    assert (gp.anchor_for(star, places, d)["kind"], gp.anchor_for(star, places, d)["label"]) == ("town", "Cyberjaya")


def test_common_words_and_state_names_are_not_route_ends():
    """Only a word that ends a single GeoNames name and is in no other (SAMARAHAN) stands for that
    town in a route. JAYA, BARU and BESAR end several names, KEDAH and PERLIS are states, MALAYSIA
    the country: ordinary address words. A milestone makes a route only right before a town at the
    dash ("KM 12 KUCHING-..."), not before other words ("KM 5, TAMAN SERI-MACHANG")."""
    p = gp.Places(fpc.parse("\n".join("\t".join(["MY", pc, place, st, "", "", "", "", "", lat, lon, "3"])
                                      for pc, place, st, lat, lon in [
        ("06000", "Jitra", "Kedah", "6.2675", "100.4210"), ("06600", "Kuala Kedah", "Kedah", "6.1000", "100.3000"),
        ("06010", "Universiti Utara Malaysia", "Kedah", "6.4600", "100.5000"),
        ("06100", "Bukit Besar", "Kedah", "6.0000", "100.5000"),
        ("18500", "Machang", "Kelantan", "5.7667", "102.2167"), ("16300", "Bachok", "Kelantan", "6.0670", "102.3960"),
        ("16600", "Kuala Besar", "Kelantan", "6.2000", "102.3000"),
        ("01000", "Kangar", "Perlis", "6.4414", "100.1986"), ("02000", "Kuala Perlis", "Perlis", "6.4000", "100.1300"),
        ("46000", "Petaling Jaya", "Selangor", "3.1000", "101.6000"), ("47500", "Subang Jaya", "Selangor", "3.0500", "101.5800"),
        ("50300", "Kampung Baru", "Selangor", "3.1600", "101.7000"), ("44000", "Kubu Baru", "Selangor", "3.5600", "101.6600")])))

    def names(address, state):
        return [n for n, _ in p.towns_in(address, state)]

    for address, state, town in (("NO 5, TAMAN SRI JAYA-JITRA", "Kedah", "Jitra"),
                                 ("NO 5, TAMAN SEJAHTERA, JITRA-KEDAH", "Kedah", "Jitra"),
                                 ("LOT 5, JITRA - MALAYSIA", "Kedah", "Jitra"), ("JITRA-BARU", "Kedah", "Jitra"),
                                 ("PASAR BESAR - MACHANG", "Kelantan", "Machang"),
                                 ("KM 5, TAMAN SERI-MACHANG", "Kelantan", "Machang"),
                                 ("KEDAI BARU - BACHOK", "Kelantan", "Bachok"),
                                 ("NO 1, PUSAT BANDAR BARU-KANGAR", "Perlis", "Kangar"),
                                 ("NO 1, KANGAR-PERLIS", "Perlis", "Kangar")):
        assert names(address, state) == [town], address
    # Still routes: a milestone right before a town at the dash, also one of two words.
    for address in ("KM 5 JITRA-ALOR", "BATU 3, JITRA - ALOR", "KM 5 KUALA KEDAH-ALOR, TAMAN X"):
        assert names(address, "Kedah") == [], address


def test_a_town_after_a_road_town_is_part_of_the_road():
    """Shop 21890 is at Batu 17 of the Kuching-Serian road, far from Serian town."""
    places = gp.Places(fpc.parse(SAMARAHAN_TXT))

    def names(address):
        return [n for n, _ in places.towns_in(address, "Sarawak")]

    for address in ("BATU 17, JLN KUCHING SERIAN", "NO. 9-18, GOLDEN SQUARE BATU 17,,JLN KUCHING /SERIAN,,",
                    "JALAN KUCHING - SERIAN, TAMAN X", "JALAN KOTA SAMARAHAN KUCHING SERIAN"):
        assert names(address) == [], address
    assert names("JALAN BESAR, SERIAN") == ["Serian"] and names("JLN KUCHING, SERIAN") == ["Serian"]
    assert names("JALAN MERANTI SERIAN") == ["Serian"]   # MERANTI is no place: an ordinary road name
    assert names("JLN KUCHING SERIAN, SERIAN") == ["Serian"]   # written again on its own


def test_a_name_written_as_one_word_matches():
    """GEORGETOWN is GeoNames' George Town; the other checks still apply."""
    pg = gp.Places(fpc.parse(STATES_TXT + "\n" + "\n".join("\t".join(r) for r in [
        ["MY", "10050", "George Town", "Pulau Pinang", "07", "", "", "", "", "5.4141", "100.3288", "4"],
        ["MY", "11500", "Sg Ara", "Pulau Pinang", "07", "", "", "", "", "5.3270", "100.2740", "4"],
        ["MY", "13000", "Butterworth", "Pulau Pinang", "07", "", "", "", "", "5.3991", "100.3638", "4"]])))
    d = gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in {
        "Pulau Pinang|Timur Laut": (5.41, 100.33), "Pulau Pinang|Barat Daya": (5.33, 100.27),
        f"{KL}|Bukit Bintang": (3.147, 101.711)}.items()}, geocoder(Fake()), {}, pg)

    def anchor(district, address):
        a = gp.anchor_for({"premise_code": "1", "premise": "KEDAI", "state": "Pulau Pinang", "district": district,
                           "address": address}, pg, d)
        return a["kind"], a["label"]

    assert anchor("Timur Laut", "42B-G-9, SRI SAUJANA, GAT LEBUH MACALLUM,GEORGETOWN, PULAU PINANG,") == (
        "town", "George Town")
    assert anchor("Timur Laut", "NO 1, GEORGE TOWN") == ("town", "George Town")
    assert anchor("Barat Daya", "NO 1, SG ARA") == ("town", "Sg Ara")
    assert anchor("Barat Daya", "NO 1, SGARA")[0] == "district"                    # 5 letters: too short
    assert anchor("Timur Laut", "NO 1, PULAUPINANG")[0] == "district"              # still the state's name
    assert anchor("Timur Laut", "JALAN GEORGETOWN, KAMPUNG X")[0] == "district"    # still a road name
    assert [n for n, _ in pg.towns_in("GEORGETOWN-BUTTERWORTH, TAMAN X", "Pulau Pinang")] == []   # a route
    assert anchor("Timur Laut", "NO 1, GEORGETOWN, KUALA LUMPUR") == ("town", "George Town")
    kl = {"premise_code": "1", "premise": "KEDAI", "state": KL, "district": "Bukit Bintang"}
    assert gp.anchor_for({**kl, "address": "NO 1, GEORGETOWN"}, pg, d)["kind"] == "district"   # another state


def test_george_town_is_the_pulau_pinang_rows_of_its_postcodes():
    """The real GeoNames table has no 'George Town': its postcodes 10000-10990 are named after the
    state, 'Pulau Pinang' (real shops 21359 and 22026: '...GEORGETOWN, PULAU PINANG')."""
    rows = [("10050", 5.4170, 100.3300), ("10200", 5.4120, 100.3340), ("10400", 5.4300, 100.3150),
            ("11400", 5.4083, 100.3086)]   # 11400 is Ayer Itam's, under the same name
    real = gp.Places(fpc.parse("\n".join(
        [f"MY\t{pc}\tPulau Pinang\tPulau Pinang\t07\t\t\t\t\t{lat}\t{lon}\t3" for pc, lat, lon in rows] +
        ["MY\t13000\tButterworth\tPulau Pinang\t07\t\t\t\t\t5.3991\t100.3638\t3"])))
    d = gp.Districts({"Pulau Pinang|Timur Laut": {"key": "Pulau Pinang|Timur Laut", "lat": "5.41", "lon": "100.33"}},
                     geocoder(Fake()), {}, real)
    shop = {"premise_code": "21359", "premise": "KEDAI", "state": "Pulau Pinang", "district": "Timur Laut"}
    george_town = (5.419667, 100.326333)   # the average of the three 10xxx rows
    for address in ("42B-G-9, SRI SAUJANA, GAT LEBUH MACALLUM,GEORGETOWN, PULAU PINANG,", "NO 1, GEORGE TOWN"):
        a = gp.anchor_for({**shop, "address": address}, real, d)
        assert (a["kind"], a["label"]) == ("town", "George Town"), address
        assert a["point"] == pytest.approx(george_town)
    # The state's own name is still not a town, nor a road named after George Town.
    for address in ("NO 5, LEBUH CHULIA, PULAU PINANG", "JALAN GEORGETOWN, KAMPUNG X"):
        assert gp.anchor_for({**shop, "address": address}, real, d)["kind"] == "district", address
    # A table with its own George Town rows uses them.
    own = gp.Places(fpc.parse(STATES_TXT + "\nMY\t10050\tGeorge Town\tPulau Pinang\t07\t\t\t\t\t5.4141\t100.3288\t4"))
    assert own.town("NO 1, GEORGETOWN", "Pulau Pinang") == ("George Town", (5.4141, 100.3288))


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


def test_the_state_name_comes_after_every_other_town():
    """In a district named after its state the state's name may be the town, but only when the
    address names no other town: 'TAMAN TEKNOLOGI CHENG, MELAKA' is in Cheng, not Melaka city."""
    tie = gp.Places(fpc.parse("\n".join("\t".join(r) for r in [
        ["MY", "75000", "Melaka", "Melaka", "04", "", "", "", "", "2.1960", "102.2480", "4"],
        ["MY", "75250", "Cheng", "Melaka", "04", "", "", "", "", "2.2700", "102.2000", "4"],
        ["MY", "80400", "Johor", "Johor", "01", "", "", "", "", "1.4784", "103.7758", "4"],
        ["MY", "81750", "Masai", "Johor", "01", "", "", "", "", "1.4900", "103.8780", "4"]])))
    d = gp.Districts({k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in {
        "Melaka|Melaka Tengah": (2.20, 102.25), "Johor|Johor Bahru": (1.4927, 103.7414)}.items()},
        geocoder(Fake()), {}, tie)

    def anchor(state, district, address):
        a = gp.anchor_for({"premise_code": "1", "premise": "KEDAI", "state": state, "district": district,
                           "address": address}, tie, d)
        return a["kind"], a["label"]

    assert anchor("Melaka", "Melaka Tengah", "NO.19395, JALAN TTC 26,TAMAN TEKNOLOGI CHENG,MELAKA,") == ("town", "Cheng")
    assert anchor("Melaka", "Melaka Tengah", "MELAKA, TAMAN TEKNOLOGI CHENG") == ("town", "Cheng")
    assert anchor("Johor", "Johor Bahru", "NO 5, JALAN X, MASAI, JOHOR") == ("town", "Masai")
    assert anchor("Melaka", "Melaka Tengah", "NO 5, JALAN HANG TUAH, MELAKA") == ("town", "Melaka")
    assert [n for n, _, _ in tie.town_hits("CHENG, MELAKA", "Melaka", "Melaka Tengah")] == ["Cheng", "Melaka"]


def test_a_spread_town_uses_its_rows_near_the_district():
    """A town name with GeoNames rows far apart (Kulai: rows 25 km apart in a table made from
    addresses) names several places. Only its rows within 10 km of where the district's shops are
    count, and only if they agree."""
    def table(rows):
        return gp.Places(fpc.parse("\n".join("\t".join(["MY", pc, "Kulai", "Johor", "01", "", "", "", "",
                                                          str(lat), str(lon), "4"]) for pc, lat, lon in rows)))

    near_a, near_b, far = [("81000", 1.66, 103.60), ("81010", 1.67, 103.61)], [("81020", 1.66, 103.82)], [
        ("81900", 1.95, 103.90)]
    assert gp.km(1.66, 103.60, 1.95, 103.90) > 45 and gp.km(1.66, 103.60, 1.66, 103.82) > 24
    kulai = {"premise_code": "1", "premise": "KEDAI", "state": "Johor", "district": "Kulai",
             "address": "NO 1, BANDAR PUTRA, KULAI"}

    def anchor(table_rows, centre, cache=None):
        p = table(table_rows)
        d = gp.Districts({"Johor|Kulai": {"key": "Johor|Kulai", "lat": str(centre[0]), "lon": str(centre[1])}},
                         geocoder(Fake()), cache or {}, p)
        a = gp.anchor_for(kulai, p, d)
        return a["kind"], a["point"]

    kind, point = anchor(near_a + far, (1.66, 103.60))   # one group near the district: its median
    assert kind == "town" and point == pytest.approx((1.665, 103.605))
    # Two groups, each near one of the district's references (its centre, and shops OpenStreetMap
    # found 24 km east): which one? Neither.
    found = {c: cache_row(c, 1.66, 103.82 + c / 10000, "name", district="Kulai", state="Johor") for c in (5, 6)}
    assert anchor(near_a + near_b, (1.66, 103.60), found)[0] == "district"
    assert anchor(near_a + near_b, (1.66, 103.60))[0] == "town"          # only the centre: group A
    assert anchor(near_a + far, (1.66, 103.72))[0] == "district"         # 13 km away: not near enough
    assert anchor(far + [("81910", 2.20, 104.10)], (1.66, 103.60))[0] == "district"   # spread, none near
    # No trusted centre and no shops found by OpenStreetMap: the search centre picks the rows.
    shared = {"Johor|Kulai": {"key": "Johor|Kulai", "lat": "1.66", "lon": "103.60", "shared": "1"}}
    p = table(near_a + far)
    d = gp.Districts(shared, geocoder(Fake()), {}, p)
    assert d.references("Johor", "Kulai") == [] and gp.anchor_for(kulai, p, d)["kind"] == "town"
    # Without anything to judge by, a spread name is not a town (as before).
    assert p.town("NO 1, KULAI", "Johor") is None


def test_a_spread_town_comes_after_the_locality_written_with_it():
    """Real shop 17734 in Lipis: 'JALAN LAMA LIPIS RAUB,BENTA, KUALA LIPIS,PAHANG.' Kuala Lipis's
    GeoNames rows lie far apart (the postal town of a wide area); Benta is where the shop is."""
    lipis = gp.Places(fpc.parse("\n".join("\t".join(["MY", pc, place, "Pahang", "06", "", "", "", "", lat, lon, "3"])
                                          for pc, place, lat, lon in [
        ("27200", "Kuala Lipis", "4.1800", "102.0500"), ("27210", "Kuala Lipis", "4.4500", "102.3000"),
        ("27300", "Benta", "4.0167", "101.9667"), ("27600", "Raub", "3.7900", "101.8600")])))
    centre = (4.15, 102.03)
    d = gp.Districts({"Pahang|Lipis": {"key": "Pahang|Lipis", "lat": str(centre[0]), "lon": str(centre[1])}},
                     geocoder(Fake()), {}, lipis)
    shop = {"premise_code": "17734", "premise": "PASARAYA MEMPAGA FRESH BENTA", "state": "Pahang", "district": "Lipis"}
    address = "JALAN LAMA LIPIS RAUB,BENTA, KUALA LIPIS,PAHANG."
    assert [n for n, _, _ in lipis.town_hits(address, "Pahang", "Lipis", [centre])] == ["Benta", "Raub", "Kuala Lipis"]
    a = gp.anchor_for({**shop, "address": address}, lipis, d)
    assert (a["kind"], a["label"]) == ("town", "Benta")
    # Alone, the spread name still counts (its rows near the district).
    a = gp.anchor_for({**shop, "address": "NO 1, KUALA LIPIS, PAHANG"}, lipis, d)
    assert (a["kind"], a["label"], a["point"]) == ("town", "Kuala Lipis", (4.18, 102.05))


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
    # Cached shop: what it had. The error changes nothing (tries 0, never tried); only the offline
    # anchor every run gives a district row (KEPONG is a checked town) is there, from before the search.
    assert rows["2"] == {**old, "precision": "town", "lat": "3.210000", "lon": "101.640000", "query": "Kepong"}
    assert st["offline"] == 1 and st["kept"] == 1 and st["errors"] == 2 and st["upgraded_offline"] == 1
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


def test_offline_anchors_upgrade_district_rows_before_any_search(tmp_path):
    """A run with no time for searches (geo_minutes 0) still gives a shop at district precision
    its checked postcode or town. tries and last_tried stay, so OpenStreetMap is still asked on the
    usual schedule. Rows already located, rows that fail the checks and shops not in the build are
    not touched."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv", MY_TXT + "\n" + KB_TXT)
    data = write_build(tmp_path / "data", {
        (KL, "Kepong"): [(1, "KEDAI 1", "NO 5, JALAN METRO, 52100 KUALA LUMPUR"),     # a checked postcode
                         (2, "KEDAI 2", "NO 6, JALAN METRO PRIMA, KEPONG"),             # a checked town
                         (3, "KEDAI 3", "NO 7, JALAN ENAU, 81100 JOHOR BAHRU"),         # another state's
                         (4, "KEDAI 4", "NO 8, JALAN 1"),                              # nothing to go on
                         (5, "KEDAI 5", "NO 9, 52100 KUALA LUMPUR"),                  # found by name
                         (6, "KEDAI 6", "NO 10, 52100 KEPONG"),                       # a town row
                         (8, "KEDAI 8", "NO 12, KEPONG")],                            # no point
        ("Kelantan", "Kota Bharu"): [(9, "KEDAI 9", "NO 1, KAMPUNG X, KUALA KRAI")]})   # 66 km: too far
    rows = [cache_row(1, 3.2, 101.63, "district", tries=1, last="2026-11-01"),
            cache_row(2, 3.2, 101.63, "district", tries=2, last="2026-10-01"),   # no more lookups due
            cache_row(3, 3.2, 101.63, "district", tries=0, last=""),
            cache_row(4, 3.2, 101.63, "district", tries=0, last=""),
            cache_row(5, 3.205, 101.645, "name", tries=1, last="2026-11-01"),
            cache_row(6, 3.215, 101.645, "town", tries=1, last="2026-11-01", query="Kepong Baru"),
            {**cache_row(8, 0, 0, "district", tries=0, last=""), "lat": "", "lon": ""},
            cache_row(9, *KB_CENTRE, "district", district="Kota Bharu", state="Kelantan", tries=0, last=""),
            cache_row(7, 3.2, 101.63, "district", tries=0, last="")]           # not in the build
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, rows)
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                       [{"key": f"{KL}|Kepong", "lat": "3.200000", "lon": "101.630000"},
                        {"key": "Kelantan|Kota Bharu", "lat": str(KB_CENTRE[0]), "lon": str(KB_CENTRE[1])}])
    fake = Fake()
    st = gp.run(data, cache, dcache, gp.Geocoder(fetch=fake, delay=0, max_minutes=0), postcodes_path=pc, today=TODAY)
    assert fake.calls == [] and st["requests"] == 0
    after = read_rows(cache)
    r1, r2, r8 = after["1"], after["2"], after["8"]
    assert (r1["precision"], r1["query"], r1["postcode"], r1["tries"], r1["last_tried"]) == (
        "postcode", "52100", "52100", "1", "2026-11-01")
    assert (float(r1["lat"]), float(r1["lon"])) == pytest.approx((3.212, 101.643))
    assert (r2["precision"], r2["query"], r2["tries"], r2["last_tried"]) == ("town", "Kepong", "2", "2026-10-01")
    assert (r8["precision"], r8["query"], r8["tries"]) == ("town", "Kepong", "0")
    for i, code in ((2, "3"), (3, "4"), (4, "5"), (5, "6"), (7, "9"), (8, "7")):
        assert after[code] == {**rows[i], "postcode": after[code]["postcode"]}, code
    assert st["upgraded_offline"] == 3
    # The next run with time: the retry pass asks as usual, rows with no real location first.
    fake = Fake()
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-11-30")
    assert shops_asked(fake) == ["KEDAI 3", "KEDAI 4", "KEDAI 9", "KEDAI 8", "KEDAI 6", "KEDAI 1"]
    assert st["upgraded_offline"] == 0 and read_rows(cache)["1"]["tries"] == "2"


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


def test_a_retry_keeps_an_old_point_for_the_same_postcode(tmp_path):
    """OpenStreetMap finds nothing again. Today's anchor is the shop's own postcode at GeoNames'
    point, 10 km from the old point (which came from OpenStreetMap): the old point stays. A shop
    whose postcode changed (a lot number read as one before) gets today's anchor."""
    old = [cache_row(1, 3.30, 101.643, "postcode", tries=0, last="", query="52100", postcode="52100"),
           cache_row(2, 3.30, 101.643, "postcode", tries=0, last="", query="52109", postcode="52109")]
    data, cache, dcache, pc = kepong(tmp_path, [(1, "KEDAI 1", "NO 1, JALAN 1, 52100 KUALA LUMPUR"),
                                                (2, "KEDAI 2", "NO 2, JALAN 2, 52100 KUALA LUMPUR")], old)
    places = gp.Places.load(pc)
    assert gp.km(3.30, 101.643, *places.postcode("52100")) == pytest.approx(9.8, abs=0.1)
    gp.run(data, cache, dcache, geocoder(Fake()), postcodes_path=pc, today=TODAY)
    rows = read_rows(cache)
    assert (rows["1"]["lat"], rows["1"]["query"], rows["1"]["tries"]) == ("3.300000", "52100", "1")
    assert (rows["2"]["query"], rows["2"]["tries"]) == ("52100", "1")
    assert (float(rows["2"]["lat"]), float(rows["2"]["lon"])) == pytest.approx(places.postcode("52100"))


def test_found_shops_vouch_only_when_two_or_more_agree():
    p = gp.Places(fpc.parse(MY_TXT))

    def found(points):
        return [({"state": KL, "district": "Kepong", "address": f"NO {i}, 52100 KEPONG"}, pt) for i, pt in enumerate(points)]

    p.learn(found([(3.22, 101.65)]))                                   # one shop: no
    assert p.vouched(KL, "postcode", "52100") is None and p.vouched(KL, "town", "Kepong") is None
    p.learn(found([(3.22, 101.65), (3.23, 101.65)]))                   # two that agree: their median
    assert p.vouched(KL, "postcode", "52100") == pytest.approx((3.225, 101.65))
    assert p.vouched(KL, "town", "Kepong") == pytest.approx((3.225, 101.65))
    assert p.vouched("Selangor", "postcode", "52100") is None          # per state
    # Four shops, two of them together and two far apart: not most of them, so no.
    p.learn(found([(3.22, 101.65), (3.23, 101.65), (3.60, 101.65), (2.90, 101.65)]))
    assert p.vouched(KL, "postcode", "52100") is None
    # Three of four together: the median of those three.
    p.learn(found([(3.22, 101.65), (3.23, 101.65), (3.24, 101.65), (3.60, 101.65)]))
    assert p.vouched(KL, "postcode", "52100") == pytest.approx((3.23, 101.65))


def test_a_spread_town_uses_its_near_rows_before_found_shops():
    """Jasin: GeoNames has 'Jasin' rows across the district (a postal town for a wide area), and the
    two shops OpenStreetMap found that name JASIN are 14 km south of the town. The rows near the
    district's shops place a Jasin shop; the found shops only when no row is near."""
    p = gp.Places(fpc.parse("\n".join(["MY\t77000\tJasin\tMelaka\t04\t\t\t\t\t2.3100\t102.4300\t3",
                                       "MY\t77300\tJasin\tMelaka\t04\t\t\t\t\t2.5500\t102.2500\t3"])))
    p.learn([({"state": "Melaka", "district": "Jasin", "address": f"NO {i}, BANDAR JASIN, JASIN, MELAKA"}, pt)
             for i, pt in enumerate([(2.15, 102.42), (2.21, 102.40)])])
    assert ("melaka", "JASIN") in p.spread_towns and p.found_town[("melaka", "JASIN")] == pytest.approx((2.18, 102.41))
    assert p.vouched("Melaka", "town", "Jasin") is None   # a spread name: its near rows come first
    shop = {"premise_code": "21257", "premise": "HANTAI TRADING", "state": "Melaka", "district": "Jasin",
            "address": "JC 2384 & JC 2385 TAMAN PERNIAGAAN JASIN,JASIN MELAKA,"}

    def anchor(centre):
        d = gp.Districts({"Melaka|Jasin": {"key": "Melaka|Jasin", "lat": str(centre[0]), "lon": str(centre[1])}},
                         geocoder(Fake()), {}, p)
        a = gp.anchor_for(shop, p, d)
        return a["kind"], a["point"]

    assert anchor((2.3058, 102.4223)) == ("town", (2.31, 102.43))              # the row near the district
    assert anchor((2.17, 102.53)) == ("town", pytest.approx((2.18, 102.41)))  # no row near: the found shops


def test_retries_are_due_more_than_20_days_after_the_last():
    """A shop or a district point last asked exactly RETRY_DAYS (20) days ago is not due yet; one
    asked 21 days ago is."""
    assert TODAY == "2026-11-06"
    shops = [{"premise_code": str(c), "state": KL, "district": "Kepong"} for c in (1, 2)]
    cache = {"1": cache_row(1, 3.2, 101.63, "district", tries=1, last="2026-10-17"),   # 20 days ago
             "2": cache_row(2, 3.2, 101.63, "district", tries=1, last="2026-10-16")}   # 21 days ago
    assert [s["premise_code"] for s in gp.retry_queue(shops, cache, TODAY, 10)] == ["2"]
    d = gp.Districts({}, geocoder(Fake()), {}, today=TODAY)
    assert d._due({"last_tried": "2026-10-17"}) is False and d._due({"last_tried": "2026-10-16"}) is True


def test_rows_upgraded_before_the_search_are_retried_after_the_district_rows(tmp_path):
    """The offline pass makes shop 1 'town' (KEPONG in its address); it is then retried after the
    shops still at district precision, in the same run."""
    rows = [cache_row(1, 3.2, 101.63, "district", tries=0, last=""), cache_row(2, 3.2, 101.63, "district", tries=0, last="")]
    data, cache, dcache, pc = kepong(tmp_path, [(1, "KEDAI 1", "NO 1, KEPONG"), (2, "KEDAI 2", "JALAN 2")], rows)
    fake = Fake()
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today=TODAY)
    assert st["upgraded_offline"] == 1 and shops_asked(fake) == ["KEDAI 2", "KEDAI 1"]


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
    assert "Perlis|Kangar" not in d   # a new district: nothing saved, so the next run asks again
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


def test_a_rejected_new_district_is_asked_again_and_empty_points_are_retried(tmp_path):
    """A wrong answer for a district new to district_geo.csv saves nothing, so the next run asks
    again. A district with no point (nothing found) is asked again every RETRY_DAYS days."""
    pc = write_postcodes(tmp_path / "postcodes_my.csv")
    data = write_build(tmp_path / "data", {("Perlis", "Kangar"): [(1, "KEDAI 1", "JALAN 1")]})
    cache, dcache = tmp_path / "p.csv", tmp_path / "d.csv"
    answers = {"Kangar, Perlis, Malaysia": (3.2, 101.6)}   # near KL: more than 50 km from Perlis
    fake = Fake(answers)
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-11-02")
    assert st["district_rejected"] == 1 and read_rows(dcache) == {} and st["missed"] == 1
    gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-11-09")
    assert fake.queries().count("Kangar, Perlis, Malaysia") == 2 and read_rows(dcache) == {}
    answers["Kangar, Perlis, Malaysia"] = None   # nothing found: saved with no point
    gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-11-16")
    row = read_rows(dcache)["Perlis|Kangar"]
    assert (row["lat"], row["tries"], row["last_tried"]) == ("", "1", "2026-11-16")
    gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-12-01")   # 15 days later
    assert fake.queries().count("Kangar, Perlis, Malaysia") == 3
    answers["Kangar, Perlis, Malaysia"] = (6.44, 100.2)
    st = gp.run(data, cache, dcache, geocoder(fake), postcodes_path=pc, today="2026-12-07")   # 21 days later
    assert fake.queries().count("Kangar, Perlis, Malaysia") == 4 and st["district_requeried"] == 1
    row = read_rows(dcache)["Perlis|Kangar"]
    assert (row["lat"], row["tries"], row["last_tried"]) == ("6.440000", "2", "2026-12-07")
    assert read_rows(cache)["1"]["precision"] == "district"
    # An old row with no point and no date (from before last_tried) is due at once.
    d = gp.Districts({"Perlis|Arau": {"key": "Perlis|Arau", "lat": "", "lon": "", "tries": "1"}},
                     geocoder(Fake({"Arau, Perlis, Malaysia": (6.43, 100.27)})), {}, today="2026-12-07")
    d.prepare([("Perlis", "Arau")])
    assert d.trusted("Perlis", "Arau") == (6.43, 100.27) and d.requeried == 1


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
    assert list(read_rows(dcache)[f"{KL}|Kepong"]) == ["key", "lat", "lon", "tries", "shared", "last_tried"]


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
