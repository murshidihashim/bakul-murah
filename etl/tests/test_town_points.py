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
    town is still wrong."""
    old_pt = (5.66284, 102.63425)
    assert gp.km(*old_pt, *ANAK_IKAN) < 3 and gp.km(*JERTIH, *ANAK_IKAN) > 20 and gp.km(*old_pt, *JERTIH) > gp.ANCHOR_KM
    assert gp.ONE_POINT_ROW_KM < gp.km(5.6111, 102.5729, 5.6795, 102.5851) < gp.ONE_POINT_KM
    plist = [(1736405, "Jertih", "Terengganu", *JERTIH, "Jerteh"), (6862226, "Kampung Anak Ikan", "Terengganu", *ANAK_IKAN, "")]
    places = table(JERTEH_POSTAL, plist)
    d = districts({"Terengganu|Besut": BESUT_CENTRE}, places)
    shop = {"premise_code": "17730", "premise": "PKT", "state": "Terengganu", "district": "Besut",
            "address": "CAWANGAN KAMPUNG ANAK IKAN,LOT 7870 - 7875. KAMPUNG ANAK IKAN JABI,22020 JERTEHTERENGGANU"}
    a = gp.anchor_for(shop, places, d)
    assert (a["kind"], a["label"], a["via"]) == ("town", "Jerteh", "postcode town") and a["point"] == pytest.approx(JERTIH)
    row = cache_row(17730, old_pt, "postcode", "Besut", "Terengganu", "22020", tries=0, last="")
    assert gp.wrong_by(row, shop, places, d) is None
    tight = table([(pc, "Jerteh", "Terengganu", 5.6795, 102.5851) for pc in ("22000", "22010", "22020")], plist)
    d = districts({"Terengganu|Besut": BESUT_CENTRE}, tight)
    assert gp.wrong_by(row, shop, tight, d) == "place"
    assert gp.wrong_by(cache_row(1, (5.7300, 102.4900), "postcode", "Besut", "Terengganu", "22020"), shop, tight, d) is None


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
    """The tolerance at its edges: the row within 10 km of the place, the place within 35 km of the town's
    postcode-table point, and that point within 35 km of where the district's shops are. Else the row is
    judged by its own distance, as before."""
    r = HULU_PERAK_SHOPS

    def why(postal_km, place_km, row_km):
        p = table([("33900", "Sungai Jauh", "Perak", *north(r, postal_km))], [(1, "Sungai Jauh", "Perak", *north(r, place_km), "")])
        shop = {"premise_code": "1", "premise": "KEDAI", "state": "Perak", "district": "Hulu Perak", "address": "NO 1, SUNGAI JAUH"}
        return gp.wrong_by(cache_row(1, north(r, row_km), "town", "Hulu Perak", "Perak", "Sungai Jauh"), shop, p,
                           districts({"Perak|Hulu Perak": r}, p))
    assert why(20.0, 54.0, 54.0) is None and why(20.0, 56.0, 56.0) == "district"    # place 34 / 36 km from the table point
    assert why(20.0, 54.0, 63.9) is None and why(20.0, 54.0, 64.1) == "district"    # row 9.9 / 10.1 km from the place
    assert why(34.0, 60.0, 60.0) is None and why(36.0, 60.0, 60.0) == "district"    # table point 34 / 36 km out


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
