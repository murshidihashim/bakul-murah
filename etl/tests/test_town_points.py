"""Tests for town points from the GeoNames place list (etl/geo/places_my.csv) in geocode_premises.py,
and for the small fixes that came with them. No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import csv
import json
import math
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_places as fpl  # noqa: E402
import fetch_postcodes as fpc  # noqa: E402
import geocode_premises as gp  # noqa: E402

TODAY = "2026-11-16"
KL = "W.P. Kuala Lumpur"

# Postal rows (postcode, place, state, lat, lon) and place-list rows, after real cases of the 2026 data.
# Bagan Serai (Kerian): the postcode table's town point is 25 km from the town, near Taiping.
KERIAN_CENTRE, BAGAN_SERAI, BAGAN_SERAI_POSTAL = (4.95, 100.60), (5.0108, 100.541), (4.85, 100.70)
# Putatan: five postcodes share one rough point; Kota Kinabalu has a point for each postcode.
PUTATAN_CENTRE, PUTATAN, PUTATAN_POSTAL = (5.89, 116.06), (5.8968, 116.0559), (5.95, 116.20)
KK_CENTRE, KOTA_KINABALU = (5.98, 116.07), (5.9749, 116.0724)
POSTAL = [("34300", "Bagan Serai", "Perak", *BAGAN_SERAI_POSTAL),
          ("34000", "Taiping", "Perak", 4.85, 100.74)] + [
          (f"8820{i}", "Putatan", "Sabah", *PUTATAN_POSTAL) for i in range(5)] + [
          ("88000", "Kota Kinabalu", "Sabah", 5.98, 116.07), ("88300", "Kota Kinabalu", "Sabah", 5.93, 116.18),
          ("88450", "Kota Kinabalu", "Sabah", 6.03, 116.12)]
PLACES = [(1734760, "Bagan Serai", "Perak", *BAGAN_SERAI, ""), (1734990, "Putatan", "Sabah", *PUTATAN, ""),
          (1733432, "Kota Kinabalu", "Sabah", *KOTA_KINABALU, "Jesselton")]


def postal_rows(rows):
    return [{"postcode": pc, "place": place, "state": st, "lat": str(lat), "lon": str(lon), "accuracy": "3"}
            for pc, place, st, lat, lon in rows]


def place_rows(rows):
    return [{"geonameid": str(gid), "name": name, "asciiname": name, "alt": alt, "lat": f"{lat:.5f}",
             "lon": f"{lon:.5f}", "code": "PPL", "state": st, "population": "0"} for gid, name, st, lat, lon, alt in rows]


def table(postal=POSTAL, places=PLACES):
    return gp.Places(postal_rows(postal), place_rows(places) if places is not None else None)


def districts(centres, places, cache=None):
    dc = {k: {"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in centres.items()}
    return gp.Districts(dc, gp.Geocoder(fetch=lambda params: [], delay=0), cache or {}, places)


def anchor(state, district, address, places, d):
    return gp.anchor_for({"premise_code": "1", "premise": "KEDAI", "state": state, "district": district,
                          "address": address}, places, d)


def found_shops(state, district, address, points):
    return [({"state": state, "district": district, "address": address}, p) for p in points]


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as f:
        return {r[next(iter(r))]: r for r in csv.DictReader(f)}


def write_build(root, shops):
    """shops: {(state, district): [(code, name, address), ...]} -> root/d/<slug>.json"""
    (root / "d").mkdir(parents=True, exist_ok=True)
    for (st, dist), rows in shops.items():
        d = {"state": st, "district": dist, "premise_cols": ["premise_code", "premise", "premise_type", "address"],
             "premises": [[c, n, "Pasar Mini", a] for c, n, a in rows], "prices": {}}
        slug = f"{st}--{dist}".lower().replace(" ", "-").replace(".", "")
        (root / "d" / f"{slug}.json").write_text(json.dumps(d), encoding="utf-8")
    return root


def cache_row(code, point, prec, district, state, query="x", tries=1, last="2026-11-01"):
    return {"premise_code": str(code), "lat": f"{point[0]:.6f}", "lon": f"{point[1]:.6f}", "precision": prec,
            "query": query, "district": district, "state": state, "postcode": "", "tries": str(tries),
            "last_tried": last}


# ---------- TP1: the place point of a town ----------

def test_a_town_is_placed_at_its_own_point_in_the_place_list():
    """Bagan Serai: the postcode table puts it 25 km away; the place list has the town. Found shops
    that name the town still come first; without the list everything is as before."""
    assert gp.km(*BAGAN_SERAI, *BAGAN_SERAI_POSTAL) == pytest.approx(25.1, abs=0.3)
    places = table()
    d = districts({"Perak|Kerian": KERIAN_CENTRE}, places)
    a = anchor("Perak", "Kerian", "NO 1, JALAN BESAR, BAGAN SERAI", places, d)
    assert (a["kind"], a["label"], a["via"], a["vouched"]) == ("town", "Bagan Serai", "place", True)
    assert a["point"] == pytest.approx(BAGAN_SERAI)
    old = table(places=None)
    a = anchor("Perak", "Kerian", "NO 1, JALAN BESAR, BAGAN SERAI", old, districts({"Perak|Kerian": KERIAN_CENTRE}, old))
    assert (a["kind"], a["via"], a["vouched"]) == ("town", "rows", False) and a["point"] == pytest.approx(BAGAN_SERAI_POSTAL)
    places.learn(found_shops("Perak", "Kerian", "NO 9, BAGAN SERAI", [(5.02, 100.55), (5.03, 100.55)]))
    a = anchor("Perak", "Kerian", "NO 1, JALAN BESAR, BAGAN SERAI", places, d)
    assert (a["via"], a["point"]) == ("found", pytest.approx((5.025, 100.55)))


def test_a_homonym_further_than_35_km_is_dropped():
    """Perak's 'Sungai Siput' in the place list is 50 km from Sungai Siput (U), whose GeoNames name
    is 'Sungai Siput Utara' (alt 'Sungai Siput'). A name match exists, so the alt name is not used,
    and the far homonym is dropped: the postcode table's point (14 km from the centre) stays."""
    centre, postal = (4.772071, 100.940878), (4.7421, 101.0601)
    places = table([("31100", "Sungai Siput", "Perak", *postal)],
                   [(1, "Sungai Siput", "Perak", 4.3945, 101.1947, "Selatan|Sungei Sipid"),
                    (2, "Sungai Siput Utara", "Perak", 4.8151, 101.0786, "Sungai Siput|Sungei Siput")])
    d = districts({"Perak|Kuala Kangsar": centre}, places)
    assert gp.km(4.3945, 101.1947, *centre) > gp.CHECK_KM
    a = anchor("Perak", "Kuala Kangsar", "NO 1, JALAN BESAR, SUNGAI SIPUT", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Sungai Siput", "rows") and a["point"] == pytest.approx(postal)
    assert places.place_point("Perak", "SUNGAI SIPUT", [centre]) is None
    assert {k[:2] for k in places.place_dropped} == {("perak", "SUNGAI SIPUT")}


def test_two_near_places_of_one_name_are_ambiguous():
    """Two places of the name more than 10 km apart, both within 15 km of where the district's shops
    are: which one? Neither (the postcode table's point is used). One of them further out: the near one."""
    centre, near, other, far = (3.00, 101.50), (3.03, 101.50), (3.13, 101.50), (3.20, 101.50)
    assert gp.km(*near, *other) > gp.ANCHOR_KM and gp.km(*other, *centre) < gp.GUESS_KM < gp.km(*far, *centre)
    postal = [("42000", "Sungai Buah", "Selangor", 3.02, 101.52)]
    two = table(postal, [(1, "Sungai Buah", "Selangor", *near, ""), (2, "Sungai Buah", "Selangor", *other, "")])
    assert two.place_point("Selangor", "SUNGAI BUAH", [centre]) is None
    assert {k[:2] for k in two.place_ambiguous} == {("selangor", "SUNGAI BUAH")}
    a = anchor("Selangor", "Kuala Langat", "NO 1, SUNGAI BUAH", two, districts({"Selangor|Kuala Langat": centre}, two))
    assert a["via"] == "rows"
    one = table(postal, [(1, "Sungai Buah", "Selangor", *near, ""), (2, "Sungai Buah", "Selangor", *far, "")])
    assert one.place_point("Selangor", "SUNGAI BUAH", [centre]) == pytest.approx(near) and not one.place_ambiguous


def test_alt_names_count_only_when_no_name_matches():
    """Pengkalan Hulu is in the place list only as an alt name of 'Keruh' (its old name). With a
    place called 'Pengkalan Hulu' elsewhere, that one is the match (and too far: none)."""
    centre, keruh = (5.55, 101.05), (5.7167, 101.0)
    postal = [("33100", "Pengkalan Hulu", "Perak", 5.4731, 101.0105)]
    places = table(postal, [(1, "Keruh", "Perak", *keruh, "Keroh|Kroh|Pengkalan Hulu")])
    d = districts({"Perak|Hulu Perak": centre}, places)
    a = anchor("Perak", "Hulu Perak", "NO 14,JALAN BETONG,PENGKALAN HULU", places, d)
    assert (a["kind"], a["via"]) == ("town", "place") and a["point"] == pytest.approx(keruh)
    other = table(postal, [(1, "Keruh", "Perak", *keruh, "Keroh|Kroh|Pengkalan Hulu"),
                           (2, "Pengkalan Hulu", "Perak", 4.95, 101.40, "")])
    assert other.place_point("Perak", "PENGKALAN HULU", [centre]) is None


def test_an_alt_name_short_for_another_postal_town_is_not_that_town():
    """'Tasek Glugor' (alt 'Gelugor' and 'Tasek Gelugor') is the postcode table's TASEK GELUGOR (13300)
    on the mainland, not GELUGOR (11700) on Penang island, 29 km away."""
    gelugor, tasek = (5.3653, 100.3067), (5.48032, 100.49849)
    places = table([("11700", "Gelugor", "Pulau Pinang", *gelugor), ("13300", "Tasek Gelugor", "Pulau Pinang", 5.48, 100.4985)],
                   [(1, "Tasek Glugor", "Penang", *tasek, "Gelugor|Gelugor Seberang Perai|Tasek Gelugor")])
    d = districts({"Pulau Pinang|Barat Daya": (5.335659, 100.23895), "Pulau Pinang|Seberang Perai Utara": (5.45, 100.45)},
                  places)
    a = anchor("Pulau Pinang", "Barat Daya", "NO.5, JALAN HELANG,SUNGAI DUA, GELUGOR", places, d)
    assert (a["label"], a["via"]) == ("Gelugor", "rows") and a["point"] == pytest.approx(gelugor)
    a = anchor("Pulau Pinang", "Seberang Perai Utara", "NO 1, TASEK GELUGOR", places, d)
    assert (a["label"], a["via"]) == ("Tasek Gelugor", "place") and a["point"] == pytest.approx(tasek)


def test_state_names_of_the_place_list_match_the_shops_states():
    """The place list writes 'Penang' and 'Kuala Lumpur'; PriceCatcher 'Pulau Pinang' and 'W.P. Kuala Lumpur'."""
    places = table([("13000", "Butterworth", "Pulau Pinang", 5.42, 100.40), ("52100", "Kepong", "Wilayah Persekutuan Kuala Lumpur",
                                                                               3.21, 101.64)],
                   [(1, "Butterworth", "Penang", 5.3991, 100.3638, ""), (2, "Kepong", "Kuala Lumpur", 3.2103, 101.6365, ""),
                    (3, "Kepong", "Johor", 1.6, 103.5, "")])
    assert places.place_point("Pulau Pinang", "BUTTERWORTH", [(5.40, 100.40)]) == pytest.approx((5.3991, 100.3638))
    for state in (KL, "Wilayah Persekutuan Kuala Lumpur", "WP Kuala Lumpur"):
        assert places.place_point(state, "KEPONG", [(3.20, 101.63)]) == pytest.approx((3.2103, 101.6365)), state
    assert places.place_point("Selangor", "KEPONG", [(3.20, 101.63)]) is None   # no Selangor place of that name


def test_neighbour_state_places_count_only_as_strong_evidence():
    """As for postcodes: a W.P. Putrajaya shop in Cyberjaya (named after its district) may use
    Selangor's Cyberjaya; a KL town written in a KL address may not use a Selangor place."""
    places = table([("63000", "Cyberjaya", "Selangor", 2.9213, 101.6559), ("53000", "Setapak", "Wilayah Persekutuan Kuala Lumpur",
                                                                             3.20, 101.72)],
                   [(1, "Cyberjaya", "Selangor", 2.9228, 101.6505, ""), (2, "Setapak", "Selangor", 3.21, 101.73, "")])
    d = districts({"W.P. Putrajaya|Cyberjaya": (2.914883, 101.669659), f"{KL}|Setiawangsa": (3.19, 101.728)}, places)
    a = anchor("W.P. Putrajaya", "Cyberjaya", "JALAN TEKNOKRAT 6, CYBERJAYA", places, d)
    assert (a["kind"], a["via"]) == ("town", "place") and a["point"] == pytest.approx((2.9228, 101.6505))
    a = anchor(KL, "Setiawangsa", "NO 1, SETAPAK", places, d)
    assert (a["kind"], a["via"]) == ("town", "rows") and a["point"] == pytest.approx((3.20, 101.72))


def test_the_districts_own_town_may_lie_far_from_its_centre():
    """Kudat's district centre is 54 km from Kudat town: as the only place of that name and the
    district's own town it counts (up to 80 km, and only when nothing nearer is written). A weak town
    that far, or an own town with two such places, does not."""
    centre, kudat = (7.231645, 117.171156), (6.8873, 116.8236)
    assert gp.CHECK_KM < gp.km(*centre, *kudat) < gp.STRONG_KM
    postal = [("89050", "Kudat", "Sabah", 6.50, 116.50), ("89100", "Pitas", "Sabah", 6.70, 117.00)]
    places = table(postal, [(1, "Kudat", "Sabah", *kudat, ""), (2, "Pitas", "Sabah", 6.80, 116.80, "")])
    d = districts({"Sabah|Kudat": centre}, places)
    a = anchor("Sabah", "Kudat", "PASAR AM KUDAT, 89058 KUDAT", places, d)
    assert (a["kind"], a["label"], a["via"], a["strong"]) == ("town", "Kudat", "place", True)
    assert a["point"] == pytest.approx(kudat)
    assert anchor("Sabah", "Kudat", "NO 1, PITAS", places, d)["kind"] == "district"   # weak and 54 km away
    two = table(postal, [(1, "Kudat", "Sabah", *kudat, ""), (2, "Kudat", "Sabah", 6.60, 116.70, "")])
    assert anchor("Sabah", "Kudat", "PASAR AM KUDAT", two, districts({"Sabah|Kudat": centre}, two))["kind"] == "district"


def test_no_place_point_without_references():
    """Without a trusted centre or found shops nothing can check a place: the table's point, as before."""
    places = table()
    shared = {"Perak|Kerian": {"key": "Perak|Kerian", "lat": str(KERIAN_CENTRE[0]), "lon": str(KERIAN_CENTRE[1]), "shared": "1"}}
    d = gp.Districts(shared, gp.Geocoder(fetch=lambda params: [], delay=0), {}, places)
    assert d.references("Perak", "Kerian") == []
    assert anchor("Perak", "Kerian", "NO 1, BAGAN SERAI", places, d)["via"] == "rows"


# ---------- TP3: a postcode whose town has one rough point ----------

def test_a_postcode_of_a_town_with_one_rough_point_gets_the_towns_own_point():
    """Putatan's five postcodes share one point, 17 km from the town: the shop gets the town's own
    point, as a town anchor. Kota Kinabalu's postcodes each have their own point (its rows spread more
    than 10 km): those are kept, even 13 km from the city's point. A postcode that found shops vouch
    for keeps their point."""
    assert gp.km(*PUTATAN, *PUTATAN_POSTAL) > gp.ANCHOR_KM
    places = table()
    d = districts({"Sabah|Putatan": PUTATAN_CENTRE, "Sabah|Kota Kinabalu": KK_CENTRE}, places)
    for address in ("LOT 1, 88200 PUTATAN", "LOT 1, JALAN 2, 88203"):
        a = anchor("Sabah", "Putatan", address, places, d)
        assert (a["kind"], a["label"], a["via"], a["vouched"]) == ("town", "Putatan", "postcode town", True), address
        assert a["point"] == pytest.approx(PUTATAN) and a["postcode"] in ("88200", "88203")
    assert ("sabah", "KOTA KINABALU") not in places.one_point
    assert gp.km(5.93, 116.18, *KOTA_KINABALU) > gp.ANCHOR_KM
    a = anchor("Sabah", "Kota Kinabalu", "NO 1, 88300 KOTA KINABALU", places, d)
    assert (a["kind"], a["label"]) == ("postcode", "88300") and a["point"] == pytest.approx((5.93, 116.18))
    places.learn(found_shops("Sabah", "Putatan", "LOT 9, 88200 PUTATAN", [(5.90, 116.07), (5.91, 116.07)]))
    a = anchor("Sabah", "Putatan", "LOT 1, 88200 PUTATAN", places, d)
    assert (a["kind"], a["via"], a["point"]) == ("postcode", "found", pytest.approx((5.905, 116.07)))
    # Without the place list, as before: the district's own postcode with only its rough point
    # counts within 15 km (see _reach), and it is 17 km out, so the shop is 'location unknown'.
    old = table(places=None)
    a = anchor("Sabah", "Putatan", "LOT 1, 88200 PUTATAN", old, districts({"Sabah|Putatan": PUTATAN_CENTRE}, old))
    assert a["kind"] == "district" and gp.km(*PUTATAN_POSTAL, *PUTATAN_CENTRE) > gp.GUESS_KM


def test_one_rough_point_means_all_rows_within_10_km():
    """A town whose rows lie 12 km apart keeps its postcode points (it may be a bigger town)."""
    rows = [("22000", "Jerteh", "Terengganu", 5.6111, 102.5729), ("22020", "Jerteh", "Terengganu", 5.6795, 102.5851)]
    assert gp.km(5.6111, 102.5729, 5.6795, 102.5851) < gp.ONE_POINT_KM
    assert ("terengganu", "JERTEH") in table(rows, []).one_point
    wide = rows + [("22010", "Jerteh", "Terengganu", 5.72, 102.60)]
    assert gp.km(5.6111, 102.5729, 5.72, 102.60) > gp.ONE_POINT_KM
    assert ("terengganu", "JERTEH") not in table(wide, []).one_point


# ---------- TP4: old points, and the counts (TP6) ----------

def putatan_files(tmp_path, places=True):
    data = write_build(tmp_path / "data", {
        ("Perak", "Kerian"): [(1, "KEDAI 1", "NO 1, JALAN BESAR, BAGAN SERAI"), (2, "KEDAI 2", "NO 2, BAGAN SERAI"),
                              (6, "KEDAI 6", "NO 6, TAIPING")],
        ("Sabah", "Putatan"): [(3, "KEDAI 3", "LOT 1, 88200 PUTATAN"), (4, "KEDAI 4", "LOT 2, 88200 PUTATAN"),
                               (5, "KEDAI 5", "LOT 3, 88200 PUTATAN")]})
    near_putatan = (PUTATAN[0] + 0.03, PUTATAN[1])   # an old point from OpenStreetMap, 3 km from the town
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [
        cache_row(1, BAGAN_SERAI_POSTAL, "town", "Kerian", "Perak", "Bagan Serai"),
        cache_row(2, KERIAN_CENTRE, "district", "Kerian", "Perak", "Kerian, Perak", tries=2),
        cache_row(6, (4.85, 100.74), "town", "Kerian", "Perak", "Taiping"),
        cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200", last="2026-10-30"),
        cache_row(4, near_putatan, "postcode", "Putatan", "Sabah", "88200")])
    dcache = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"], [
        {"key": "Perak|Kerian", "lat": str(KERIAN_CENTRE[0]), "lon": str(KERIAN_CENTRE[1])},
        {"key": "Sabah|Putatan", "lat": str(PUTATAN_CENTRE[0]), "lon": str(PUTATAN_CENTRE[1])}])
    pc = write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(POSTAL))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PLACES)) if places else None
    return data, cache, dcache, pc, pl


def offline_run(tmp_path, places_path, sub="run"):
    """A run with no time for searches, on fresh copies of the files in tmp_path."""
    work = tmp_path / sub
    work.mkdir()
    for name in ("premise_geo.csv", "district_geo.csv"):
        shutil.copy(tmp_path / name, work / name)
    calls = []
    geo = gp.Geocoder(fetch=lambda params: calls.append(params) or [], delay=0, max_minutes=0)
    st = gp.run(tmp_path / "data", work / "premise_geo.csv", work / "district_geo.csv", geo,
                postcodes_path=tmp_path / "postcodes_my.csv", today=TODAY, places_path=places_path)
    assert calls == []
    return read_rows(work / "premise_geo.csv"), st, (work / "premise_geo.csv").read_bytes()


def test_old_points_are_corrected_by_the_place_list_before_any_search(tmp_path):
    """Before any search: an old town row at the table's point 25 km from the town's own point moves
    there; a district row of that town gets it; an old postcode row at Putatan's rough point becomes a
    town row (the one way a postcode row may become a town row); an old postcode row near the town
    (it may have come from OpenStreetMap) stays; a new shop gets the town's point. tries and
    last_tried stay. The summary counts each kind."""
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    before = read_rows(cache)
    rows, st, _ = offline_run(tmp_path, pl)
    r1, r2, r3, r4, r5 = (rows[str(c)] for c in (1, 2, 3, 4, 5))
    assert (r1["precision"], r1["query"], r1["tries"], r1["last_tried"]) == ("town", "Bagan Serai", "1", "2026-11-01")
    assert (float(r1["lat"]), float(r1["lon"])) == pytest.approx(BAGAN_SERAI, abs=1e-5)
    assert (r2["precision"], r2["query"], r2["tries"]) == ("town", "Bagan Serai", "2")
    assert (r3["precision"], r3["query"], r3["postcode"], r3["tries"], r3["last_tried"]) == (
        "town", "Putatan", "88200", "1", "2026-10-30")
    assert (float(r3["lat"]), float(r3["lon"])) == pytest.approx(PUTATAN, abs=1e-5)
    assert {k: r4[k] for k in ("lat", "lon", "precision", "query")} == {k: before["4"][k] for k in ("lat", "lon", "precision", "query")}
    assert (r5["precision"], r5["query"], r5["tries"]) == ("town", "Putatan", "0")
    assert rows["6"] == {**before["6"], "postcode": rows["6"]["postcode"]}   # Taiping: no place point, not touched
    assert (st["place_towns"], st["place_postcodes"], st["place_corrected"]) == (2, 2, 2)
    assert (st["upgraded_offline"], st["corrected_offline"], st["offline"]) == (1, 2, 1)
    assert st["place_ambiguous"] == 0 and st["place_dropped"] == 0
    # Without the place list nothing of this happens (the run is as before): the old points stay, and
    # the new Putatan shop is 'location unknown' (Putatan's rough point is 17 km out, see _reach).
    rows, st, _ = offline_run(tmp_path, None, "old")
    assert (rows["1"]["lat"], rows["2"]["lat"], rows["3"]["precision"], rows["5"]["precision"]) == (
        before["1"]["lat"], f"{BAGAN_SERAI_POSTAL[0]:.6f}", "postcode", "district")
    assert (st["place_towns"], st["place_postcodes"], st["place_corrected"]) == (0, 0, 0)


def test_a_postcode_row_stays_when_today_uses_its_postcodes_point():
    """Kuala Krai's 18000 point lies 5 km from the town's own point, so today's anchor uses it: an
    old 18000 row of a shop in Dabong, 27 km from the town (and 22 km from Dabong's centre), is not
    judged by the place list."""
    p18000, g, dabong, old = (5.5693, 102.1786), (5.5313, 102.1993), (5.376738, 102.009922), (5.2887, 102.1953)
    places = table([("18000", "Kuala Krai", "Kelantan", *p18000)], [(1, "Kuala Krai", "Kelantan", *g, "")])
    d = districts({"Kelantan|Dabong": dabong}, places)
    assert gp.km(*p18000, *g) < gp.ANCHOR_KM < gp.km(*old, *g) and gp.km(*old, *dabong) < gp.CHECK_KM
    shop = {"premise_code": "21223", "premise": "KEDAI", "state": "Kelantan", "district": "Dabong",
            "address": "KG STONG,,KEMUBU, 18000 KUALA KRAI, KELANTAN.,"}
    row = cache_row(21223, old, "postcode", "Dabong", "Kelantan", "18000")
    assert gp.wrong_by(row, shop, places, d) is None
    # A Putatan postcode row at the rough point is wrong by the place list; one near the town is not.
    putatan = table()
    d = districts({"Sabah|Putatan": PUTATAN_CENTRE}, putatan)
    shop = {**shop, "state": "Sabah", "district": "Putatan", "address": "LOT 1, 88200 PUTATAN"}
    assert gp.wrong_by(cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200"), shop, putatan, d) == "place"
    assert gp.wrong_by(cache_row(4, (5.92, 116.06), "postcode", "Putatan", "Sabah", "88200"), shop, putatan, d) is None


# ---------- the place list file ----------

def test_without_a_usable_place_list_the_run_is_as_before(tmp_path, capsys):
    """No list, a missing one, an empty one, one with only a header, one without its columns and one
    that is not text: the same rows as a run without the list, and a warning (no stop)."""
    putatan_files(tmp_path, places=False)
    _, _, expected = offline_run(tmp_path, None, "none")
    capsys.readouterr()
    bad = {"missing.csv": None, "empty.csv": b"", "header.csv": (",".join(fpl.FIELDS) + "\n").encode(),
           "columns.csv": b"id,town,x,y\n1,Bagan Serai,5.0108,100.541\n", "binary.csv": b"\xff\xfe\x00\x81\x00garbage"}
    for name, content in bad.items():
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        _, st, got = offline_run(tmp_path, path, f"run-{name}")
        assert got == expected, name
        assert f"place list {path}" in capsys.readouterr().err, name
        assert (st["place_towns"], st["place_postcodes"]) == (0, 0)
    assert gp.read_places(tmp_path / "missing.csv") is None and gp.read_places(None) is None


def test_a_place_list_with_a_byte_order_mark_and_bad_rows_still_works(tmp_path):
    """A list saved by a spreadsheet (byte-order mark) is read as written. Rows whose point is not a
    point on Earth (nan, inf, 1e400, out of range) are left out; the others still count."""
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    _, _, expected = offline_run(tmp_path, pl, "good")
    pl.write_bytes(b"\xef\xbb\xbf" + pl.read_bytes())
    assert gp.read_places(pl)[0]["geonameid"] == "1734760"
    _, _, got = offline_run(tmp_path, pl, "bom")
    assert got == expected
    junk = [(9000 + i, "Bagan Serai", "Perak", lat, lon, "") for i, (lat, lon) in
            enumerate([(math.nan, 100.6), (math.inf, 100.6), (5.0, -math.inf), (95.0, 100.6), (5.0, 200.0)])]
    rows = place_rows(junk + PLACES)
    rows[0]["lat"], rows[1]["lat"] = "nan", "1e400"
    write_csv(pl, fpl.FIELDS, rows)
    assert len(gp.read_places(pl)) == len(PLACES)
    _, _, got = offline_run(tmp_path, pl, "junk")
    assert got == expected


def test_the_run_is_deterministic(tmp_path):
    """The same files give the same rows and summary, whatever the order of the place list's rows."""
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    _, st1, a = offline_run(tmp_path, pl, "one")
    _, st2, b = offline_run(tmp_path, pl, "two")
    write_csv(pl, fpl.FIELDS, list(reversed(place_rows(PLACES + [(5, "Putatan", "Sabah", 5.92, 116.30, "")]))))
    _, st3, c = offline_run(tmp_path, pl, "three")
    assert a == b == c and st1 == st2
    assert {k: st3[k] for k in ("place_towns", "place_postcodes", "place_corrected")} == {
        k: st1[k] for k in ("place_towns", "place_postcodes", "place_corrected")}


# ---------- TP5: small fixes ----------

def test_a_point_must_be_finite_and_on_earth(tmp_path):
    """_point: 'inf' and '1e400' (inf to Python) used to stop the whole run with a math error."""
    for lat, lon in (("inf", "100"), ("1e400", "100"), ("nan", "100"), ("5", "-inf"), ("91", "100"), ("5", "181"),
                     ("", "100"), (None, "1")):
        assert gp._point({"lat": lat, "lon": lon}) is None, (lat, lon)
    assert gp._point({"lat": "-90", "lon": "180"}) == (-90.0, 180.0)
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    rows = list(read_rows(cache).values())
    rows[0] = {**rows[0], "lat": "1e400", "precision": "name"}     # shop 1: a broken found point
    write_csv(cache, gp.FIELDS, rows)
    write_csv(pc, fpc.FIELDS, postal_rows(POSTAL) + [{"postcode": "34310", "place": "Bagan Serai", "state": "Perak",
                                                       "lat": "inf", "lon": "100.5", "accuracy": "3"}])
    got, st, _ = offline_run(tmp_path, pl)
    assert (got["1"]["precision"], got["1"]["query"]) == ("town", "Bagan Serai") and st["upgraded_offline"] == 2


SIBU_POSTAL = [("96000", "Sibu", "Sarawak", 2.2873, 111.8305), ("96007", "Sibu", "Sarawak", 2.10, 112.10)]
SIBU_TOWN, SIBU_CENTRE, SIBUJAYA_CENTRE = (2.2873, 111.8305), (2.30, 111.83), (2.24, 111.97)


def test_shops_of_another_district_do_not_place_a_spread_town():
    """Sibu Jaya shop 5304 ('...,SIBU JAYA,SIBU, SARAWAK'): SIBU's postal rows spread, and the shops
    OpenStreetMap found that name it are in Sibu town (district Sibu), 16 km from Sibu Jaya. Neither
    their point nor Sibu's place point places this shop, and its old row there is wrong. A Sibu shop
    still gets the town."""
    assert gp.km(*SIBU_TOWN, *SIBUJAYA_CENTRE) > gp.ANCHOR_KM and gp.km(*SIBU_POSTAL[0][3:], *SIBU_POSTAL[1][3:]) > 20
    for place_list in (None, [(1, "Sibu", "Sarawak", *SIBU_TOWN, "")]):
        places = table(SIBU_POSTAL, place_list)
        cache = {str(c): cache_row(c, p, "name", "Sibu", "Sarawak") for c, p in ((1, (2.29, 111.83)), (2, (2.30, 111.84)))}
        places.learn(found_shops("Sarawak", "Sibu", "NO 1, JALAN PULAU, SIBU", [(2.29, 111.83), (2.30, 111.84)]))
        d = districts({"Sarawak|Sibu": SIBU_CENTRE, "Sarawak|Sibujaya": SIBUJAYA_CENTRE}, places, cache)
        shop = {"premise_code": "5304", "premise": "PASAR BASAH SIBUJAYA", "state": "Sarawak", "district": "Sibujaya",
                "address": "MENYAN LAND DISTRICT,SIBU JAYA,SIBU, SARAWAK"}
        assert gp.anchor_for(shop, places, d)["kind"] == "district", place_list
        old = cache_row(5304, (2.295, 111.835), "town", "Sibujaya", "Sarawak", "Sibu", tries=0, last="")
        assert gp.wrong_by(old, shop, places, d) == "found"
        sibu = {**shop, "district": "Sibu", "address": "NO 2, JALAN KAMPUNG NYABOR, SIBU"}
        assert gp.anchor_for(sibu, places, d)["kind"] == "town"


def test_alias_names_are_no_route_ends_and_george_town_is_on_the_island():
    """GeoNames names George Town's postcodes after the state: the alias 'George Town' made TOWN the
    end of a GeoNames name, so 'BUTTERWORTH-TOWN' read as a route. And GEORGETOWN in a mainland
    address is not where the shop is."""
    rows = [("10050", "Pulau Pinang", "Pulau Pinang", 5.4170, 100.3300), ("10200", "Pulau Pinang", "Pulau Pinang", 5.4120, 100.3340),
            ("13000", "Butterworth", "Pulau Pinang", 5.3991, 100.3638)]
    places = table(rows, None)
    assert "TOWN" not in places.name_ends and ("pulau pinang", "GEORGE TOWN") in places.alias_districts
    assert [n for n, _ in places.towns_in("NO 1, BUTTERWORTH-TOWN", "Pulau Pinang")] == ["Butterworth"]
    d = districts({"Pulau Pinang|Timur Laut": (5.41, 100.33), "Pulau Pinang|Seberang Perai Utara": (5.45, 100.45)}, places)
    assert anchor("Pulau Pinang", "Timur Laut", "NO 1, GEORGETOWN", places, d)["label"] == "George Town"
    assert anchor("Pulau Pinang", "Seberang Perai Utara", "NO 1, GEORGETOWN", places, d)["kind"] == "district"


# ---------- review round: old postcode rows (5 km), unique far places, spread names, counts ----------

JERTEH_POSTAL = [("22000", "Jerteh", "Terengganu", 5.6111, 102.5729), ("22010", "Jerteh", "Terengganu", 5.6209, 102.5933),
                 ("22020", "Jerteh", "Terengganu", 5.6795, 102.5851)]
JERTIH, ANAK_IKAN, BESUT_CENTRE = (5.7336, 102.4897), (5.64396, 102.65181), (5.583333, 102.5)


def test_an_old_postcode_row_is_judged_by_the_town_point_only_when_the_towns_rows_lie_within_5_km():
    """Shop 17730 ('KAMPUNG ANAK IKAN JABI,22020 JERTEH...'): its old row came from OpenStreetMap's postcode
    search, 2.9 km from the village; Jerteh's own point (the list's 'Jertih') is 20.5 km away. Jerteh's postal
    rows lie 7.7 km apart: one rough point for a new shop's anchor (within 10 km), but not to judge an old
    row (within 5 km), so the row stays. Where the town's rows share one point, an old row far from the
    town is still wrong. (Since round 2 the village written in this address also rules out Jerteh's point,
    see test_a_smaller_place_vetoes_a_far_town: the shop's anchor is its district, and Jerteh's point judges
    no row of it; an address that names no village gets Jerteh's point as before.)"""
    old_pt = (5.66284, 102.63425)
    assert gp.km(*old_pt, *ANAK_IKAN) < 3 and gp.km(*JERTIH, *ANAK_IKAN) > 20 and gp.km(*old_pt, *JERTIH) > gp.ANCHOR_KM
    assert gp.ONE_POINT_ROW_KM < gp.km(5.6111, 102.5729, 5.6795, 102.5851) < gp.ONE_POINT_KM
    plist = [(1736405, "Jertih", "Terengganu", *JERTIH, "Jerteh"), (6862226, "Kampung Anak Ikan", "Terengganu", *ANAK_IKAN, "")]
    places = table(JERTEH_POSTAL, plist)
    d = districts({"Terengganu|Besut": BESUT_CENTRE}, places)
    shop = {"premise_code": "17730", "premise": "PKT", "state": "Terengganu", "district": "Besut",
            "address": "CAWANGAN KAMPUNG ANAK IKAN,LOT 7870 - 7875. KAMPUNG ANAK IKAN JABI,22020 JERTEHTERENGGANU"}
    plain = {**shop, "address": "LOT 7870 - 7875, 22020 JERTEH, TERENGGANU"}
    assert gp.anchor_for(shop, places, d)["kind"] == "district"
    a = gp.anchor_for(plain, places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Jerteh", "postcode town") and a["point"] == pytest.approx(JERTIH)
    row = cache_row(17730, old_pt, "postcode", "Besut", "Terengganu", "22020", tries=0, last="")
    assert gp.wrong_by(row, shop, places, d) is None and gp.wrong_by(row, plain, places, d) is None
    tight = table([(pc, "Jerteh", "Terengganu", 5.6795, 102.5851) for pc in ("22000", "22010", "22020")], plist)
    d = districts({"Terengganu|Besut": BESUT_CENTRE}, tight)
    assert gp.wrong_by(row, plain, tight, d) == "place"
    assert gp.wrong_by(row, shop, tight, d) is None    # Jerteh's point is ruled out for this shop: no judge
    assert gp.wrong_by(cache_row(1, (5.7300, 102.4900), "postcode", "Besut", "Terengganu", "22020"), plain, tight, d) is None


HULU_PERAK_SHOPS, KERUH, PH_POSTAL, GERIK = (5.428714, 101.128814), (5.71667, 101.0), (5.4731, 101.0105), (5.41667, 101.13333)
KERUH_ROW = (1735518, "Keruh", "Perak", *KERUH, "Keroh|Kroh|Pengkalan Hulu")


def test_the_only_place_of_a_name_may_lie_a_little_beyond_35_km_when_the_postcode_table_puts_the_town_inside():
    """Pengkalan Hulu: its only place in the list is 'Keruh' (alt name), 35.05 km from the shops OpenStreetMap
    found in Hulu Perak (at Gerik); its postal point is 14 km from those shops and 27 km from Keruh. The table
    puts the town well inside the district (within 15 km) and Keruh lies within 35 km of that point: Keruh is
    the town, for a town written in the address and for its postcode, but only when nothing in the address
    lies within 35 km. Old rows at the postal point are wrong; a row at Keruh is not."""
    gap = gp.km(*KERUH, *HULU_PERAK_SHOPS)
    assert gp.CHECK_KM < gap < gp.CHECK_KM + 0.1 and gp.km(*PH_POSTAL, *HULU_PERAK_SHOPS) < gp.GUESS_KM
    assert gp.km(*KERUH, *PH_POSTAL) < gp.CHECK_KM
    places = table([("33100", "Pengkalan Hulu", "Perak", *PH_POSTAL), ("33300", "Gerik", "Perak", 5.43, 101.12)],
                   [KERUH_ROW, (2, "Gerik", "Perak", *GERIK, "Grik")])
    d = districts({"Perak|Hulu Perak": HULU_PERAK_SHOPS}, places)
    a = anchor("Perak", "Hulu Perak", "NO 14,JALAN BETONG,PENGKALAN HULU", places, d)
    assert (a["kind"], a["label"], a["via"], a["vouched"]) == ("town", "Pengkalan Hulu", "place", True)
    assert a["point"] == pytest.approx(KERUH)
    a = anchor("Perak", "Hulu Perak", "PASAR AWAM PENGKALAN HULU,33100 PENGKALAN HULU,PERAK DARUL RIDZUAN.", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Pengkalan Hulu", "postcode town") and a["point"] == pytest.approx(KERUH)
    a = anchor("Perak", "Hulu Perak", "NO 14,JALAN BETONG,PENGKALAN HULU, GERIK", places, d)
    assert (a["label"], a["point"]) == ("Gerik", pytest.approx(GERIK))   # a town within 35 km comes first
    shop = {"premise_code": "20965", "premise": "KEDAI", "state": "Perak", "district": "Hulu Perak",
            "address": "NO 14,JALAN BETONG,PENGKALAN HULU"}
    assert gp.wrong_by(cache_row(20965, PH_POSTAL, "town", "Hulu Perak", "Perak", "Pengkalan Hulu"), shop, places, d) == "place"
    assert gp.wrong_by(cache_row(20965, KERUH, "town", "Hulu Perak", "Perak", "Pengkalan Hulu"), shop, places, d) is None


def test_a_far_place_is_not_the_town_when_another_place_has_the_name_or_the_postal_point_does_not_vouch():
    """Not Keruh when another place of the state is called that too; nor a far place called that by its
    name when another place has the name as an alt name (Sungai Siput: the far one is a homonym); nor when
    the town's postal point is itself far out (Bukit Pasir's, 33 km from Batu Pahat's shops: a town of
    another district), or when Keruh lies more than 35 km from the postal point."""
    def g(postal, plist):
        p = table([("33100", "Pengkalan Hulu", "Perak", *postal)], plist)
        return p.place_point("Perak", "PENGKALAN HULU", [HULU_PERAK_SHOPS])
    assert g(PH_POSTAL, [KERUH_ROW]) == pytest.approx(KERUH)
    other = (3, "Kampung Pengkalan", "Perak", 4.60, 101.10, "Pengkalan Hulu")
    assert g(PH_POSTAL, [KERUH_ROW, other]) is None
    homonym = (4, "Pengkalan Hulu", "Perak", 5.50, 100.80, "")
    assert gp.km(5.50, 100.80, *HULU_PERAK_SHOPS) > gp.CHECK_KM > gp.km(5.50, 100.80, *PH_POSTAL)
    assert g(PH_POSTAL, [KERUH_ROW, homonym]) is None
    far_out, south = (5.518646, 100.848768), (5.320795, 101.155915)
    assert gp.km(*far_out, *HULU_PERAK_SHOPS) > gp.GUESS_KM and gp.km(*KERUH, *far_out) < gp.CHECK_KM
    assert g(far_out, [KERUH_ROW]) is None
    assert gp.km(*south, *HULU_PERAK_SHOPS) < gp.GUESS_KM < gp.CHECK_KM < gp.km(*KERUH, *south)
    assert g(south, [KERUH_ROW]) is None


SPREAD_POSTAL = [("96000", "Sibu", "Sarawak", 2.2873, 111.8305), ("96007", "Sibu", "Sarawak", 2.10, 112.10)]
SIBU_G, SIBU_FOUND = (2.2950, 111.8250), [(2.31, 111.84), (2.32, 111.84)]


def spread_point(district, centre, found_district=None):
    places = table(SPREAD_POSTAL, [(1, "Sibu", "Sarawak", *SIBU_G, "")])
    if found_district:
        places.learn(found_shops("Sarawak", found_district, "NO 1, JALAN PULAU, SIBU", SIBU_FOUND))
    d = districts({f"Sarawak|{district}": centre}, places)
    return anchor("Sarawak", district, "NO 2, JALAN BARU, SIBU", places, d)


def test_a_spread_town_uses_near_rows_then_found_shops_then_its_place_point():
    """SIBU's postal rows lie 36 km apart (several places). Its rows near the district's shops come first;
    else the found shops that name it, when they count for the shop (its district, or their point within
    10 km of the district's shops); else its point in the place list. Found shops that do not count (another
    district, further away) rule out the place point too: the shop gets no town."""
    found_point = (2.315, 111.84)
    a = spread_point("Selangau", (2.40, 111.95))                 # no near rows, no found shops
    assert (a["kind"], a["via"], a["point"]) == ("town", "place", pytest.approx(SIBU_G))
    a = spread_point("Selangau", (2.25, 111.85), "Selangau")     # a row within 10 km: it comes first
    assert (a["via"], a["point"]) == ("rows", pytest.approx((2.2873, 111.8305)))
    a = spread_point("Selangau", (2.40, 111.95), "Selangau")     # found shops of the shop's district
    assert (a["via"], a["point"], a["vouched"]) == ("area", pytest.approx(found_point), False)
    a = spread_point("Kanowit", (2.37, 111.88), "Selangau")      # another district, within 10 km of its shops
    assert gp.km(*found_point, 2.37, 111.88) < gp.ANCHOR_KM < gp.km(2.2873, 111.8305, 2.37, 111.88)
    assert (a["via"], a["point"]) == ("area", pytest.approx(found_point))
    a = spread_point("Sibujaya", (2.24, 111.97), "Selangau")     # another district, 17 km away
    assert gp.km(*found_point, 2.24, 111.97) > gp.ANCHOR_KM and a["kind"] == "district"


KUDAT_CENTRE, KUDAT = (7.231645, 117.171156), (6.8873, 116.8236)


def test_tp3_keeps_a_postcode_point_near_the_town_and_needs_one_place_name():
    """A postcode of a town with one rough point keeps its own point when that lies within 10 km of the
    town's place point. A postcode with two place names gets the town's point only through the place
    written right after it; an old row is judged the same way."""
    near_g = (5.93, 116.08)
    assert gp.km(*near_g, *PUTATAN) < gp.ANCHOR_KM
    places = table([(f"8820{i}", "Putatan", "Sabah", *near_g) for i in range(5)], PLACES)
    d = districts({"Sabah|Putatan": PUTATAN_CENTRE}, places)
    a = anchor("Sabah", "Putatan", "LOT 1, 88200 PUTATAN", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("postcode", "88200", "rows") and a["point"] == pytest.approx(near_g)
    two = table(POSTAL + [("88200", "Kinarut", "Sabah", *PUTATAN_POSTAL)], PLACES)
    d = districts({"Sabah|Putatan": PUTATAN_CENTRE}, two)
    a = anchor("Sabah", "Putatan", "LOT 1, JALAN 2, 88200", two, d)
    assert (a["kind"], a["label"]) == ("postcode", "88200")       # Putatan or Kinarut? no town point
    a = anchor("Sabah", "Putatan", "LOT 1, 88200 PUTATAN", two, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Putatan", "postcode town")
    shop = {"premise_code": "3", "premise": "KEDAI", "state": "Sabah", "district": "Putatan", "address": "LOT 3, 88200 PUTATAN"}
    assert gp.wrong_by(cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200"), shop, two, d) == "place"
    shop["address"] = "LOT 3, JALAN 2, 88200"
    assert gp.wrong_by(cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200"), shop, two, d) is None


def test_tp3_uses_a_far_town_point_only_when_nothing_nearer_is_written():
    """Kudat's postcode (one rough point) and the district's own town 54 km out: the shop gets the town
    through its postcode, but in the first pass a town within 35 km written in the address wins."""
    postal = [("89050", "Kudat", "Sabah", 6.50, 116.50), ("89100", "Pitas", "Sabah", 6.70, 117.00)]
    pitas = (7.05, 117.10)
    assert gp.km(*pitas, *KUDAT_CENTRE) < gp.CHECK_KM < gp.km(*KUDAT, *KUDAT_CENTRE)
    places = table(postal, [(1, "Kudat", "Sabah", *KUDAT, ""), (2, "Pitas", "Sabah", *pitas, "")])
    d = districts({"Sabah|Kudat": KUDAT_CENTRE}, places)
    a = anchor("Sabah", "Kudat", "LOT 1, 89050 KUDAT", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Kudat", "postcode town") and a["point"] == pytest.approx(KUDAT)
    a = anchor("Sabah", "Kudat", "LOT 1, PITAS, 89050 KUDAT", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Pitas", "place") and a["point"] == pytest.approx(pitas)


def test_tp3_judges_a_neighbour_states_postcode_by_its_own_states_rows():
    """A W.P. Putrajaya shop in Cyberjaya writes Selangor's 63000 CYBERJAYA (strong evidence): the town's
    rows are Selangor's, so its rough point gives way to the town's own point."""
    rough, cyberjaya = (2.80, 101.70), (2.9228, 101.6505)
    assert gp.km(*rough, *cyberjaya) > gp.ANCHOR_KM
    places = table([("63000", "Cyberjaya", "Selangor", *rough)], [(1, "Cyberjaya", "Selangor", *cyberjaya, "")])
    d = districts({"W.P. Putrajaya|Cyberjaya": (2.914883, 101.669659)}, places)
    a = anchor("W.P. Putrajaya", "Cyberjaya", "JALAN TEKNOKRAT 6, 63000 CYBERJAYA", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Cyberjaya", "postcode town") and a["point"] == pytest.approx(cyberjaya)


def test_place_point_limits():
    """The limits of place_point, at their edges: a place 34 km out counts, 36 km does not; two places 5 km
    apart are not ambiguous (the nearer counts); the district's own town counts up to 80 km (75 here); the
    answer for the own town is not the answer for a weak name of the same spelling."""
    centre = (3.0, 101.5)
    near34, far36, a6, a11 = (3.305769, 101.5), (3.323756, 101.5), (3.053959, 101.5), (3.098925, 101.5)
    assert gp.km(*near34, *centre) < gp.CHECK_KM < gp.km(*far36, *centre)
    places = table([("43000", "Kajang", "Selangor", 3.0, 101.5)],
                   [(1, "Kampung Satu", "Selangor", *near34, ""), (2, "Kampung Dua", "Selangor", *far36, ""),
                    (3, "Kampung Tiga", "Selangor", *a6, ""), (4, "Kampung Tiga", "Selangor", *a11, "")])
    assert places.place_point("Selangor", "KAMPUNG SATU", [centre]) == pytest.approx(near34)
    assert places.place_point("Selangor", "KAMPUNG DUA", [centre]) is None
    assert gp.km(*a6, *a11) == pytest.approx(5, abs=0.01)
    assert places.place_point("Selangor", "KAMPUNG TIGA", [centre]) == pytest.approx(a6) and not places.place_ambiguous
    own75 = (6.692052, 116.763217)
    assert 70 < gp.km(*own75, *KUDAT_CENTRE) < gp.STRONG_KM
    kudat = table([("89050", "Kudat", "Sabah", 6.50, 116.50)], [(1, "Kudat", "Sabah", *own75, "")])
    assert kudat.place_point("Sabah", "KUDAT", [KUDAT_CENTRE], True, False) is None
    assert kudat.place_point("Sabah", "KUDAT", [KUDAT_CENTRE], True, True) == pytest.approx(own75)
    assert kudat.place_point("Sabah", "KUDAT", [KUDAT_CENTRE]) is None


def test_a_row_already_at_todays_anchor_is_not_corrected_again(tmp_path):
    """Found shops put PUTATAN 11.5 km from its place point, but a postcode's town point is the place point
    (see Places.postcode_town). The first run moves shop 3's rough postcode row there; later runs judge the
    town row by the found shops but have nothing better: nothing is rewritten or counted again."""
    found = (5.99, 116.10)
    assert gp.km(*found, *PUTATAN) > gp.ANCHOR_KM
    data = write_build(tmp_path / "data", {("Sabah", "Putatan"): [
        (1, "A", "LOT 1, KG PETAGAS, PUTATAN"), (2, "B", "LOT 2, KG PETAGAS, PUTATAN"), (3, "C", "LOT 3, 88200 PUTATAN")]})
    write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [
        cache_row(1, found, "name", "Putatan", "Sabah", "A"), cache_row(2, (found[0] + 0.005, found[1]), "name", "Putatan", "Sabah", "B"),
        cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200")])
    write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
              [{"key": "Sabah|Putatan", "lat": str(PUTATAN_CENTRE[0]), "lon": str(PUTATAN_CENTRE[1])}])
    write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(POSTAL))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PLACES))
    assert data.is_dir()
    rows, st, first = offline_run(tmp_path, pl, "one")
    assert (rows["3"]["precision"], rows["3"]["query"], st["corrected_offline"], st["place_postcodes"]) == ("town", "Putatan", 1, 1)
    (tmp_path / "premise_geo.csv").write_bytes(first)
    rows, st, second = offline_run(tmp_path, pl, "two")
    assert second == first and (st["corrected_offline"], st["place_postcodes"], st["place_corrected"]) == (0, 0, 0)


def test_a_postcode_row_becomes_a_town_row_only_through_its_towns_rough_point(tmp_path):
    """A wrong postcode row (a lot number read as a far postcode: the district check) whose shop's anchor
    today is a town written in the address is not made a town row before any search (never less
    precise); the retry replaces it."""
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    write_build(tmp_path / "data", {("Perak", "Kerian"): [(7, "KEDAI 7", "NO 7, BAGAN SERAI")]})
    rows = list(read_rows(cache).values()) + [cache_row(7, (3.21, 101.64), "postcode", "Kerian", "Perak", "52100")]
    write_csv(cache, gp.FIELDS, rows)
    got, st, _ = offline_run(tmp_path, pl)
    assert (got["7"]["precision"], got["7"]["query"], got["7"]["lat"]) == ("postcode", "52100", "3.210000")


def test_the_summary_counts_rows_as_the_run_leaves_them(tmp_path):
    """A shop given a town's point before any search and then found by the retry search is not counted in
    place_towns, nor a new shop found by its search in place_postcodes; a row corrected for another reason
    (found shops) is not counted in place_corrected; the names the list could not place (two near places;
    all too far) are counted."""
    data, cache, dcache, pc, pl = putatan_files(tmp_path)
    write_build(tmp_path / "data", {("Perak", "Larut"): [(8, "KEDAI 8", "NO 8, SUNGAI BUAH"), (9, "KEDAI 9", "NO 9, KAMPUNG JAUH")],
                                    ("Perak", "Kerian"): [(10, "A", "NO 10, TAIPING"), (11, "B", "NO 11, TAIPING"),
                                                         (1, "KEDAI 1", "NO 1, JALAN BESAR, BAGAN SERAI"),
                                                         (2, "KEDAI 2", "NO 2, BAGAN SERAI"), (6, "KEDAI 6", "NO 6, TAIPING"),
                                                         (12, "KEDAI 12", "NO 12, BAGAN SERAI")]})
    taiping = (4.95, 100.85)
    assert gp.km(*taiping, 4.85, 100.74) > gp.ANCHOR_KM
    write_csv(cache, gp.FIELDS, list(read_rows(cache).values()) + [
        cache_row(10, taiping, "name", "Kerian", "Perak", "A"), cache_row(11, (taiping[0] + 0.01, taiping[1]), "name", "Kerian", "Perak", "B"),
        cache_row(12, KERIAN_CENTRE, "district", "Kerian", "Perak", "Kerian, Perak", tries=0, last="")])
    larut = (4.85, 100.74)
    write_csv(dcache, ["key", "lat", "lon"], list(read_rows(dcache).values()) + [{"key": "Perak|Larut", "lat": str(larut[0]), "lon": str(larut[1])}])
    write_csv(pc, fpc.FIELDS, postal_rows(POSTAL + [("34990", "Sungai Buah", "Perak", 4.86, 100.75),
                                                   ("34991", "Kampung Jauh", "Perak", 4.87, 100.76)]))
    write_csv(pl, fpl.FIELDS, place_rows(PLACES + [(11, "Sungai Buah", "Perak", 4.88, 100.74, ""),
                                                   (12, "Sungai Buah", "Perak", 4.98, 100.74, ""),
                                                   (13, "Kampung Jauh", "Perak", 5.40, 100.74, "")]))

    def fetch(params):
        q = params.get("q", "")
        hits = {"KEDAI 5": [{"lat": "5.8970", "lon": "116.0560"}], "KEDAI 12": [{"lat": "5.0120", "lon": "100.5420"}]}
        return next((v for k, v in hits.items() if q.startswith(k + ",")), [])
    work = tmp_path / "timed"
    work.mkdir()
    for name in ("premise_geo.csv", "district_geo.csv"):
        shutil.copy(tmp_path / name, work / name)
    st = gp.run(tmp_path / "data", work / "premise_geo.csv", work / "district_geo.csv", gp.Geocoder(fetch=fetch, delay=0),
                postcodes_path=pc, today=TODAY, places_path=pl)
    rows = read_rows(work / "premise_geo.csv")
    assert (rows["5"]["precision"], rows["5"]["query"]) == ("name", "KEDAI 5, Putatan")
    assert (rows["12"]["precision"], rows["12"]["query"], rows["12"]["tries"]) == ("name", "KEDAI 12, Bagan Serai", "1")
    assert (rows["6"]["precision"], rows["6"]["query"]) == ("town", "Taiping")
    assert (float(rows["6"]["lat"]), float(rows["6"]["lon"])) == pytest.approx((taiping[0] + 0.005, taiping[1]), abs=1e-5)
    assert (st["place_towns"], st["upgraded_offline"]) == (2, 2)          # shops 1 and 2; not 12
    assert (st["place_postcodes"], st["place_corrected"], st["corrected_offline"]) == (1, 2, 3)
    assert (st["place_ambiguous"], st["place_dropped"]) == (1, 1)


def test_the_place_list_leaves_out_rows_without_a_name_or_a_state(tmp_path):
    rows = place_rows(PLACES) + place_rows([(7, "", "Perak", 5.0, 100.5, ""), (8, "Kampung X", "", 5.0, 100.5, "")])
    rows[-2]["asciiname"] = ""
    path = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, rows)
    assert [r["geonameid"] for r in gp.read_places(path)] == [str(p[0]) for p in PLACES]


def test_the_command_line_uses_the_place_list_by_default(monkeypatch):
    """main() passes etl/geo/places_my.csv unless told --places ''."""
    seen = []
    monkeypatch.setattr(gp, "run", lambda *a, **k: seen.append(k.get("places_path")) or {})
    assert gp.main(["--max-minutes", "0"]) == 0
    assert gp.main(["--max-minutes", "0", "--places", ""]) == 0
    assert seen == [gp.PLACES, None]


# ---------- review round 2: references that move, the edges of each limit, counts ----------

def north(p, d):
    """The point d km north of p (south for d < 0), on its meridian: km() gives back d."""
    return (p[0] + d / (6371.0 * math.pi / 180), p[1])


PH_MOVED = (HULU_PERAK_SHOPS[0] - 0.006, HULU_PERAK_SHOPS[1] + 0.008)   # 1.1 km from HULU_PERAK_SHOPS
PH_POSTAL_ROWS = [("33100", "Pengkalan Hulu", "Perak", *PH_POSTAL), ("33300", "Gerik", "Perak", 5.43, 101.12)]
PH_PLACES = [KERUH_ROW, (2, "Gerik", "Perak", *GERIK, "Grik")]


def test_a_row_at_its_towns_only_place_stays_when_the_references_move():
    """Pengkalan Hulu's place, Keruh, passed place_point's limits by 0.05 km and 1 km. Move where the district's
    shops are by 1.1 km: a new shop's anchor is the postcode table's rough point again (the limits stay), but an
    old row at Keruh, the only place called that, is not wrong: the table's point for the town (15 km out) still
    lies within 35 km. It is wrong when that point lies further, or when another place has the name."""
    assert gp.km(*PH_MOVED, *HULU_PERAK_SHOPS) < 1.2
    assert gp.km(*PH_POSTAL, *PH_MOVED) > gp.GUESS_KM and gp.km(*KERUH, *PH_MOVED) > gp.CHECK_KM
    places = table(PH_POSTAL_ROWS, PH_PLACES)
    d = districts({"Perak|Hulu Perak": PH_MOVED}, places)
    shop = {"premise_code": "20965", "premise": "KEDAI", "state": "Perak", "district": "Hulu Perak",
            "address": "NO 14,JALAN BETONG,PENGKALAN HULU"}
    a = gp.anchor_for(shop, places, d)
    assert (a["kind"], a["via"]) == ("town", "rows") and a["point"] == pytest.approx(PH_POSTAL)
    at_keruh = cache_row(20965, KERUH, "town", "Hulu Perak", "Perak", "Pengkalan Hulu")
    assert gp.wrong_by(at_keruh, shop, places, d) is None
    far = north(PH_POSTAL, -36)
    assert gp.km(*PH_POSTAL, *far) > gp.CHECK_KM
    assert gp.wrong_by(at_keruh, shop, places, districts({"Perak|Hulu Perak": far}, places)) == "district"
    two = table(PH_POSTAL_ROWS, PH_PLACES + [(3, "Kampung Pengkalan", "Perak", 4.60, 101.10, "Pengkalan Hulu")])
    assert gp.wrong_by(at_keruh, shop, two, districts({"Perak|Hulu Perak": PH_MOVED}, two)) == "district"


def test_rows_at_a_towns_only_place_stay_in_runs_when_the_references_move(tmp_path):
    """The same in a run with no time (the pass before any search) and in one whose searches find nothing:
    shop 20965's town row, and shop 1211's town row made from its postcode (33100: one rough point for the
    town), stay at Keruh, although today's anchors are the table's rough points, 27 km off."""
    write_build(tmp_path / "data", {("Perak", "Hulu Perak"): [
        (1211, "KEDAI A", "PASAR AWAM PENGKALAN HULU,33100 PENGKALAN HULU,PERAK DARUL RIDZUAN."),
        (20965, "KEDAI B", "NO 14,JALAN BETONG,PENGKALAN HULU")]})
    pc = write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(PH_POSTAL_ROWS))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PH_PLACES))
    for minutes in (0, None):
        work = tmp_path / f"run-{minutes}"
        work.mkdir()
        write_csv(work / "premise_geo.csv", gp.FIELDS, [
            cache_row(1211, KERUH, "town", "Hulu Perak", "Perak", "Pengkalan Hulu", tries=0, last=""),
            cache_row(20965, KERUH, "town", "Hulu Perak", "Perak", "Pengkalan Hulu", tries=0, last="")])
        write_csv(work / "district_geo.csv", ["key", "lat", "lon"],
                  [{"key": "Perak|Hulu Perak", "lat": str(PH_MOVED[0]), "lon": str(PH_MOVED[1])}])
        st = gp.run(tmp_path / "data", work / "premise_geo.csv", work / "district_geo.csv",
                    gp.Geocoder(fetch=lambda params: [], delay=0, max_minutes=minutes), postcodes_path=pc, today=TODAY,
                    places_path=pl)
        rows = read_rows(work / "premise_geo.csv")
        for code in ("1211", "20965"):
            assert (rows[code]["precision"], rows[code]["query"]) == ("town", "Pengkalan Hulu"), (minutes, code)
            assert (float(rows[code]["lat"]), float(rows[code]["lon"])) == pytest.approx(KERUH, abs=1e-5), (minutes, code)
        assert (st["corrected_offline"], st["retried"]) == (0, 0 if minutes == 0 else 2)


def test_the_limits_for_a_far_only_place_at_their_edges():
    """A town that is the only place of its name and lies more than 35 km out counts when its postcode-table
    point lies within 15 km of where the district's shops are (14.9 km: yes; 15.1 km: no) and the place within
    35 km of that point (34 km: yes; 36 km: no); not when the table's rows of the name spread (several places),
    even when their average lies 14 km out."""
    r = HULU_PERAK_SHOPS

    def g(postal_points, place):
        p = table([(f"3390{i}", "Sungai Jauh", "Perak", *pt) for i, pt in enumerate(postal_points)],
                  [(1, "Sungai Jauh", "Perak", *place, "")])
        return p.place_point("Perak", "SUNGAI JAUH", [r])
    for postal_km, place_km, ok in ((14.0, 48.0, True), (14.0, 50.0, False), (14.9, 41.9, True), (15.1, 42.1, False)):
        assert gp.km(*north(r, place_km), *r) > gp.CHECK_KM
        assert (g([north(r, postal_km)], north(r, place_km)) is not None) == ok, (postal_km, place_km)
    spread = [north(r, 3.0), north(r, 25.0)]
    assert gp._spread_over(spread, gp.TOWN_SPREAD_KM) and g(spread, north(r, 41.0)) is None


def jerteh(gap_km):
    """Jerteh's postal rows: 22020 at its 2026 point and 22000 gap_km south of it; the place list's Jertih."""
    rows = [("22020", "Jerteh", "Terengganu", 5.6795, 102.5851),
            ("22000", "Jerteh", "Terengganu", *north((5.6795, 102.5851), -gap_km))]
    return table(rows, [(1736405, "Jertih", "Terengganu", *JERTIH, "Jerteh")])


def test_one_rough_point_at_its_edges_for_new_anchors_and_old_rows():
    """A new shop's 22020 gives the town's own point when the town's rows lie within 10 km (9.9 km: yes;
    10.1 km: no, its postcode point stays); an old 22020 row far from the town is judged by it when they lie
    within 5 km (4.9 km: wrong; 5.1 km: not judged so)."""
    shop = {"premise_code": "17730", "premise": "PKT", "state": "Terengganu", "district": "Besut",
            "address": "KAMPUNG ANAK IKAN JABI,22020 JERTEH, TERENGGANU"}
    old = cache_row(17730, (5.66284, 102.63425), "postcode", "Besut", "Terengganu", "22020")
    for gap_km, kind in ((9.9, "town"), (10.1, "postcode")):
        places = jerteh(gap_km)
        assert gp.anchor_for(shop, places, districts({"Terengganu|Besut": BESUT_CENTRE}, places))["kind"] == kind, gap_km
    for gap_km, why in ((4.9, "place"), (5.1, None)):
        places = jerteh(gap_km)
        assert gp.wrong_by(old, shop, places, districts({"Terengganu|Besut": BESUT_CENTRE}, places)) == why, gap_km


def test_a_wrong_row_at_todays_point_under_another_town_or_district_is_rewritten(tmp_path):
    """The pass before any search leaves a wrong row alone only when it is today's anchor in every way. At
    Putatan's point but under another town's name (shop 3: 'Kinarut', which the list puts 12 km away), or with
    an older district (shop 4), it is rewritten and counted."""
    found, kinarut = (5.99, 116.10), (5.80, 116.00)
    assert gp.km(*kinarut, *PUTATAN) > gp.ANCHOR_KM
    write_build(tmp_path / "data", {("Sabah", "Putatan"): [
        (1, "A", "LOT 1, KG PETAGAS, PUTATAN"), (2, "B", "LOT 2, KG PETAGAS, PUTATAN"),
        (3, "C", "LOT 3, 88200 PUTATAN"), (4, "D", "LOT 4, 88200 PUTATAN")]})
    write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [
        cache_row(1, found, "name", "Putatan", "Sabah", "A"), cache_row(2, (found[0] + 0.005, found[1]), "name", "Putatan", "Sabah", "B"),
        cache_row(3, PUTATAN, "town", "Putatan", "Sabah", "Kinarut"), cache_row(4, PUTATAN, "town", "Penampang", "Sabah", "Putatan")])
    write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
              [{"key": "Sabah|Putatan", "lat": str(PUTATAN_CENTRE[0]), "lon": str(PUTATAN_CENTRE[1])}])
    write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(POSTAL + [("88100", "Kinarut", "Sabah", 5.81, 116.01)]))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PLACES + [(2, "Kinarut", "Sabah", *kinarut, "")]))
    rows, st, _ = offline_run(tmp_path, pl)
    assert [(rows[c]["query"], rows[c]["district"]) for c in ("3", "4")] == [("Putatan", "Putatan")] * 2
    for c in ("3", "4"):
        assert (float(rows[c]["lat"]), float(rows[c]["lon"])) == pytest.approx(PUTATAN, abs=1e-5)
    assert (st["corrected_offline"], st["place_corrected"], st["place_postcodes"]) == (2, 1, 2)


def test_ambiguous_and_dropped_names_are_counted_apart(tmp_path):
    """One name with two near places (ambiguous) and two names whose only places lie too far (dropped)."""
    larut = (4.85, 100.74)
    write_build(tmp_path / "data", {("Perak", "Larut"): [(8, "KEDAI 8", "NO 8, SUNGAI BUAH"), (9, "KEDAI 9", "NO 9, KAMPUNG JAUH"),
                                                         (13, "KEDAI 13", "NO 13, KAMPUNG LAIN")]})
    write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [])
    write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"], [{"key": "Perak|Larut", "lat": str(larut[0]), "lon": str(larut[1])}])
    write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows([
        ("34990", "Sungai Buah", "Perak", 4.86, 100.75), ("34991", "Kampung Jauh", "Perak", 4.87, 100.76),
        ("34992", "Kampung Lain", "Perak", 4.88, 100.77)]))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows([
        (11, "Sungai Buah", "Perak", 4.88, 100.74, ""), (12, "Sungai Buah", "Perak", 4.98, 100.74, ""),
        (13, "Kampung Jauh", "Perak", 5.40, 100.74, ""), (14, "Kampung Lain", "Perak", 4.20, 100.74, "")]))
    rows, st, _ = offline_run(tmp_path, pl)
    assert (st["place_ambiguous"], st["place_dropped"]) == (1, 2)
    assert [rows[c]["precision"] for c in ("8", "9", "13")] == ["town"] * 3     # at the postcode table's points


def test_a_corrected_row_that_a_retry_leaves_as_it_was_stays_counted(tmp_path):
    """Shop 3's rough 88200 row is corrected to Putatan's own point before any search. Its retry finds nothing
    and writes the same row again (found shops place PUTATAN elsewhere, but nothing is better): it is still
    counted in place_corrected, as the summary counts rows as the run leaves them."""
    found = (5.99, 116.10)
    write_build(tmp_path / "data", {("Sabah", "Putatan"): [
        (1, "A", "LOT 1, KG PETAGAS, PUTATAN"), (2, "B", "LOT 2, KG PETAGAS, PUTATAN"), (3, "C", "LOT 3, 88200 PUTATAN")]})
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [
        cache_row(1, found, "name", "Putatan", "Sabah", "A"), cache_row(2, (found[0] + 0.005, found[1]), "name", "Putatan", "Sabah", "B"),
        cache_row(3, PUTATAN_POSTAL, "postcode", "Putatan", "Sabah", "88200", tries=1, last="2026-10-01")])
    dc = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"],
                   [{"key": "Sabah|Putatan", "lat": str(PUTATAN_CENTRE[0]), "lon": str(PUTATAN_CENTRE[1])}])
    pc = write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(POSTAL))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PLACES))
    st = gp.run(tmp_path / "data", cache, dc, gp.Geocoder(fetch=lambda params: [], delay=0), postcodes_path=pc,
                today=TODAY, places_path=pl)
    r3 = read_rows(cache)["3"]
    assert (r3["precision"], r3["query"], r3["tries"], r3["last_tried"]) == ("town", "Putatan", "2", TODAY)
    assert (float(r3["lat"]), float(r3["lon"])) == pytest.approx(PUTATAN, abs=1e-5)
    assert (st["retried"], st["corrected_offline"], st["place_corrected"], st["place_postcodes"]) == (1, 1, 1, 1)


def test_one_town_name_in_two_districts_gets_each_districts_place():
    """place_point keeps its answers per name and per references: two districts with a place of the same name
    nearby each get their own."""
    places = table([("43000", "Kampung Baru", "Selangor", 3.0, 101.5)],
                   [(1, "Kampung Baru", "Selangor", 3.0, 101.5, ""), (2, "Kampung Baru", "Selangor", 3.6, 101.5, "")])
    assert places.place_point("Selangor", "KAMPUNG BARU", [(3.05, 101.5)]) == pytest.approx((3.0, 101.5))
    assert places.place_point("Selangor", "KAMPUNG BARU", [(3.55, 101.5)]) == pytest.approx((3.6, 101.5))


def test_a_towns_name_does_not_depend_on_the_order_of_the_postcode_tables_rows():
    """81750 has a 'Masai' row and a 'masai' row: the town is written as the table writes it most often, then
    in sort order, whatever the order of the rows."""
    rows = [("81750", "masai", "Johor", 1.49, 103.88), ("81750", "Masai", "Johor", 1.49, 103.88)]
    for order in (rows, rows[::-1]):
        assert [n for n, _ in table(order, []).towns_in("NO 1, MASAI", "Johor")] == ["Masai"]
    assert [n for n, _ in table(rows + rows[:1], []).towns_in("NO 1, MASAI", "Johor")] == ["masai"]


def test_a_row_at_its_towns_only_place_needs_the_place_near_the_towns_table_point():
    """The tolerance at its edges: place_point's own limits, each 5 km wider. The row within 10 km of the place,
    and the place within 40 km of where the district's shops are, or within 40 km of the town's postcode-table
    point while that point lies within 20 km of them. Else the row is judged by its own distance, as before.
    (The table point lies more than 15 km out or more than 35 km from the place, so the place list itself has no
    point for the town, and more than 20 km from the place, so it is no far town's point either.)"""
    r = HULU_PERAK_SHOPS

    def why(postal_km, place_km, row_km):
        p = table([("33900", "Sungai Jauh", "Perak", *north(r, postal_km))], [(1, "Sungai Jauh", "Perak", *north(r, place_km), "")])
        shop = {"premise_code": "1", "premise": "KEDAI", "state": "Perak", "district": "Hulu Perak", "address": "NO 1, SUNGAI JAUH"}
        d = districts({"Perak|Hulu Perak": r}, p)
        assert p.place_point("Perak", "SUNGAI JAUH", [r]) is None
        return gp.wrong_by(cache_row(1, north(r, row_km), "town", "Hulu Perak", "Perak", "Sungai Jauh"), shop, p, d)
    assert why(-16.0, 39.9, 39.9) is None and why(-16.0, 40.1, 40.1) == "district"  # place 39.9 / 40.1 km out
    assert why(18.0, 57.9, 57.9) is None and why(18.0, 58.1, 58.1) == "district"    # place 39.9 / 40.1 km from the table point
    assert why(19.9, 50.0, 50.0) is None and why(20.1, 50.0, 50.0) == "district"    # table point 19.9 / 20.1 km out
    assert why(18.0, 50.0, 59.9) is None and why(18.0, 50.0, 60.1) == "district"    # row 9.9 / 10.1 km from the place
    # The old band (place within 35 km of a table point 35 km out) kept rows up to 80 km out; now at most 70 km.
    assert why(34.0, 60.0, 60.0) == "district"


def test_a_retry_turns_a_town_row_into_its_postcodes_row_unless_that_has_only_its_towns_rough_point(tmp_path):
    """A retry that finds nothing gives a town row its postcode's point when that is the postcode's own (Kota
    Kinabalu's postcodes each have one: shop 1) or found shops vouch for it (shop 2); a town row of a postcode
    whose town has one rough point stays (see the Pengkalan Hulu test above)."""
    vouched = (5.92, 116.08)
    assert gp.km(*vouched, *PUTATAN) < gp.ANCHOR_KM
    write_build(tmp_path / "data", {("Sabah", "Kota Kinabalu"): [(1, "KEDAI 1", "NO 1, 88300 KOTA KINABALU")],
                                    ("Sabah", "Putatan"): [(2, "KEDAI 2", "LOT 2, 88200 PUTATAN"), (10, "A", "LOT 10, 88200 PUTATAN"),
                                                           (11, "B", "LOT 11, 88200 PUTATAN")]})
    cache = write_csv(tmp_path / "premise_geo.csv", gp.FIELDS, [
        cache_row(1, KOTA_KINABALU, "town", "Kota Kinabalu", "Sabah", "Kota Kinabalu", last="2026-10-01"),
        cache_row(2, PUTATAN, "town", "Putatan", "Sabah", "Putatan", last="2026-10-01"),
        cache_row(10, vouched, "name", "Putatan", "Sabah", "A"), cache_row(11, (vouched[0] + 0.004, vouched[1]), "name", "Putatan", "Sabah", "B")])
    dc = write_csv(tmp_path / "district_geo.csv", ["key", "lat", "lon"], [
        {"key": "Sabah|Kota Kinabalu", "lat": str(KK_CENTRE[0]), "lon": str(KK_CENTRE[1])},
        {"key": "Sabah|Putatan", "lat": str(PUTATAN_CENTRE[0]), "lon": str(PUTATAN_CENTRE[1])}])
    pc = write_csv(tmp_path / "postcodes_my.csv", fpc.FIELDS, postal_rows(POSTAL))
    pl = write_csv(tmp_path / "places_my.csv", fpl.FIELDS, place_rows(PLACES))
    gp.run(tmp_path / "data", cache, dc, gp.Geocoder(fetch=lambda params: [], delay=0), postcodes_path=pc, today=TODAY,
           places_path=pl)
    rows = read_rows(cache)
    assert (rows["1"]["precision"], rows["1"]["query"], rows["1"]["lat"]) == ("postcode", "88300", "5.930000")
    assert (rows["2"]["precision"], rows["2"]["query"]) == ("postcode", "88200")
    assert (float(rows["2"]["lat"]), float(rows["2"]["lon"])) == pytest.approx((vouched[0] + 0.002, vouched[1]), abs=1e-5)


def test_a_place_list_without_its_columns_says_which(tmp_path, capsys):
    path = write_csv(tmp_path / "places_my.csv", ["id", "town", "x", "y"], [{"id": "1", "town": "Bagan Serai", "x": "5.0108", "y": "100.541"}])
    assert gp.read_places(path) is None
    assert "has no name, asciiname, lat, lon, state column" in capsys.readouterr().err


# ---------- round 2: retries, far homonyms, wrong rows, written towns, smaller places, edges ----------

def retry_run(tmp_path, sub, shops, rows, centres, postal, place_list, minutes=None, fetch=None):
    """One run on its own files in tmp_path/sub (shops: {(state, district): [(code, name, address)]}); a run whose
    searches find nothing (minutes None: no time limit; 0: no time for searches). -> (rows, summary)."""
    work = tmp_path / sub
    write_build(work / "data", shops)
    cache = write_csv(work / "premise_geo.csv", gp.FIELDS, rows)
    dc = write_csv(work / "district_geo.csv", ["key", "lat", "lon"],
                   [{"key": k, "lat": str(p[0]), "lon": str(p[1])} for k, p in centres.items()])
    pc = write_csv(work / "postcodes_my.csv", fpc.FIELDS, postal_rows(postal))
    pl = write_csv(work / "places_my.csv", fpl.FIELDS, place_rows(place_list)) if place_list is not None else None
    st = gp.run(work / "data", cache, dc, gp.Geocoder(fetch=fetch or (lambda params: []), delay=0, max_minutes=minutes),
                postcodes_path=pc, today=TODAY, places_path=pl)
    return read_rows(cache), st


ROUGH_NEAR = (5.93, 116.08)   # a rough point Putatan's postcodes share, within 10 km of the town: its postcodes keep it


def test_without_the_place_list_a_retry_turns_a_town_row_into_its_postcodes_row(tmp_path):
    """The retry rule for a postcode's one-point town needs the place list: without it a run is as before the list
    (the town row becomes the postcode's row); with it the town row stays."""
    assert gp.km(*ROUGH_NEAR, *PUTATAN) < gp.ANCHOR_KM
    postal = [(f"8820{i}", "Putatan", "Sabah", *ROUGH_NEAR) for i in range(5)]
    shops = {("Sabah", "Putatan"): [(3, "KEDAI C", "LOT 3, 88200 PUTATAN")]}
    old = [cache_row(3, PUTATAN, "town", "Putatan", "Sabah", "Putatan", last="2026-10-01")]
    for sub, place_list, want in (("list", PLACES, ("town", "Putatan")), ("none", None, ("postcode", "88200"))):
        rows, st = retry_run(tmp_path, sub, shops, old, {"Sabah|Putatan": PUTATAN_CENTRE}, postal, place_list)
        assert (rows["3"]["precision"], rows["3"]["query"], st["retried"]) == (*want, 1), sub


def test_a_retry_keeps_a_town_row_of_a_two_name_postcode_only_for_the_place_written_after_it(tmp_path):
    """88200 has two place names (Putatan and Kinarut, each with one rough point). A town row for Putatan stays when the
    address writes PUTATAN right after the postcode (the town postcode_pick would take); with no place written there,
    which town? none: the retry gives the postcode's own point, as it does for a postcode with no town."""
    postal = [(f"8820{i}", "Putatan", "Sabah", *ROUGH_NEAR) for i in range(5)] + [("88200", "Kinarut", "Sabah", *ROUGH_NEAR)]
    places = table(postal, PLACES)
    assert [" ".join(n) for n in places.postcode_places["88200"]] == ["PUTATAN", "KINARUT"]
    assert places.one_point_town("88200", "Sabah") is None
    assert places.one_point_town("88200", "Sabah", "PUTATAN") == "PUTATAN"
    for sub, address, want in (("written", "LOT 3, 88200 PUTATAN", ("town", "Putatan")),
                               ("unwritten", "LOT 3, JALAN 2, 88200", ("postcode", "88200"))):
        rows, _ = retry_run(tmp_path, sub, {("Sabah", "Putatan"): [(3, "KEDAI C", address)]},
                            [cache_row(3, PUTATAN, "town", "Putatan", "Sabah", "Putatan", last="2026-10-01")],
                            {"Sabah|Putatan": PUTATAN_CENTRE}, postal, PLACES)
        assert (rows["3"]["precision"], rows["3"]["query"]) == want, sub


# T2-1: Batu Pahat's shops (refs), Muar's Bukit Pasir (the list's only one, 43.5 km out), the 84300 table point.
BATU_PAHAT_SHOPS = (1.85, 102.93)
BUKIT_PASIR_FAR = (2.10, 102.6388)
PARIT_RAJA = (1.87, 103.11)


def bukit_pasir(postal_from_place_km):
    """The table's Bukit Pasir point lies postal_from_place_km east of the list's (far) Bukit Pasir; Parit Raja is
    a near town of Batu Pahat. -> (places, districts)"""
    postal = (BUKIT_PASIR_FAR[0], BUKIT_PASIR_FAR[1] + postal_from_place_km / (6371.0 * math.pi / 180 * math.cos(math.radians(2.1))))
    places = table([("84300", "Bukit Pasir", "Johor", *postal), ("86400", "Parit Raja", "Johor", 1.87, 103.10)],
                   [(1, "Bukit Pasir", "Johor", *BUKIT_PASIR_FAR, ""), (2, "Parit Raja", "Johor", *PARIT_RAJA, "")])
    return places, districts({"Johor|Batu Pahat": BATU_PAHAT_SHOPS}, places), postal


def test_a_far_towns_table_point_is_not_used_either():
    """Shop 21036 ('NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR,' in Batu Pahat): the list's only Bukit Pasir is 43.5 km
    out (dropped), and the table's point, 33 km out, lies 13.7 km from it: both are that far town. The shop does not
    get the table's point: the next town in its address, else its district. An old row at that point is wrong; a
    table point more than 20 km from every dropped place (Sungai Siput's) is still used."""
    places, d, postal = bukit_pasir(13.7)
    assert gp.km(*BUKIT_PASIR_FAR, *BATU_PAHAT_SHOPS) > 40 and gp.CHECK_KM - 3 < gp.km(*postal, *BATU_PAHAT_SHOPS) < gp.CHECK_KM
    assert places.place_point("Johor", "BUKIT PASIR", [BATU_PAHAT_SHOPS]) is None
    assert places.far_town("Johor", "BUKIT PASIR", [BATU_PAHAT_SHOPS], postal)
    assert anchor("Johor", "Batu Pahat", "NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR,", places, d)["kind"] == "district"
    a = anchor("Johor", "Batu Pahat", "TAMAN BUKIT PASIR, PARIT RAJA", places, d)
    assert (a["label"], a["via"]) == ("Parit Raja", "place")
    shop = {"premise_code": "21036", "premise": "KEDAI", "state": "Johor", "district": "Batu Pahat",
            "address": "NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR,"}
    assert gp.wrong_by(cache_row(21036, postal, "town", "Batu Pahat", "Johor", "Bukit Pasir"), shop, places, d) == "place"
    near_shops = north(BATU_PAHAT_SHOPS, 5)   # a row far from the far town's point: judged by its distance, as before
    assert gp.wrong_by(cache_row(21036, near_shops, "town", "Batu Pahat", "Johor", "Bukit Pasir"), shop, places, d) is None
    for gap_km, kind in ((19.9, "district"), (20.1, "town")):
        places, d, postal = bukit_pasir(gap_km)
        assert anchor("Johor", "Batu Pahat", "TAMAN BUKIT PASIR", places, d)["kind"] == kind, gap_km
    # A row at the town's only place that place_point's limits, 5 km wider, still accept stays even where the table's
    # point is that same far town's (the tolerance of test_a_row_at_its_towns_only_place_..._table_point comes first);
    # a row at that table point and not at the place is wrong.
    r = BATU_PAHAT_SHOPS
    near_far = table([("84300", "Bukit Pasir", "Johor", *north(r, 30))], [(1, "Bukit Pasir", "Johor", *north(r, 38), "")])
    d = districts({"Johor|Batu Pahat": r}, near_far)
    assert near_far.far_town("Johor", "BUKIT PASIR", [r], north(r, 30))
    assert gp.wrong_by(cache_row(21036, north(r, 34), "town", "Batu Pahat", "Johor", "Bukit Pasir"), shop, near_far, d) is None
    assert gp.wrong_by(cache_row(21036, north(r, 27), "town", "Batu Pahat", "Johor", "Bukit Pasir"), shop, near_far, d) == "place"
    without = table([("84300", "Bukit Pasir", "Johor", *bukit_pasir(13.7)[2])], None)   # no list: as before it existed
    a = anchor("Johor", "Batu Pahat", "TAMAN BUKIT PASIR", without, districts({"Johor|Batu Pahat": BATU_PAHAT_SHOPS}, without))
    assert (a["kind"], a["via"]) == ("town", "rows")


SIBU_JAYA_ROW = (2.295, 111.835)   # shop 5304's old row at Sibu town, 16 km from Sibu Jaya


def sibu_files(tmp_path, sub, rows, centres=None):
    shops = {("Sarawak", "Sibujaya"): [(5304, "PASAR BASAH SIBUJAYA", "MENYAN LAND DISTRICT,SIBU JAYA,SIBU, SARAWAK")],
             ("Sarawak", "Sibu"): [(1, "A", "NO 1, JALAN PULAU, SIBU"), (2, "B", "NO 2, JALAN PULAU, SIBU")]}
    found = [cache_row(1, (2.29, 111.83), "name", "Sibu", "Sarawak"), cache_row(2, (2.30, 111.84), "name", "Sibu", "Sarawak")]
    centres = centres if centres is not None else {"Sarawak|Sibu": SIBU_CENTRE, "Sarawak|Sibujaya": SIBUJAYA_CENTRE}
    return retry_run(tmp_path, sub, shops, found + rows, centres, SIBU_POSTAL, [(1, "Sibu", "Sarawak", *SIBU_TOWN, "")], minutes=0)


def test_a_wrong_point_with_no_better_anchor_becomes_a_district_row_before_any_search(tmp_path):
    """Shop 5304's old row at Sibu town is wrong (found shops of another district), and its anchor today is its
    district: a run with no time for searches makes it a district row at once (the district centre), with its tries
    and last_tried, and counts it; a second such run changes nothing; a run with time still searches it."""
    rows, st = sibu_files(tmp_path, "one", [cache_row(5304, SIBU_JAYA_ROW, "town", "Sibujaya", "Sarawak", "Sibu", tries=0, last="")])
    r = rows["5304"]
    assert (r["precision"], r["query"], r["tries"], r["last_tried"]) == ("district", "Sibujaya, Sarawak", "0", "")
    assert (float(r["lat"]), float(r["lon"])) == pytest.approx(SIBUJAYA_CENTRE, abs=1e-5)
    assert (st["corrected_offline"], st["place_corrected"], st["upgraded_offline"]) == (1, 0, 0)
    first = (tmp_path / "one" / "premise_geo.csv").read_bytes()
    rows2, st2 = sibu_files(tmp_path, "two", list(rows.values()))
    assert (tmp_path / "two" / "premise_geo.csv").read_bytes() == first
    assert (st2["corrected_offline"], st2["upgraded_offline"]) == (0, 0)
    assert gp.retry_queue([{"premise_code": "5304"}], rows2, TODAY, 10) == [{"premise_code": "5304"}]


def test_a_wrong_point_whose_district_has_no_centre_becomes_a_row_with_no_point(tmp_path):
    """No district centre and no other shops in the district: no anchor at all. The wrong row still goes (a district
    row with no point: 'location unknown', as the build reads a row with no point)."""
    rows, st = sibu_files(tmp_path, "nocentre", [cache_row(5304, SIBU_JAYA_ROW, "town", "Sibujaya", "Sarawak", "Sibu", tries=1)],
                          centres={"Sarawak|Sibu": SIBU_CENTRE})
    r = rows["5304"]
    assert (r["precision"], r["lat"], r["lon"], r["query"], r["tries"]) == ("district", "", "", "Sibujaya, Sarawak", "1")
    assert st["corrected_offline"] == 1


def test_a_row_at_a_far_towns_table_point_becomes_a_district_row_before_any_search(tmp_path):
    """Shop 21036 in a run with no time for searches: its row at the far town's table point is wrong (the place
    list says so) and nothing better is written, so it becomes a district row, counted in place_corrected."""
    places, d, postal = bukit_pasir(13.7)
    rows, st = retry_run(tmp_path, "run", {("Johor", "Batu Pahat"): [(21036, "KEDAI", "NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR,")]},
                         [cache_row(21036, postal, "town", "Batu Pahat", "Johor", "Bukit Pasir", tries=0, last="")],
                         {"Johor|Batu Pahat": BATU_PAHAT_SHOPS},
                         [("84300", "Bukit Pasir", "Johor", *postal), ("86400", "Parit Raja", "Johor", 1.87, 103.10)],
                         [(1, "Bukit Pasir", "Johor", *BUKIT_PASIR_FAR, ""), (2, "Parit Raja", "Johor", *PARIT_RAJA, "")], minutes=0)
    assert (rows["21036"]["precision"], rows["21036"]["query"]) == ("district", "Batu Pahat, Johor")
    assert (st["corrected_offline"], st["place_corrected"], st["place_dropped"]) == (1, 1, 1)


# T2-3: a written town beats an unwritten postcode place (2026 points).
KS_SHOPS, SEKINCHAN, KUALA_SELANGOR = (3.34, 101.26), (3.5074, 101.1041), (3.35, 101.25)
KS_POSTAL = [("45400", "Sekinchan", "Selangor", 3.3788, 101.2798), ("45000", "Kuala Selangor", "Selangor", 3.2457, 101.3678)]
KS_PLACES = [(1, "Sekinchan", "Selangor", *SEKINCHAN, ""), (2, "Kuala Selangor", "Selangor", *KUALA_SELANGOR, "")]


def test_a_written_town_beats_an_unwritten_postcode_place():
    """4015 ('45400 KUALA SELANGOR'): 45400's place is Sekinchan (one rough point, 24 km from the town's own point),
    which the address does not write, while it writes Kuala Selangor: the shop gets Kuala Selangor, not Sekinchan.
    With no town written the postcode's town is used as before; so it is when the address writes it."""
    places = table(KS_POSTAL, KS_PLACES)
    d = districts({"Selangor|Kuala Selangor": KS_SHOPS}, places)
    assert gp.km(3.3788, 101.2798, *SEKINCHAN) > gp.ANCHOR_KM
    a = anchor("Selangor", "Kuala Selangor", "202 & 203 (Q),PEKAN PASIR PENAMBANG,45400 KUALA SELANGOR, SELANGOR", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Kuala Selangor", "place") and a["point"] == pytest.approx(KUALA_SELANGOR)
    for address in ("LOT 1, JALAN 2, 45400", "LOT 1, 45400 SEKINCHAN", "LOT 1, SEKINCHAN, 45400 KUALA SELANGOR"):
        a = anchor("Selangor", "Kuala Selangor", address, places, d)
        assert (a["kind"], a["label"], a["via"]) == ("town", "Sekinchan", "postcode town"), address
    # 20943 ('TAMAN PAGOH JAYA, 84500 PAGOH'): 84500's place is Panchor, 30 km from 84500's point; Pagoh is written.
    places = table([("84500", "Panchor", "Johor", 2.0423, 102.9538), ("84600", "Pagoh", "Johor", 2.087, 102.8871)],
                   [(1, "Panchor", "Johor", 2.1722, 102.7083, "Pancur"), (2, "Pagoh", "Johor", 2.1495, 102.7704, "")])
    d = districts({"Johor|Muar": (2.10, 102.80)}, places)
    a = anchor("Johor", "Muar", "NO 9, 11 & 13 JALAN JAYA 1/5 A,TAMAN PAGOH JAYA,84500 PAGOH", places, d)
    assert (a["label"], a["via"]) == ("Pagoh", "place")
    assert anchor("Johor", "Muar", "NO 9, JALAN JAYA, 84500", places, d)["label"] == "Panchor"


def test_a_postcode_place_written_in_the_address_still_gives_its_town():
    """6437 ('98200 BATU NIAH'): the address writes Niah, 98200's place, so the postcode gives Niah's own point as
    before (11.7 km from where OpenStreetMap found that shop: a known limit, see etl/geo/README.md)."""
    niah = (3.86203, 113.71521)
    places = table([("98200", "Niah", "Sarawak", 3.195, 112.6848)], [(1, "Niah", "Sarawak", *niah, "Kampong Niah")])
    d = districts({"Sarawak|Subis": (3.80, 113.78)}, places)
    assert not places.place_unwritten("98200", "LOT 1, 98200 BATU NIAH", "Sarawak", "Subis")
    a = anchor("Sarawak", "Subis", "LOT 1, 98200 BATU NIAH", places, d)
    assert (a["label"], a["via"]) == ("Niah", "postcode town") and a["point"] == pytest.approx(niah)


KINTA_SHOPS, IPOH = (4.60, 101.08), (4.5975, 101.0901)


def test_a_retry_never_turns_a_written_towns_row_into_an_unwritten_postcode_places_row(tmp_path):
    """12352 ('... OFF JALAN KG BERCHAM, 34100 IPOH'): 34100 is Selama's postcode. Should its point come within 35 km
    of where Kinta's shops are, a retry that finds nothing keeps the 'Ipoh' row (before, it became a postcode row at
    Selama, 34 km off). A new shop with that address still gets the postcode: the rule is for the written town's row."""
    selama = north(KINTA_SHOPS, 34)
    postal = [("30000", "Ipoh", "Perak", *IPOH), ("34100", "Selama", "Perak", *selama)]
    place_list = [(1, "Ipoh", "Perak", *IPOH, ""), (2, "Selama", "Perak", *selama, "")]
    address = "NO 1, OFF JALAN KG BERCHAM, 34100 IPOH"
    places = table(postal, place_list)
    a = anchor("Perak", "Kinta", address, places, districts({"Perak|Kinta": KINTA_SHOPS}, places))
    assert (a["kind"], a["label"]) == ("postcode", "34100")
    for minutes in (0, None):
        rows, st = retry_run(tmp_path, f"run-{minutes}", {("Perak", "Kinta"): [(12352, "KEDAI", address)]},
                             [cache_row(12352, IPOH, "town", "Kinta", "Perak", "Ipoh", last="2026-10-01")],
                             {"Perak|Kinta": KINTA_SHOPS}, postal, place_list, minutes=minutes)
        assert (rows["12352"]["precision"], rows["12352"]["query"], st["retried"]) == ("town", "Ipoh", 0 if minutes == 0 else 1)
        assert (float(rows["12352"]["lat"]), float(rows["12352"]["lon"])) == pytest.approx(IPOH, abs=1e-5)
    # A town row for a town the address does not write (Kampar) gets no such protection: the postcode's row.
    kampar = north(KINTA_SHOPS, -20)
    rows, _ = retry_run(tmp_path, "kampar", {("Perak", "Kinta"): [(12352, "KEDAI", address)]},
                        [cache_row(12352, kampar, "town", "Kinta", "Perak", "Kampar", last="2026-10-01")],
                        {"Perak|Kinta": KINTA_SHOPS}, postal + [("31900", "Kampar", "Perak", *kampar)],
                        place_list + [(3, "Kampar", "Perak", *kampar, "")])
    assert (rows["12352"]["precision"], rows["12352"]["query"]) == ("postcode", "34100")


def test_an_old_postcode_row_is_not_judged_by_an_unwritten_postcode_place():
    """17976 ('LOT 2142,JALAN BESAR KAMPUNG NYIOR,23100 DUNGUN'): 23100's place is Paka, which the address does not
    write. Today's anchor does not use Paka, so Paka's point does not judge the old postcode row either; the district
    check does. Written as '23100 PAKA', the row far from Paka is wrong as before. A place name is written only as whole
    words: 'KG PAKAN' does not write PAKA."""
    paka, rough = (4.6372, 103.4366), (4.50, 103.30)
    places = table([("23100", "Paka", "Terengganu", *rough), ("23000", "Dungun", "Terengganu", 4.76, 103.41)],
                   [(1, "Paka", "Terengganu", *paka, ""), (2, "Dungun", "Terengganu", 4.7566, 103.4119, "")])
    d = districts({"Terengganu|Dungun": (4.70, 103.42)}, places)
    shop = {"premise_code": "17976", "premise": "KEDAI", "state": "Terengganu", "district": "Dungun",
            "address": "LOT 2142,JALAN BESAR KAMPUNG NYIOR,23100 DUNGUN TERENGGANU"}
    row = cache_row(17976, rough, "postcode", "Dungun", "Terengganu", "23100")
    assert gp.km(*rough, *paka) > gp.ANCHOR_KM and gp.km(*rough, 4.70, 103.42) < gp.CHECK_KM
    assert gp.wrong_by(row, shop, places, d) is None
    assert gp.wrong_by(row, {**shop, "address": "LOT 2142, 23100 PAKA"}, places, d) == "place"
    pakan = "LOT 2142, KG PAKAN, 23100 DUNGUN"
    assert places.place_unwritten("23100", pakan, "Terengganu", "Dungun")
    assert gp.wrong_by(row, {**shop, "address": pakan}, places, d) is None


# T2-2: a more specific place vetoes a far town (2026 points).
PAPAR_SHOPS, PAPAR, KINARUT = (5.70, 115.98), (5.73333, 115.93333), (5.8231, 116.0466)
PAPAR_POSTAL = [("89600", "Papar", "Sabah", 5.5174, 115.9036)]
LAWAS_CENTRE, LAWAS, TRUSAN = (4.455121, 115.427188), (4.8604, 115.409), (4.78486, 115.27463)
LAWAS_POSTAL = [("98850", "Lawas", "Sarawak", 3.9117, 114.0909), ("98857", "Lawas", "Sarawak", 4.8211, 115.4274)]


def test_a_smaller_place_vetoes_a_far_town():
    """21246 ('...KINARUT,PAPAR'): Kinarut, a place of the list but no postal town, lies 16 km from Papar's point: the
    shop does not get Papar (its district instead), and an old row at Papar is wrong. 18109 ('PEKAN TRUSAN,LAWAS'):
    Trusan is 17 km from Lawas town; both lie 40-45 km from Lawas's district centre, so the place counts within 35 km
    of the town itself. No veto when a place of that name (any of its names) lies within 10 km of the town, or when
    every place of that name lies more than 35 km from the district's shops and the town (another place), 45 km for
    the district's own town, as Papar (see test_a_smaller_place_rules_out_the_districts_own_town_from_further_out)."""
    assert gp.km(*KINARUT, *PAPAR) > gp.ANCHOR_KM and gp.km(*KINARUT, *PAPAR_SHOPS) < gp.CHECK_KM

    def papar(kinarut_places):
        places = table(PAPAR_POSTAL, [(1, "Papar", "Sabah", *PAPAR, "")] + kinarut_places)
        return places, districts({"Sabah|Papar": PAPAR_SHOPS}, places)
    places, d = papar([(2, "Kinarut", "Sabah", *KINARUT, "Kinarut Station")])
    address = "TINGKAT 1-2, LOT 72,,THE PALM SQUARE COMMERCIAL CENTRE KINARUT,PAPAR"
    assert anchor("Sabah", "Papar", "LOT 72, PAPAR", places, d)["label"] == "Papar"
    assert anchor("Sabah", "Papar", address, places, d)["kind"] == "district"
    shop = {"premise_code": "21246", "premise": "KEDAI", "state": "Sabah", "district": "Papar", "address": address}
    assert gp.wrong_by(cache_row(21246, PAPAR, "town", "Papar", "Sabah", "Papar"), shop, places, d) == "place"
    assert gp.wrong_by(cache_row(21246, PAPAR, "town", "Papar", "Sabah", "Papar"), {**shop, "address": "LOT 72, PAPAR"},
                       places, d) is None
    near_papar = north(PAPAR, 9.9)
    places, d = papar([(2, "Kinarut", "Sabah", *KINARUT, ""), (3, "Kinarut Lama", "Sabah", *near_papar, "Kinarut")])
    assert anchor("Sabah", "Papar", address, places, d)["label"] == "Papar"     # a place called Kinarut 9.9 km from Papar
    places, d = papar([(2, "Kinarut", "Sabah", *KINARUT, ""), (3, "Kinarut Lama", "Sabah", *north(PAPAR, 10.1), "Kinarut")])
    assert anchor("Sabah", "Papar", address, places, d)["kind"] == "district"  # ... 10.1 km: no
    far = north(PAPAR_SHOPS, 46)    # the town lies within 35 km of the district's shops, so only they count
    assert gp.km(*far, *PAPAR_SHOPS) > gp.OWN_VETO_KM > gp.CHECK_KM > gp.km(*PAPAR, *PAPAR_SHOPS)
    places, d = papar([(2, "Kinarut", "Sabah", *far, "")])
    assert anchor("Sabah", "Papar", address, places, d)["label"] == "Papar"     # another Kinarut, 46 km away
    places, d = papar([(2, "Kinarut", "Sabah", *north(PAPAR_SHOPS, 36), "")])
    assert anchor("Sabah", "Papar", address, places, d)["kind"] == "district"  # 36 km: Papar is the district's own town
    places = table(LAWAS_POSTAL, [(1, "Lawas", "Sarawak", *LAWAS, ""), (2, "Trusan", "Sarawak", *TRUSAN, "Terusan")])
    d = districts({"Sarawak|Lawas": LAWAS_CENTRE}, places)
    assert gp.km(*TRUSAN, *LAWAS_CENTRE) > gp.CHECK_KM and gp.km(*LAWAS, *LAWAS_CENTRE) > gp.CHECK_KM
    assert gp.ANCHOR_KM < gp.km(*TRUSAN, *LAWAS) < gp.CHECK_KM
    assert anchor("Sarawak", "Lawas", "PASAR LAWAS,LAWAS,", places, d)["label"] == "Lawas"
    assert anchor("Sarawak", "Lawas", "PEKAN TRUSAN,LAWAS,", places, d)["kind"] == "district"


def test_a_smaller_place_vetoes_a_postcodes_town_and_lets_the_next_town_count():
    """17147 ('TENANG STATION, 85000 SEGAMAT'): Tenang lies 16.6 km from Segamat's own point, which 85000 (one rough
    point) would give, and which the written SEGAMAT gives too: no town; the district. A town written that lies within
    10 km of Tenang still counts; Labis, 14 km away, does not."""
    segamat, tenang, labis = (2.5148, 102.8158), (2.3729, 102.8954), (2.3834, 103.0217)
    sepang_jaya = north(tenang, 5)   # a made-up postal town 5 km from Tenang
    places = table([("85000", "Segamat", "Johor", 2.65, 102.70), ("85300", "Labis", "Johor", 2.38, 103.02),
                    ("85310", "Sepang Jaya", "Johor", *sepang_jaya)],
                   [(1, "Segamat", "Johor", *segamat, ""), (2, "Tenang", "Johor", *tenang, ""), (3, "Labis", "Johor", *labis, ""),
                    (4, "Sepang Jaya", "Johor", *sepang_jaya, "")])
    d = districts({"Johor|Segamat": (2.45, 102.85)}, places)
    assert gp.km(*tenang, *segamat) > gp.ANCHOR_KM and gp.km(2.65, 102.70, *segamat) > gp.ANCHOR_KM
    a = anchor("Johor", "Segamat", "NO 48 & 49, 85000, SEGAMAT,JOHOR,", places, d)
    assert (a["label"], a["via"]) == ("Segamat", "postcode town")
    assert anchor("Johor", "Segamat", "NO 48 & 49,TENANG STATION,85000, SEGAMAT,JOHOR,", places, d)["kind"] == "district"
    assert gp.ANCHOR_KM < gp.km(*tenang, *labis) < gp.CHECK_KM
    assert anchor("Johor", "Segamat", "NO 48 & 49,TENANG STATION,LABIS, JOHOR,", places, d)["kind"] == "district"
    a = anchor("Johor", "Segamat", "NO 48 & 49,TENANG STATION,SEPANG JAYA, JOHOR,", places, d)
    assert (a["kind"], a["label"]) == ("town", "Sepang Jaya")


def test_what_names_a_smaller_place():
    """Not a smaller place: a postal town (or part of one written there), the state's or the district's name, a name of
    generic words or numbers (KOTA, BATU 10, SIMPANG EMPAT, CHABANG EMPAT), of words found in 100 or more names of the
    list (KAMPUNG GONG; the places' names count, not their ascii or alt names), a road (JALAN TENANG), a subdistrict
    (MUKIM SALA), a housing estate (TAMAN MEWAH, or a name right after TAMAN), a name of fewer than 4 letters, another
    state's place. KG reads as KAMPUNG; a name matches by its ascii name too."""
    centre = (6.0, 100.4)
    common = [(100 + i, f"Kampung Gong Bendang {i}", "Kedah", 6.0, 100.4, "") for i in range(100)]   # the decided 100
    common += [(300 + i, f"Kampung Tualang {i}", "Kedah", 6.0, 100.4, "") for i in range(99)]
    # TUALANG in a 100th place's ascii name only (its name has an accent) and in a 101st's alt name only: still 99 names.
    common += [(400, "Kampung Tu\u00e2lang", "Kedah", 6.0, 100.4, ""), (401, "Kampung Seratus", "Kedah", 6.0, 100.4, "Kampung Tualang")]
    plist = [(1, "Kota", "Kedah", 6.01, 100.41, ""), (2, "Batu 10", "Kedah", 6.01, 100.41, ""),
             (3, "Simpang Empat", "Kedah", 6.01, 100.41, ""), (4, "Chabang Empat", "Kedah", 6.01, 100.41, ""),
             (5, "Kampung Gong", "Kedah", 6.01, 100.41, ""), (6, "Tenang", "Kedah", 6.01, 100.41, ""),
             (7, "Sala", "Kedah", 6.01, 100.41, ""), (8, "Taman Mewah", "Kedah", 6.01, 100.41, ""),
             (9, "Awah", "Kedah", 6.01, 100.41, ""), (10, "Ulu", "Kedah", 6.01, 100.41, ""),
             (11, "Jitra", "Kedah", 6.01, 100.41, ""), (12, "Kubang Pasu", "Kedah", 6.01, 100.41, ""),
             (13, "Kedah", "Kedah", 6.01, 100.41, ""), (14, "Kampung Bakap", "Kedah", 6.01, 100.41, ""),
             (15, "Kampong Tok Lebai", "Kedah", 6.01, 100.41, ""), (16, "Perai", "Pulau Pinang", 6.01, 100.41, ""),
             (17, "Bukit Kayu Hitam", "Kedah", 6.01, 100.41, ""), (19, "Kayu Hitam", "Kedah", 6.01, 100.41, ""),
             (18, "Changloon", "Kedah", 6.01, 100.41, "")] + common
    rows = place_rows(plist)
    ascii_names = {"18": "Changlun", "400": "Kampung Tualang"}   # Changloon, ascii name Changlun
    for r in rows:
        r["asciiname"] = ascii_names.get(r["geonameid"], r["asciiname"])
    places = gp.Places(postal_rows([("06000", "Jitra", "Kedah", *centre), ("06050", "Bukit Kayu Hitam", "Kedah", *centre),
                                    ("13700", "Seberang Jaya", "Pulau Pinang", 5.40, 100.40)]), rows)
    assert "GONG" in places.common_words and "BENDANG" in places.common_words and "TUALANG" not in places.common_words

    def names(address):
        return [w for w, _ in places.smaller_places(address, "Kedah", "Kubang Pasu")]
    assert names("KOTA, BATU 10, SIMPANG EMPAT, CHABANG EMPAT, KAMPUNG GONG, JITRA") == []
    assert names("JALAN TENANG, MUKIM SALA, TAMAN MEWAH, TAMAN AWAH INDAH, ULU, KUBANG PASU, KEDAH, PERAI") == []
    assert names("NO 1, BUKIT KAYU HITAM") == [] and names("NO 1, KAYU HITAM") == ["KAYU HITAM"]  # part of a postal town
    assert names("KAMPUNG, BAKAP") == []    # the words of one name are written next to each other
    assert names("KG BAKAP, TENANG, AWAH, KG TOK LEBAI, CHANGLUN") == ["KAMPUNG TOK LEBAI", "KAMPUNG BAKAP", "TENANG",
                                                                         "AWAH", "CHANGLUN"]
    assert gp.Places(postal_rows([("06000", "Jitra", "Kedah", *centre)]), None).smaller_places("NO 1, TENANG", "Kedah") == []


def test_a_kampung_name_and_its_core_are_one_place_for_the_veto():
    """21769 ('KEDAI KAMPUNG BERANGAN,TUMPAT'): the list's 'Kampung Berangan' lies 15 km from Tumpat, but its
    'Berangan' 1.4 km: that may be the place written, so Tumpat is not ruled out."""
    tumpat, far_berangan, berangan = (6.1978, 102.171), (6.06, 102.20), (6.19, 102.16)
    postal = [("16200", "Tumpat", "Kelantan", *tumpat)]
    one = table(postal, [(1, "Tumpat", "Kelantan", *tumpat, ""), (2, "Kampung Berangan", "Kelantan", *far_berangan, "")])
    two = table(postal, [(1, "Tumpat", "Kelantan", *tumpat, ""), (2, "Kampung Berangan", "Kelantan", *far_berangan, ""),
                         (3, "Berangan", "Kelantan", *berangan, "")])
    assert gp.km(*far_berangan, *tumpat) > gp.ANCHOR_KM > gp.km(*berangan, *tumpat)
    for places, kind in ((one, "district"), (two, "town")):
        d = districts({"Kelantan|Tumpat": (6.15, 102.17)}, places)
        assert anchor("Kelantan", "Tumpat", "417-B, KEDAI KAMPUNG BERANGAN,TUMPAT,", places, d)["kind"] == kind


def test_a_vetoed_town_row_becomes_a_district_row_before_any_search(tmp_path):
    """A run with no time for searches: 21246's old row at Papar is wrong (Kinarut), and nothing better is written: a
    district row, counted in place_corrected; a second such run changes nothing."""
    plist = [(1, "Papar", "Sabah", *PAPAR, ""), (2, "Kinarut", "Sabah", *KINARUT, "")]
    shops = {("Sabah", "Papar"): [(21246, "KEDAI", "TINGKAT 1-2, LOT 72,,THE PALM SQUARE COMMERCIAL CENTRE KINARUT,PAPAR")]}
    rows, st = retry_run(tmp_path, "one", shops, [cache_row(21246, PAPAR, "town", "Papar", "Sabah", "Papar", tries=0, last="")],
                         {"Sabah|Papar": PAPAR_SHOPS}, PAPAR_POSTAL, plist, minutes=0)
    assert (rows["21246"]["precision"], rows["21246"]["query"]) == ("district", "Papar, Sabah")
    assert (st["corrected_offline"], st["place_corrected"]) == (1, 1)
    rows2, st2 = retry_run(tmp_path, "two", shops, list(rows.values()), {"Sabah|Papar": PAPAR_SHOPS}, PAPAR_POSTAL, plist, minutes=0)
    assert rows2 == rows and (st2["corrected_offline"], st2["upgraded_offline"]) == (0, 0)


# T2-7: the decided cuts at their edges, and the scope of round 1's rules.

def test_two_places_of_a_name_are_ambiguous_from_10_km_apart():
    """place_point: two places of the name within 15 km of where the district's shops are, 9.9 km apart: the nearer;
    10.1 km apart: which one? none."""
    centre = (3.0, 101.5)
    near = north(centre, 2)
    for gap_km, want in ((9.9, near), (10.1, None)):
        other = north(near, gap_km)
        assert gp.km(*other, *centre) < gp.GUESS_KM
        places = table([("43000", "Kajang", "Selangor", *centre)],
                       [(1, "Sungai Buah", "Selangor", *near, ""), (2, "Sungai Buah", "Selangor", *other, "")])
        got = places.place_point("Selangor", "SUNGAI BUAH", [centre])
        assert (got == pytest.approx(want, abs=1e-4)) if want else got is None, gap_km


def test_a_postcodes_town_point_replaces_its_rough_point_from_10_km():
    """A postcode of a town with one rough point keeps that point when it lies 9.9 km from the town's own point, and
    gives the town's point at 10.1 km."""
    for gap_km, kind in ((9.9, "postcode"), (10.1, "town")):
        rough = north(PUTATAN, gap_km)
        places = table([(f"8820{i}", "Putatan", "Sabah", *rough) for i in range(5)], PLACES)
        a = anchor("Sabah", "Putatan", "LOT 1, 88200 PUTATAN", places, districts({"Sabah|Putatan": PUTATAN_CENTRE}, places))
        assert a["kind"] == kind, gap_km


def test_old_rows_are_judged_by_the_towns_own_point_from_10_km():
    """An old town row 9.9 km from the town's own point (no found shops) stands, 10.1 km is wrong; so for an old postcode
    row and its town's point, when today's anchor gives the town's point instead of the postcode's rough one."""
    places = table()
    d = districts({"Perak|Kerian": KERIAN_CENTRE, "Sabah|Putatan": PUTATAN_CENTRE}, places)
    shop = {"premise_code": "1", "premise": "KEDAI", "state": "Perak", "district": "Kerian", "address": "NO 1, BAGAN SERAI"}
    putatan = {"premise_code": "3", "premise": "KEDAI", "state": "Sabah", "district": "Putatan", "address": "LOT 3, 88200 PUTATAN"}
    for gap_km, why in ((9.9, None), (10.1, "place")):
        row = cache_row(1, north(BAGAN_SERAI, gap_km), "town", "Kerian", "Perak", "Bagan Serai")
        assert gp.wrong_by(row, shop, places, d) == why, gap_km
        row = cache_row(3, north(PUTATAN, -gap_km), "postcode", "Putatan", "Sabah", "88200")
        assert gp.wrong_by(row, putatan, places, d) == why, gap_km


def test_the_retry_rule_takes_a_town_whose_rows_spread_5_to_10_km(tmp_path):
    """The retry rule's one rough point is the new anchors' (rows within 10 km), not the old rows' (5 km): Jerteh's rows
    7 km apart; an old 'Jerteh' town row stays when a retry's anchor is 22020's own point (3 km from the town)."""
    p22020, p22000 = north(JERTIH, 3), north(JERTIH, -4)
    postal = [("22020", "Jerteh", "Terengganu", *p22020), ("22000", "Jerteh", "Terengganu", *p22000)]
    places = table(postal, [(1736405, "Jertih", "Terengganu", *JERTIH, "Jerteh")])
    assert ("terengganu", "JERTEH") in places.one_point and ("terengganu", "JERTEH") not in places.one_point_rows
    shops = {("Terengganu", "Besut"): [(1, "KEDAI", "LOT 1, 22020 JERTEH")]}
    a = anchor("Terengganu", "Besut", "LOT 1, 22020 JERTEH", places, districts({"Terengganu|Besut": JERTIH}, places))
    assert (a["kind"], a["label"], a["via"]) == ("postcode", "22020", "rows")
    rows, st = retry_run(tmp_path, "spread", shops,
                         [cache_row(1, JERTIH, "town", "Besut", "Terengganu", "Jerteh", last="2026-10-01")],
                         {"Terengganu|Besut": JERTIH}, postal, [(1736405, "Jertih", "Terengganu", *JERTIH, "Jerteh")])
    assert (rows["1"]["precision"], rows["1"]["query"], st["retried"]) == ("town", "Jerteh", 1)


def test_the_districts_own_town_counts_up_to_80_km():
    """The district's own town, the only place of its name: 79 km out counts, 81 km does not."""
    for gap_km, ok in ((79.0, True), (81.0, False)):
        own = north(KUDAT_CENTRE, -gap_km)
        kudat = table([("89050", "Kudat", "Sabah", *north(KUDAT_CENTRE, 40))], [(1, "Kudat", "Sabah", *own, "")])
        got = kudat.place_point("Sabah", "KUDAT", [KUDAT_CENTRE], True, True)
        assert (got is not None) == ok, gap_km


def test_a_place_is_found_by_its_ascii_name_too():
    """GeoNames' Changloon has the ascii name Changlun, as the postcode table names the town."""
    rows = place_rows([(1, "Changloon", "Kedah", 6.4290, 100.4290, "")])
    rows[0]["asciiname"] = "Changlun"
    places = gp.Places(postal_rows([("06010", "Changlun", "Kedah", 6.40, 100.43)]), rows)
    assert places.place_point("Kedah", "CHANGLUN", [(6.42, 100.43)]) == pytest.approx((6.4290, 100.4290))
    a = anchor("Kedah", "Kubang Pasu", "NO 1, CHANGLUN", places, districts({"Kedah|Kubang Pasu": (6.42, 100.43)}, places))
    assert (a["label"], a["via"]) == ("Changlun", "place")


def test_a_kl_shops_town_row_at_a_selangor_only_place_stays_only_for_strong_evidence():
    """at_only_place takes the NEIGHBOURS' places only with strong evidence, as place_point does: a W.P. Kuala Lumpur
    shop's old row at the only 'Sungai Jauh', in Selangor, 42 km out (the KL table's point 17 km out, 25 km from it)
    is judged by its distance (wrong) for a weak town; for the district's own town it may stay."""
    r = (3.15, 101.70)
    postal, place = north(r, 17), north(r, 42)
    places = table([("52990", "Sungai Jauh", "Wilayah Persekutuan Kuala Lumpur", *postal)],
                   [(1, "Sungai Jauh", "Selangor", *place, "")])
    assert places.at_only_place(KL, "SUNGAI JAUH", place, [r], strong=True)
    assert not places.at_only_place(KL, "SUNGAI JAUH", place, [r])
    shop = {"premise_code": "1", "premise": "KEDAI", "state": KL, "district": "Kepong", "address": "NO 1, SUNGAI JAUH"}
    d = districts({f"{KL}|Kepong": r}, places)
    assert gp.wrong_by(cache_row(1, place, "town", "Kepong", KL, "Sungai Jauh"), shop, places, d) == "district"
    own = {**shop, "district": "Sungai Jauh"}
    d = districts({f"{KL}|Sungai Jauh": r}, places)
    assert gp.wrong_by(cache_row(1, place, "town", "Sungai Jauh", KL, "Sungai Jauh"), own, places, d) is None


# ---------- round 2 fix-ups: the veto at found shops' points and for the district's own town, and the edges ----------

TANAH_MERAH_CENTRE = (5.7663, 102.0341)


def test_a_smaller_place_vetoes_a_town_that_found_shops_place():
    """20826 ('LOT PT 4408, KAMPUNG GUAL IPOH,TANAH MERAH,'): two found shops that name TANAH MERAH agree on its point
    F, so the town's anchor is F ('found'). Kampung Gual Ipoh lies 12.7 km from F and within 35 km of where the
    district's shops are: no town anchor, and an old town row at F is wrong ('place': the veto comes before the found
    shops' own check, which the row passes). With the place 9.9 km from F the town stays, and so does the row."""
    found = north(TANAH_MERAH_CENTRE, 5)
    address = "LOT PT 4408, KAMPUNG GUAL IPOH,TANAH MERAH,"
    shop = {"premise_code": "20826", "premise": "KEDAI", "state": "Kelantan", "district": "Tanah Merah", "address": address}
    for gap_km, kind, why in ((12.7, "district", "place"), (9.9, "town", None)):
        places = table([("17500", "Tanah Merah", "Kelantan", *north(TANAH_MERAH_CENTRE, 20))],
                       [(1, "Tanah Merah", "Kelantan", *north(TANAH_MERAH_CENTRE, 8), ""),
                        (2, "Kampung Gual Ipoh", "Kelantan", *north(found, -gap_km), "")])
        places.learn(found_shops("Kelantan", "Tanah Merah", "NO 1, TANAH MERAH", [north(found, -0.2), north(found, 0.2)]))
        d = districts({"Kelantan|Tanah Merah": TANAH_MERAH_CENTRE}, places)
        assert anchor("Kelantan", "Tanah Merah", "NO 1, TANAH MERAH", places, d)["via"] == "found"
        assert anchor("Kelantan", "Tanah Merah", address, places, d)["kind"] == kind, gap_km
        assert gp.wrong_by(cache_row(20826, found, "town", "Tanah Merah", "Kelantan", "Tanah Merah"), shop, places, d) == why


PEKAN_SHOPS, PEKAN = (3.3782, 103.2096), (3.4000, 103.2100)
NENASI_ADDRESS = "NO 63 & 64 ,JALAN BESAR NENASI,PEKAN, PAHANG"


def pekan_table(nenasi_km):
    """Pekan (the town and its postcode) by where Pekan's shops are, and Nenasi nenasi_km south of them."""
    return table([("26600", "Pekan", "Pahang", *PEKAN)],
                 [(1, "Pekan", "Pahang", *PEKAN, ""), (2, "Nenasi", "Pahang", *north(PEKAN_SHOPS, -nenasi_km), "Kampong Nenasi")])


def test_a_smaller_place_rules_out_the_districts_own_town_from_further_out():
    """5316 ('NO 63 & 64 ,JALAN BESAR NENASI,PEKAN, PAHANG'): Nenasi lies 37.4 km from where Pekan's shops are. PEKAN,
    the district's own town, may name only the district (a large one): a smaller place up to 45 km out rules it out,
    so the shop gets its district and an old row at Pekan is wrong. For a town not named after the district the place
    must lie within 35 km, as before; and beyond 45 km nothing is ruled out."""
    assert gp.CHECK_KM < 37.4 < gp.OWN_VETO_KM
    places = pekan_table(37.4)
    d = districts({"Pahang|Pekan": PEKAN_SHOPS}, places)
    assert anchor("Pahang", "Pekan", "NO 63 & 64, PEKAN, PAHANG", places, d)["label"] == "Pekan"
    assert anchor("Pahang", "Pekan", NENASI_ADDRESS, places, d)["kind"] == "district"
    shop = {"premise_code": "5316", "premise": "KEDAI", "state": "Pahang", "district": "Pekan", "address": NENASI_ADDRESS}
    assert gp.wrong_by(cache_row(5316, PEKAN, "town", "Pekan", "Pahang", "Pekan"), shop, places, d) == "place"
    other = districts({"Pahang|Rompin": PEKAN_SHOPS}, places)     # Pekan is not this district's own town
    a = anchor("Pahang", "Rompin", NENASI_ADDRESS, places, other)
    assert (a["kind"], a["label"]) == ("town", "Pekan")
    assert gp.wrong_by(cache_row(5316, PEKAN, "town", "Rompin", "Pahang", "Pekan"), {**shop, "district": "Rompin"},
                       places, other) is None
    for nenasi_km, own, vetoed in ((44.9, True, True), (45.1, True, False), (34.9, False, True), (35.1, False, False)):
        places = pekan_table(nenasi_km)
        smaller = places.smaller_places(NENASI_ADDRESS, "Pahang", "Pekan")
        assert [w for w, _ in smaller] == ["NENASI"]
        assert places.vetoes(PEKAN, smaller, [PEKAN_SHOPS], "Pahang", own) is vetoed, (nenasi_km, own)
    far = pekan_table(45.1)
    assert anchor("Pahang", "Pekan", NENASI_ADDRESS, far, districts({"Pahang|Pekan": PEKAN_SHOPS}, far))["label"] == "Pekan"


def test_a_smaller_place_rules_out_the_districts_own_town_that_its_postcode_gives():
    """The same for the town a postcode gives (one rough point, see Places.postcode_town): '26600 PEKAN', with 26600's
    rough point 15 km from Pekan, gives Pekan's own point, and NENASI (37.4 km out) rules it out. An old 26600 row at
    the rough point is then judged by its distance from the district, not by Pekan's point (as for any veto). In a
    district whose own town Pekan is not (Rompin, with its shops where Pekan's are), the place must lie within 35 km:
    37.4 km out it rules nothing out, so the shop gets Pekan's own point and the old row is judged by it (wrong)."""
    rough = north(PEKAN, 15)
    places = table([("26600", "Pekan", "Pahang", *rough)],
                   [(1, "Pekan", "Pahang", *PEKAN, ""), (2, "Nenasi", "Pahang", *north(PEKAN_SHOPS, -37.4), "")])
    d = districts({"Pahang|Pekan": PEKAN_SHOPS}, places)
    a = anchor("Pahang", "Pekan", "NO 63 & 64, 26600 PEKAN", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Pekan", "postcode town")
    address = "NO 63 & 64 ,JALAN BESAR NENASI,26600 PEKAN, PAHANG"
    assert anchor("Pahang", "Pekan", address, places, d)["kind"] == "district"
    shop = {"premise_code": "5316", "premise": "KEDAI", "state": "Pahang", "district": "Pekan", "address": address}
    row = cache_row(5316, rough, "postcode", "Pekan", "Pahang", "26600")
    assert gp.wrong_by(row, {**shop, "address": "NO 63 & 64, 26600 PEKAN"}, places, d) == "place"
    assert gp.wrong_by(row, shop, places, d) is None
    other = districts({"Pahang|Rompin": PEKAN_SHOPS}, places)
    a = anchor("Pahang", "Rompin", address, places, other)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Pekan", "postcode town") and a["point"] == pytest.approx(PEKAN)
    rompin = {**shop, "district": "Rompin"}
    assert gp.wrong_by(cache_row(5316, rough, "postcode", "Rompin", "Pahang", "26600"), rompin, places, other) == "place"


def test_a_vetoed_own_town_row_becomes_a_district_row_before_any_search(tmp_path):
    """A run with no time for searches: 5316's old row at Pekan is wrong (Nenasi), and nothing else is written: a
    district row, counted in place_corrected; a second such run changes nothing."""
    plist = [(1, "Pekan", "Pahang", *PEKAN, ""), (2, "Nenasi", "Pahang", *north(PEKAN_SHOPS, -37.4), "")]
    shops = {("Pahang", "Pekan"): [(5316, "KEDAI", NENASI_ADDRESS)]}
    postal = [("26600", "Pekan", "Pahang", *PEKAN)]
    rows, st = retry_run(tmp_path, "one", shops, [cache_row(5316, PEKAN, "town", "Pekan", "Pahang", "Pekan", tries=0, last="")],
                         {"Pahang|Pekan": PEKAN_SHOPS}, postal, plist, minutes=0)
    assert (rows["5316"]["precision"], rows["5316"]["query"]) == ("district", "Pekan, Pahang")
    assert (st["corrected_offline"], st["place_corrected"]) == (1, 1)
    rows2, st2 = retry_run(tmp_path, "two", shops, list(rows.values()), {"Pahang|Pekan": PEKAN_SHOPS}, postal, plist, minutes=0)
    assert rows2 == rows and (st2["corrected_offline"], st2["upgraded_offline"]) == (0, 0)


def test_the_vetoes_references():
    """The town's own point counts as a reference for the veto only when it lies further than 35 km out (as the
    district's own town may): a smaller place 12 km beyond a town 34 km out lies 46 km from the district's shops (no
    veto), beyond one 36 km out it rules the town out; so for the district's own town 40 km out, with a place 52 km
    from the district's shops. With no references there is nothing to check a place against."""
    r = (5.0, 102.0)
    for town_km, own, vetoed in ((34.0, False, False), (36.0, False, True), (40.0, True, True)):
        town = north(r, town_km)
        places = table([("16000", "Kota Bharu", "Kelantan", *r)], [(1, "Kampung Telok Rengas", "Kelantan", *north(town, 12), "")])
        smaller = places.smaller_places("NO 1, KG TELOK RENGAS, KOTA BHARU", "Kelantan", "Kota Bharu")
        assert [w for w, _ in smaller] == ["KAMPUNG TELOK RENGAS"]
        assert places.vetoes(town, smaller, [r], "Kelantan", own) is vetoed, town_km
        assert places.vetoes(town, smaller, [], "Kelantan", own) is False


def test_the_veto_needs_a_place_of_the_written_name_near_the_district():
    """'KG BERANGAN': the list's only 'Kampung Berangan' lies 50 km out (another place of that name). A 'Berangan' 20 km
    out (a place it may also be, see test_a_kampung_name_and_its_core_are_one_place_for_the_veto) does not make the
    written name this district's: no veto."""
    tumpat, centre = (6.1978, 102.171), (6.15, 102.17)
    places = table([("16200", "Tumpat", "Kelantan", *tumpat)],
                   [(1, "Tumpat", "Kelantan", *tumpat, ""), (2, "Kampung Berangan", "Kelantan", *north(centre, -50), ""),
                    (3, "Berangan", "Kelantan", *north(centre, -20), "")])
    a = anchor("Kelantan", "Tumpat", "417-B, KEDAI KAMPUNG BERANGAN,TUMPAT,", places, districts({"Kelantan|Tumpat": centre}, places))
    assert (a["kind"], a["label"]) == ("town", "Tumpat")


def test_a_far_towns_table_point_with_two_far_places_of_the_name():
    """Two places called Bukit Pasir, both further than 35 km from where Batu Pahat's shops are: the table's point,
    13.7 km from one of them, is that far town's, whatever the other and whichever the list writes first; 20.5 km
    from the nearer, it is used."""
    r = BATU_PAHAT_SHOPS
    postal = north(r, 33)
    for gap_km, far in ((13.7, True), (20.5, False)):
        two = [(1, "Bukit Pasir", "Johor", *north(postal, gap_km), ""), (2, "Bukit Pasir", "Johor", *north(r, -60), "")]
        for place_list in (two, two[::-1]):
            places = table([("84300", "Bukit Pasir", "Johor", *postal)], place_list)
            assert places.place_point("Johor", "BUKIT PASIR", [r]) is None
            assert places.far_town("Johor", "BUKIT PASIR", [r], postal) is far, (gap_km, place_list[0][0])
            d = districts({"Johor|Batu Pahat": r}, places)
            kind = "district" if far else "town"
            assert anchor("Johor", "Batu Pahat", "TAMAN BUKIT PASIR", places, d)["kind"] == kind, (gap_km, place_list[0][0])


def test_found_shops_place_a_town_before_the_far_town_rule():
    """Found shops first, also for a far town's name: should 2 shops OpenStreetMap found that write TAMAN BUKIT PASIR
    agree on a point 8 km from where Batu Pahat's shops are, that point is the town's, though the list's only Bukit
    Pasir is far (dropped) and the table's point 13.7 km from it; an old row there is not wrong. Without them, the
    district (test_a_far_towns_table_point_is_not_used_either)."""
    places, d, postal = bukit_pasir(13.7)
    assert places.far_town("Johor", "BUKIT PASIR", [BATU_PAHAT_SHOPS], postal)
    assert anchor("Johor", "Batu Pahat", "NO 9, TAMAN BUKIT PASIR", places, d)["kind"] == "district"
    spot = north(BATU_PAHAT_SHOPS, 8)
    places.learn(found_shops("Johor", "Batu Pahat", "NO 1, TAMAN BUKIT PASIR", [north(spot, -0.2), north(spot, 0.2)]))
    a = anchor("Johor", "Batu Pahat", "NO 9, TAMAN BUKIT PASIR", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Bukit Pasir", "found") and a["point"] == pytest.approx(spot)
    shop = {"premise_code": "21036", "premise": "KEDAI", "state": "Johor", "district": "Batu Pahat",
            "address": "NO 9, TAMAN BUKIT PASIR"}
    assert gp.wrong_by(cache_row(21036, spot, "town", "Batu Pahat", "Johor", "Bukit Pasir"), shop, places, d) is None


def test_an_old_row_within_10_km_of_a_far_towns_table_point_is_wrong():
    """An old Bukit Pasir row 9.9 km from the far town's table point is at that point (wrong); 10.1 km from it, and
    23 km from where the district's shops are, it is judged by its distance, as before (it stays)."""
    r = BATU_PAHAT_SHOPS
    postal = north(r, 33)
    places = table([("84300", "Bukit Pasir", "Johor", *postal)], [(1, "Bukit Pasir", "Johor", *north(r, 43.5), "")])
    d = districts({"Johor|Batu Pahat": r}, places)
    shop = {"premise_code": "21036", "premise": "KEDAI", "state": "Johor", "district": "Batu Pahat",
            "address": "NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR,"}
    for gap_km, why in ((9.9, "place"), (10.1, None)):
        row = cache_row(21036, north(postal, -gap_km), "town", "Batu Pahat", "Johor", "Bukit Pasir")
        assert gp.wrong_by(row, shop, places, d) == why, gap_km


def test_far_town_uses_the_evidence_place_point_was_asked_with():
    """The district's own town whose only place lies 85 km out (beyond even its 80 km), with the table's point 15 km
    from that place: a far town for the own town's evidence (strong, own), as place_point dropped it then. A W.P. Kuala
    Lumpur shop's own town whose only place is Selangor's (a NEIGHBOURS place: strong evidence only): an old row at the
    table's point is at that far town's (wrong), though it lies within the own town's 80 km of the district's shops."""
    place = north(KUDAT_CENTRE, -85)
    kudat = table([("89050", "Kudat", "Sabah", *north(place, 15))], [(1, "Kudat", "Sabah", *place, "")])
    assert kudat.place_point("Sabah", "KUDAT", [KUDAT_CENTRE], True, True) is None
    assert kudat.far_town("Sabah", "KUDAT", [KUDAT_CENTRE], north(place, 15), True, True)
    r = (3.15, 101.70)
    place = north(r, 85)
    postal = north(place, -15)
    places = table([("52990", "Sungai Jauh", "Wilayah Persekutuan Kuala Lumpur", *postal)], [(1, "Sungai Jauh", "Selangor", *place, "")])
    d = districts({f"{KL}|Sungai Jauh": r}, places)
    shop = {"premise_code": "1", "premise": "KEDAI", "state": KL, "district": "Sungai Jauh", "address": "NO 1, SUNGAI JAUH"}
    assert gp.km(*postal, *r) < gp.STRONG_KM
    assert gp.wrong_by(cache_row(1, postal, "town", "Sungai Jauh", KL, "Sungai Jauh"), shop, places, d) == "place"


def test_how_names_of_a_smaller_place_are_read():
    """A name that names a smaller place hides the shorter names in it (ARA DAMANSARA, near the town, is not also the
    far DAMANSARA), and so does one left out (the estate TAMAN SRI KEMAYAN is not KEMAYAN); a district name of more
    words than any postal town's is no smaller place, nor a part of it; a postal town of the NEIGHBOURS states (BATU
    CAVES, Selangor, in a W.P. Kuala Lumpur address) is none either. Estates written TMN, subdistricts after DAERAH
    and roads (JLN. X) name none; a comma after JALAN ends the road, but not one after MUKIM: 21772 ('KAMPUNG JAYA
    MUKIM,CHUKAI') writes Mukim Chukai. A word of the address is part of one postal town at most, as the written town is
    read (the longest name first, then the first written): of two names that overlap where written (made up: TANAH
    RATA and RATA BEREMBUN in 'TANAH RATA BEREMBUN') the first is the town's, and BEREMBUN may still name a smaller
    place (at worst a veto too many: 'location unknown')."""
    pj = (3.10, 101.60)
    places = table([("47300", "Petaling Jaya", "Selangor", *pj), ("28000", "Tasek", "Pahang", 3.5, 102.4)],
                   [(1, "Petaling Jaya", "Selangor", *pj, ""), (2, "Ara Damansara", "Selangor", *north(pj, 3), ""),
                    (3, "Damansara", "Selangor", *north(pj, 20), ""), (4, "Sri Kemayan", "Pahang", 3.6, 102.4, ""),
                    (5, "Kemayan", "Pahang", 3.7, 102.4, "")])
    assert [w for w, _ in places.smaller_places("NO 1, ARA DAMANSARA, PETALING JAYA", "Selangor", "Petaling")] == ["ARA DAMANSARA"]
    a = anchor("Selangor", "Petaling", "NO 1, ARA DAMANSARA, PETALING JAYA", places, districts({"Selangor|Petaling": pj}, places))
    assert (a["kind"], a["label"]) == ("town", "Petaling Jaya")
    assert places.smaller_places("NO 1, TAMAN SRI KEMAYAN, TASEK", "Pahang", "Temerloh") == []
    assert [w for w, _ in places.smaller_places("NO 1, KEMAYAN, TASEK", "Pahang", "Temerloh")] == ["KEMAYAN"]
    for address in ("NO 1, TMN KEMAYAN, TASEK", "NO 1, TMN. KEMAYAN", "DAERAH KEMAYAN", "NO 1, JLN. KEMAYAN",
                    "KAMPUNG JAYA MUKIM,KEMAYAN", "NO 1, DAERAH, KEMAYAN"):
        assert places.smaller_places(address, "Pahang", "Temerloh") == [], address
    assert [w for w, _ in places.smaller_places("NO 1, JALAN, KEMAYAN", "Pahang", "Temerloh")] == ["KEMAYAN"]
    rata = gp.Places(postal_rows([("39000", "Tanah Rata", "Pahang", 4.47, 101.38), ("39010", "Rata Berembun", "Pahang", 4.50, 101.40)]),
                     place_rows([(1, "Berembun", "Pahang", 4.60, 101.40, "")]))
    assert [w for w, _ in rata.smaller_places("NO 1, TANAH RATA BEREMBUN", "Pahang", "Cameron Highlands")] == ["BEREMBUN"]
    assert rata.smaller_places("NO 1, RATA BEREMBUN, TANAH RATA", "Pahang", "Cameron Highlands") == []
    penang = gp.Places(postal_rows([("14000", "Bukit Mertajam", "Pulau Pinang", 5.36, 100.46)]),
                       place_rows([(1, "Seberang Perai", "Pulau Pinang", 5.40, 100.40, "")]))
    assert penang.smaller_places("NO 1, SEBERANG PERAI TENGAH", "Pulau Pinang", "Seberang Perai Tengah") == []
    assert [w for w, _ in penang.smaller_places("NO 1, SEBERANG PERAI", "Pulau Pinang", "Timur Laut")] == ["SEBERANG PERAI"]
    kl = gp.Places(postal_rows([("68100", "Batu Caves", "Selangor", 3.24, 101.68), ("52100", "Kepong", KL, 3.21, 101.64)]),
                   place_rows([(1, "Batu Caves", "Kuala Lumpur", 3.24, 101.68, "")]))
    assert kl.smaller_places("NO 1, BATU CAVES, KEPONG", KL, "Kepong") == []


# Every decided generic address word (KG and KAMPONG are read as KAMPUNG before any name is matched), the street words
# (JALAN, LORONG, PERSIARAN and the others of STREET_WORDS) included.
DECIDED_GENERIC = ("TAMAN", "KAMPUNG", "PEKAN", "BANDAR", "KOTA", "LOT", "NO", "BATU", "BUKIT", "SUNGAI", "SG", "SIMPANG",
                   "CHABANG", "CABANG", "JAYA", "BARU", "UTAMA", "INDAH", "PERDANA", "SATU", "DUA", "TIGA", "EMPAT", "LIMA",
                   "ENAM", "TUJUH", "LAPAN", "SEMBILAN", "SEPULUH", "KEDAI", "BALAI", "KILANG", "PEJABAT", "BANGUNAN",
                   "MEDAN", "GERAI", "MASJID", "SURAU", "SEKOLAH", "STESEN", "BAWAH", "ATAS", "DALAM", "LUAR", "HILIR",
                   "LAMA", "PANTAI", "LEMBAH", "KOLAM", "MELAYU", "BAHAGIA", "MAWAR",
                   "JALAN", "JLN", "LORONG", "LRG", "PERSIARAN", "LEBUH", "LEBUHRAYA")


def test_names_made_of_generic_address_words_name_no_smaller_place():
    """A place of the list named only with generic address words ('Bukit Lot', 'Kedai': GeoNames has a 'Kedai' in
    Sarawak, a 'Pantai' in Negeri Sembilan) is no smaller place: 'KEDAI NO 5' or 'TINGKAT BAWAH' names no village. One
    word that is not generic makes it one. The street words count too: 9 places of the 2026 list are named only with
    them and other generic or common words ('Kampong Lorong' in Kedah, 'Kampung Lebuh' in Pahang, 'Kampung Jalan
    Baharu' in Perak, Pulau Pinang and Terengganu)."""
    for word in DECIDED_GENERIC:
        other = "SUNGAI" if word == "BUKIT" else "BUKIT"
        names = [f"{other} {word}".title()] + ([word.title()] if len(word) >= 4 else [])
        places = gp.Places(postal_rows([("06000", "Jitra", "Kedah", 6.0, 100.4)]),
                           place_rows([(i, n, "Kedah", 6.1, 100.4, "") for i, n in enumerate(names)]))
        address = ", ".join(n.upper() for n in names) + ", JITRA"
        assert places.smaller_places(address, "Kedah", "Kubang Pasu") == [], word
    places = gp.Places(postal_rows([("06000", "Jitra", "Kedah", 6.0, 100.4)]), place_rows([(1, "Bukit Tenang", "Kedah", 6.1, 100.4, "")]))
    assert [w for w, _ in places.smaller_places("NO 1, BUKIT TENANG, JITRA", "Kedah", "Kubang Pasu")] == ["BUKIT TENANG"]
    assert set(DECIDED_GENERIC) <= gp.GENERIC_WORDS


def test_a_postcodes_place_written_as_one_word_is_written():
    """'83000 BATUPAHAT' writes 83000's place, Batu Pahat (a name of 2 words written as one): the place is written, so a
    written town does not beat it. '83000 PARIT RAJA' writes another postal town and not Batu Pahat."""
    places = table([("83000", "Batu Pahat", "Johor", 1.85, 102.93), ("86400", "Parit Raja", "Johor", 1.87, 103.10)], None)
    assert not places.place_unwritten("83000", "LOT 1, 83000 BATUPAHAT, JOHOR", "Johor", "Batu Pahat")
    assert places.place_unwritten("83000", "LOT 1, 83000 PARIT RAJA, JOHOR", "Johor", "Batu Pahat")


def test_before_any_search_a_written_towns_wrong_row_never_becomes_an_unwritten_postcode_places_row(tmp_path):
    """12352 ('..., 34100 IPOH') with an old Ipoh row 15 km from Ipoh's own point (wrong): before any search it takes
    the written town's anchor (Ipoh's point), not 34100's point at Selama, though that lies within 35 km of where
    Kinta's shops are and a postcode is more precise than a town."""
    selama = north(KINTA_SHOPS, 34)
    postal = [("30000", "Ipoh", "Perak", *IPOH), ("34100", "Selama", "Perak", *selama)]
    place_list = [(1, "Ipoh", "Perak", *IPOH, ""), (2, "Selama", "Perak", *selama, "")]
    rows, st = retry_run(tmp_path, "run", {("Perak", "Kinta"): [(12352, "KEDAI", "NO 1, OFF JALAN KG BERCHAM, 34100 IPOH")]},
                         [cache_row(12352, north(IPOH, 15), "town", "Kinta", "Perak", "Ipoh", tries=0, last="")],
                         {"Perak|Kinta": KINTA_SHOPS}, postal, place_list, minutes=0)
    r = rows["12352"]
    assert (r["precision"], r["query"], st["corrected_offline"]) == ("town", "Ipoh", 1)
    assert (float(r["lat"]), float(r["lon"])) == pytest.approx(IPOH, abs=1e-5)


def test_a_row_judged_wrong_by_the_distance_check_with_no_anchor_becomes_a_district_row(tmp_path):
    """A wrong point is never kept, whatever judged it: an old postcode row 200 km from where Kerian's shops are (a lot
    number read as another state's postcode: the distance check) whose address writes no postcode or town becomes a
    district row before any search."""
    rows, st = retry_run(tmp_path, "run", {("Perak", "Kerian"): [(7, "KEDAI 7", "NO 7, KAMPUNG TELUK")]},
                         [cache_row(7, (3.21, 101.64), "postcode", "Kerian", "Perak", "52100", tries=0, last="")],
                         {"Perak|Kerian": KERIAN_CENTRE}, POSTAL, PLACES, minutes=0)
    assert (rows["7"]["precision"], rows["7"]["query"], st["corrected_offline"]) == ("district", "Kerian, Perak", 1)


# Round 2 rules both ways and at each place they are used: several smaller places, homonyms, no references, the written
# town's row, the district's name.

def test_any_smaller_place_that_rules_a_town_out_is_enough():
    """An address that names two smaller places (smaller_places gives the longer name first, wherever it is written):
    the town is ruled out when either rules it out. Kampung Benoni (made up) 6 km from Papar rules nothing out, while
    Kinarut, 16 km away, rules Papar out: no Papar, in both orders; nor with Kampung Benoni 16 km out and a Kinarut 6
    km from Papar. With both within 10 km of Papar, Papar."""
    for benoni_km, kinarut, kind in ((6, KINARUT, "district"), (-16, north(PAPAR, 6), "district"), (6, north(PAPAR, -6), "town")):
        places = table(PAPAR_POSTAL, [(1, "Papar", "Sabah", *PAPAR, ""), (2, "Kinarut", "Sabah", *kinarut, ""),
                                      (3, "Kampung Benoni", "Sabah", *north(PAPAR, benoni_km), "")])
        d = districts({"Sabah|Papar": PAPAR_SHOPS}, places)
        for address in ("LOT 1, KG BENONI, KINARUT, PAPAR", "LOT 1, KINARUT, KG BENONI, PAPAR"):
            assert [w for w, _ in places.smaller_places(address, "Sabah", "Papar")] == ["KAMPUNG BENONI", "KINARUT"]
            assert anchor("Sabah", "Papar", address, places, d)["kind"] == kind, (benoni_km, address)


def test_a_near_place_of_the_written_name_rules_a_town_out_whatever_its_homonyms():
    """21246 ('...KINARUT,PAPAR') with a second Kinarut 60 km out (another village of that name): the Kinarut 16 km from
    Papar, within 35 km of where Papar's shops are, still rules Papar out, whichever the list writes first, and an old
    row at Papar is wrong."""
    address = "TINGKAT 1-2, LOT 72,,THE PALM SQUARE COMMERCIAL CENTRE KINARUT,PAPAR"
    shop = {"premise_code": "21246", "premise": "KEDAI", "state": "Sabah", "district": "Papar", "address": address}
    two = [(2, "Kinarut", "Sabah", *KINARUT, ""), (3, "Kinarut", "Sabah", *north(PAPAR_SHOPS, -60), "")]
    for kinaruts in (two, two[::-1]):
        places = table(PAPAR_POSTAL, [(1, "Papar", "Sabah", *PAPAR, "")] + kinaruts)
        d = districts({"Sabah|Papar": PAPAR_SHOPS}, places)
        assert anchor("Sabah", "Papar", address, places, d)["kind"] == "district", kinaruts[0][-3:-1]
        assert gp.wrong_by(cache_row(21246, PAPAR, "town", "Papar", "Sabah", "Papar"), shop, places, d) == "place"


def test_no_veto_where_nothing_says_where_the_districts_shops_are():
    """A district whose centre is shared with another district's (a fallback answer: not trusted) and that has no found
    shops has no references: nothing to check a smaller place against, so '...KINARUT,PAPAR' keeps Papar (the postcode
    table's point, as before the place list); its untrusted centre does not rule it out."""
    places = table(PAPAR_POSTAL, [(1, "Papar", "Sabah", *PAPAR, ""), (2, "Kinarut", "Sabah", *KINARUT, "")])
    d = districts({"Sabah|Papar": PAPAR_SHOPS, "Sabah|Kinabatangan": PAPAR_SHOPS}, places)
    assert d.references("Sabah", "Papar") == [] and d.centre("Sabah", "Papar") == pytest.approx(PAPAR_SHOPS)
    a = anchor("Sabah", "Papar", "LOT 72, KINARUT, PAPAR", places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Papar", "rows") and a["point"] == pytest.approx(PAPAR_POSTAL[0][3:])


def test_before_any_search_a_district_row_gets_its_postcode_though_the_address_writes_another_town(tmp_path):
    """A written town beats an unwritten postcode place, for a row, only when that row is the written town's: a shop
    whose row is a district row ('..., 34100 IPOH', 34100 being Selama's, with a point of its own 26 km from where
    Kinta's shops are) gets the postcode's point before any search, as a new shop does (1148, '06660 ALOR STAR' where
    the table names 06660 Alor Setar, in a run from the older file)."""
    selama = north(KINTA_SHOPS, 20)
    postal = [("30000", "Ipoh", "Perak", *IPOH), ("34100", "Selama", "Perak", *selama), ("34100", "Selama", "Perak", *north(selama, 12))]
    place_list = [(1, "Ipoh", "Perak", *IPOH, ""), (2, "Selama", "Perak", *north(selama, 30), "")]
    rows, st = retry_run(tmp_path, "run", {("Perak", "Kinta"): [(12352, "KEDAI", "NO 1, OFF JALAN KG BERCHAM, 34100 IPOH")]},
                         [cache_row(12352, KINTA_SHOPS, "district", "Kinta", "Perak", "Kinta, Perak", tries=0, last="")],
                         {"Perak|Kinta": KINTA_SHOPS}, postal, place_list, minutes=0)
    assert (rows["12352"]["precision"], rows["12352"]["query"], st["upgraded_offline"]) == ("postcode", "34100", 1)


def test_a_town_written_after_the_districts_own_town_is_no_written_towns_row():
    """4169 ('NO. 48-49,MAIN BAZAAR,97100 SEBAUH,BINTULU,SARAWAK,') in district Sebauh writes Sebauh; Bintulu after it is
    the division, the wider area, as the written town is read: a 'Bintulu' town row is no written town's row (a retry
    may replace it), a 'Sebauh' row is one."""
    sebauh, bintulu = (3.11667, 113.26667), (3.16667, 113.03333)   # the 2026 list's points
    places = table([("97100", "Sebauh", "Sarawak", 2.7811, 112.6702), ("97000", "Bintulu", "Sarawak", 3.1906, 113.0901)],
                   [(1, "Sebauh", "Sarawak", *sebauh, ""), (2, "Bintulu", "Sarawak", *bintulu, "")])
    shop = {"premise_code": "4169", "premise": "LAU LIONG KEE", "state": "Sarawak", "district": "Sebauh",
            "address": "NO. 48-49,MAIN BAZAAR,97100 SEBAUH,BINTULU,SARAWAK,"}
    assert places.written_towns(shop["address"], "Sarawak", "Sebauh") == {"SEBAUH"}
    assert not gp.written_town_row(cache_row(4169, bintulu, "town", "Sebauh", "Sarawak", "Bintulu"), shop, places)
    assert gp.written_town_row(cache_row(4169, sebauh, "town", "Sebauh", "Sarawak", "Sebauh"), shop, places)


KOTA_PADAWAN_SHOPS, SIBURAN, PADAWAN = (1.4277, 110.3256), (1.36667, 110.4), (1.16428, 110.25876)   # 2026 points


def test_the_districts_name_rules_out_no_town_where_anchors_and_old_rows_are_judged():
    """21654 ('...,Jalan Penrissen & Pekan Batu 10,,Kota Padawan,  Siburan, Sarawak') in district Kota Padawan: the
    list's Padawan, 27 km from Siburan and 30 km from where the district's shops are, is part of the district's name
    written there, so it rules nothing out: the shop gets Siburan's own point, an old row there is not wrong, and an
    old postcode row at a rough point of Siburan's (made up: 15 km from the town) is judged by Siburan's point."""
    address = "Lot 1682,Block 71,121, KCLD,,Jalan Penrissen & Pekan Batu 10,,Kota Padawan,  Siburan, Sarawak"
    rough = north(SIBURAN, -15)
    places = table([("94200", "Siburan", "Sarawak", *rough)],
                   [(1, "Siburan", "Sarawak", *SIBURAN, ""), (2, "Padawan", "Sarawak", *PADAWAN, "Kampung Padawan|Pedawan")])
    d = districts({"Sarawak|Kota Padawan": KOTA_PADAWAN_SHOPS}, places)
    assert gp.km(*PADAWAN, *SIBURAN) > gp.ANCHOR_KM and gp.km(*PADAWAN, *KOTA_PADAWAN_SHOPS) < gp.CHECK_KM
    assert places.smaller_places(address, "Sarawak", "Kota Padawan") == []
    a = anchor("Sarawak", "Kota Padawan", address, places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Siburan", "place")
    shop = {"premise_code": "21654", "premise": "KEDAI", "state": "Sarawak", "district": "Kota Padawan", "address": address}
    assert gp.wrong_by(cache_row(21654, SIBURAN, "town", "Kota Padawan", "Sarawak", "Siburan"), shop, places, d) is None
    postcode_row = cache_row(21654, rough, "postcode", "Kota Padawan", "Sarawak", "94200")
    assert gp.wrong_by(postcode_row, {**shop, "address": "LOT 1, KOTA PADAWAN, 94200 SIBURAN"}, places, d) == "place"
