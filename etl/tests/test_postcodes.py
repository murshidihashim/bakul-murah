"""Tests for the shoppers' postcode table: GeoNames points checked against the shops OpenStreetMap
found (build_dataset.check_postcodes), and what etl/check_data.py says about it. No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_dataset as bd  # noqa: E402
import check_data as cd  # noqa: E402
import geocode_premises as gp  # noqa: E402
import test_check as tc  # noqa: E402
import test_etl as te  # noqa: E402

KM_PER_DEG = 6371.0 * math.pi / 180   # km per degree of latitude, as bd.km measures it
GN_HEAD = "postcode,place,state,lat,lon,accuracy\n"
GEO_HEAD = "premise_code,lat,lon,precision,query,district,state,postcode,tries,last_tried\n"


def north(p, d_km):
    """The point d_km due north of p (south when negative)."""
    return (p[0] + d_km / KM_PER_DEG, p[1])


def east(p, d_km):
    """The point d_km due east of p (west when negative), along p's parallel."""
    return (p[0], p[1] + d_km / (KM_PER_DEG * math.cos(math.radians(p[0]))))


def write_geonames(path, rows, bom=False):
    """rows: (postcode, place, state, lat, lon)."""
    text = GN_HEAD + "".join(f"{pc},{place},{state},{lat},{lon},3\n" for pc, place, state, lat, lon in rows)
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def write_cache(path, shops, bom=False):
    """shops: (premise_code, lat, lon, precision, postcode)."""
    text = GEO_HEAD + "".join(f"{c},{la},{lo},{prec},q,D,S,{pc},1,\n" for c, la, lo, prec, pc in shops)
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def run_check(tmp_path, gn_rows, shops=(), name="t"):
    gn = write_geonames(tmp_path / f"{name}_postcodes.csv", gn_rows)
    geo = write_cache(tmp_path / f"{name}_geo.csv", shops)
    return bd.check_postcodes(bd.load_postcode_rows(gn), bd.load_geo(geo))


def r4(p):
    return [round(p[0], 4), round(p[1], 4)]


def as_read(p):
    """A shop point as load_geo reads it back from the cache (5 decimals)."""
    return round(p[0], 5), round(p[1], 5)


# ---------- when shops agree ----------

def test_agreed_point_is_the_geocoders_rule():
    """Shops agree when at least 2, and more than half, lie within 10 km of the median of them all;
    the point is then the median of those. The geocoder checks its own postcode anchors this way."""
    p = (3.0, 101.5)
    cases = [
        ([], None),
        ([p], None),                                                  # 1 shop
        ([p, north(p, 25)], None),                                    # 2 that disagree: 12.5 km each way
        ([p, north(p, 19.8)], (north(p, 9.9)[0], p[1])),              # 2 that agree: 9.9 km each way
        ([p, north(p, 1), north(p, 50)], (north(p, 0.5)[0], p[1])),   # a majority: 2 of 3
        ([p, north(p, 9.5), north(p, 11.5), north(p, 40)], None),     # exactly half (2 of 4) is not enough
        ([p, north(p, 1), north(p, 2), north(p, 50), north(p, 100)], (north(p, 1)[0], p[1])),   # 3 of 5
        ([p, north(p, 9.9), north(p, 19.8)], (north(p, 9.9)[0], p[1])),   # 9.9 km from the median: agree
        ([p, north(p, 10.1), north(p, 20.2)], None),                      # 10.1 km: only the middle one
        # Uneven: the median of those that agree (1 km north), not their mean (2.33 km north).
        ([p, north(p, 1), north(p, 6)], (north(p, 1)[0], p[1])),
        ([north(p, 50), p, north(p, 6), north(p, 1)], (north(p, 1)[0], p[1])),   # 3 of 4 agree
    ]
    for pts, want in cases:
        got = bd.agreed_point(pts)
        assert (got is None) == (want is None), (pts, got)
        if want:
            assert got == pytest.approx(want, abs=1e-9)
    assert bd.ANCHOR_KM == 10.0
    # The geocoder's own copy of the rule, where this checkout has it, gives the same answers.
    theirs = getattr(gp, "_agreed", None)
    if theirs is not None:
        for pts, _ in cases:
            assert theirs(pts) == bd.agreed_point(pts)


def test_a_postcode_is_placed_at_the_shops_that_agree(tmp_path):
    muar, found = (1.9909, 102.823), (2.05, 102.57)   # GeoNames point; where OpenStreetMap found its shops
    tangkak = (2.27, 102.54)
    # Placed at the median of the 3 that agree (found), not at their mean (2.33 km north of found).
    shops = [(1, *north(found, -1), "name", "84000"), (2, *found, "address", "84000"),
             (3, *north(found, 6), "Name", "84000"),
             (4, 2.30, 102.90, "name", "84000"),       # 45 km off: the other 3 still agree (3 of 4)
             (5, 1.99, 102.82, "postcode", "84000"),   # an anchor, not a shop OpenStreetMap found
             (6, 1.99, 102.82, "town", "84000"), (7, 1.99, 102.82, "district", "84000"),
             (8, tangkak[0], tangkak[1] + 0.005, "name", "84900"), (9, tangkak[0], tangkak[1] - 0.005, "address", "84900"),
             (10, 2.0, 102.6, "name", "84999"), (11, 2.0, 102.6, "name", "84999")]   # 84999: not in GeoNames
    res = run_check(tmp_path, [("84000", "Muar", "Johor", *muar), ("84900", "Tangkak", "Johor", *north(tangkak, 1))],
                    shops)
    assert res["postcodes"] == {"84000": r4(found), "84900": r4(tangkak)}   # 84999 is never added
    assert res["checks"] == {"from_shops": 2, "moved": 1, "set_aside": 0}   # 84900 moved only 1 km
    (d, pc, name), = res["moves"]
    assert (pc, name) == ("84000", "Muar (Johor)") and d == pytest.approx(bd.km(*muar, *found))
    assert res["set_aside"] == []


def test_one_shop_two_that_disagree_or_anchors_only_keep_the_geonames_point(tmp_path):
    a, b, c = (3.1, 101.6), (4.6, 101.1), (5.4, 100.3)
    shops = [(1, *north(a, 20), "name", "10000"),                                   # 1 shop
             (2, *north(b, 12.5), "name", "20000"), (3, *north(b, -12.5), "address", "20000"),   # 25 km apart
             (4, *north(c, 15), "postcode", "30000"), (5, *north(c, 15), "town", "30000")]       # anchors only
    gn = [("10000", "Alpha", "Selangor", *a), ("20000", "Beta", "Perak", *b), ("30000", "Gamma", "Pulau Pinang", *c)]
    res = run_check(tmp_path, gn, shops)
    assert res["postcodes"] == {"10000": r4(a), "20000": r4(b), "30000": r4(c)}
    assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 0} and res["moves"] == []
    assert res["kept"] == []


def test_two_shops_far_apart_keep_a_geonames_point_one_of_them_backs(tmp_path):
    """Only 2 shops agree and they lie more than 10 km apart: their middle can be in neither town
    (30200 Ipoh: a shop in Ipoh and one in Batu Gajah; 42700 Banting: 2 shops 18 km apart, their middle
    9 km from each). When one of them is within 5 km of the GeoNames point, the postcode keeps that point
    ("kept"). Else, with a place list, they do not place the postcode: the other steps judge it, and here
    it keeps its GeoNames point too. Without a place list no town's point can take over, so they place it
    at their middle, as before the place list. Each postcode here has first 3 digits of its own, so no
    other judges it."""
    g = {pc: (2.0 + 0.5 * i, 102.0) for i, pc in enumerate(["30200", "31210", "32220", "33230", "34240",
                                                             "35250", "36260"])}
    km_north = {"30200": (3.7, -8.9), "31210": (4.9, 15.8), "32220": (5.1, 16.0), "33230": (1, 10.9),
                "34240": (1, 11.1), "35250": (1, 8, 12), "36260": (28, 41)}
    at = {pc: [as_read(north(g[pc], d)) for d in ds] for pc, ds in km_north.items()}
    shops = [(10 * i + k, *p, "name", pc) for i, (pc, ps) in enumerate(at.items()) for k, p in enumerate(ps)]
    gn = [(pc, f"Town {pc}", "Perak", *p) for pc, p in g.items()]
    elsewhere = [("Kota Lain", "", 6.0, 116.0, "PPL", "Sabah", 100)]   # a place list that has none of these towns
    res = run_places(tmp_path, gn, elsewhere, shops)
    pts = res["postcodes"]

    def middle(pc):   # the median of pc's shops, as the build reads them
        return r4((statistics.median(p[0] for p in at[pc]), statistics.median(p[1] for p in at[pc])))

    assert pts["30200"] == r4(g["30200"])                       # 12.6 km apart, one 3.7 km away: kept
    assert pts["31210"] == r4(g["31210"])                       # one 4.9 km away: kept
    assert pts["32220"] == r4(g["32220"]) != middle("32220")    # 5.1 km: not close enough, and not placed
    assert pts["33230"] == middle("33230")                      # 9.9 km apart: they agree closely enough
    assert pts["34240"] == r4(g["34240"])                       # 10.1 km apart: kept
    assert pts["35250"] == middle("35250") == r4(at["35250"][1])   # 3 shops: their median, however wide
    assert pts["36260"] == r4(g["36260"]) != middle("36260")    # neither near the GeoNames point: not placed
    assert res["checks"] == {"from_shops": 2, "moved": 2, "to_place": 0, "set_aside": 0}
    assert res["kept"] == [
        ("30200", "Town 30200 (Perak)", "its 2 shops lie 12.6 km apart and one of them is 3.7 km from that point"),
        ("31210", "Town 31210 (Perak)", "its 2 shops lie 10.9 km apart and one of them is 4.9 km from that point"),
        ("34240", "Town 34240 (Perak)", "its 2 shops lie 10.1 km apart and one of them is 1.0 km from that point")]
    assert [m[1] for m in res["moves"]] == ["35250", "33230"]
    assert bd.BACKED_KM == 5.0
    # Without a place list, 32220 and 36260 go to the middle of their 2 shops; nothing else changes.
    plain = run_check(tmp_path, gn, shops, name="plain")
    assert plain["postcodes"] == {**pts, "32220": middle("32220"), "36260": middle("36260")}
    assert plain["checks"] == {"from_shops": 4, "moved": 4, "set_aside": 0} and plain["kept"] == res["kept"]
    assert [m[1] for m in plain["moves"]] == ["36260", "32220", "35250", "33230"]
    # A point a shop backs is never set aside, though its place's other postcodes lie 40 km away.
    town = (4.6, 101.08)
    gn = [("30200", "Ipoh", "Perak", *north(town, 40)), ("30100", "Ipoh", "Perak", *town),
          ("30250", "Ipoh", "Perak", *north(town, 1)), ("30300", "Ipoh", "Perak", *north(town, -1))]
    shops = [(1, *north(town, 43), "name", "30200"), (2, *north(town, 31), "address", "30200")]
    res = run_check(tmp_path, gn, shops, name="backed")
    assert res["postcodes"]["30200"] == r4(north(town, 40)) and res["checks"]["set_aside"] == 0
    assert [k[0] for k in res["kept"]] == ["30200"]
    res = run_check(tmp_path, gn, name="no_shops")          # without the shops it is set aside
    assert "30200" not in res["postcodes"] and res["checks"]["set_aside"] == 1 and res["kept"] == []


def test_two_shops_exactly_anchor_km_apart_are_not_far_apart(tmp_path, monkeypatch):
    """A shop backs the GeoNames point only when the 2 shops that agree lie more than ANCHOR_KM apart:
    exactly that far apart, the postcode is placed at their middle."""
    p = (3.0, 101.5)
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", [("30200", "Ipoh", "Perak", *p)]))
    a, b = as_read(north(p, 3.7)), as_read(north(p, -8.9))
    geo = bd.load_geo(write_cache(tmp_path / "geo.csv", [(1, *a, "name", "30200"), (2, *b, "address", "30200")]))
    apart = bd.km(*a, *b)
    monkeypatch.setattr(bd, "ANCHOR_KM", apart)
    res = bd.check_postcodes(rows, geo)
    assert res["kept"] == [] and res["postcodes"]["30200"] == r4(((a[0] + b[0]) / 2, p[1]))
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(apart, 0))
    res = bd.check_postcodes(rows, geo)
    assert [k[0] for k in res["kept"]] == ["30200"] and res["postcodes"]["30200"] == r4(p)


def test_three_shops_that_agree_are_placed_at_their_median_in_any_order(tmp_path):
    """The exception above is for exactly 2 shops. 3 shops that agree are placed at their median, also
    when the first 2 the cache lists lie more than 10 km apart and one of them is near the GeoNames point."""
    g = (2.0, 102.0)
    for order in ((1, 12, 8), (12, 1, 8), (8, 1, 12)):
        shops = [(i + 1, *as_read(north(g, d)), "name", "30250") for i, d in enumerate(order)]
        res = run_check(tmp_path, [("30250", "Town", "Perak", *g)], shops, name="order_" + "_".join(map(str, order)))
        assert res["postcodes"] == {"30250": r4(as_read(north(g, 8)))}, order
        assert res["kept"] == [] and res["checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0}


def test_two_shops_more_than_anchor_km_apart_place_nothing(tmp_path, monkeypatch):
    """42700 Banting: its 2 shops agree (each 9 km from their middle) but lie 18 km apart, neither within
    5 km of its GeoNames point. Their middle is in neither town, so with a place list they do not place it:
    it keeps its GeoNames point, or goes to its place's point by the place list's rules, as without the
    shops. 2 shops exactly ANCHOR_KM apart still place it at their middle; 9.9 km apart they do, 10.1 km
    apart not. Without a place list no town's point can take over, and they place it at their middle."""
    p = (2.8, 101.5)
    gn = [("42700", "Banting", "Selangor", *north(p, -11)), ("43000", "Kajang", "Selangor", 3.0, 101.8),
          ("44000", "Kuala Kubu Baru", "Selangor", 3.6, 101.6)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", gn))
    a, b = as_read(north(p, 0.3)), as_read(north(p, -17.9))
    c, d = as_read(north((3.0, 101.8), 6)), as_read(north((3.0, 101.8), 15.9))   # 9.9 km apart
    e, f = as_read(north((3.6, 101.6), 6)), as_read(north((3.6, 101.6), 16.1))   # 10.1 km apart
    shops = [(1, *a, "name", "42700"), (2, *b, "name", "42700"), (3, *c, "name", "43000"),
             (4, *d, "address", "43000"), (5, *e, "name", "44000"), (6, *f, "address", "44000")]
    geo = bd.load_geo(write_cache(tmp_path / "geo.csv", shops))
    apart = bd.km(*a, *b)
    # A place list that has none of these towns: the 2-shop limit applies, and nothing else changes.
    other = bd.load_places(write_places(tmp_path / "other.csv", [("Kota Lain", "", 6.0, 116.0, "PPL", "Sabah", 100)]))
    res = bd.check_postcodes(rows, geo, other, {})
    assert res["postcodes"]["42700"] == r4(north(p, -11)) and res["kept"] == []
    assert res["postcodes"]["43000"] == r4(((c[0] + d[0]) / 2, c[1]))   # 9.9 km apart: placed at their middle
    assert res["postcodes"]["44000"] == [3.6, 101.6]                    # 10.1 km apart: not
    assert res["checks"] == {"from_shops": 1, "moved": 1, "to_place": 0, "set_aside": 0}
    places = bd.load_places(write_places(tmp_path / "pl.csv", [("Banting", "", *p, "PPL", "Selangor", 93497)]))
    res = bd.check_postcodes(rows, geo, places, {})
    assert res["postcodes"]["42700"] == r4(p) and [m[1] for m in res["to_place"]] == ["42700"]
    # Without a place list, the 2 shops place 42700 and 44000 at their middle all the same.
    res = bd.check_postcodes(rows, geo)
    assert res["postcodes"] == {"42700": r4(((a[0] + b[0]) / 2, a[1])), "43000": r4(((c[0] + d[0]) / 2, c[1])),
                                "44000": r4(((e[0] + f[0]) / 2, e[1]))}
    assert res["checks"] == {"from_shops": 3, "moved": 3, "set_aside": 0} and res["kept"] == []
    monkeypatch.setattr(bd, "ANCHOR_KM", apart)   # exactly that far apart: they place it at their middle
    res = bd.check_postcodes(rows, geo, other, {})
    assert res["postcodes"]["42700"] == r4(((a[0] + b[0]) / 2, a[1])) and res["checks"]["from_shops"] == 3
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(apart, 0))
    res = bd.check_postcodes(rows, geo, other, {})
    assert res["postcodes"]["42700"] == r4(north(p, -11)) and res["checks"]["from_shops"] == 2


# ---------- points far from their place ----------

def test_a_wild_point_is_set_aside_when_its_place_has_3_other_postcodes(tmp_path):
    k = (3.8, 103.3)
    wild = ("25608", "Kuantan", "Pahang", *north(k, 163))
    two = [("25000", "Kuantan", "Pahang", *k), ("25050", "KUANTAN", "Pahang", *north(k, 2))]   # any case
    selangor = [("26060", "Kuantan", "Selangor", *k), ("27000", "Kuantan", "Selangor", *k)]   # another state
    res = run_check(tmp_path, two + selangor + [wild], name="two")
    assert "25608" in res["postcodes"] and res["checks"]["set_aside"] == 0   # only 2 others in its place
    res = run_check(tmp_path, two + selangor + [("25100", "Kuantan", "Pahang", *north(k, -2)), wild], name="three")
    assert "25608" not in res["postcodes"] and len(res["postcodes"]) == 5
    assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 1}
    assert res["set_aside"] == [("25608", "Kuantan (Pahang)",
                                 "163.0 km from the median of the 3 other postcodes of its place")]


def test_a_postcode_is_judged_by_every_place_it_names(tmp_path):
    """26070's first row names a place with no other postcode; its second names Kuantan, whose 3 other
    postcodes lie 40 km away. It is judged by all of them, so it is set aside."""
    k = (3.8, 103.3)
    gn = [("26070", "Sungai Lembing Lama", "Pahang", *north(k, 40)), ("26070", "Kuantan", "Pahang", *north(k, 40)),
          ("25000", "Kuantan", "Pahang", *k), ("25050", "Kuantan", "Pahang", *north(k, 1)),
          ("25100", "Kuantan", "Pahang", *north(k, -1))]
    res = run_check(tmp_path, gn)
    assert res["set_aside"] == [("26070", "Kuantan (Pahang) / Sungai Lembing Lama (Pahang)",
                                 "40.0 km from the median of the 3 other postcodes of its place")]


def test_a_place_whose_other_postcodes_share_one_point_judges_nothing(tmp_path):
    """GeoNames puts 98057, 98058 and 98059 Baram on one point near Sri Aman, 445 km from Marudi.
    Postcodes on one shared point are one guess, so 98050 is not judged by them and keeps its point.
    With another postcode on a point of its own, or one of the three placed from its shops, the
    place has 2 different points and is judged as usual."""
    shared, baram = (1.3526, 111.5176), (2.0768, 112.3837)
    gn = [("98050", "Baram", "Sarawak", *baram)] + [(f"9805{i}", "Baram", "Sarawak", *shared) for i in (7, 8, 9)]
    res = run_check(tmp_path, gn)
    assert res["postcodes"] == {"98050": r4(baram), **{f"9805{i}": r4(shared) for i in (7, 8, 9)}}
    assert res["checks"]["set_aside"] == 0 and bd.PLACE_POINTS == 2
    why = f"{bd.km(*baram, *shared):.1f} km from the median of the 4 other postcodes of its place"
    res = run_check(tmp_path, gn + [("98060", "Baram", "Sarawak", *north(shared, 2))], name="fourth")
    assert res["set_aside"] == [("98050", "Baram (Sarawak)", why)]
    a, b = as_read(north(shared, 3)), as_read(north(shared, 3.5))
    res = run_check(tmp_path, gn, [(1, *a, "name", "98058"), (2, *b, "address", "98058")], name="shops")
    assert res["postcodes"]["98058"] == r4(((a[0] + b[0]) / 2, a[1]))
    assert res["set_aside"] == [("98050", "Baram (Sarawak)", why.replace("4 other", "3 other"))]


def test_rows_on_one_point_stay_one_point_after_averaging(tmp_path):
    """GeoNames can list several rows for one postcode on one point (in the 2026-11-16 table, 4 postcodes
    have 2 rows each on one point). Their average can differ from that point in the last bit, but it is the
    same point, so 98050 is still not judged."""
    shared, baram = (1.3526, 112.3837), (2.0768, 113.3837)
    gn = [("98050", "Baram", "Sarawak", *baram), ("98057", "Baram", "Sarawak", *shared),
          ("98057", "Long Lama", "Sarawak", *shared), ("98057", "Long Bedian", "Sarawak", *shared),
          ("98058", "Baram", "Sarawak", *shared), ("98059", "Baram", "Sarawak", *shared)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "postcodes_my.csv", gn))
    assert bd._mean_point([r[2] for r in rows["98057"]]) != rows["98058"][0][2]   # the average is off in the last bit
    res = bd.check_postcodes(rows, {})
    assert res["postcodes"]["98050"] == r4(baram) and res["checks"]["set_aside"] == 0
    assert res["postcodes"]["98057"] == res["postcodes"]["98058"] == r4(rows["98058"][0][2])


def test_the_30_km_edge(tmp_path):
    p = (2.2, 102.25)
    gn = [(f"7500{i}", "Melaka", "Melaka", *p) for i in range(3)]
    gn += [("75100", "Melaka", "Melaka", *north(p, 29.9)), ("75200", "Melaka", "Melaka", *north(p, 30.1))]
    res = run_check(tmp_path, gn)
    assert "75100" in res["postcodes"] and "75200" not in res["postcodes"]   # more than 30 km: set aside
    assert res["set_aside"] == [("75200", "Melaka (Melaka)", "30.1 km from the median of the 4 other postcodes of its place")]
    assert bd.PLACE_KM == 30.0 and bd.PLACE_MIN == 3


def compact_place(prefix, town, at_km, more=(7, 12)):
    """GeoNames rows: 8 postcodes on the town's point, one more postcode at each of `more` km north, and the
    postcode <prefix>99 at_km north (south when negative). With more=(7, 12), 9 in 10 of the others lie
    within 7 km of their median, the town's point."""
    gn = [(f"{prefix}{i:02d}", "Kota", "Sabah", *town) for i in range(10, 18)]
    gn += [(f"{prefix}{20 + i:02d}", "Kota", "Sabah", *north(town, d)) for i, d in enumerate(more)]
    return gn + [(f"{prefix}99", "Kota", "Sabah", *north(town, at_km))]


def test_a_point_far_out_of_a_compact_place_is_set_aside_beyond_15_km(tmp_path):
    """In a compact place (at least 10 other postcodes, 9 in 10 of them within 7.5 km of their median), a
    point more than 15 km from that median is set aside, not only one more than 30 km away. 88000 Kota
    Kinabalu, the city centre, lay 18.4 km out, and the app showed the shops of Putatan. A sprawling or
    small place keeps 30 km."""
    town = (5.98, 116.07)
    res = run_check(tmp_path, compact_place("880", town, -15.1), name="out")
    assert "88099" not in res["postcodes"] and len(res["postcodes"]) == 10
    assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 1}
    assert res["set_aside"] == [("88099", "Kota (Sabah)", "15.1 km from the median of the 10 other postcodes of its "
                                                          "place, 90% of which lie within 7.0 km of it")]
    assert "88099" in run_check(tmp_path, compact_place("880", town, -14.9), name="in")["postcodes"]
    res = run_check(tmp_path, compact_place("880", town, -40), name="far")   # over 30 km: the usual reason
    assert res["set_aside"] == [("88099", "Kota (Sabah)", "40.0 km from the median of the 10 other postcodes of its place")]
    # Not compact: the 9th nearest of the 10 others lies 7.6 km out. Then only more than 30 km counts.
    assert "88099" in run_check(tmp_path, compact_place("880", town, -29.9, (7.6, 12)), name="wide")["postcodes"]
    res = run_check(tmp_path, compact_place("880", town, -30.1, (7.6, 12)), name="wide_far")
    assert [a[0] for a in res["set_aside"]] == ["88099"] and "90%" not in res["set_aside"][0][2]
    # Small: 9 others (7 on the town's point) are too few to call the place compact.
    small = [r for r in compact_place("880", town, -20) if r[0] != "88010"]
    assert "88099" in run_check(tmp_path, small, name="small")["postcodes"]
    # Sprawling, like Kuantan: 10 others 2 km apart in a row, 9 in 10 within 9 km of their median.
    k = (3.8, 103.3)
    row = [(f"2{i:04d}", "Kuantan", "Pahang", *north(k, 2 * i)) for i in range(10)]
    assert "29999" in run_check(tmp_path, row + [("29999", "Kuantan", "Pahang", *north(k, 9 + 18.3))],
                                name="sprawl")["postcodes"]
    # A postcode placed at its shops is never set aside, in a compact place too: 16.25 km out.
    shops = [(1, *as_read(north(town, -16)), "name", "88099"), (2, *as_read(north(town, -16.5)), "address", "88099")]
    res = run_check(tmp_path, compact_place("880", town, -20), shops, name="shops")
    assert res["postcodes"]["88099"] == r4(((shops[0][1] + shops[1][1]) / 2, shops[0][2]))
    assert res["checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0}
    assert (bd.COMPACT_MIN, bd.COMPACT_SHARE, bd.COMPACT_KM) == (10, 0.9, 15.0)


def test_the_compact_limits_are_exact(tmp_path, monkeypatch):
    """A point exactly COMPACT_KM from the median is kept, and a place whose 9th nearest of 10 others lies
    exactly COMPACT_KM / 2 out is compact."""
    town = (5.98, 116.07)
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", compact_place("880", town, -20)))
    others = [r[0][2] for pc, r in rows.items() if pc != "88099"]
    mid = bd._median_point(others)
    d, core = bd.km(*rows["88099"][0][2], *mid), sorted(bd.km(*p, *mid) for p in others)[8]
    monkeypatch.setattr(bd, "COMPACT_KM", d)
    assert "88099" in bd.check_postcodes(rows, {})["postcodes"]
    monkeypatch.setattr(bd, "COMPACT_KM", math.nextafter(d, 0))
    assert "88099" not in bd.check_postcodes(rows, {})["postcodes"]
    monkeypatch.setattr(bd, "COMPACT_KM", 2 * core)        # the 9th nearest lies exactly half of it out
    assert [a[0] for a in bd.check_postcodes(rows, {})["set_aside"]] == ["88099"]
    monkeypatch.setattr(bd, "COMPACT_KM", math.nextafter(2 * core, 0))
    assert bd.check_postcodes(rows, {})["set_aside"] == []


def test_one_shared_point_does_not_make_a_place_compact(tmp_path):
    """Postcodes on one shared GeoNames point are one guess. 10 others on one point and one far away do not
    make a compact place, so a point 20 km out is judged by the 30 km limit only. With 2 different points
    in the core, the place is compact and the point is set aside."""
    town = (5.98, 116.07)
    gn = [(f"880{i:02d}", "Kota", "Sabah", *town) for i in range(10, 20)]
    gn += [("88050", "Kota", "Sabah", *north(town, 100)), ("88099", "Kota", "Sabah", *north(town, -20))]
    assert "88099" in run_check(tmp_path, gn)["postcodes"]
    two = [r for r in gn if r[0] != "88010"] + [("88010", "Kota", "Sabah", *north(town, 1))]
    res = run_check(tmp_path, two, name="two")
    assert "88099" not in res["postcodes"] and "90% of which lie within 1.0 km of it" in dict(
        (a[0], a[2]) for a in res["set_aside"])["88099"]


def test_the_compact_share_counts_up_to_the_next_whole_postcode(tmp_path):
    """90% of 11 others is 9.9 postcodes, so the 10th nearest decides; 90% of 15 is 13.5, so the 14th. Here
    the others near the town lie on 2 points 0.5 km apart and the rest 8 km out. With one too few near
    the town, the deciding one lies 8 km out: not compact, and a point 20 km out stays."""
    town = (5.98, 116.07)
    x = ("88999", "Kota", "Sabah", *north(town, 20))
    for n in (11, 15):
        need = math.ceil(0.9 * n)   # 10 of 11, 14 of 15
        for inside, compact in ((need - 1, False), (need, True)):
            gn = [(f"88{i:03d}", "Kota", "Sabah", *(town if i % 2 else north(town, 0.5))) for i in range(inside)]
            gn += [(f"88{i:03d}", "Kota", "Sabah", *north(town, -8)) for i in range(inside, n)]
            res = run_check(tmp_path, gn + [x], name=f"c{n}_{inside}")
            assert ("88999" not in res["postcodes"]) == compact, (n, inside)
            if compact:
                assert res["set_aside"][-1][0] == "88999" and "90% of which lie within" in res["set_aside"][-1][2]


def test_each_limit_is_the_last_distance_that_counts(tmp_path, monkeypatch):
    """A shop exactly ANCHOR_KM from the median of them all agrees, a shop exactly BACKED_KM from the
    GeoNames point backs it, a move of exactly MOVED_KM is not counted and a point exactly PLACE_KM from
    its place is kept. Each limit is set to a measured distance, so rounding cannot hide > from >=."""
    p = (3.0, 101.5)
    # PLACE_KM: 3 others on 2 different points, and one postcode 40 km north of them.
    gn = [("40000", "Town", "Selangor", *p), ("40100", "Town", "Selangor", *p),
          ("40200", "Town", "Selangor", *north(p, 1)), ("40300", "Town", "Selangor", *north(p, 40))]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "place.csv", gn))
    d = bd.km(*rows["40300"][0][2], *bd._median_point([rows[o][0][2] for o in ("40000", "40100", "40200")]))
    monkeypatch.setattr(bd, "PLACE_KM", d)
    assert "40300" in bd.check_postcodes(rows, {})["postcodes"]
    monkeypatch.setattr(bd, "PLACE_KM", math.nextafter(d, 0))
    assert [a[0] for a in bd.check_postcodes(rows, {})["set_aside"]] == ["40300"]
    # MOVED_KM: 2 shops that agree, 3.25 km from the GeoNames point.
    a, b = as_read(north(p, 3)), as_read(north(p, 3.5))
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "moved.csv", [("50000", "City", "Selangor", *p)]))
    geo = bd.load_geo(write_cache(tmp_path / "moved_geo.csv", [(1, *a, "name", "50000"), (2, *b, "name", "50000")]))
    d = bd.km(*rows["50000"][0][2], *bd._median_point([g[:2] for g in geo.values()]))
    monkeypatch.setattr(bd, "MOVED_KM", d)
    assert bd.check_postcodes(rows, geo)["checks"] == {"from_shops": 1, "moved": 0, "set_aside": 0}
    monkeypatch.setattr(bd, "MOVED_KM", math.nextafter(d, 0))
    assert bd.check_postcodes(rows, geo)["checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0}
    # BACKED_KM: 2 shops 12.6 km apart, the nearer 3.7 km from the GeoNames point.
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "backed.csv", [("30200", "Ipoh", "Perak", *p)]))
    geo = bd.load_geo(write_cache(tmp_path / "backed_geo.csv", [(1, *as_read(north(p, 3.7)), "name", "30200"),
                                                                (2, *as_read(north(p, -8.9)), "address", "30200")]))
    near = min(bd.km(*g[:2], *rows["30200"][0][2]) for g in geo.values())
    monkeypatch.setattr(bd, "BACKED_KM", near)
    assert [k[0] for k in bd.check_postcodes(rows, geo)["kept"]] == ["30200"]
    monkeypatch.setattr(bd, "BACKED_KM", math.nextafter(near, 0))
    other = bd.load_places(write_places(tmp_path / "other.csv", [("Kota Lain", "", 6.0, 116.0, "PPL", "Sabah", 100)]))
    res = bd.check_postcodes(rows, geo, other, {})   # not backed: 2 shops more than ANCHOR_KM apart place nothing
    assert res["kept"] == [] and res["checks"]["from_shops"] == 0 and res["postcodes"]["30200"] == r4(p)
    res = bd.check_postcodes(rows, geo)   # ... unless there is no place list: then their middle
    mid = bd._median_point([g[:2] for g in geo.values()])
    assert res["kept"] == [] and res["checks"]["from_shops"] == 1 and res["postcodes"]["30200"] == r4(mid)
    # ANCHOR_KM: 2 shops on each of 2 points 16 km apart, so all 4 lie 8 km from the median of them all.
    top, bottom = as_read(north(p, 36)), as_read(north(p, 20))
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "anchor.csv", [("60000", "Kota", "Johor", *p)]))
    geo = bd.load_geo(write_cache(tmp_path / "anchor_geo.csv", [(1, *top, "name", "60000"), (2, *top, "name", "60000"),
                                                                (3, *bottom, "name", "60000"),
                                                                (4, *bottom, "address", "60000")]))
    mid = bd._median_point([g[:2] for g in geo.values()])
    apart = [bd.km(*g[:2], *mid) for g in geo.values()]
    monkeypatch.setattr(bd, "ANCHOR_KM", max(apart))
    assert bd.check_postcodes(rows, geo)["postcodes"]["60000"] == r4(mid)                 # all 4 agree
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(min(apart), 0))
    assert bd.check_postcodes(rows, geo)["postcodes"]["60000"] == r4(rows["60000"][0][2])   # none agree


def test_the_place_is_judged_by_checked_points_and_shop_points_are_never_set_aside(tmp_path):
    """Two postcodes of the place sit 200 km away in GeoNames, but their shops put them back in town.
    The place's median uses those checked points, so a postcode 5 km from town stays."""
    town, far = (5.33, 103.14), north((5.33, 103.14), 200)
    gn = [("20000", "Kuala Terengganu", "Terengganu", *town),
          ("20050", "Kuala Terengganu", "Terengganu", *far), ("20100", "Kuala Terengganu", "Terengganu", *far),
          ("20200", "Kuala Terengganu", "Terengganu", *north(town, 5))]
    shops = [(1, *town, "name", "20050"), (2, *north(town, 0.5), "address", "20050"),
             (3, *town, "name", "20100"), (4, *north(town, -0.5), "name", "20100")]
    res = run_check(tmp_path, gn, shops)
    assert set(res["postcodes"]) == {"20000", "20050", "20100", "20200"}
    assert res["postcodes"]["20200"] == r4(north(town, 5))
    assert res["checks"] == {"from_shops": 2, "moved": 2, "set_aside": 0}
    assert [m[1] for m in res["moves"]] == ["20100", "20050"]   # 200.25 and 199.75 km: largest first
    # Judged by GeoNames points alone, the median of the others lies 195-200 km from each of the four.
    no_shops = run_check(tmp_path, gn, name="no_shops")
    assert {pc for pc, _, _ in no_shops["set_aside"]} == {"20000", "20050", "20100", "20200"}


def test_postcodes_whose_rows_lie_far_apart_keep_their_average(tmp_path):
    maran = (3.59, 102.77)
    jengka = north(maran, 33)                          # 26500 names two places 33 km apart
    at2 = (1.9, 103.3)
    raub = (3.8, 101.85)
    gn = [("26500", "Bandar Pusat Jengka", "Pahang", *jengka), ("26500", "Maran", "Pahang", *maran),
          ("26400", "Maran", "Pahang", *north(maran, 1)), ("26410", "Maran", "Pahang", *north(maran, -1)),
          ("26420", "Maran", "Pahang", *maran),
          # 81920's average is 90 km from the other postcodes of its places: set aside like any other.
          ("81920", "Ayer Tawar 2", "Johor", *at2), ("81920", "Ayer Tawar 5", "Johor", *north(at2, 180)),
          ("81900", "Ayer Tawar 2", "Johor", *at2), ("81910", "Ayer Tawar 2", "Johor", *north(at2, 1)),
          ("81930", "Ayer Tawar 2", "Johor", *north(at2, -1)),
          # 3 rows at 0, 0 and 30 km: the average (10 km north), not the median (0 km).
          ("27600", "Raub", "Pahang", *raub), ("27600", "Sungai Ruan", "Pahang", *raub),
          ("27600", "Dong", "Pahang", *north(raub, 30))]
    res = run_check(tmp_path, gn)
    assert res["postcodes"]["26500"] == r4(((maran[0] + jengka[0]) / 2, maran[1]))   # 16.5 km from Maran
    assert res["postcodes"]["27600"] == r4(north(raub, 10)) == bd.load_postcodes(tmp_path / "t_postcodes.csv")["27600"]
    assert res["set_aside"] == [("81920", "Ayer Tawar 2 (Johor) / Ayer Tawar 5 (Johor)",
                                 "90.0 km from the median of the 3 other postcodes of its place")]
    assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 1}


# ---------- far from the confirmed postcodes that share its first digits ----------

def test_a_postcode_far_from_the_confirmed_postcodes_of_its_first_digits_is_set_aside(tmp_path, monkeypatch):
    """GeoNames puts 98057 Baram on a point in Betong, 431 km from Miri. Shops place 98000 and 98007 in Miri:
    they are confirmed, and they share 98057's first 3 digits. A postcode more than 60 km from each of 2 or
    more confirmed postcodes that share its first 3 digits is set aside (98057, and 98059 at 60.1 km), and
    one that close to one of them stays (98050, 59.9 km). Confirmed postcodes are never set aside (98008,
    placed from its shops 100 km away), and those of other first digits judge nothing (98100)."""
    miri = (4.4, 114.0)
    gn = [("98000", "Miri", "Sarawak", *north(miri, 5)), ("98007", "Miri", "Sarawak", *north(miri, -5)),
          ("98008", "Miri", "Sarawak", *miri), ("98050", "Baram", "Sarawak", *north(miri, -59.9)),
          ("98057", "Baram", "Sarawak", *north(miri, -431)), ("98059", "Baram", "Sarawak", *north(miri, -60.1)),
          ("98100", "Lutong", "Sarawak", *north(miri, -100))]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", gn))
    far = as_read(north(miri, 100))
    shops = [(1, *miri, "name", "98000"), (2, *miri, "address", "98000"), (3, *miri, "name", "98007"),
             (4, *miri, "name", "98007"), (5, *far, "name", "98008"), (6, *far, "address", "98008")]
    geo = bd.load_geo(write_cache(tmp_path / "geo.csv", shops))
    res = bd.check_postcodes(rows, geo)
    assert res["postcodes"] == {"98000": list(miri), "98007": list(miri), "98008": r4(far),
                                "98050": r4(north(miri, -59.9)), "98100": r4(north(miri, -100))}
    assert res["checks"] == {"from_shops": 3, "moved": 3, "set_aside": 2, "far_from_prefix": 2}
    assert res["set_aside"] == [
        ("98057", "Baram (Sarawak)", "431.0 km from the nearest of the 3 confirmed postcodes that start 980"),
        ("98059", "Baram (Sarawak)", "60.1 km from the nearest of the 3 confirmed postcodes that start 980")]
    assert (bd.PREFIX_DIGITS, bd.PREFIX_MIN, bd.PREFIX_KM) == (3, 2, 60.0)
    # Set aside in steps 3 and 4 alike, listed by postcode: 99050 is 40 km from the 3 others of Limbang.
    b = (4.75, 115.0)
    limbang = [("99050", "Limbang", "Sarawak", *north(b, 40)), ("99000", "Limbang", "Sarawak", *b),
               ("99010", "Limbang", "Sarawak", *b), ("99020", "Limbang", "Sarawak", *north(b, 1))]
    both = bd.check_postcodes(bd.load_postcode_rows(write_geonames(tmp_path / "both.csv", limbang + gn)), geo)
    assert [a[0] for a in both["set_aside"]] == ["98057", "98059", "99050"]
    assert both["checks"] == {"from_shops": 3, "moved": 3, "set_aside": 3, "far_from_prefix": 2}
    # The limit is exact: a point exactly PREFIX_KM from the nearest confirmed postcode stays.
    d = bd.km(*rows["98059"][0][2], *miri)
    monkeypatch.setattr(bd, "PREFIX_KM", d)
    assert [a[0] for a in bd.check_postcodes(rows, geo)["set_aside"]] == ["98057"]
    monkeypatch.setattr(bd, "PREFIX_KM", math.nextafter(d, 0))
    assert [a[0] for a in bd.check_postcodes(rows, geo)["set_aside"]] == ["98057", "98059"]
    monkeypatch.undo()
    # One confirmed postcode judges nothing (the count is then not in checks); PREFIX_MIN of them do.
    one = bd.load_geo(write_cache(tmp_path / "one.csv", shops[:2]))
    res = bd.check_postcodes(rows, one)
    assert res["checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0} and res["set_aside"] == []
    monkeypatch.setattr(bd, "PREFIX_MIN", 1)
    assert [a[0] for a in bd.check_postcodes(rows, one)["set_aside"]] == ["98057", "98059"]
    monkeypatch.undo()
    # A postcode kept at its GeoNames point by one of its 2 shops is confirmed too: with 98000 in Miri and
    # 98007 kept 5 km south, 98059 lies 55.1 km from 98007, and 98057 426 km.
    kept = shops[:2] + [(3, *as_read(north(miri, -6)), "name", "98007"), (4, *as_read(north(miri, -18)), "name", "98007")]
    res = bd.check_postcodes(rows, bd.load_geo(write_cache(tmp_path / "kept.csv", kept)))
    assert [k[0] for k in res["kept"]] == ["98007"] and res["set_aside"] == [
        ("98057", "Baram (Sarawak)", f"{bd.km(*rows['98057'][0][2], *north(miri, -5)):.1f} km from the nearest of "
                                     "the 2 confirmed postcodes that start 980")]


def test_postcodes_moved_to_or_near_their_places_point_are_confirmed_too(tmp_path):
    """With the place list, a postcode moved to its place's point, or already within 10 km of it, is confirmed
    too. Miri's 98000 and 98007 lie near the town's point, so 98057 Baram, 431 km away, is set aside (the
    place list has no Baram). 89050 Kudat lies 9 km from Kudat's point, 183 km from Keningau: confirmed, it
    stays, and the Kudat postcodes moved to Kudat's point are confirmed too."""
    miri, keningau = (4.4, 114.0), (5.34, 116.16)
    kudat = north(keningau, 183)
    gn = [("98000", "Miri", "Sarawak", *north(miri, 5)), ("98007", "Miri", "Sarawak", *north(miri, -6)),
          ("98057", "Baram", "Sarawak", *north(miri, -431)),
          ("89000", "Keningau", "Sabah", *keningau), ("89050", "Kudat", "Sabah", *north(kudat, 9))]
    gn += [(f"8900{i}", "Keningau", "Sabah", *north(keningau, 15)) for i in (7, 8, 9)]
    gn += [(f"8905{i}", "Kudat", "Sabah", *north(kudat, 25)) for i in (7, 8, 9)]
    shops = [(1, *keningau, "name", "89000"), (2, *keningau, "address", "89000")]
    places = [("Miri", "", *miri, "PPLA2", "Sarawak", 300000), ("Keningau", "", *keningau, "PPLA3", "Sabah", 170000),
              ("Kudat", "", *kudat, "PPLA3", "Sabah", 30000)]
    res = run_places(tmp_path, gn, places, shops)
    assert sorted(m[1] for m in res["to_place"]) == ["89007", "89008", "89009", "89057", "89058", "89059"]
    assert res["set_aside"] == [("98057", "Baram (Sarawak)", f"{bd.km(*north(miri, -431), *north(miri, -6)):.1f} "
                                 "km from the nearest of the 2 confirmed postcodes that start 980")]
    assert res["postcodes"]["89050"] == r4(north(kudat, 9))
    assert res["checks"] == {"from_shops": 1, "moved": 0, "to_place": 6, "set_aside": 1, "far_from_prefix": 1}


def test_a_group_of_first_digits_whose_confirmed_postcodes_lie_in_2_towns_judges_by_the_nearest(tmp_path):
    """The postcodes that start 770 lie in 2 towns 150 km apart, Alpha and Beta, and shops place one
    postcode of each (77000, 77050). The median of the 2 would lie between the towns, 75 km from each, and
    set aside 77010 and 77060, 2 km from their towns' confirmed postcodes; the app would then place 77060
    in Alpha. Judged by the nearest confirmed postcode, both stay. With 2 confirmed postcodes in Alpha and
    1 in Beta, 180 km apart, the median lies in Alpha, and 77050, 3 km from Beta's, stays too."""
    a = (3.0, 101.5)
    b = north(a, -150)

    def shops_at(code, p, pc):
        return [(code, *as_read(north(p, 0.2)), "name", pc), (code + 1, *as_read(north(p, -0.2)), "address", pc)]

    gn = [("77000", "Alpha", "Johor", *a), ("77010", "Alpha", "Johor", *north(a, 2)),
          ("77050", "Beta", "Johor", *b), ("77060", "Beta", "Johor", *north(b, 2))]
    res = run_check(tmp_path, gn, shops_at(1, a, "77000") + shops_at(3, b, "77050"))
    assert res["set_aside"] == [] and "far_from_prefix" not in res["checks"]
    assert res["postcodes"]["77010"] == r4(north(a, 2)) and res["postcodes"]["77060"] == r4(north(b, 2))
    b = north(a, -180)
    gn = [("77000", "Alpha", "Johor", *a), ("77001", "Alpha", "Johor", *north(a, 1)),
          ("77080", "Beta", "Johor", *b), ("77050", "Gamma", "Johor", *north(b, 3))]
    shops = shops_at(1, a, "77000") + shops_at(3, north(a, 1), "77001") + shops_at(5, b, "77080")
    res = run_check(tmp_path, gn, shops, name="majority")
    assert res["set_aside"] == [] and res["postcodes"]["77050"] == r4(north(b, 3))
    assert res["checks"] == {"from_shops": 3, "moved": 0, "set_aside": 0}
    # 61 km from Beta's confirmed postcode and 119 km from Alpha's, 77050 is set aside.
    gn[-1] = ("77050", "Gamma", "Johor", *north(b, 61))
    res = run_check(tmp_path, gn, shops, name="far")
    assert res["set_aside"] == [("77050", "Gamma (Johor)", f"{bd.km(*north(b, 61), *as_read(b)):.1f} km from the "
                                 "nearest of the 3 confirmed postcodes that start 770")]


# ---------- rows far apart ----------

def test_a_postcode_whose_rows_lie_so_far_apart_that_their_average_is_in_none_of_its_places_is_set_aside(
        tmp_path, monkeypatch):
    """GeoNames lists 81920 under Ayer Tawar 2, 3 and 4, east of Kota Tinggi, and under Ayer Tawar 5, near
    Muar, 170 km west: the average of the 4 rows lay 38 km from the nearest of them (here the fourth lies
    129 km west, and the average 32 km from the nearest). 83400 Seri Medan has 2 rows 84 km apart, so its
    average lay 42 km from each. A postcode whose point, the average of its rows, lies more than PLACE_KM
    from each of them is in none of its places, and is set aside (with or without the place list). 26500's
    2 rows lie 33 km apart: their average, 16.5 km from each, stays. A postcode placed from shops, or within
    10 km of its place's point, is confirmed, and never set aside so."""
    e = (1.72, 103.92)
    gn = [("81920", "Ayer Tawar 2", "Johor", *north(e, 2)), ("81920", "Ayer Tawar 3", "Johor", *e),
          ("81920", "Ayer Tawar 4", "Johor", *north(e, -2)), ("81920", "Ayer Tawar 5", "Johor", *east(e, -129)),
          ("83400", "Seri Medan", "Johor", *north(e, 10)), ("83400", "Sri Medan", "Johor", *north(e, 94)),
          ("26500", "Maran", "Pahang", 3.59, 102.77), ("26500", "Bandar Pusat Jengka", "Pahang", *north((3.59, 102.77), 33))]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", gn))
    avg = {pc: bd._mean_point([r[2] for r in v]) for pc, v in rows.items()}
    d = {pc: min(bd.km(*avg[pc], *r[2]) for r in v) for pc, v in rows.items()}
    assert 32 < d["81920"] < 33 and 41.9 < d["83400"] < 42.1 and 16.4 < d["26500"] < 16.6
    res = bd.check_postcodes(rows, {})
    assert res["set_aside"] == [
        ("81920", "Ayer Tawar 2 (Johor) / Ayer Tawar 3 (Johor) / Ayer Tawar 4 (Johor) / Ayer Tawar 5 (Johor)",
         f"{d['81920']:.1f} km from the nearest of its 4 GeoNames rows, whose average it is"),
        ("83400", "Seri Medan (Johor) / Sri Medan (Johor)",
         f"{d['83400']:.1f} km from the nearest of its 2 GeoNames rows, whose average it is")]
    assert res["postcodes"] == {"26500": r4(avg["26500"])}
    assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 2, "far_from_rows": 2}
    other = bd.load_places(write_places(tmp_path / "other.csv", [("Kota Lain", "", 6.0, 116.0, "PPL", "Sabah", 100)]))
    with_list = bd.check_postcodes(rows, {}, other, {})
    assert (with_list["postcodes"], with_list["set_aside"]) == (res["postcodes"], res["set_aside"])
    assert with_list["checks"] == {"from_shops": 0, "moved": 0, "to_place": 0, "set_aside": 2, "far_from_rows": 2}
    # The limit is exact: a point exactly PLACE_KM from its nearest row stays.
    monkeypatch.setattr(bd, "PLACE_KM", d["81920"])
    assert [a[0] for a in bd.check_postcodes(rows, {})["set_aside"]] == ["83400"]
    monkeypatch.setattr(bd, "PLACE_KM", math.nextafter(d["81920"], 0))
    assert [a[0] for a in bd.check_postcodes(rows, {})["set_aside"]] == ["81920", "83400"]
    monkeypatch.undo()
    # Placed from its shops, 83400 is confirmed and stays; without a set aside postcode, no count.
    shops = [(1, *as_read(north(e, 10)), "name", "83400"), (2, *as_read(north(e, 10.5)), "address", "83400")]
    res = bd.check_postcodes(rows, bd.load_geo(write_cache(tmp_path / "geo.csv", shops)))
    assert [a[0] for a in res["set_aside"]] == ["81920"] and res["postcodes"]["83400"] == r4(north(e, 10.25))
    rows2 = {pc: v for pc, v in rows.items() if pc != "81920"}
    res = bd.check_postcodes(rows2, bd.load_geo(write_cache(tmp_path / "geo.csv", shops)))
    assert res["set_aside"] == [] and res["checks"] == {"from_shops": 1, "moved": 1, "set_aside": 0}
    # Within 10 km of its place's point, a postcode is confirmed: Kota's 2 rows lie 35 km either side of it.
    k = (2.5, 102.5)
    gn = [("85000", "Kota", "Johor", *north(k, 35)), ("85000", "Kota", "Johor", *north(k, -35))]
    res = run_places(tmp_path, gn, [("Kota", "", *k, "PPL", "Johor", 5000)], name="at_place")
    assert res["set_aside"] == [] and res["postcodes"]["85000"] == r4(bd._mean_point([north(k, 35), north(k, -35)]))
    # The rows of one point are one row: 3 rows on one point 100 km from a fourth average 25 km from it.
    gn = [("86000", "Kota", "Johor", *k)] * 3 + [("86000", "Lain", "Johor", *north(k, 100))]
    res = run_check(tmp_path, gn, name="one_point")
    assert res["set_aside"] == [] and res["postcodes"]["86000"] == r4(north(k, 25))
    # With the fourth 160 km away, the average lies 40 km from the 3 and is set aside; the reason counts 4 rows.
    gn = [("87000", "Kota", "Johor", *k)] * 3 + [("87000", "Lain", "Johor", *north(k, 160))]
    res = run_check(tmp_path, gn, name="one_point_far")
    assert res["set_aside"] == [("87000", "Kota (Johor) / Lain (Johor)",
                                 "40.0 km from the nearest of its 4 GeoNames rows, whose average it is")]


# ---------- missing and messy files ----------

def test_no_cache_or_no_table(tmp_path):
    k = (3.8, 103.3)
    gn = write_geonames(tmp_path / "postcodes_my.csv", [(f"2500{i}", "Kuantan", "Pahang", *north(k, i - 1))
                                                         for i in range(3)]
                        + [("25608", "Kuantan", "Pahang", *north(k, 163))])
    geo = write_cache(tmp_path / "premise_geo.csv", [(1, *k, "name", "25608"), (2, *k, "name", "25608"),
                                                     (3, *k, "name", "99999"), (4, *k, "name", "99999")])
    rows = bd.load_postcode_rows(gn)
    for no_cache in (bd.load_geo(None), bd.load_geo(tmp_path / "missing.csv")):
        res = bd.check_postcodes(rows, no_cache)   # GeoNames alone: the wild point is still set aside
        assert set(res["postcodes"]) == {"25000", "25001", "25002"}
        assert res["checks"] == {"from_shops": 0, "moved": 0, "set_aside": 1}
    with_cache = bd.check_postcodes(rows, bd.load_geo(geo))   # its shops put 25608 back in town
    assert with_cache["postcodes"]["25608"] == r4(k) and with_cache["checks"]["from_shops"] == 1
    for no_table in (bd.load_postcode_rows(None), bd.load_postcode_rows(tmp_path / "missing.csv")):
        assert no_table == {}
        assert bd.check_postcodes(no_table, bd.load_geo(geo)) == {
            "postcodes": {}, "checks": {"from_shops": 0, "moved": 0, "set_aside": 0}, "moves": [], "set_aside": [],
            "kept": []}


def test_nan_blank_rows_and_a_byte_order_mark(tmp_path):
    gn = tmp_path / "postcodes_my.csv"
    gn.write_text("﻿" + GN_HEAD +
                  "1000,Kangar,Perlis,6.4414,100.1986,3\n"     # leading 0 lost in a spreadsheet
                  "01000,Kangar,Perlis,6.45,100.2,3\n"
                  "02000,Kuala Perlis,Perlis,nan,100.1,3\n"
                  "02100,Padang Besar,Perlis,,,3\n"
                  ",Nowhere,Perlis,6.4,100.2,3\n"
                  "03000,,Perlis,6.5,100.3,3\n"               # no place name: nothing to judge it by
                  "ABCDE,Bad,Perlis,6.4,100.2,1\n"
                  "\n", encoding="utf-8")
    geo = tmp_path / "premise_geo.csv"
    geo.write_text("﻿" + GEO_HEAD +
                   "1,6.44,100.2,name,q,Kangar,Perlis,01000,1,\n"
                   "2,6.442,100.202,Address,q,Kangar,Perlis,1000,1,\n"
                   "3,nan,100.2,name,q,Kangar,Perlis,01000,1,\n"
                   "4,,,name,q,Kangar,Perlis,01000,1,\n"
                   "5,6.6,100.4,name,q,Kangar,Perlis,,1,\n"
                   "x,6.6,100.4,name,q,Kangar,Perlis,01000,1,\n"
                   "\n", encoding="utf-8")
    rows = bd.load_postcode_rows(gn)
    assert sorted(rows) == ["01000", "03000"] and len(rows["01000"]) == 2
    assert rows["03000"] == [("", "Perlis", (6.5, 100.3))]
    res = bd.check_postcodes(rows, bd.load_geo(geo))
    assert res["postcodes"] == {"01000": [6.441, 100.201], "03000": [6.5, 100.3]}
    assert res["checks"] == {"from_shops": 1, "moved": 0, "set_aside": 0}
    assert bd.load_postcodes(gn) == {"01000": [6.4457, 100.1993], "03000": [6.5, 100.3]}


# ---------- the check only moves or removes ----------

def test_the_check_only_moves_or_removes_and_is_deterministic(tmp_path):
    rnd = random.Random(20261116)
    towns = {"Alpha": (3.0, 101.5), "Beta": (4.0, 101.0), "Gamma": (5.5, 100.4), "Delta": (2.2, 102.2)}
    gn, shops = [], []
    for i in range(120):
        name = rnd.choice(sorted(towns))
        base = towns[name]
        pc = f"{10000 + 7 * i:05d}"
        pt = north(base, rnd.uniform(40, 600)) if rnd.random() < 0.15 else (
            base[0] + rnd.uniform(-0.1, 0.1), base[1] + rnd.uniform(-0.1, 0.1))
        gn.append((pc, name, "State", *pt))
        if rnd.random() < 0.2:   # a second row, maybe of another place far away
            other = rnd.choice(sorted(towns))
            gn.append((pc, other, "State", *north(towns[other], rnd.uniform(-5, 5))))
        for _ in range(rnd.randint(0, 4)):
            spot = (base[0] + rnd.uniform(-0.15, 0.15), base[1] + rnd.uniform(-0.15, 0.15))
            shops.append((len(shops) + 1, round(spot[0], 5), round(spot[1], 5),
                          rnd.choice(["name", "address", "postcode", "town", "district"]), pc))
    shops += [(9000 + k, 3.0, 101.5, "name", "99999") for k in range(3)]   # a postcode GeoNames lacks
    for i in range(3):   # 2 shops 12 km apart, one 2 km from the GeoNames point: it keeps that point
        pc, pt = f"{20000 + i:05d}", north(towns["Alpha"], 3 * i)
        gn.append((pc, "Alpha", "State", *pt))
        shops += [(8000 + 2 * i, *as_read(north(pt, 2)), "name", pc),
                  (8001 + 2 * i, *as_read(north(pt, -10)), "address", pc)]
    gn_path = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = bd.load_geo(write_cache(tmp_path / "premise_geo.csv", shops))
    rows = bd.load_postcode_rows(gn_path)
    before = bd.load_postcodes(gn_path)
    res = bd.check_postcodes(rows, geo)
    out, c = res["postcodes"], res["checks"]

    assert list(out) == sorted(out) and set(out) <= set(before) and "99999" not in out
    removed = set(before) - set(out)
    assert removed == {pc for pc, _, _ in res["set_aside"]} and len(removed) == c["set_aside"]
    agreed, backed, split = {}, set(), set()
    for pc in before:
        found = [g[:2] for g in geo.values() if g[2] in ("name", "address") and g[3] == pc]
        if bd.agreed_point(found):
            mid = statistics.median(p[0] for p in found), statistics.median(p[1] for p in found)
            close = [p for p in found if bd.km(*p, *mid) <= 10]
            average = (statistics.mean(r[2][0] for r in rows[pc]), statistics.mean(r[2][1] for r in rows[pc]))
            if len(close) == 2 and bd.km(*close[0], *close[1]) > 10:   # 2 far apart
                if min(bd.km(*p, *average) for p in close) <= 5:   # one backs GeoNames
                    backed.add(pc)
                    continue
                split.add(pc)   # without a place list, they place it at their middle all the same
            agreed[pc] = bd.agreed_point(found)
    assert {pc for pc, _, _ in res["kept"]} == backed
    for pc, v in out.items():   # each postcode in the table: its GeoNames point, or where its shops agree
        assert v == (r4(agreed[pc]) if pc in agreed else before[pc])
        assert all(isinstance(x, float) and round(x, 4) == x for x in v) and len(v) == 2
    assert not removed & (set(agreed) | backed)              # a postcode placed from shops or backed is kept
    assert c["from_shops"] == len(agreed) and c["moved"] == len(res["moves"]) <= c["from_shops"]
    assert all(d > bd.MOVED_KM for d, _, _ in res["moves"])
    assert [m[0] for m in res["moves"]] == sorted((m[0] for m in res["moves"]), reverse=True)
    assert 0 < c["set_aside"] and 0 < c["from_shops"] and 0 < c["moved"]   # the data tries every rule
    assert 0 < len(backed) and 0 < len(split)
    # Deterministic: the same answer from postcodes and shops in the opposite order.
    assert bd.check_postcodes(dict(reversed(list(rows.items()))), dict(reversed(list(geo.items())))) == res


# ---------- the GeoNames place list ----------

PL_HEAD = "geonameid,name,asciiname,alt,lat,lon,code,state,population\n"


def write_places(path, rows, bom=False):
    """rows: (name, alt, lat, lon, code, state, population), as fetch_places.py writes them."""
    text = PL_HEAD + "".join(f"{i},{name},{name},{alt},{lat},{lon},{code},{state},{pop}\n"
                             for i, (name, alt, lat, lon, code, state, pop) in enumerate(rows, 1))
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def run_places(tmp_path, gn_rows, place_rows, shops=(), addresses=None, name="p"):
    """check_postcodes with a place list; addresses: premise code -> (state, address)."""
    gn = write_geonames(tmp_path / f"{name}_postcodes.csv", gn_rows)
    geo = write_cache(tmp_path / f"{name}_geo.csv", shops)
    places = bd.load_places(write_places(tmp_path / f"{name}_places.csv", place_rows))
    return bd.check_postcodes(bd.load_postcode_rows(gn), bd.load_geo(geo), places, addresses or {})


def test_load_places_reads_names_states_and_populations(tmp_path):
    """Names and other names as words, the state as the build compares states, and only rows with a
    point, a state and a name. A population that is blank, odd, negative or not a finite number (nan, inf,
    1e400) counts as 0."""
    rows = ("1,Kota Kinabalu,Kota Kinabalu,Api-Api|Jesselton,5.9749,116.0724,ppla,Sabah,500421\n"
            "2,Kampung Jeli,Kampung Jeli,Jeli|kampung jeli,5.7,101.84,PPL,Kelantan,nan\n"   # the name again
            "3,George Town,George Town,,5.41,100.33,PPLA,Penang,\n"
            "4,Kuala Lumpur,Kuala Lumpur,,3.14,101.69,PPLC,W.P. Kuala Lumpur,-3\n"
            "5,Bandar,Bandar,,nan,101.0,PPL,Selangor,5\n"
            "6,Bandar,Bandar,,3.0,inf,PPL,Selangor,5\n"
            "7,Bandar,Bandar,,3.0,101.0,PPL,,5\n"                # no state
            "8,,,,3.0,101.0,PPL,Selangor,5\n"                    # no name
            "9,Pekan,Pekan,,3.49,103.39,PPL,Pahang,1e3\n"
            "10,Raub,Raub,,3.79,101.86,PPL,Pahang,inf\n"         # not a finite number: 0
            "11,Bentong,Bentong,,3.52,101.91,PPL,Pahang,1e400\n"
            "\n")
    want = [("sabah", frozenset({"KOTA KINABALU"}), frozenset({"API API", "JESSELTON"}), (5.9749, 116.0724), "PPLA",
             500421),
            ("kelantan", frozenset({"KAMPUNG JELI"}), frozenset({"JELI"}), (5.7, 101.84), "PPL", 0),
            ("pulau pinang", frozenset({"GEORGE TOWN"}), frozenset(), (5.41, 100.33), "PPLA", 0),
            ("kuala lumpur", frozenset({"KUALA LUMPUR"}), frozenset(), (3.14, 101.69), "PPLC", 0),
            ("pahang", frozenset({"PEKAN"}), frozenset(), (3.49, 103.39), "PPL", 1000),
            ("pahang", frozenset({"RAUB"}), frozenset(), (3.79, 101.86), "PPL", 0),
            ("pahang", frozenset({"BENTONG"}), frozenset(), (3.52, 101.91), "PPL", 0)]
    for bom in ("", "﻿"):
        (tmp_path / "places_my.csv").write_text(bom + PL_HEAD + rows, encoding="utf-8")
        assert bd.load_places(tmp_path / "places_my.csv") == want


def test_no_place_list_or_a_broken_one_gives_the_shops_only_check(tmp_path):
    """No file, an empty one, a header only, other columns, bytes that are not UTF-8, a field too long for
    the csv module (more than 131,072 characters) or a folder: no places. The check is then the shops-only
    check, with the same result as without a place list."""
    empty, header, other, broken, huge = (tmp_path / f"{n}.csv" for n in ("empty", "header", "other", "broken", "huge"))
    empty.write_text("", encoding="utf-8")
    header.write_text(PL_HEAD, encoding="utf-8")
    other.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    broken.write_bytes(PL_HEAD.encode() + b"1,\xff\xfe,\x00,,5.0,100.0,PPL,Perak,9\n")
    huge.write_text(PL_HEAD + '1,Ipoh,Ipoh,"' + "x" * 200_000 + '",4.6,101.08,PPLA,Perak,1\n', encoding="utf-8")
    for path in (None, tmp_path / "missing.csv", empty, header, other, broken, huge, tmp_path):
        assert bd.load_places(path) == [], path
    town = (5.9258, 116.0609)
    gn = [(f"8872{i}", "Putatan", "Sabah", *north(town, -99)) for i in range(1, 4)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "gn.csv", gn))
    for places in (None, [], bd.load_places(broken)):
        res = bd.check_postcodes(rows, {}, places, {})
        assert res == bd.check_postcodes(rows, {}) and "to_place" not in res and "to_place" not in res["checks"]
        assert "far_rows" not in res
        assert res["postcodes"] == {pc: r4(north(town, -99)) for pc, *_ in gn}


def test_states_compare_as_the_geocoder_compares_them():
    """PriceCatcher, GeoNames and the place list spell some states differently."""
    pairs = [("W.P. Kuala Lumpur", "Kuala Lumpur"), ("WP Labuan", "Labuan"),
             ("Wilayah Persekutuan Putrajaya", "PUTRAJAYA"), ("Penang", "Pulau Pinang"), ("Malacca", "melaka"),
             ("Negri Sembilan", "Negeri Sembilan"), ("Johore", " Johor "), ("Trengganu", "Terengganu")]
    for a, b in pairs:
        assert bd.state_key(a) == bd.state_key(b), (a, b)
    assert bd.state_key("Perak") != bd.state_key("Perlis") and bd.state_key(None) == ""
    theirs = getattr(gp, "state_key", None)   # the geocoder's own copy, where this checkout has it
    if theirs is not None:
        for pair in pairs:
            assert [theirs(x) for x in pair] == [bd.state_key(x) for x in pair]
    assert bd.words("Sungai Siput (U)") == "SUNGAI SIPUT U" and bd.words(None) == ""


def test_a_place_point_needs_the_places_shops_or_one_notable_entry(monkeypatch):
    """With a reference (where the place's shops are): the entry by name nearest to it, within CHECK_KM;
    other names count only when no name matches. Without one: the one notable entry by name (it has a
    population or a seat-of-government code), also among entries of that name that are not notable,
    unless an entry with that name as another name lies more than ANCHOR_KM from it; with no entry by
    name, the one entry with it as another name, when that entry is notable. Entries are ((lat, lon),
    code, population)."""
    town = (4.8151, 101.0786)            # Sungai Siput Utara, other name Sungai Siput
    far = north(town, -46.5)             # 'Sungai Siput', population 49,000
    ref = north(town, 1)

    def e(pt, pop=0, code="PPL"):
        return pt, code, pop
    assert bd.place_point([e(far), e(town)], [], ref) == town      # the nearer of two
    assert bd.place_point([e(far, 49000)], [], ref) is None        # 47.5 km: a town of that name elsewhere
    assert bd.place_point([e(far)], [e(town)], ref) is None        # other names only when no name matches
    assert bd.place_point([], [e(town)], ref) == town
    assert bd.place_point([], [], ref) is None
    d = bd.km(*far, *ref)
    monkeypatch.setattr(bd, "CHECK_KM", d)
    assert bd.place_point([e(far)], [], ref) == far
    monkeypatch.setattr(bd, "CHECK_KM", math.nextafter(d, 0))
    assert bd.place_point([e(far)], [], ref) is None
    monkeypatch.undo()
    # Without a reference.
    assert bd.place_point([e(town, 33000)], [], None) == town
    assert bd.place_point([e(town)], [], None) is None             # no population and not a seat
    for code in ("PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC"):
        assert bd.place_point([e(town, 0, code)], [], None) == town, code
    for code in ("PPL", "PPLX", "PPLG", "PPLL"):
        assert bd.place_point([e(town, 0, code)], [], None) is None, code
    assert bd.place_point([e(town, 33000), e(far, 9)], [], None) is None   # 2 notable by name: which one?
    assert bd.place_point([e(town, 15031), e(far)], [], None) == town      # Engkilili: the one notable of 2
    assert bd.place_point([e(far), e(north(town, 5)), e(town, 0, "PPLA3")], [], None) == town   # ... a seat
    assert bd.place_point([e(town), e(far)], [], None) is None             # none of them notable
    assert bd.place_point([], [e(town, 24401)], None) == town             # Kerteh: the one by another name
    assert bd.place_point([], [e(town, 0, "PPLA2")], None) == town
    assert bd.place_point([], [e(town)], None) is None                    # not notable
    assert bd.place_point([], [e(town, 24401), e(far)], None) is None     # 2 by another name: which one?
    assert bd.place_point([e(far)], [e(town, 24401)], None) is None       # other names only when no name matches
    assert bd.place_point([e(far, 49000)], [e(town)], None) is None       # Sungai Siput
    assert bd.place_point([e(far, 49000), e(town)], [e(town)], None) is None   # ... also among 2 by name
    twin = north(far, 1)                                                  # the same town listed twice
    assert bd.place_point([e(far, 49000)], [e(twin)], None) == far
    monkeypatch.setattr(bd, "ANCHOR_KM", bd.km(*far, *twin))
    assert bd.place_point([e(far, 49000)], [e(twin)], None) == far
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(bd.km(*far, *twin), 0))
    assert bd.place_point([e(far, 49000)], [e(twin)], None) is None
    monkeypatch.undo()
    assert (bd.CHECK_KM, bd.ANCHOR_KM, bd.SEATS) == (35.0, 10.0, ("PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC"))


def test_an_address_names_a_place_in_whole_words_outside_longer_names():
    assert bd.names_place("LOT 1 88200 PUTATAN SABAH", "PUTATAN")
    assert not bd.names_place("TAMAN PUTATANJAYA", "PUTATAN")                # whole words only
    assert bd.names_place("LOT 1 KOTA KINABALU", "KOTA KINABALU") and not bd.names_place("KOTA BELUD", "KOTA KINABALU")
    assert not bd.names_place("36000 TELUK INTAN PERAK", "INTAN", ["TELUK INTAN"])
    assert bd.names_place("TAMAN INTAN 36000 TELUK INTAN", "INTAN", ["TELUK INTAN"])   # once outside it
    assert not bd.names_place("13300 TASEK GELUGOR", "GELUGOR", ["TASEK GELUGOR"])
    assert bd.names_place("13300 TASEK GELUGOR", "TASEK GELUGOR", ["TASEK GELUGOR LAMA"])
    assert not bd.names_place("", "PUTATAN") and not bd.names_place("PUTATAN", "")


def test_postcodes_on_one_rough_point_go_to_their_town(tmp_path):
    """Putatan's PO box postcodes share one GeoNames point 99 km from the town. The shops whose address
    names Putatan agree on where it is, and the place list has a Putatan there (and a village of that
    name 60 km away). The shops-only check leaves them: postcodes on one shared point are not judged."""
    town = (5.9258, 116.0609)
    box = north(town, -99)
    gn = [(f"8872{i}", "Putatan", "Sabah", *box) for i in range(1, 6)]
    shops = [(1, *as_read(north(town, 0.5)), "name", ""), (2, *as_read(north(town, -1)), "address", "88200"),
             (3, *as_read(north(town, 1.5)), "name", "88200"), (4, *as_read(north(town, 50)), "postcode", "")]
    addresses = {1: ("Sabah", "LOT 1, GRAND PLAZA PUTATAN"), 2: ("Sabah", "88200 PUTATAN, SABAH"),
                 3: ("Sabah", "JALAN PUTATAN,88200 PUTATAN."), 4: ("Sabah", "88200 PUTATAN")}   # 4: not found by OSM
    places = [("Putatan", "", *town, "PPL", "Sabah", 0), ("Putatan", "", *north(town, 60), "PPL", "Sabah", 0)]
    res = run_places(tmp_path, gn, places, shops, addresses)
    assert res["postcodes"] == {pc: r4(town) for pc, *_ in gn}
    assert res["checks"] == {"from_shops": 0, "moved": 0, "to_place": 5, "set_aside": 0}
    why = "all 5 GeoNames rows of its place lie within 5 km of each other"
    assert res["to_place"] == [(pytest.approx(99), pc, "Putatan (Sabah)", why) for pc, *_ in gn]
    # No shop names the town, and 2 entries by name: no place point.
    res = run_places(tmp_path, gn, places, shops, name="no_addresses")
    assert res["postcodes"] == {pc: r4(box) for pc, *_ in gn} and res["checks"]["to_place"] == 0
    assert run_check(tmp_path, gn, shops, name="shops_only")["postcodes"] == {pc: r4(box) for pc, *_ in gn}


def test_an_outlier_goes_to_its_town_and_a_spread_town_keeps_its_points(tmp_path):
    """Kuantan's GeoNames rows spread, so they keep their points, except one more than 10 km from the
    town's place point and from every postcode of the town near it. When no postcode lies near the
    place point (Gemas), each one more than 10 km out goes there."""
    k = (3.8076, 103.326)
    gn = [("25000", "Kuantan", "Pahang", *k), ("25050", "Kuantan", "Pahang", *north(k, 4)),
          ("25100", "Kuantan", "Pahang", *north(k, -4)),
          ("25150", "Kuantan", "Pahang", *north(k, 13)),      # 13 km out, 9 km from 25050: stays
          ("25200", "Kuantan", "Pahang", *north(k, 26)),      # 26 km out, 22 km from 25050: an outlier
          ("26340", "Kuantan", "Pahang", *north(k, -132))]
    res = run_places(tmp_path, gn, [("Kuantan", "", *k, "PPLA", "Pahang", 0)])
    assert res["postcodes"] == {"25000": r4(k), "25050": r4(north(k, 4)), "25100": r4(north(k, -4)),
                                "25150": r4(north(k, 13)), "25200": r4(k), "26340": r4(k)}
    assert [m[1] for m in res["to_place"]] == ["26340", "25200"]   # largest first
    assert {m[3] for m in res["to_place"]} == {"more than 10 km from each of the 3 other postcodes of its place "
                                               "near that point"}
    assert res["checks"] == {"from_shops": 0, "moved": 0, "to_place": 2, "set_aside": 0}
    shops_only = run_check(tmp_path, gn, name="shops_only")   # without the place list 26340 is only set aside
    assert [a[0] for a in shops_only["set_aside"]] == ["26340"] and shops_only["postcodes"]["25200"] == r4(north(k, 26))
    g = (2.6, 102.6)
    gn = [("73400", "Gemas", "Negeri Sembilan", *north(g, 12)), ("73420", "Gemas", "Negeri Sembilan", *north(g, -11))]
    res = run_places(tmp_path, gn, [("Gemas", "", *g, "PPL", "Negeri Sembilan", 5000)], name="gemas")
    assert res["postcodes"] == {"73400": r4(g), "73420": r4(g)}
    assert {m[3] for m in res["to_place"]} == {"no other postcode of its place lies near that point"}
    gn = [("98750", "Nanga Medamit", "Sarawak", *north(g, 300))]   # the only row of its place
    res = run_places(tmp_path, gn, [("Nanga Medamit", "", *g, "PPL", "Sarawak", 900)], name="one")
    assert res["to_place"] == [(pytest.approx(300), "98750", "Nanga Medamit (Sarawak)",
                                "its place has no other GeoNames row")]


def test_many_postcodes_none_near_their_places_point_keep_their_points_without_shops(tmp_path, monkeypatch):
    """GeoNames puts Nusajaya's postcodes 10 to 18 km from the place list's point for Iskandar Puteri (other
    name Nusajaya), spread over 15 km, and 79570 in Kedah; no shop names Nusajaya. When no other postcode lies
    within ANCHOR_KM of the place's point, a postcode goes there only when its place has fewer than
    COMPACT_MIN other postcodes (as Gemas, above) or shops that say where the place is. Here each has 10
    others: they keep their points, and step 3 sets 79570 aside, far from them. The limit is exact: with 9
    others they go, 79570 too. With shops that name Nusajaya near the point, or a postcode near it, they go."""
    g = (1.3932, 103.6232)
    c = north(g, 12)
    gn = [(f"79{i:03d}", "Nusajaya", "Johor", *east(c, i % 4)) for i in range(10)]   # on 4 points, 1 km apart
    gn += [("79570", "Nusajaya", "Johor", 6.12, 100.37)]
    places = [("Iskandar Puteri", "Nusajaya", *g, "PPL", "Johor", 575977)]
    res = run_places(tmp_path, gn, places)
    assert res["to_place"] == [] and res["checks"]["to_place"] == 0
    assert res["postcodes"] == {pc: r4((lat, lon)) for pc, _, _, lat, lon in gn[:-1]}
    mid = bd._median_point([(lat, lon) for _, _, _, lat, lon in gn[:-1]])
    assert res["set_aside"] == [("79570", "Nusajaya (Johor)", f"{bd.km(6.12, 100.37, *mid):.1f} km from the median of "
                                 "the 10 other postcodes of its place")]
    # 10 postcodes in all, so 9 others each: they go, 79570 too.
    res = run_places(tmp_path, gn[1:], places, name="nine")
    assert sorted(m[1] for m in res["to_place"]) == [pc for pc, *_ in gn[1:]]
    assert {m[3] for m in res["to_place"]} == {"no other postcode of its place lies near that point"}
    assert res["set_aside"] == [] and set(map(tuple, res["postcodes"].values())) == {tuple(r4(g))}
    # Shops that name Nusajaya, 0.5 km either side of the point: all 11 go.
    shops = [(1, *as_read(north(g, 0.5)), "name", ""), (2, *as_read(north(g, -0.5)), "address", "")]
    addresses = {1: ("Johor", "PASAR NUSAJAYA"), 2: ("Johor", "JALAN 1, NUSAJAYA, JOHOR")}
    res = run_places(tmp_path, gn, places, shops, addresses, name="shops")
    assert sorted(m[1] for m in res["to_place"]) == [pc for pc, *_ in gn] and res["set_aside"] == []
    # 79800 lies 1 km from the point: the others lie more than 10 km from it, outliers, and go.
    res = run_places(tmp_path, gn + [("79800", "Nusajaya", "Johor", *north(g, 1))], places, name="near")
    assert sorted(m[1] for m in res["to_place"]) == [pc for pc, *_ in gn]
    assert {m[3] for m in res["to_place"]} == {"more than 10 km from each of the 1 other postcodes of its place "
                                               "near that point"}
    assert bd.COMPACT_MIN == 10
    monkeypatch.setattr(bd, "COMPACT_MIN", 11)   # the limit is COMPACT_MIN: with it at 11, 10 others are few enough
    assert len(run_places(tmp_path, gn, places, name="limit")["to_place"]) == 11


def test_the_place_limits_are_exact(tmp_path, monkeypatch):
    """A point exactly ANCHOR_KM from its place point stays, and rows exactly ROUGH_KM apart are one rough
    point. 08010 lies 13 km out and 4 km from 08000, which lies near the place point: it goes there only
    because the rows of its place lie close together."""
    g = (5.5, 100.4)
    gn = [("08000", "Kota", "Kedah", *north(g, 9)), ("08010", "Kota", "Kedah", *north(g, 13))]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", gn))
    places = bd.load_places(write_places(tmp_path / "pl.csv", [("Kota", "", *g, "PPLA2", "Kedah", 0)]))
    spread = bd.km(*rows["08000"][0][2], *rows["08010"][0][2])
    monkeypatch.setattr(bd, "ROUGH_KM", spread)
    res = bd.check_postcodes(rows, {}, places, {})
    assert [m[1] for m in res["to_place"]] == ["08010"]
    assert res["postcodes"] == {"08000": r4(north(g, 9)), "08010": r4(g)}
    monkeypatch.setattr(bd, "ROUGH_KM", math.nextafter(spread, 0))
    assert bd.check_postcodes(rows, {}, places, {})["to_place"] == []
    monkeypatch.undo()
    d = bd.km(*rows["08010"][0][2], *g)
    monkeypatch.setattr(bd, "ANCHOR_KM", d)
    assert bd.check_postcodes(rows, {}, places, {})["to_place"] == []
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(d, 0))
    assert [m[1] for m in bd.check_postcodes(rows, {}, places, {})["to_place"]] == ["08010"]
    monkeypatch.undo()
    assert (bd.ANCHOR_KM, bd.ROUGH_KM) == (10.0, 5.0)


def test_the_outlier_and_one_shop_limits_are_exact(tmp_path, monkeypatch):
    """With ANCHOR_KM set to a measured distance: a postcode that far from the postcodes near the place
    point is no outlier; a postcode that far from the place point counts as near it; and a shop that far
    from the place point agrees with it."""
    g = (2.6, 102.6)
    places = bd.load_places(write_places(tmp_path / "pl.csv", [("Kota", "", *g, "PPL", "Johor", 5000)]))

    def check(gn, shops=(), name="x"):
        rows = bd.load_postcode_rows(write_geonames(tmp_path / f"{name}.csv", gn))
        geo = bd.load_geo(write_cache(tmp_path / f"{name}_geo.csv", shops))
        return rows, geo, [m[1] for m in bd.check_postcodes(rows, geo, places, {})["to_place"]]
    # Outlier: X 30 km out, 28 km from Y, which lies 2 km from the place point.
    rows, geo, _ = check([("81000", "Kota", "Johor", *north(g, 30)), ("81010", "Kota", "Johor", *north(g, 2))],
                         name="o")
    d = bd.km(*rows["81000"][0][2], *rows["81010"][0][2])
    monkeypatch.setattr(bd, "ANCHOR_KM", d)
    assert bd.check_postcodes(rows, geo, places, {})["to_place"] == []
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(d, 0))
    assert [m[1] for m in bd.check_postcodes(rows, geo, places, {})["to_place"]] == ["81000"]
    monkeypatch.undo()
    # Near: Y exactly ANCHOR_KM from the place point counts as near it, so X, 7 km from Y, is no outlier.
    rows, geo, _ = check([("81000", "Kota", "Johor", *north(g, 15)), ("81010", "Kota", "Johor", *north(g, 8))],
                         name="n")
    monkeypatch.setattr(bd, "ANCHOR_KM", bd.km(*rows["81010"][0][2], *g))
    assert bd.check_postcodes(rows, geo, places, {})["to_place"] == []
    monkeypatch.undo()
    # The postcodes near the place point are taken where step 1 put them, and judged there: Y's shops lie 8 km
    # from it, its GeoNames point 40 km; Z and its shops lie at the place point. X, 15 km out, lies 7 km from Y's
    # shops (25 km from Y's GeoNames point): no outlier. Kota's reference, the median of Y and Z (4 km out), lies
    # nearer the place point than X, so the outlier test alone keeps X there. Without Y's shops, X is an outlier
    # (15 km from Z), and Y (40 km out) too: both go to the place point.
    gn = [("81000", "Kota", "Johor", *north(g, 15)), ("81010", "Kota", "Johor", *north(g, 40)),
          ("81020", "Kota", "Johor", *g)]
    z = [(3, *as_read(north(g, 0.5)), "name", "81020"), (4, *as_read(north(g, -0.5)), "address", "81020")]
    shops = [(1, *as_read(north(g, 7.5)), "name", "81010"), (2, *as_read(north(g, 8.5)), "address", "81010")]
    assert check(gn, shops + z, name="p")[2] == []
    assert check(gn, z, name="p_no_y")[2] == ["81010", "81000"]
    # One shop: 24300 lies 14 km out and 8 km from 24310 (no outlier); its shop exactly ANCHOR_KM from the
    # place point agrees with it.
    gn = [("24300", "Kota", "Johor", *north(g, 14)), ("24310", "Kota", "Johor", *north(g, 6)),
          ("24320", "Kota", "Johor", *north(g, -6))]
    rows, geo, _ = check(gn, [(1, *as_read(north(g, -9)), "name", "24300")], name="s")
    monkeypatch.setattr(bd, "ANCHOR_KM", bd.km(*next(iter(geo.values()))[:2], *g))
    assert [m[1] for m in bd.check_postcodes(rows, geo, places, {})["to_place"]] == ["24300"]
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(bd.ANCHOR_KM, 0))
    assert bd.check_postcodes(rows, geo, places, {})["to_place"] == []
    # ... and a shop exactly ANCHOR_KM from the postcode's own point does not lie more than that from it.
    rows, geo, _ = check(gn, [(1, *as_read(north(g, 1)), "name", "24300")], name="t")
    monkeypatch.setattr(bd, "ANCHOR_KM", bd.km(*next(iter(geo.values()))[:2], *rows["24300"][0][2]))
    assert bd.check_postcodes(rows, geo, places, {})["to_place"] == []
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(bd.ANCHOR_KM, 0))
    assert [m[1] for m in bd.check_postcodes(rows, geo, places, {})["to_place"]] == ["24300"]


def test_the_places_shops_choose_its_point(tmp_path):
    """The reference is the median of the place's postcodes placed from shops, else the shops found by name
    or address whose address names the place, in its state, when they agree. Simpang Empat has 2 entries
    by name 20 km apart, a with a population and b without; its PO box postcodes share a point 50 km south
    of the town. Without a reference, a, the one notable entry, is the place's point."""
    a = (5.53, 100.43)
    b = north(a, 20)
    gn = [("06650", "Simpang Empat", "Kedah", *north(a, -50)), ("06651", "Simpang Empat", "Kedah", *north(a, -50))]
    places = [("Simpang Empat", "", *a, "PPL", "Kedah", 9149), ("Simpang Empat", "", *b, "PPL", "Kedah", 0)]
    near_b = [(10, *as_read(north(b, 1)), "name", ""), (11, *as_read(north(b, -1)), "address", "")]
    named_b = {10: ("Kedah", "PEKAN SIMPANG EMPAT"), 11: ("KEDAH", "06650 SIMPANG EMPAT, KEDAH")}

    def run(shops, addresses, name):
        return run_places(tmp_path, gn, places, shops, addresses, name=name)["postcodes"]["06650"]
    assert run(near_b, named_b, "named") == r4(b)
    # A postcode of the place placed from its shops comes first: it lies near a.
    placed = [("06600", "Simpang Empat", "Kedah", *a)]
    shops_a = [(20, *as_read(north(a, 0.5)), "name", "06600"), (21, *as_read(north(a, -0.5)), "name", "06600")]
    res = run_places(tmp_path, gn + placed, places, near_b + shops_a, named_b, name="placed")
    assert res["postcodes"]["06650"] == r4(a) and res["checks"]["from_shops"] == 1
    # Shops of another state, a street of that name, or shops that do not agree: no reference, so not b,
    # where these shops are, but a, the one notable entry of the 2 by name. With b notable too, no point.
    assert run(near_b, {10: ("Perlis", "SIMPANG EMPAT"), 11: ("Perlis", "SIMPANG EMPAT")}, "state") == r4(a)
    assert run(near_b, {10: ("Kedah", "TAMAN SIMPANG EMPATJAYA"), 11: named_b[11]}, "one") == r4(a)
    apart = [(10, *as_read(north(b, 12)), "name", ""), (11, *as_read(north(b, -12)), "address", "")]
    assert run(apart, named_b, "apart") == r4(a)
    anchor = [near_b[0], (11, *as_read(north(b, -1)), "town", "")]   # a town-level point is no shop found by OSM
    assert run(anchor, named_b, "anchor") == r4(a)
    both = [places[0], ("Simpang Empat", "", *b, "PPL", "Kedah", 120)]
    res = run_places(tmp_path, gn, both, apart, named_b, name="both")
    assert res["postcodes"]["06650"] == r4(north(a, -50)) and res["to_place"] == []
    # Only b in the place list, 20 km from where the shops are: it counts. A town of that name 40 km away
    # does not (the shops near a, with a dropped from the list).
    only_b = [p for p in places if p[2] == b[0]]
    res = run_places(tmp_path, gn + placed, only_b, shops_a, {}, name="only_b")
    assert res["postcodes"]["06650"] == r4(b)
    far = [("Simpang Empat", "", *north(a, 40), "PPL", "Kedah", 9149)]
    res = run_places(tmp_path, gn + placed, far, shops_a, {}, name="far")
    assert res["postcodes"]["06650"] == r4(north(a, -50)) and res["to_place"] == []


def test_states_and_other_names_find_the_place(tmp_path):
    """The place list says Penang where GeoNames says Pulau Pinang, and PriceCatcher says W.P. Kuala
    Lumpur where GeoNames says Kuala Lumpur. Other names count when no name matches: the place list
    calls Pengkalan Hulu 'Keruh', other name 'Pengkalan Hulu'; without shops naming it, no point."""
    bm = (5.37, 100.56)
    gn = [(pc, "Bukit Mertajam", "Pulau Pinang", *north(bm, 30)) for pc in ("14000", "14007")]
    res = run_places(tmp_path, gn, [("Bukit Mertajam", "", *bm, "PPL", "Penang", 0)],
                     [(1, *as_read(north(bm, 1)), "name", ""), (2, *as_read(bm), "address", "")],
                     {1: ("PULAU PINANG", "BUKIT MERTAJAM"), 2: ("Penang", "14000 BUKIT MERTAJAM")})
    assert res["postcodes"] == {"14000": r4(bm), "14007": r4(bm)}
    kl = (3.1412, 101.6865)
    gn = [(pc, "Kuala Lumpur", "Kuala Lumpur", *north(kl, -25)) for pc in ("50050", "50088")]
    res = run_places(tmp_path, gn, [("Kuala Lumpur", "", *kl, "PPL", "Kuala Lumpur", 0)],
                     [(1, *as_read(north(kl, 1)), "name", ""), (2, *as_read(kl), "address", "")],
                     {1: ("W.P. Kuala Lumpur", "JALAN TAR, KUALA LUMPUR"),
                      2: ("WP Kuala Lumpur", "50050 KUALA LUMPUR")},
                     name="kl")
    assert res["postcodes"] == {"50050": r4(kl), "50088": r4(kl)}
    keruh = (5.53, 101.0)
    gn = [("33100", "Pengkalan Hulu", "Perak", *north(keruh, 25))]
    places = [("Keruh", "Pengkalan Hulu|Kroh", *keruh, "PPL", "Perak", 0),
              ("Pengkalan Hulu Lama", "", *north(keruh, 3), "PPL", "Perak", 0)]
    shops = [(1, *as_read(north(keruh, 1)), "name", ""), (2, *as_read(keruh), "address", "")]
    named = {1: ("Perak", "PEKAN PENGKALAN HULU"), 2: ("Perak", "33100 PENGKALAN HULU, PERAK")}
    assert run_places(tmp_path, gn, places, shops, named, name="keruh")["postcodes"] == {"33100": r4(keruh)}
    assert run_places(tmp_path, gn, places, shops, name="keruh_no_ref")["postcodes"] == {"33100": r4(north(keruh, 25))}


def test_one_shop_that_agrees_with_the_place_point_moves_its_postcode(tmp_path):
    """24300 lies 14 km from Kerteh's place point and 8 km from a postcode near it, so it is no outlier. Its
    only shop found by name or address lies 1 km from the place point and 13 km from its GeoNames point:
    it goes to the place point. A shop that does not agree, or that lies near the GeoNames point too,
    or 2 shops that do not agree with each other, move nothing."""
    g = (4.5, 103.45)
    gn = [("24300", "Kerteh", "Terengganu", *north(g, 14)), ("24310", "Kerteh", "Terengganu", *north(g, 6)),
          ("24320", "Kerteh", "Terengganu", *north(g, -6))]
    places = [("Kerteh", "", *g, "PPL", "Terengganu", 12000)]
    res = run_places(tmp_path, gn, places, [(1, *as_read(north(g, 1)), "name", "24300")])
    assert res["postcodes"]["24300"] == r4(g)
    assert res["to_place"] == [(pytest.approx(14), "24300", "Kerteh (Terengganu)",
                                "its one shop found by name or address lies 1.0 km from that point")]
    for i, shops in enumerate([[(1, *as_read(north(g, -12)), "name", "24300")],            # 12 km from the place point
                               [(1, *as_read(north(g, 5)), "name", "24300")],              # 9 km from its own point
                               [(1, *as_read(north(g, 1)), "postcode", "24300")],          # not found by OpenStreetMap
                               [(1, *as_read(north(g, 1)), "name", "24300"),
                                (2, *as_read(north(g, 40)), "address", "24300")]]):        # 2 that do not agree
        res = run_places(tmp_path, gn, places, shops, name=f"n{i}")
        assert res["postcodes"]["24300"] == r4(north(g, 14)) and res["to_place"] == [], i


def test_postcodes_moved_to_the_place_point_do_not_make_a_place_compact(tmp_path):
    """Kuantan's 12 postcodes other than 26070: 9 within 4 km of the place point, 25150 9 km out, and 2
    outliers 60 km out that go to the place point. With those 2 there, 11 of the 12 lie within 4 km of the
    others' median: the place would look compact, and 26070, 17 km out, would be left out. They count at
    their GeoNames points instead, so the place is not compact and 26070 stays. When the 2 lie in town by
    GeoNames, the place is compact and 26070 is left out. The core is measured from the others' median after
    the moves, the median that the distance is measured from too."""
    k = (3.8076, 103.326)
    near = [(f"250{i}0", "Kuantan", "Pahang", *north(k, d)) for i, d in enumerate((-4, -3, -2, -1, 0, 1, 2, 3, 4))]
    rest = [("25150", "Kuantan", "Pahang", *north(k, 9)), ("26070", "Kuantan", "Pahang", *north(k, 17))]
    places = [("Kuantan", "", *k, "PPLA", "Pahang", 0)]
    out = [("25990", "Kuantan", "Pahang", *north(k, -60)), ("26340", "Kuantan", "Pahang", *north(k, -61))]
    res = run_places(tmp_path, near + rest + out, places)
    assert [m[1] for m in res["to_place"]] == ["26340", "25990"] and res["set_aside"] == []
    assert res["postcodes"]["26070"] == r4(north(k, 17))
    in_town = [("25990", "Kuantan", "Pahang", *k), ("26340", "Kuantan", "Pahang", *k)]
    res = run_places(tmp_path, near + rest + in_town, places, name="in_town")
    assert res["to_place"] == [] and "26070" not in res["postcodes"]
    assert res["set_aside"] == [("26070", "Kuantan (Pahang)", "17.0 km from the median of the 12 other postcodes of "
                                 "its place, 90% of which lie within 4.0 km of it")]
    # The core is measured from the median after the moves, as the distance is: 7.9 km north (with the 2 moved
    # postcodes on the place point), where the 18 others reach 7.4 km at most. From the median of the GeoNames
    # points (8.2 km north, with the 2 still 70 km out), they would reach 7.7 km: not compact.
    others = [(f"25{i:03d}", "Kuantan", "Pahang", *north(k, d))
              for i, d in enumerate((0.5, 1, 2, 3, 4, 5, 6, 7.9, 7.9, 8.2, 8.2, 9, 10, 11, 12, 13, 14, 15))]
    gn = others + [("26900", "Kuantan", "Pahang", *north(k, 70)), ("26910", "Kuantan", "Pahang", *north(k, 71)),
                   ("26070", "Kuantan", "Pahang", *north(k, -8))]
    res = run_places(tmp_path, gn, places, name="centre")
    assert [m[1] for m in res["to_place"]] == ["26910", "26900"]
    assert res["set_aside"] == [("26070", "Kuantan (Pahang)", "15.9 km from the median of the 20 other postcodes of "
                                 "its place, 90% of which lie within 7.4 km of it")]


def test_set_aside_runs_after_the_moves_and_never_sets_aside_a_moved_postcode(tmp_path):
    """Kapit's 3 PO box postcodes share a point 60 km out and go to the town. The place is then judged with
    them in town, so 96800 and 96850 stay; judged by GeoNames points alone, both are set aside."""
    t = (1.99, 112.93)
    gn = [("96800", "Kapit", "Sarawak", *t), ("96850", "Kapit", "Sarawak", *north(t, 3))]
    gn += [(f"9680{i}", "Kapit", "Sarawak", *north(t, 60)) for i in (5, 7, 9)]
    res = run_places(tmp_path, gn, [("Kapit", "", *t, "PPL", "Sarawak", 33014)])
    assert res["postcodes"] == {"96800": r4(t), "96805": r4(t), "96807": r4(t), "96809": r4(t),
                                "96850": r4(north(t, 3))}
    assert res["checks"] == {"from_shops": 0, "moved": 0, "to_place": 3, "set_aside": 0}
    assert {a[0] for a in run_check(tmp_path, gn, name="shops_only")["set_aside"]} == {"96800", "96850"}


def test_postcodes_placed_from_shops_or_kept_never_go_to_the_place_point(tmp_path):
    t = (4.6, 101.08)
    gn = [("30100", "Ipoh", "Perak", *t), ("30200", "Ipoh", "Perak", *north(t, 25)),
          ("30300", "Ipoh", "Perak", *north(t, -25))]
    shops = [(1, *as_read(north(t, 21)), "name", "30200"), (2, *as_read(north(t, 22)), "address", "30200"),
             # 30300: 2 shops 11.5 km apart, one 3.5 km from its GeoNames point, which they back.
             (3, *as_read(north(t, -21.5)), "name", "30300"), (4, *as_read(north(t, -33)), "name", "30300")]
    res = run_places(tmp_path, gn, [("Ipoh", "", *t, "PPLA", "Perak", 700000)], shops)
    assert res["postcodes"] == {"30100": r4(t), "30200": r4(as_read(north(t, 21.5))), "30300": r4(north(t, -25))}
    assert res["to_place"] == [] and [k[0] for k in res["kept"]] == ["30300"] and res["checks"]["from_shops"] == 1


def test_a_postcode_of_several_places_goes_only_to_a_point_they_share(tmp_path):
    """05150 names Alor Setar and Alor Star, one town in the place list (Alor Star is another name of it).
    06400 names Pendang and Pokok Sena, 2 towns, and 06010 Changloon and Universiti Utara Malaysia, which
    is not in the place list: they keep their points."""
    a = (6.12, 100.37)
    gn = [("05100", "Alor Setar", "Kedah", *a), ("05100", "Alor Star", "Kedah", *a),
          ("05150", "Alor Setar", "Kedah", *north(a, 20)), ("05150", "Alor Star", "Kedah", *north(a, 20)),
          ("06400", "Pendang", "Kedah", *north(a, 20)), ("06400", "Pokok Sena", "Kedah", *north(a, 20)),
          ("06010", "Changloon", "Kedah", *north(a, 60)),
          ("06010", "Universiti Utara Malaysia", "Kedah", *north(a, 60))]
    places = [("Alor Setar", "Alor Star", *a, "PPLA", "Kedah", 217000),
              ("Changloon", "", *north(a, 45), "PPL", "Kedah", 9000),
              ("Pendang", "", *north(a, -20), "PPL", "Kedah", 5000),
              ("Pokok Sena", "", *north(a, 35), "PPL", "Kedah", 5000)]
    shops = [(1, *as_read(north(a, 0.5)), "name", "05100"), (2, *as_read(north(a, -0.5)), "name", "05100")]
    res = run_places(tmp_path, gn, places, shops)
    assert res["postcodes"]["05150"] == r4(a) and res["postcodes"]["06400"] == r4(north(a, 20))
    assert res["postcodes"]["06010"] == r4(north(a, 60))
    assert [m[1] for m in res["to_place"]] == ["05150"]


def test_one_shop_picks_the_point_of_one_of_several_places(tmp_path):
    """GeoNames lists 73000 under Johol and Tampin; its point is the average of the two rows, 14.7 km from
    Tampin's place point. Its only shop found by name or address lies 0.4 km from that point, so it goes
    there, whether or not Johol has a point of its own. A shop that agrees with the points of both places
    or of neither, or 2 shops that do not agree, move nothing; nor does a shop more than 10 km from the
    postcode's point when that point lies within 10 km of the place point the shop agrees with."""
    tampin, johol = (2.4701, 102.2302), (2.5979, 102.2636)
    gn = [("73000", "Johol", "Negeri Sembilan", 2.636, 102.2933),
          ("73000", "Tampin", "Negeri Sembilan", 2.5687, 102.1824)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "rows.csv", gn))
    own = bd._mean_point([r[2] for r in rows["73000"]])
    t_entry = ("Tampin", "", *tampin, "PPL", "Negeri Sembilan", 28238)
    shop = [(1, *as_read(north(tampin, 0.4)), "address", "73000")]
    why = "its one shop found by name or address lies 0.4 km from that point"
    for i, j_pop in enumerate((0, 2000)):   # Johol without a point (no shops, no population), then with one
        res = run_places(tmp_path, gn, [("Johol", "", *johol, "PPL", "Negeri Sembilan", j_pop), t_entry], shop,
                         name=f"j{i}")
        assert res["postcodes"] == {"73000": r4(tampin)}
        assert res["to_place"] == [(pytest.approx(bd.km(*own, *tampin)), "73000",
                                    "Johol (Negeri Sembilan) / Tampin (Negeri Sembilan)", why)]
    j_entry = ("Johol", "", *johol, "PPL", "Negeri Sembilan", 2000)
    near = north(tampin, 8)   # both rows here: the postcode's point lies 8 km from Tampin's place point
    for i, (rows_at, places, shops) in enumerate([
            (None, [("Johol", "", *north(tampin, -8), "PPL", "Negeri Sembilan", 2000), t_entry],
             [(1, *as_read(north(tampin, -4)), "name", "73000")]),                       # agrees with both
            (None, [j_entry, t_entry], [(1, *as_read(north(tampin, -12)), "name", "73000")]),   # with neither
            (None, [t_entry], [(1, *as_read(north(tampin, 0.4)), "name", "73000"),
                               (2, *as_read(north(johol, 25)), "address", "73000")]),    # 2 shops
            (near, [j_entry, t_entry], [(1, *as_read(north(tampin, -4)), "name", "73000")])]):   # 8 km out
        rows_gn = gn if rows_at is None else [(pc, place, st, *rows_at) for pc, place, st, *_ in gn]
        res = run_places(tmp_path, rows_gn, places, shops, name=f"n{i}")
        assert res["postcodes"] == {"73000": r4(own if rows_at is None else rows_at)} and res["to_place"] == [], i


def test_the_one_shop_limit_among_several_places_is_exact(tmp_path, monkeypatch):
    """73000's 2 rows (Johol, Tampin) lie on one point, 12 km from Tampin's point; Johol's point is 40 km the
    other way, and the one shop lies 0.4 km beyond Tampin's point. With ANCHOR_KM set to the distance from
    73000's point to Tampin's, that point lies within it: 73000 stays. Just below it, 73000 goes there."""
    tampin = (2.4701, 102.2302)
    x = north(tampin, 12)
    gn = [("73000", "Johol", "Negeri Sembilan", *x), ("73000", "Tampin", "Negeri Sembilan", *x)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "rows.csv", gn))
    geo = bd.load_geo(write_cache(tmp_path / "geo.csv", [(1, *as_read(north(tampin, -0.4)), "name", "73000")]))
    places = bd.load_places(write_places(tmp_path / "pl.csv", [
        ("Johol", "", *north(tampin, 40), "PPL", "Negeri Sembilan", 2000),
        ("Tampin", "", *tampin, "PPL", "Negeri Sembilan", 28238)]))
    d = bd.km(*rows["73000"][0][2], *tampin)
    monkeypatch.setattr(bd, "ANCHOR_KM", d)
    assert bd.check_postcodes(rows, geo, places, {})["to_place"] == []
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(d, 0))
    res = bd.check_postcodes(rows, geo, places, {})
    assert [m[1] for m in res["to_place"]] == ["73000"] and res["postcodes"]["73000"] == r4(tampin)


def test_a_place_name_of_two_states_counts_where_the_place_list_has_it(tmp_path):
    """GeoNames lists 27000 Jerantut in Pahang and in Selangor, on one point 67 km from the town. The place
    list has Jerantut only in Pahang, so 27000 is judged as a postcode of Jerantut, Pahang: more than 10 km
    from the postcodes near the town, it goes there. When the place list has a Jerantut in both states, on
    2 points or with no point in Selangor, it keeps its point. Old and new spellings of a name are one name;
    2 different names (Changloon and Universiti Utara Malaysia, which is not in the place list) never count
    as one."""
    j, far = (3.936, 102.3626), (3.4004, 102.0898)
    gn = [("27000", "Jerantut", "Pahang", *far), ("27000", "Jerantut", "Selangor", *far),
          ("27010", "Jerantut", "Pahang", *north(j, 2)), ("27020", "Jerantut", "Pahang", *north(j, -3))]
    places = [("Jerantut", "", *j, "PPL", "Pahang", 33606)]
    res = run_places(tmp_path, gn, places)
    assert res["postcodes"]["27000"] == r4(j)
    assert res["to_place"] == [(pytest.approx(bd.km(*far, *j)), "27000", "Jerantut (Pahang) / Jerantut (Selangor)",
                                "more than 10 km from each of the 2 other postcodes of its place near that point")]
    # Its Pahang row lies 67 km from Jerantut's point, but its Selangor row (no point) is on the same point,
    # so that point still counts: no far row.
    assert res["far_rows"] == []
    # It does not go to Jerantut's point then. Step 4 leaves it out instead: 27010 and 27020, which share its
    # first 3 digits, lie near the town's point, so they are confirmed, and 27000 lies 64 km from the nearer.
    why = [("27000", "Jerantut (Pahang) / Jerantut (Selangor)", f"{bd.km(*far, *north(j, -3)):.1f} km from the "
            "nearest of the 2 confirmed postcodes that start 270")]
    res = run_places(tmp_path, gn, places + [("Jerantut", "", 3.3, 101.6, "PPL", "Selangor", 900)], name="both")
    assert "27000" not in res["postcodes"] and res["to_place"] == [] and res["set_aside"] == why
    # The place list has Jerantut in Selangor twice, with no population and no shops: no point there, but the
    # list has the name in both states, so both count and 27000 does not go to Jerantut's point.
    selangor = [("Jerantut", "", 3.3, 101.6, "PPL", "Selangor", 0), ("Jerantut", "", 3.5, 101.4, "PPL", "Selangor", 0)]
    res = run_places(tmp_path, gn, places + selangor, name="no_point")
    assert "27000" not in res["postcodes"] and res["to_place"] == [] and res["set_aside"] == why
    # Old and new spellings are one name here too: GeoNames lists 17600 as Kampong Jeli (Kelantan) and
    # Kampung Jeli (Perak), and the place list has Kampung Jeli in Kelantan only.
    k = (5.7, 101.84)
    gn17 = [("17600", "Kampong Jeli", "Kelantan", *far), ("17600", "Kampung Jeli", "Perak", *far)]
    res = run_places(tmp_path, gn17, [("Kampung Jeli", "", *k, "PPL", "Kelantan", 9000)], name="spelled")
    assert res["postcodes"]["17600"] == r4(k)
    gn = [("06010", "Changloon", "Kedah", *far), ("06010", "Universiti Utara Malaysia", "Kedah", *far)]
    res = run_places(tmp_path, gn, [("Changloon", "", *j, "PPL", "Kedah", 9000)], name="names")
    assert res["postcodes"]["06010"] == r4(far) and res["to_place"] == []


def test_old_and_new_spellings_name_one_place(tmp_path):
    """The postcode table says Tanjong Piandang where the place list says Tanjung Piandang, the place list
    says Tanjong Aru where the postcode table says Tanjung Aru, and shops write either. Old spellings
    (Tanjong, Telok, Kampong, Sungei, Ayer, Sri) compare as the new ones."""
    assert bd.spelled("TANJONG PIANDANG") == "TANJUNG PIANDANG" == bd.spelled("TANJUNG PIANDANG")
    assert bd.spelled("KAMPONG SUNGEI TELOK AYER SRI GADING") == "KAMPUNG SUNGAI TELUK AIR SERI GADING"
    assert bd.spelled("TANJONGKARANG 2") == "TANJONGKARANG 2" and bd.spelled("") == ""
    t = (5.0724, 100.3811)
    res = run_places(tmp_path, [("34250", "Tanjong Piandang", "Perak", *north(t, -28))],
                     [("Tanjung Piandang", "", *t, "PPL", "Perak", 8352)])
    assert res["postcodes"] == {"34250": r4(t)}
    a = (5.9569, 116.0528)
    res = run_places(tmp_path, [("88150", "Tanjung Aru", "Sabah", *north(a, 15))],
                     [("Tanjong Aru", "", *a, "PPL", "Sabah", 900)], name="aru")
    assert res["postcodes"] == {"88150": r4(a)}
    # Shops naming the place in either spelling are its shops: of 2 entries by name, the one near them counts.
    s = (2.6579, 101.5629)
    places = [("Tanjung Sepat", "", *s, "PPL", "Selangor", 0),
              ("Tanjung Sepat", "", *north(s, 30), "PPL", "Selangor", 0)]
    shops = [(1, *as_read(north(s, 0.5)), "name", ""), (2, *as_read(north(s, -0.5)), "address", "")]
    addresses = {1: ("Selangor", "PASAR TANJONG SEPAT"), 2: ("Selangor", "42800 TANJUNG SEPAT, KUALA LANGAT")}
    res = run_places(tmp_path, [("42800", "Tanjong Sepat", "Selangor", *north(s, 12))], places, shops, addresses,
                     name="sepat")
    assert res["postcodes"] == {"42800": r4(s)}
    # Other names too. Made up for this test: the place list has Tanjong Malim only as another name of
    # Kampung Hulu Bernam; the shops naming Tanjung Malim confirm that entry, so 35900 goes there.
    m = (3.6851, 101.5244)
    shops = [(1, *as_read(north(m, 0.5)), "name", ""), (2, *as_read(north(m, -0.5)), "address", "")]
    addresses = {1: ("Perak", "PASAR TANJUNG MALIM"), 2: ("Perak", "35900 TANJUNG MALIM, PERAK")}
    res = run_places(tmp_path, [("35900", "Tanjung Malim", "Perak", *north(m, 25))],
                     [("Kampung Hulu Bernam", "Tanjong Malim", *m, "PPL", "Perak", 0)], shops, addresses, name="alt")
    assert res["postcodes"] == {"35900": r4(m)}


def test_old_and_new_spellings_of_a_place_are_one_place(tmp_path):
    """GeoNames lists Tanjung Bungah (Penang) as Tanjong Bungah too. Its postcodes are one place: 11230, 45 km
    from the others, is set aside, as with one spelling. Spelled apart, 11230 would have only 1 other
    postcode of its place, too few to judge it by. The set-aside names the place as GeoNames spells it."""
    t = (5.4632, 100.2877)
    gn = [("11200", "Tanjong Bungah", "Pulau Pinang", *t), ("11210", "Tanjong Bungah", "Pulau Pinang", *north(t, 1)),
          ("11220", "Tanjung Bungah", "Pulau Pinang", *north(t, 2)),
          ("11230", "Tanjung Bungah", "Pulau Pinang", *north(t, 45))]
    res = run_check(tmp_path, gn)
    assert sorted(res["postcodes"]) == ["11200", "11210", "11220"]
    assert res["set_aside"] == [("11230", "Tanjung Bungah (Pulau Pinang)",
                                 "44.0 km from the median of the 3 other postcodes of its place")]
    assert bd.place_key("pulau pinang|tanjong bungah") == "pulau pinang|tanjung bungah" == \
        bd.place_key("pulau pinang|tanjung  bungah")
    assert bd.place_key("") == "" and bd.place_key("melaka|sungai rambai'") == "melaka|sungai rambai"
    assert bd.place_key("perak|-") == "perak|-"


def test_a_longer_place_name_of_the_same_state_does_not_name_the_place(tmp_path):
    """Shops whose address reads '13300 TASEK GELUGOR' name Tasek Gelugor, a longer place name of the same
    state in the postcode table, not Gelugor. So Gelugor has no reference, and the place list's only entry
    with that name, Tasek Glugor (other names Gelugor and Tasek Gelugor), gives it no point: 11700 stays.
    Where the postcode table has Tasek Gelugor only in another state, the same shops do name Gelugor."""
    island, tasek = (5.3653, 100.3067), (5.4803, 100.4985)   # 24.8 km apart, across the strait
    places = [("Tasek Glugor", "Gelugor|Tasek Gelugor", *tasek, "PPL", "Pulau Pinang", 0)]
    shops = [(1, *as_read(north(tasek, 0.5)), "name", ""), (2, *as_read(north(tasek, -0.5)), "address", "")]
    addresses = {1: ("Pulau Pinang", "PASAR AWAM, 13300 TASEK GELUGOR"), 2: ("Penang", "13300 TASEK GELUGOR, P.P.")}
    gn = [("11700", "Gelugor", "Pulau Pinang", *island), ("13300", "Tasek Gelugor", "Pulau Pinang", *north(tasek, 2))]
    res = run_places(tmp_path, gn, places, shops, addresses)
    assert res["postcodes"] == {"11700": r4(island), "13300": r4(north(tasek, 2))} and res["to_place"] == []
    gn[1] = ("13300", "Tasek Gelugor", "Kedah", *north(tasek, 2))
    res = run_places(tmp_path, gn, places, shops, addresses, name="other_state")
    assert res["postcodes"]["11700"] == r4(tasek)
    assert res["to_place"] == [(pytest.approx(bd.km(*island, *tasek)), "11700", "Gelugor (Pulau Pinang)",
                                "its place has no other GeoNames row")]


def test_a_postcode_never_moves_away_from_where_its_places_shops_are(tmp_path):
    """The shops naming Gelugor lie on the island next to 11700's GeoNames point, and the only entry with
    that name within 35 km of them is the place list's Tasek Glugor (other name Gelugor), 24.8 km away on
    the mainland. That entry is Gelugor's place point, but 11700 stays: a postcode goes to its place's
    point only when that point lies nearer the place's shops than its own point does."""
    island, tasek = (5.3653, 100.3067), (5.4803, 100.4985)
    places = [("Tasek Glugor", "Gelugor", *tasek, "PPL", "Pulau Pinang", 0)]

    def run(gn, ref, name):   # 2 shops 1 km apart whose addresses name Gelugor, their median at ref
        shops = [(1, *as_read(north(ref, 0.5)), "name", ""), (2, *as_read(north(ref, -0.5)), "address", "")]
        addresses = {1: ("Pulau Pinang", "TAMAN TUN SARDON, GELUGOR"), 2: ("Pulau Pinang", "11700 GELUGOR")}
        return run_places(tmp_path, gn, places, shops, addresses, name=name)
    gn = [("11700", "Gelugor", "Pulau Pinang", *island)]
    res = run(gn, north(island, 0.5), "island")
    assert res["postcodes"] == {"11700": r4(island)} and res["to_place"] == []
    res = run(gn, north(tasek, 1), "mainland")
    assert res["postcodes"] == {"11700": r4(tasek)} and [m[1] for m in res["to_place"]] == ["11700"]
    # 11700 30 km from the place point: shops 14.9 km from that point are nearer it than 11700; 15.1 km, not.
    gn = [("11700", "Gelugor", "Pulau Pinang", *north(tasek, 30))]
    assert run(gn, north(tasek, 14.9), "nearer")["postcodes"] == {"11700": r4(tasek)}
    assert run(gn, north(tasek, 15.1), "further")["postcodes"] == {"11700": r4(north(tasek, 30))}


def test_at_the_same_distance_from_the_places_shops_a_postcode_stays(tmp_path):
    """The place point and 81000's GeoNames point lie on one parallel, a quarter of a degree of longitude
    either side of the median of the 2 shops naming Kota: exactly as far from it, in floating point too. A
    postcode goes only to a point nearer its place's shops than its own, so 81000 stays; a little further
    out, it goes."""
    ref, g = (2.5, 102.5), (2.5, 102.25)
    shops = [(1, 2.5, 102.46875, "name", ""), (2, 2.5, 102.53125, "address", "")]   # their median: ref
    addresses = {1: ("Johor", "PASAR AWAM KOTA"), 2: ("Johor", "81000 KOTA, JOHOR")}
    places = [("Kota", "", *g, "PPL", "Johor", 0)]
    assert bd.km(*g, *ref) == bd.km(2.5, 102.75, *ref)
    res = run_places(tmp_path, [("81000", "Kota", "Johor", 2.5, 102.75)], places, shops, addresses)
    assert res["postcodes"] == {"81000": [2.5, 102.75]} and res["to_place"] == []
    res = run_places(tmp_path, [("81000", "Kota", "Johor", 2.5, 102.7501)], places, shops, addresses, name="out")
    assert res["postcodes"] == {"81000": [2.5, 102.25]} and [m[1] for m in res["to_place"]] == ["81000"]


def test_the_guard_reads_the_reference_of_the_place_whose_point_it_is(tmp_path):
    """06400 names Pendang and Pokok Sena, both on its GeoNames point a. Pendang's shops (those of 06700) lie
    at a too. Its one shop lies 1 km from Pokok Sena's point, 18 km away, and no shop names Pokok Sena, so
    that point comes from the place list's one notable entry: 06400 goes there. Pendang's shops do not stop
    it, as they say nothing about where Pokok Sena is. With 2 shops naming Pokok Sena at a, it stays."""
    a = (6.0129, 100.4907)
    pok = north(a, 18)
    gn = [("06400", "Pendang", "Kedah", *a), ("06400", "Pokok Sena", "Kedah", *a), ("06700", "Pendang", "Kedah", *a)]
    shops = [(1, *as_read(north(pok, 1)), "name", "06400"),
             (2, *as_read(north(a, 0.5)), "name", "06700"), (3, *as_read(north(a, -0.5)), "address", "06700")]
    places = [("Pendang", "", *north(a, -1), "PPL", "Kedah", 5000), ("Pokok Sena", "", *pok, "PPL", "Kedah", 5000)]
    res = run_places(tmp_path, gn, places, shops)
    assert res["postcodes"]["06400"] == r4(pok) and res["checks"]["from_shops"] == 1
    assert [m[1:] for m in res["to_place"]] == [("06400", "Pendang (Kedah) / Pokok Sena (Kedah)",
                                                 "its one shop found by name or address lies 1.0 km from that point")]
    named = [(4, *as_read(north(a, 0.3)), "name", ""), (5, *as_read(north(a, -0.3)), "address", "")]
    addresses = {4: ("Kedah", "PASAR POKOK SENA"), 5: ("Kedah", "06400 POKOK SENA, KEDAH")}
    res = run_places(tmp_path, gn, places, shops + named, addresses, name="named")
    assert res["postcodes"]["06400"] == r4(a) and res["to_place"] == []


def test_shops_with_another_places_postcode_are_not_that_places_shops(tmp_path):
    """'84600 PAGOH, MUAR' is a shop in Pagoh: GeoNames lists 84600 under Pagoh only, and the address names
    Muar as the district. Such shops of Pagoh and Tangkak are no reference for Muar, so its one notable entry
    gives its point, and Muar's 2 postcodes, 15 km from it on one rough point, go there. Taken as Muar's
    shops, they would keep both where they are: Muar's point lies further from them than they do. Shops
    with Muar's own postcodes, with none or with one that GeoNames does not have do count."""
    muar, x = (2.0442, 102.5689), (2.0102, 102.6981)
    gn = [("84000", "Muar", "Johor", *x), ("84200", "Muar", "Johor", *north(x, 1)),
          ("84600", "Pagoh", "Johor", 2.087, 102.8871), ("84900", "Tangkak", "Johor", 2.1462, 102.7848)]
    places = [("Muar", "", *muar, "PPL", "Johor", 127897)]
    addresses = {1: ("Johor", "J32,PASAR AWAM PAGOH,84600 PAGOH,MUAR"),
                 2: ("Johor", "JALAN SOLOK,84900 TANGKAK MUAR JOHOR,")}
    for pcs, moved in ((("84600", "84900"), True), (("84200", "84000"), False), (("", ""), False),
                       (("84999", "84998"), False)):
        shops = [(1, 2.1437, 102.7665, "name", pcs[0]), (2, 2.2137, 102.6522, "name", pcs[1])]
        res = run_places(tmp_path, gn, places, shops, addresses, name=f"s{pcs[0]}")
        assert res["checks"]["from_shops"] == 0, pcs
        assert [res["postcodes"][pc] for pc in ("84000", "84200")] == ([r4(muar)] * 2 if moved else
                                                                        [r4(x), r4(north(x, 1))]), pcs
        assert [m[1] for m in res["to_place"]] == (["84000", "84200"] if moved else []), pcs


def test_the_reference_of_postcodes_placed_from_shops_is_their_median(tmp_path):
    """3 postcodes of Kota are placed from shops, 0, 2 and 30 km north of a. Their median (2 km) is nearest the
    entry 1 km north, their mean (10.7 km) the entry 11 km north: Kota's point is the entry 1 km north, and
    81090, an outlier 40 km south, goes there."""
    a = (2.0, 103.0)
    gn, shops = [], []
    for i, (pc, k) in enumerate((("81000", 0), ("81010", 2), ("81020", 30))):
        gn.append((pc, "Kota", "Johor", *north(a, k)))
        shops += [(2 * i + 1, *as_read(north(a, k + 0.3)), "name", pc),
                  (2 * i + 2, *as_read(north(a, k - 0.3)), "address", pc)]
    gn.append(("81090", "Kota", "Johor", *north(a, -40)))
    places = [("Kota", "", *north(a, 1), "PPL", "Johor", 0), ("Kota", "", *north(a, 11), "PPL", "Johor", 0)]
    res = run_places(tmp_path, gn, places, shops)
    assert res["checks"]["from_shops"] == 3 and [m[1] for m in res["to_place"]] == ["81090"]
    assert res["postcodes"]["81090"] == r4(north(a, 1))


def test_a_rough_place_is_judged_by_its_own_geonames_rows(tmp_path):
    """Kota's GeoNames rows lie within 5 km of each other, 8 to 13 km from its place point: one rough point,
    so 08010, more than 10 km out, goes to the place point. That holds when another postcode of Kota also
    has a far row of another place, and when a postcode of Kota is placed from shops near the town: the
    place's own GeoNames rows count, not other rows and not where step 1 put a postcode."""
    g = (5.5, 100.4)
    places = [("Kota", "", *g, "PPLA2", "Kedah", 0)]
    why = "all 3 GeoNames rows of its place lie within 5 km of each other"
    gn = [("08010", "Kota", "Kedah", *north(g, 12)), ("08020", "Kota", "Kedah", *north(g, 8)),
          ("08030", "Kota", "Kedah", *north(g, 9)), ("08030", "Lain", "Kedah", *north(g, -30))]
    res = run_places(tmp_path, gn, places, name="other_place")
    assert res["to_place"] == [(pytest.approx(12), "08010", "Kota (Kedah)", why)]
    gn = [("08000", "Kota", "Kedah", *north(g, 12)), ("08010", "Kota", "Kedah", *north(g, 13)),
          ("08020", "Kota", "Kedah", *north(g, 9))]
    shops = [(1, *as_read(north(g, 0.5)), "name", "08000"), (2, *as_read(north(g, 1.5)), "address", "08000")]
    res = run_places(tmp_path, gn, places, shops, name="placed")
    assert res["checks"]["from_shops"] == 1
    assert res["to_place"] == [(pytest.approx(13), "08010", "Kota (Kedah)", why)]


def test_an_outlier_is_judged_by_the_postcodes_of_every_place_it_names(tmp_path):
    """05150 names Alor Setar and Alor Star, both at the city's point, and lies 14 km out. No other postcode
    of Alor Setar lies near the point, but 05160 of Alor Star does, 7 km from 05150: 05150 is no outlier.
    05170 (Alor Setar only, 30 km out) is one."""
    a = (6.12, 100.37)
    gn = [("05150", "Alor Setar", "Kedah", *north(a, 14)), ("05150", "Alor Star", "Kedah", *north(a, 14)),
          ("05160", "Alor Star", "Kedah", *north(a, 7)), ("05170", "Alor Setar", "Kedah", *north(a, 30))]
    places = [("Alor Setar", "", *a, "PPLA", "Kedah", 217000), ("Alor Star", "", *a, "PPL", "Kedah", 5000)]
    res = run_places(tmp_path, gn, places)
    assert res["postcodes"]["05150"] == r4(north(a, 14)) and res["postcodes"]["05170"] == r4(a)
    assert [m[1:] for m in res["to_place"]] == [("05170", "Alor Setar (Kedah)",
                                                 "no other postcode of its place lies near that point")]


def test_the_one_notable_entry_of_several_by_name_is_the_places_point(tmp_path):
    """The place list has 2 entries named Engkilili, 15.5 km apart: one with a population of 15,031 and one
    with none. Without shops naming the town, the notable one is its point, and 95800, 86 km out on its own,
    goes there. With 2 notable entries there is no telling which, and it stays."""
    town, other = (1.13864, 111.66665), (1.25, 111.75)
    gn = [("95800", "Engkilili", "Sarawak", 1.2817, 110.9074)]
    res = run_places(tmp_path, gn, [("Engkilili", "", *other, "PPL", "Sarawak", 0),
                                    ("Engkilili", "Engkelili", *town, "PPL", "Sarawak", 15031)])
    assert res["postcodes"] == {"95800": r4(town)}
    assert res["to_place"] == [(pytest.approx(bd.km(1.2817, 110.9074, *town)), "95800", "Engkilili (Sarawak)",
                                "its place has no other GeoNames row")]
    res = run_places(tmp_path, gn, [("Engkilili", "", *other, "PPL", "Sarawak", 12),
                                    ("Engkilili", "Engkelili", *town, "PPL", "Sarawak", 15031)], name="two")
    assert res["postcodes"] == {"95800": [1.2817, 110.9074]} and res["to_place"] == []


def test_the_one_notable_entry_with_the_name_among_its_other_names_is_the_places_point(tmp_path, monkeypatch):
    """The place list has Kerteh only as another name of Kertih (population 24,401): without shops naming the
    town, that entry is its point, and 24300, 23 km out on its own, goes there. Not when 2 entries have that
    other name, nor when the one entry is not notable. Nor when another of its names is a longer place of
    the postcode table with none of the place's postcodes: Tasek Glugor (other names Gelugor and Tasek
    Gelugor) is Tasek Gelugor (13300) on the mainland, so 11700 Gelugor stays on Penang island. Nor when
    another of its names is the name or other name of an entry more than ANCHOR_KM away: Kampung Kuala
    Sepetang (other names Kuala Sepetang and Port Weld) lies 32 km from Kampung Sepetang (other name Port
    Weld), which is the town."""
    kertih, own = (4.5141, 103.4483), (4.6985, 103.3568)
    gn = [("24300", "Kerteh", "Terengganu", *own)]
    entry = ("Kertih", "Kerteh|Kertikh", *kertih, "PPL", "Terengganu", 24401)
    res = run_places(tmp_path, gn, [entry])
    assert res["postcodes"] == {"24300": r4(kertih)}
    assert res["to_place"] == [(pytest.approx(bd.km(*own, *kertih)), "24300", "Kerteh (Terengganu)",
                                "its place has no other GeoNames row")]
    for i, places in enumerate([[entry, ("Perkampungan Kertih", "Kerteh", 4.6, 103.4, "PPL", "Terengganu", 0)],
                                [("Kertih", "Kerteh", *kertih, "PPL", "Terengganu", 0)]]):
        res = run_places(tmp_path, gn, places, name=f"k{i}")
        assert res["postcodes"] == {"24300": list(own)} and res["to_place"] == [], i
    # Gelugor. Without Tasek Gelugor in the postcode table, or when it has Gelugor's postcode too, the entry counts.
    island, tasek = (5.3653, 100.3067), (5.4803, 100.4985)
    glugor = [("Tasek Glugor", "Gelugor|Tasek Gelugor", *tasek, "PPL", "Pulau Pinang", 135786)]
    gn = [("11700", "Gelugor", "Pulau Pinang", *island), ("13300", "Tasek Gelugor", "Pulau Pinang", *north(tasek, 2))]
    res = run_places(tmp_path, gn, glugor, name="gelugor")
    assert res["postcodes"] == {"11700": list(island), "13300": r4(north(tasek, 2))} and res["to_place"] == []
    for i, rows in enumerate([gn[:1], gn + [("11700", "Tasek Gelugor", "Pulau Pinang", *island)]]):
        res = run_places(tmp_path, rows, glugor, name=f"g{i}")
        assert res["postcodes"]["11700"] == r4(tasek) and [m[1] for m in res["to_place"]] == ["11700"], i
    # With a reference these vetoes do not apply: shops naming Gelugor at Tasek Glugor make it Gelugor's point.
    shops = [(1, *as_read(north(tasek, 0.5)), "name", ""), (2, *as_read(north(tasek, -0.5)), "address", "")]
    addresses = {1: ("Pulau Pinang", "JALAN SUNGAI DUA, GELUGOR"), 2: ("Penang", "GELUGOR, PULAU PINANG")}
    res = run_places(tmp_path, gn, glugor, shops, addresses, name="g_ref")
    assert res["postcodes"]["11700"] == r4(tasek) and [m[1] for m in res["to_place"]] == ["11700"]
    # Kuala Sepetang. Without the other Port Weld, or with it no more than ANCHOR_KM away, the entry counts.
    sepetang, own = (4.5429, 100.6597), (4.9204, 100.6607)
    gn = [("34650", "Kuala Sepetang", "Perak", *own)]
    ks = ("Kampung Kuala Sepetang", "Kuala Sepetang|Port Weld", *sepetang, "PPL", "Perak", 1264)
    town = ("Kampung Sepetang", "Port Weld|Sapetang", 4.83333, 100.63333, "PPL", "Perak", 0)
    res = run_places(tmp_path, gn, [ks, town], name="sepetang")
    assert res["postcodes"] == {"34650": list(own)} and res["to_place"] == []
    res = run_places(tmp_path, gn, [ks], name="sepetang_one")
    assert res["postcodes"] == {"34650": r4(sepetang)} and [m[1] for m in res["to_place"]] == ["34650"]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "ks.csv", gn))
    places = bd.load_places(write_places(tmp_path / "ks_pl.csv", [ks, town]))
    d = bd.km(*sepetang, 4.83333, 100.63333)
    monkeypatch.setattr(bd, "ANCHOR_KM", d)
    assert [m[1] for m in bd.check_postcodes(rows, {}, places, {})["to_place"]] == ["34650"]
    monkeypatch.setattr(bd, "ANCHOR_KM", math.nextafter(d, 0))
    assert bd.check_postcodes(rows, {}, places, {})["to_place"] == []


def test_the_vetoes_on_an_entry_by_another_name_leave_the_entries_by_name_alone(tmp_path):
    """The place list's 'Sungai Siput' (population 49,000) lies 46.5 km from the town, which it calls 'Sungai
    Siput Utara', other name 'Sungai Siput'. That other name makes the entry by name doubtful, and Sungai
    Siput gets no point, also when the postcode table has a place Sungai Siput Utara of its own (which would
    stop the entry by another name from being Sungai Siput's point were there no entry by name)."""
    town = (4.8151, 101.0786)
    far = north(town, -46.5)
    gn = [("31100", "Sungai Siput", "Perak", *north(town, 3)), ("31110", "Sungai Siput Utara", "Perak", *town)]
    places = [("Sungai Siput", "", *far, "PPL", "Perak", 49000), ("Sungai Siput Utara", "Sungai Siput", *town,
                                                                   "PPL", "Perak", 0)]
    res = run_places(tmp_path, gn, places)
    assert res["postcodes"] == {"31100": r4(north(town, 3)), "31110": list(town)} and res["to_place"] == []


def test_the_place_lists_ascii_name_counts_too(tmp_path):
    """GeoNames gives each place a name and an ASCII name. A place of the postcode table that matches only
    the ASCII name ('Kampung Nipah' for 'Kampung Ñipah') is found too."""
    pt = (2.0, 103.0)
    path = tmp_path / "places_my.csv"
    path.write_text(PL_HEAD + f"1,Kampung Ñipah,Kampung Nipah,,{pt[0]},{pt[1]},PPL,Johor,5000\n", encoding="utf-8")
    places = bd.load_places(path)
    assert places == [("johor", frozenset({"KAMPUNG IPAH", "KAMPUNG NIPAH"}), frozenset(), pt, "PPL", 5000)]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", [("86000", "Kampung Nipah", "Johor",
                                                                        *north(pt, 20))]))
    res = bd.check_postcodes(rows, {}, places, {})
    assert res["postcodes"] == {"86000": r4(pt)} and [m[1] for m in res["to_place"]] == ["86000"]


def test_every_place_whose_point_it_is_must_have_its_shops_nearer_that_point(tmp_path):
    """06400 names Alor Setar and Alor Star, whose place point is one entry (Alor Star is its other name), 20
    km from 06400's point x, where all rows of both places lie: one rough point. Alor Setar's shops (those
    of 05000) lie at the place point, Alor Star's (those of 05300) at x. That point lies further than x
    from Alor Star's shops, so 06400 stays. Without shops of Alor Star, it goes there, with 05300."""
    g = (6.12, 100.37)
    x = north(g, 20)
    gn = [("05000", "Alor Setar", "Kedah", *x), ("05300", "Alor Star", "Kedah", *x),
          ("06400", "Alor Setar", "Kedah", *x), ("06400", "Alor Star", "Kedah", *x)]
    places = [("Alor Setar", "Alor Star", *g, "PPLA", "Kedah", 217000)]
    setar = [(1, *as_read(north(g, 0.5)), "name", "05000"), (2, *as_read(north(g, -0.5)), "address", "05000")]
    star = [(3, *as_read(north(x, 0.5)), "name", "05300"), (4, *as_read(north(x, -0.5)), "address", "05300")]
    res = run_places(tmp_path, gn, places, setar + star)
    assert res["checks"]["from_shops"] == 2 and res["postcodes"]["06400"] == r4(x) and res["to_place"] == []
    res = run_places(tmp_path, gn, places, setar, name="setar_only")
    assert res["postcodes"]["06400"] == r4(g) and sorted(m[1] for m in res["to_place"]) == ["05300", "06400"]


def test_others_moved_onto_one_point_are_one_guess_whatever_their_geonames_points(tmp_path):
    """Kota's 3 other postcodes lie on 2 GeoNames points, 20 and 25 km south of its place point, and all go to
    it (no postcode of Kota lies near it). 42000, listed under Kota and Lain (not in the place list), does
    not move, and lies 40 km north. The others now share one point, one guess, so they judge nothing and
    42000 stays. Counted at their GeoNames points they would be 2 points, and 42000 would be set aside."""
    g = (3.0, 101.5)
    gn = [("42010", "Kota", "Selangor", *north(g, -20)), ("42020", "Kota", "Selangor", *north(g, -20)),
          ("42030", "Kota", "Selangor", *north(g, -25)),
          ("42000", "Kota", "Selangor", *north(g, 40)), ("42000", "Lain", "Selangor", *north(g, 40))]
    res = run_places(tmp_path, gn, [("Kota", "", *g, "PPL", "Selangor", 5000)])
    assert sorted(m[1] for m in res["to_place"]) == ["42010", "42020", "42030"]
    assert res["postcodes"]["42000"] == r4(north(g, 40)) and res["set_aside"] == []


def test_the_other_states_place_of_a_two_state_postcode_judges_nothing(tmp_path):
    """27000 is listed under Jerantut in Pahang and in Selangor; the place list has Jerantut only in Pahang,
    so 27000 is a postcode of Jerantut, Pahang, and Selangor's Jerantut judges nothing. 45000, of Jerantut in
    Selangor, lies within 10 km of both the town's point and 27000, but 27000 is still an outlier from the
    postcodes of Jerantut, Pahang near that point. And the rows of Jerantut, Pahang are one rough point,
    whatever the rows of Selangor's Jerantut."""
    j = (3.936, 102.3626)
    places = [("Jerantut", "", *j, "PPL", "Pahang", 33606)]
    gn = [("27000", "Jerantut", "Pahang", *north(j, 15)), ("27000", "Jerantut", "Selangor", *north(j, 15)),
          ("27010", "Jerantut", "Pahang", *j), ("27020", "Jerantut", "Pahang", *north(j, 1)),
          ("45000", "Jerantut", "Selangor", *north(j, 7))]
    res = run_places(tmp_path, gn, places)
    assert [m[1:] for m in res["to_place"]] == [("27000", "Jerantut (Pahang) / Jerantut (Selangor)",
                                                 "more than 10 km from each of the 2 other postcodes of its place "
                                                 "near that point")]
    gn = [("27000", "Jerantut", "Pahang", *north(j, 15)), ("27000", "Jerantut", "Selangor", *north(j, 15)),
          ("27010", "Jerantut", "Pahang", *north(j, 14)), ("27020", "Jerantut", "Pahang", *north(j, 16)),
          ("45000", "Jerantut", "Selangor", *north(j, 100))]
    res = run_places(tmp_path, gn, places, name="rough")
    assert sorted(m[1] for m in res["to_place"]) == ["27000", "27010", "27020"]
    assert {m[3] for m in res["to_place"]} == {"all 3 GeoNames rows of its place lie within 5 km of each other"}


def test_the_reference_is_the_median_of_all_placed_postcodes_not_the_first(tmp_path):
    """3 postcodes of Kota are placed from shops 0, 10 and 12 km north of a. Their median (10 km) is nearest the
    entry 11 km north, the first of them (81000, 0 km) the entry 1 km north: Kota's point is the entry 11 km
    north, and 81090, an outlier 40 km south, goes there."""
    a = (2.0, 103.0)
    gn, shops = [], []
    for i, (pc, k) in enumerate((("81000", 0), ("81010", 10), ("81020", 12))):
        gn.append((pc, "Kota", "Johor", *north(a, k)))
        shops += [(2 * i + 1, *as_read(north(a, k + 0.3)), "name", pc),
                  (2 * i + 2, *as_read(north(a, k - 0.3)), "address", pc)]
    gn.append(("81090", "Kota", "Johor", *north(a, -40)))
    places = [("Kota", "", *north(a, 1), "PPL", "Johor", 0), ("Kota", "", *north(a, 11), "PPL", "Johor", 0)]
    res = run_places(tmp_path, gn, places, shops)
    assert res["checks"]["from_shops"] == 3 and [m[1] for m in res["to_place"]] == ["81090"]
    assert res["postcodes"]["81090"] == r4(north(a, 11))


def test_a_rough_place_is_one_whose_rows_all_lie_close_in_any_direction(tmp_path):
    """The rows of Alpha: 40030 9.9 km north of the place point (near it), 40000 and 40020 12 km north and 3
    km west and east (6 km apart), 40010 14 km north. The largest distance between 2 rows, west to east, is
    6 km: not one rough point. 40000, 40010 and 40020 each lie within 10 km of 40030: no outliers, so
    nothing moves. The first and last rows in sorted order are only 4.1 km apart."""
    g = (3.0, 101.5)
    a, b, c = east(north(g, 12), -3), north(g, 14), east(north(g, 12), 3)
    gn = [("40000", "Alpha", "Selangor", *a), ("40010", "Alpha", "Selangor", *b),
          ("40020", "Alpha", "Selangor", *c), ("40030", "Alpha", "Selangor", *north(g, 9.9))]
    rows = bd.load_postcode_rows(write_geonames(tmp_path / "pcs.csv", gn))
    places = bd.load_places(write_places(tmp_path / "pl.csv", [("Alpha", "", *g, "PPLA", "Selangor", 0)]))
    pts = [r[2] for v in rows.values() for r in v]
    assert bd._spread(pts) == pytest.approx(bd.km(*a, *c), abs=1e-9) and 5.9 < bd._spread(pts) < 6.1
    assert bd.km(*sorted(pts)[0], *sorted(pts)[-1]) < 5
    assert bd.check_postcodes(rows, {}, places, {})["to_place"] == []


def test_moved_postcodes_never_make_a_place_compact(tmp_path):
    """Kota's rows lie 8 to 12.5 km north of its place point: one rough point. 12 postcodes at 8 to 9 km stay,
    and 41000 to 41002, 10.5 to 12.5 km out, go to the place point. 42000 is listed under Kota (9 km north)
    and Lain (31 km south; not in the place list), so its point lies 11 km south, 19.5 km from the median of
    Kota's 15 others; it is not moved. Moved postcodes count among the others but never inside the compact
    core, wherever their GeoNames points lie: 12 of 15 are too few, Kota is not compact, and 42000 stays.
    Counted at their GeoNames points, 2 to 4 km from that median, they would make Kota compact."""
    g = (3.0, 101.5)
    gn = [(f"40{i:03d}", "Kota", "Selangor", *north(g, 8.0 + (i % 3) * 0.5)) for i in range(12)]
    gn += [(f"41{i:03d}", "Kota", "Selangor", *north(g, d)) for i, d in enumerate((10.5, 11.5, 12.5))]
    gn += [("42000", "Kota", "Selangor", *north(g, 9)), ("42000", "Lain", "Selangor", *north(g, -31))]
    res = run_places(tmp_path, gn, [("Kota", "", *g, "PPLA2", "Selangor", 50000)])
    assert sorted(m[1] for m in res["to_place"]) == ["41000", "41001", "41002"]
    assert res["set_aside"] == [] and res["postcodes"]["42000"] == r4(north(g, -11))


def test_with_a_place_list_the_check_still_only_moves_or_removes_and_is_deterministic(tmp_path):
    rnd = random.Random(20261117)
    towns = {"Alpha": (3.0, 101.5), "Beta": (4.0, 101.0), "Gamma": (5.5, 100.4), "Delta": (2.2, 102.2),
             "Epsilon": (6.0, 116.0)}
    gn, shops, addresses = [], [], {}
    for i in range(160):
        name = rnd.choice(sorted(towns))
        base = towns[name]
        pc = f"{10000 + 7 * i:05d}"
        if rnd.random() < 0.25:
            pt = north(base, rnd.choice([-1, 1]) * rnd.uniform(11, 300))
        else:
            pt = (base[0] + rnd.uniform(-0.08, 0.08), base[1] + rnd.uniform(-0.08, 0.08))
        gn.append((pc, name, "State", *pt))
        if rnd.random() < 0.15:
            other = rnd.choice(sorted(towns))
            gn.append((pc, other, "State", *north(towns[other], rnd.uniform(-5, 5))))
        for _ in range(rnd.randint(0, 3)):
            c = len(shops) + 1
            spot = (base[0] + rnd.uniform(-0.12, 0.12), base[1] + rnd.uniform(-0.12, 0.12))
            prec = rnd.choice(["name", "address", "postcode", "district"])
            shops.append((c, round(spot[0], 5), round(spot[1], 5), prec, rnd.choice([pc, ""])))
            addresses[c] = ("State", f"LOT {c}, {pc} {name.upper()}" if rnd.random() < 0.7 else "JALAN BESAR")
    place_rows = [(name, "", *north(pt, rnd.uniform(-3, 3)), rnd.choice(["PPL", "PPLA"]), "State",
                   rnd.choice([0, 5000])) for name, pt in sorted(towns.items())]
    place_rows.append(("Gamma", "", *north(towns["Gamma"], 80), "PPL", "State", 100))   # a village of the same name
    gn_path = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = bd.load_geo(write_cache(tmp_path / "premise_geo.csv", shops))
    places = bd.load_places(write_places(tmp_path / "places_my.csv", place_rows))
    rows = bd.load_postcode_rows(gn_path)
    before = bd.load_postcodes(gn_path)
    res = bd.check_postcodes(rows, geo, places, addresses)
    out, c = res["postcodes"], res["checks"]
    plain = bd.check_postcodes(rows, geo)   # without the place list
    assert list(out) == sorted(out) and set(out) <= set(before)
    removed = set(before) - set(out)
    assert removed == {pc for pc, _, _ in res["set_aside"]} and len(removed) == c["set_aside"]
    moved = {m[1] for m in res["to_place"]}
    assert len(moved) == c["to_place"] and not moved & removed and c["to_place"] > 0 and c["from_shops"] > 0
    far = {m[1] for m in res["far_rows"]}   # without a GeoNames row far from its place's point, then judged as usual
    assert far
    # 2 shops that agree but lie more than ANCHOR_KM apart, neither within BACKED_KM of the GeoNames point,
    # place a postcode only without the place list.
    split = set()
    for pc in before:
        close = bd._agreeing([g[:2] for g in geo.values() if g[2] in ("name", "address") and g[3] == pc])
        if close and len(close) == 2 and bd.km(*close[0], *close[1]) > bd.ANCHOR_KM and min(
                bd.km(*q, *bd._mean_point([r[2] for r in rows[pc]])) for q in close) > bd.BACKED_KM:
            split.add(pc)
    assert split <= set(plain["postcodes"]) | {pc for pc, _, _ in plain["set_aside"]}
    place_points = {tuple(r4(p[3])) for p in places}
    for pc, v in out.items():
        if pc in moved:   # moved to a place-list point more than ANCHOR_KM from its point (its GeoNames point,
            #                 or, without a far row, its other row)
            starts = [r[2] for r in rows[pc]] if pc in far else [bd._mean_point([r[2] for r in rows[pc]])]
            assert tuple(v) in place_points and any(bd.km(*q, *v) > bd.ANCHOR_KM for q in starts)
        elif pc in far:   # the average of its other rows: here 2 rows on 2 points, so the other row
            assert len({r[2] for r in rows[pc]}) == 2 and v in [r4(r[2]) for r in rows[pc]]
        else:             # as without the place list: its GeoNames point or where its shops agree
            assert v == (before[pc] if pc in split else plain["postcodes"].get(pc, before[pc]))
        assert all(isinstance(x, float) and round(x, 4) == x for x in v) and len(v) == 2
    shop_moves = [m for m in plain["moves"] if m[1] not in split]
    assert (c["from_shops"], c["moved"], res["moves"], res["kept"]) == (
        plain["checks"]["from_shops"] - len(split), len(shop_moves), shop_moves, plain["kept"])
    assert [m[0] for m in res["to_place"]] == sorted((m[0] for m in res["to_place"]), reverse=True)
    assert [m[0] for m in res["far_rows"]] == sorted((m[0] for m in res["far_rows"]), reverse=True)
    # Deterministic: the same answer from every input in the opposite order.
    assert bd.check_postcodes(dict(reversed(list(rows.items()))), dict(reversed(list(geo.items()))),
                              list(reversed(places)), dict(reversed(list(addresses.items())))) == res


def test_a_row_far_from_its_places_point_does_not_count_while_another_row_does(tmp_path, monkeypatch):
    """GeoNames lists 42920 under Pelabuhan Klang, on a point in Kuala Lumpur 42 km from Port Klang's point
    in the place list, and under Pulau Indah, the island off the town (no entry in the place list). Its
    average lay 23 km from each, in Petaling. A row more than PLACE_KM from its own place's point does not
    count while a row on another point does: 42920 is placed at its Pulau Indah row. The limit is exact.
    The build lists such postcodes by how far their point moved, largest first."""
    port, kl, indah = (2.99959, 101.39287), (3.1373, 101.7471), (2.9662, 101.3768)
    gn = [("42000", "Pelabuhan Klang", "Selangor", *north(port, 2)), ("42009", "Pelabuhan Klang", "Selangor", *north(port, 1)),
          ("42920", "Pelabuhan Klang", "Selangor", *kl), ("42920", "Pulau Indah", "Selangor", *indah)]
    places = [("Pelabuhan Klang", "Port Klang", *port, "PPL", "Selangor", 20000)]
    res = run_places(tmp_path, gn, places)
    avg = bd._mean_point([kl, indah])
    assert res["postcodes"]["42920"] == r4(indah)
    assert res["far_rows"] == [(pytest.approx(bd.km(*avg, *indah)), "42920", "Pelabuhan Klang (Selangor) / Pulau Indah "
                                "(Selangor)", f"its row of Pelabuhan Klang (Selangor) lies {bd.km(*kl, *port):.1f} km "
                                "from that place's point")]
    assert res["to_place"] == [] and res["set_aside"] == [] and res["checks"]["to_place"] == 0
    assert bd.check_postcodes(bd.load_postcode_rows(tmp_path / "p_postcodes.csv"), {})["postcodes"]["42920"] == r4(avg)
    d = bd.km(*kl, *port)
    monkeypatch.setattr(bd, "PLACE_KM", d)   # exactly PLACE_KM from its place's point, the row counts
    res = run_places(tmp_path, gn, places, name="exact")
    assert res["postcodes"]["42920"] == r4(avg) and res["far_rows"] == []
    monkeypatch.setattr(bd, "PLACE_KM", math.nextafter(d, 0))
    assert run_places(tmp_path, gn, places, name="below")["postcodes"]["42920"] == r4(indah)
    monkeypatch.undo()
    # 42990's Pelabuhan Klang row lies 70 km north of Port Klang's point and its other row 3 km, so its point
    # moves 33.5 km, more than 42920's 22.7 km: it comes first, though its postcode comes after.
    gn += [("42990", "Pelabuhan Klang", "Selangor", *north(port, 70)), ("42990", "Lain", "Selangor", *north(port, 3))]
    res = run_places(tmp_path, gn, places, name="two")
    assert [(m[1], round(m[0], 1)) for m in res["far_rows"]] == [("42990", 33.5), ("42920", round(bd.km(*avg, *indah), 1))]
    assert res["postcodes"]["42990"] == r4(north(port, 3)) and res["set_aside"] == []


def test_a_far_row_counts_when_its_places_shops_lie_nearer_to_it_and_shop_points_keep_every_row(tmp_path):
    """Kota's point lies 31 km from 41000's Kota row. When Kota's shops (those of 41100) lie 20 km north of
    the point, 11 km from that row, the row counts: 41000 keeps the average of its 2 rows. When they lie
    1 km from the point, the row does not count, and 41000 is placed at its other row, Lain's. 41100, placed
    from its shops, keeps their point whatever its rows."""
    k = (2.5, 102.5)
    gn = [("41000", "Kota", "Johor", *north(k, 31)), ("41000", "Lain", "Johor", *north(k, -1)),
          ("41100", "Kota", "Johor", *north(k, 40)), ("41100", "Lain", "Johor", *north(k, -1))]
    places = [("Kota", "", *k, "PPL", "Johor", 5000)]

    def shops(at):
        return [(1, *as_read(north(k, at + 0.2)), "name", "41100"), (2, *as_read(north(k, at - 0.2)), "address", "41100")]

    res = run_places(tmp_path, gn, places, shops(20), name="near_row")
    assert res["postcodes"]["41000"] == r4(north(k, 15)) and res["far_rows"] == []
    assert res["postcodes"]["41100"] == r4(bd._median_point([as_read(north(k, 20.2)), as_read(north(k, 19.8))]))
    res = run_places(tmp_path, gn, places, shops(1), name="near_point")
    assert res["postcodes"]["41000"] == r4(north(k, -1))
    assert res["far_rows"] == [(pytest.approx(16.0), "41000", "Kota (Johor) / Lain (Johor)",
                                f"its row of Kota (Johor) lies {bd.km(*north(k, 31), *k):.1f} km from that place's point")]
    assert res["postcodes"]["41100"] == r4(bd._median_point([as_read(north(k, 1.2)), as_read(north(k, 0.8))]))


def test_a_far_row_counts_when_the_other_rows_lie_far_from_that_place_too(tmp_path):
    """GeoNames lists 81500 under Pekan Nenas, 20 km north-west of that town's point in the place list, and
    under Sungai Mati, 21 km east of it. The place list's Sungai Mati (Bandar Sungai Mati, other name Sungai
    Mati) lies near Muar, 145 km from that row, but the Pekan Nenas row lies 109 km from it too: the postcode
    is not near that Sungai Mati, and its row may name another place of that name. So it keeps counting, and
    81500 keeps the average of its 2 rows, 10 km from Pekan Nenas (its Pekan Nenas row lies 20 km from it)."""
    nenas, mati = (1.51, 103.5141), (2.1467, 102.5655)
    rows = [("81500", "Pekan Nenas", "Johor", 1.6627, 103.4182), ("81500", "Sungai Mati", "Johor", 1.5141, 103.7014),
            ("84400", "Sungai Mati", "Johor", 2.0749, 102.7197)]
    places = [("Pekan Nenas", "Pekan Nanas", *nenas, "PPL", "Johor", 23292),
              ("Bandar Sungai Mati", "Sungai Mati", *mati, "PPL", "Johor", 1737)]
    res = run_places(tmp_path, rows, places)
    avg = bd._mean_point([(1.6627, 103.4182), (1.5141, 103.7014)])
    assert bd.km(1.5141, 103.7014, *mati) > bd.PLACE_KM and bd.km(1.6627, 103.4182, *mati) > bd.PLACE_KM
    assert res["postcodes"]["81500"] == r4(avg) and res["far_rows"] == [] and 9 < bd.km(*avg, *nenas) < 11
    # Had Bandar Sungai Mati lain 25 km from the Pekan Nenas row (and 52 km from the Sungai Mati row), that
    # row would not count, and 81500 would lie on its Pekan Nenas row.
    near = north((1.6627, 103.4182), 25)
    assert bd.km(1.5141, 103.7014, *near) > bd.PLACE_KM
    res = run_places(tmp_path, rows, places[:1] + [("Bandar Sungai Mati", "Sungai Mati", *near, "PPL", "Johor", 1737)],
                     name="near")
    assert res["postcodes"]["81500"] == [1.6627, 103.4182] and [m[1] for m in res["far_rows"]] == ["81500"]
    # The limit is exact: the other rows exactly PLACE_KM from that place's point are near enough.
    d = bd.km(1.6627, 103.4182, *near)
    near_places = places[:1] + [("Bandar Sungai Mati", "Sungai Mati", *near, "PPL", "Johor", 1737)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bd, "PLACE_KM", d)
        assert run_places(tmp_path, rows, near_places, name="exact")["postcodes"]["81500"] == [1.6627, 103.4182]
        mp.setattr(bd, "PLACE_KM", math.nextafter(d, 0))
        assert run_places(tmp_path, rows, near_places, name="below")["postcodes"]["81500"] == r4(avg)
    # Rows of 2 places far from their points: both stop counting only when the other rows lie near both places.
    c = (3.0, 102.0)
    rows = [("70000", "Alfa", "Johor", *north(c, 50)), ("70000", "Bravo", "Johor", *north(c, -40)),
            ("70000", "Lain", "Johor", *c)]
    alfa, bravo = ("Alfa", "", *north(c, 10), "PPL", "Johor", 900), ("Bravo", "", *north(c, -100), "PPL", "Johor", 900)
    res = run_places(tmp_path, rows, [alfa, bravo], name="two")
    assert res["postcodes"]["70000"] == r4(north(c, 10 / 3)) and res["far_rows"] == []
    bravo = ("Bravo", "", *north(c, 25), "PPL", "Johor", 900)   # 65 km from the Bravo row, 25 km from Lain's
    res = run_places(tmp_path, rows, [alfa, bravo], name="both")
    assert res["postcodes"]["70000"] == r4(c) and [m[1] for m in res["far_rows"]] == ["70000"]


def test_a_far_row_exactly_as_far_from_its_places_shops_as_the_point_counts(tmp_path):
    """Kota's point (the place list's Kota, chosen by the shops naming Kota) and 41000's Kota row lie on one
    parallel, a quarter of a degree of longitude either side of those shops: exactly as far from them, in
    floating point too. The row lies 56 km from Kota's point, but it is not further from the shops than the
    point is, so it counts; a little further out, it does not."""
    ref, g = (2.5, 102.5), (2.5, 102.25)
    shops = [(1, 2.5, 102.46875, "name", ""), (2, 2.5, 102.53125, "address", "")]   # their median: ref
    addresses = {1: ("Johor", "PASAR AWAM KOTA"), 2: ("Johor", "41100 KOTA, JOHOR")}
    places = [("Kota", "", *g, "PPL", "Johor", 0)]
    assert bd.km(*g, *ref) == bd.km(2.5, 102.75, *ref) and bd.km(*g, 2.5, 102.75) > bd.PLACE_KM
    gn = [("41000", "Kota", "Johor", 2.5, 102.75), ("41000", "Lain", "Johor", *ref)]
    res = run_places(tmp_path, gn, places, shops, addresses)
    assert res["postcodes"] == {"41000": [2.5, 102.625]} and res["far_rows"] == []
    gn[0] = ("41000", "Kota", "Johor", 2.5, 102.7501)
    res = run_places(tmp_path, gn, places, shops, addresses, name="out")
    assert res["postcodes"] == {"41000": list(ref)} and [m[1] for m in res["far_rows"]] == ["41000"]


def test_without_its_far_row_a_postcode_is_judged_at_its_new_point(tmp_path):
    """41000's Kota row lies 70 km north of Kota's point; its Lain row 2 km south of it. Without the far row,
    its point is the Lain row, 2 km from the median of Kota's 3 other postcodes, and it stays (at the average
    of both rows it would lie 34 km from them, and be set aside). 42000's row of Xa lies 35 km from Xa's
    point, 10 km east of the middle of its 2 other rows, which lie 70 km apart, and Xa's point lies 25 km
    west of that middle: without the far row, its point, the average of the 2 others, lies 35 km from each
    of them, and it is set aside. The far row stops counting because that average, 25 km from Xa's point,
    lies within PLACE_KM of it, though each of the 2 other rows lies 43 km from it."""
    k = (3.0, 101.5)
    gn = [("40100", "Kota", "Perak", *north(k, 1)), ("40200", "Kota", "Perak", *k),
          ("40300", "Kota", "Perak", *north(k, -1)),
          ("41000", "Kota", "Perak", *north(k, 70)), ("41000", "Lain", "Perak", *north(k, -2))]
    m = (4.5, 101.0)
    gn += [("42000", "Xa", "Perak", *east(m, 10)), ("42000", "Yb", "Perak", *north(m, 35)),
           ("42000", "Zc", "Perak", *north(m, -35))]
    res = run_places(tmp_path, gn, [("Kota", "", *k, "PPL", "Perak", 5000), ("Xa", "", *east(m, -25), "PPL", "Perak", 900)])
    xa = east(m, -25)
    assert [round(bd.km(*p, *xa), 1) for p in (north(m, 35), north(m, -35), m)] == [43.0, 43.0, 25.0]
    assert res["postcodes"]["41000"] == r4(north(k, -2)) and "42000" not in res["postcodes"]
    assert res["set_aside"] == [("42000", "Xa (Perak) / Yb (Perak) / Zc (Perak)",
                                 f"{bd.km(*bd._mean_point([north(m, 35), north(m, -35)]), *north(m, 35)):.1f} km from "
                                 "the nearest of its 2 GeoNames rows, whose average it is")]
    assert [x[1] for x in res["far_rows"]] == ["41000", "42000"] and res["checks"]["far_from_rows"] == 1


def test_a_postcode_kept_by_one_of_its_shops_keeps_its_far_row(tmp_path):
    """41000's Kota row lies 40 km north of Kota's point, its Lain row 1 km south of it, and their average 19.5
    km north. Its 2 shops lie 12 km apart, one 1 km from that average: the shop backs the GeoNames point, and
    the postcode keeps it, far row and all."""
    k = (2.5, 102.5)
    gn = [("41000", "Kota", "Johor", *north(k, 40)), ("41000", "Lain", "Johor", *north(k, -1))]
    shops = [(1, *as_read(north(k, 20.5)), "name", "41000"), (2, *as_read(north(k, 32.5)), "address", "41000")]
    res = run_places(tmp_path, gn, [("Kota", "", *k, "PPL", "Johor", 5000)], shops)
    assert [x[0] for x in res["kept"]] == ["41000"] and res["far_rows"] == []
    assert res["postcodes"] == {"41000": r4(north(k, 19.5))}


def test_the_rows_that_count_keep_their_weight_in_the_average(tmp_path):
    """43000's Kota row lies 45 km from Kota's point and stops counting. Of the rows that count, 2 (Lain and
    Lain Dua) share a point 2 km north of Kota's point and 1 (Baru) lies 10 km south: their average, 2 km
    south, is its point (the average of the 2 points would lie 4 km south)."""
    k = (2.5, 102.5)
    gn = [("43000", "Kota", "Johor", *north(k, 45)), ("43000", "Lain", "Johor", *north(k, 2)),
          ("43000", "Lain Dua", "Johor", *north(k, 2)), ("43000", "Baru", "Johor", *north(k, -10))]
    res = run_places(tmp_path, gn, [("Kota", "", *k, "PPL", "Johor", 5000)])
    assert res["postcodes"] == {"43000": r4(north(k, -2))}
    assert [x[:2] for x in res["far_rows"]] == [(pytest.approx(bd.km(*north(k, 9.75), *north(k, -2))), "43000")]


def test_a_far_row_on_a_point_that_still_counts_does_not_hold_back_another(tmp_path):
    """44000's Kota row lies 50 km from Kota's point, on the point of its Lain row (Lain has no place point),
    so it still counts. Its Jauh row lies 61 km from Jauh's point, and the rows that count lie 10 km from
    Jauh's point: the Jauh row stops counting. Only the rows that stop counting must lie near their place's
    point: that Kota's point lies 50 km away holds nothing back."""
    k = (2.5, 102.5)
    top = north(k, 50)
    gn = [("44000", "Kota", "Johor", *top), ("44000", "Lain", "Johor", *top),
          ("44000", "Jauh", "Johor", *east(top, 60))]
    places = [("Kota", "", *k, "PPL", "Johor", 5000), ("Jauh", "", *north(k, 40), "PPL", "Johor", 900)]
    res = run_places(tmp_path, gn, places)
    assert res["postcodes"] == {"44000": r4(top)}
    assert res["far_rows"] == [(pytest.approx(20.0, abs=0.1), "44000", "Jauh (Johor) / Kota (Johor) / Lain (Johor)",
                                f"its row of Jauh (Johor) lies {bd.km(*east(top, 60), *north(k, 40)):.1f} km from that "
                                "place's point")]


def test_a_postcode_its_shops_do_not_place_is_not_confirmed(tmp_path):
    """Confirmed postcodes are those placed or kept by their shops (or moved to or near their place's point),
    not any postcode with a found shop. 98057 has one found shop, on its GeoNames point 431 km from Miri's
    confirmed postcodes: it is set aside, and it is no reference for 98058 on the same point. So too with 2
    shops 12 km apart, neither within BACKED_KM of its point, which with a place list do not place it."""
    miri = (4.4, 114.0)
    far = north(miri, -431)
    gn = [("98000", "Miri", "Sarawak", *north(miri, 1)), ("98007", "Miri", "Sarawak", *north(miri, -1)),
          ("98057", "Baram", "Sarawak", *far), ("98058", "Baram", "Sarawak", *far)]
    miri_shops = [(1, *miri, "name", "98000"), (2, *miri, "address", "98000"), (3, *miri, "name", "98007"),
                  (4, *miri, "address", "98007")]
    res = run_check(tmp_path, gn, miri_shops + [(5, *as_read(far), "name", "98057")])
    assert sorted(res["postcodes"]) == ["98000", "98007"] and [a[0] for a in res["set_aside"]] == ["98057", "98058"]
    assert res["checks"] == {"from_shops": 2, "moved": 0, "set_aside": 2, "far_from_prefix": 2}
    two = miri_shops + [(5, *as_read(north(far, 6)), "name", "98057"),
                        (6, *as_read(north(far, -6)), "address", "98057")]
    res = run_places(tmp_path, gn, [("Kota Lain", "", 6.0, 116.0, "PPL", "Sabah", 100)], two, name="two")
    assert res["kept"] == [] and sorted(res["postcodes"]) == ["98000", "98007"]
    assert res["checks"] == {"from_shops": 2, "moved": 0, "to_place": 0, "set_aside": 2, "far_from_prefix": 2}


def test_a_postcode_set_aside_in_step_3_is_no_reference_for_its_first_digits(tmp_path):
    """77000 lies at Kota's point, so it would be confirmed, but the median of Kota's 3 other postcodes
    (one placed from shops 31 km north) lies 32 km from it: step 3 leaves it out. Left out, it is no
    reference: 77090, 70 km south, has 1 confirmed postcode that shares its first 3 digits, and stays."""
    g = (3.0, 101.5)
    ref = north(g, 31)
    gn = [("77000", "Kota", "Selangor", *g), ("77001", "Kota", "Selangor", *north(g, 30)),
          ("77002", "Kota", "Selangor", *north(g, 32)), ("77003", "Kota", "Selangor", *north(g, 33)),
          ("77090", "Lain", "Selangor", *north(g, -70))]
    shops = [(1, *as_read(north(ref, 0.2)), "name", "77001"), (2, *as_read(north(ref, -0.2)), "address", "77001")]
    res = run_places(tmp_path, gn, [("Kota", "", *g, "PPL", "Selangor", 5000)], shops)
    assert [a[0] for a in res["set_aside"]] == ["77000"] and "far_from_prefix" not in res["checks"]
    assert res["postcodes"]["77090"] == r4(north(g, -70))


def test_the_sungai_siput_guard_reads_the_notable_entry_not_the_first():
    """2 entries by name: a village 30 km away (no population, listed first) and the town (notable). An entry
    with the name among its other names lies 1 km from the town, so the town is the point; 12 km from it,
    the list may mean either, and there is no point."""
    town = (4.0, 101.0)
    village = north(town, 30)
    assert bd.place_point([(village, "PPL", 0), (town, "PPL", 5000)], [(north(town, 1), "PPL", 0)], None) == town
    assert bd.place_point([(village, "PPL", 0), (town, "PPL", 5000)], [(north(town, 12), "PPL", 0)], None) is None


def test_the_veto_on_an_entry_by_another_name_reads_its_own_names_too(tmp_path):
    """Both vetoes read the entry's own name as well as its other names. The entry named Tasek Gelugor
    (other name Gelugor) is Tasek Gelugor (13300), a place of the postcode table with none of Gelugor's
    postcodes, so 11700 Gelugor does not take its point. The entry named Kampung Kuala Sepetang (other
    name Kuala Sepetang) shares its name with an entry 32 km away, so 34650 does not take its point either;
    without that entry, it does."""
    island, tasek = (5.3653, 100.3067), (5.4803, 100.4985)
    gn = [("11700", "Gelugor", "Pulau Pinang", *island), ("13300", "Tasek Gelugor", "Pulau Pinang", *north(tasek, 2))]
    res = run_places(tmp_path, gn, [("Tasek Gelugor", "Gelugor", *tasek, "PPL", "Pulau Pinang", 135786)])
    assert res["postcodes"]["11700"] == list(island) and res["to_place"] == []
    s = (4.5429, 100.6597)
    gn = [("34650", "Kuala Sepetang", "Perak", *north(s, 40))]
    entry = ("Kampung Kuala Sepetang", "Kuala Sepetang", *s, "PPL", "Perak", 1264)
    twin = ("Kampung Kuala Sepetang", "", *north(s, 32), "PPL", "Perak", 0)
    assert run_places(tmp_path, gn, [entry, twin], name="twin")["to_place"] == []
    assert [m[1] for m in run_places(tmp_path, gn, [entry], name="one")["to_place"]] == ["34650"]


def test_the_veto_on_an_entry_by_another_name_reads_the_places_postcodes_in_every_spelling(tmp_path):
    """Here GeoNames spells the state 2 ways: 11700 is Gelugor and Tasek Gelugor in 'Pulau Pinang', 11900 is
    Gelugor in 'Penang'. The entry Tasek Glugor (other names Gelugor and Tasek Gelugor) shares 11700 with
    Gelugor, whose postcodes count in both spellings, so no veto: both postcodes take its point."""
    island, tasek = (5.3653, 100.3067), (5.4803, 100.4985)
    gn = [("11700", "Gelugor", "Pulau Pinang", *island), ("11700", "Tasek Gelugor", "Pulau Pinang", *island),
          ("11900", "Gelugor", "Penang", *north(island, 1))]
    res = run_places(tmp_path, gn, [("Tasek Glugor", "Gelugor|Tasek Gelugor", *tasek, "PPL", "Pulau Pinang", 135786)])
    assert sorted(m[1] for m in res["to_place"]) == ["11700", "11900"]
    assert res["postcodes"] == {"11700": r4(tasek), "11900": r4(tasek)}


def test_one_other_name_of_an_entry_for_two_places_of_the_table(tmp_path):
    """Bandar X (notable) has the other names Alpha and Beta, two places of the postcode table with their
    own postcodes. Neither name holds the other, so neither vetoes: both places take Bandar X's point."""
    x = (3.0, 101.5)
    gn = [("77000", "Alpha", "Johor", *north(x, 20)), ("77100", "Beta", "Johor", *north(x, -20))]
    res = run_places(tmp_path, gn, [("Bandar X", "Alpha|Beta", *x, "PPL", "Johor", 5000)])
    assert res["postcodes"] == {"77000": r4(x), "77100": r4(x)}


def test_compact_min_counts_the_moved_postcodes(tmp_path):
    """Kota has 10 other postcodes: 9 within 1 km of their median (on 2 points) and 1 moved to the place's
    point. COMPACT_MIN counts all 10, and 9 in 10 lie close: compact, so 42000, 16 km out, is set aside."""
    g = (3.0, 101.5)
    c = north(g, 5)
    gn = [(f"40{i:03d}", "Kota", "Selangor", *north(c, 0.3 * (i % 2))) for i in range(9)]
    gn += [("41000", "Kota", "Selangor", *north(g, -40))]   # an outlier of Kota: moved
    gn += [("42000", "Kota", "Selangor", *north(c, 16)), ("42000", "Lain", "Selangor", *north(c, 16))]   # not moved
    res = run_places(tmp_path, gn, [("Kota", "", *g, "PPLA2", "Selangor", 50000)])
    assert [m[1] for m in res["to_place"]] == ["41000"]
    # The median of the 10 others is c: 5 lie on it, 4 at 0.3 km north and 41000 on the place's point, 5 km south.
    assert res["set_aside"] == [("42000", "Kota (Selangor) / Lain (Selangor)", f"{bd.km(*north(c, 16), *c):.1f} km from "
                                 "the median of the 10 other postcodes of its place, 90% of which lie within 0.3 km "
                                 "of it")]


# ---------- the build ----------

def test_build_writes_the_checked_table_and_prints_moves_and_set_asides(tmp_path, capsys):
    """Every located row of the cache counts, also shops that are not in this build."""
    raw = te.write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")], [(1, "Johor", "Muar"), (2, "Johor", "Muar")],
                       {"2026-10": [("2026-10-10", 1, 1, 3.0), ("2026-10-10", 2, 1, 3.1)]})
    muar, found = (1.9909, 102.823), (2.0522, 102.5713)
    ipoh, jb, bg = (4.6, 101.08), (1.43, 103.62), (4.47, 101.04)
    gn = [("84000", "Muar", "Johor", *muar)]
    gn += [(f"300{i:02d}", "Ipoh", "Perak", *north(ipoh, 2 + i)) for i in range(1, 13)]   # 3..14 km from shops
    gn += [("79100", "Nusajaya", "Johor", *jb), ("79200", "Nusajaya", "Johor", *north(jb, 1)),
           ("79250", "Nusajaya", "Johor", *north(jb, -1)), ("79570", "Nusajaya", "Johor", 6.12, 100.37)]
    gn += [("31000", "Batu Gajah", "Perak", *bg)]
    shops = [(1, *found, "name", "84000"),                    # shop 1 is in this build ...
             (500, found[0] + 0.001, found[1], "address", "84000")]   # ... shop 500 is not
    shops += [(600 + 2 * i + k, *ipoh, "name", f"300{i:02d}") for i in range(1, 13) for k in (0, 1)]
    # 31000's 2 shops lie 12.6 km apart, one 3.7 km from its GeoNames point: it keeps that point.
    shops += [(700, *north(bg, 3.7), "name", "31000"), (701, *north(bg, -8.9), "address", "31000")]
    pcs = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = write_cache(tmp_path / "premise_geo.csv", shops)
    out = tmp_path / "out"
    assert bd.main(["--raw", str(raw), "--out", str(out), "--geo", str(geo), "--postcodes", str(pcs),
                    "--district-geo", "", "--places", ""]) == 0   # shops only; the place list: see below
    text = capsys.readouterr().out
    meta = te.read_json(out / "meta.json")
    g = meta["geo"]
    want = {pc: [round(x, 4) for x in pt] for pc, pt in bd.load_postcodes(pcs).items() if pc != "79570"}
    want["84000"] = r4((statistics.median([found[0], found[0] + 0.001]), found[1]))
    want.update({f"300{i:02d}": r4(ipoh) for i in range(1, 13)})
    assert g["postcodes"] == want and list(g["postcodes"]) == sorted(want)
    assert g["postcode_checks"] == {"from_shops": 13, "moved": 13, "set_aside": 1}
    assert g["postcode_source"] == "GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap"
    assert g["postcode_source_url"] == "https://www.geonames.org/"
    js = (out / "meta.js").read_text(encoding="utf-8")
    assert json.loads(js[js.index("=", js.index("]")) + 1:-2]) == meta   # the .js twin holds the same table

    assert ("postcode table: 17 postcodes from GeoNames, checked against shops found by OpenStreetMap: "
            "13 placed at those shops (13 moved more than 2 km), 1 set aside") in text
    moved = re.findall(r"^  moved (\d{5}) (.+): ([\d.]+) km$", text, flags=re.M)
    assert len(moved) == 10   # the 10 largest moves, largest first
    assert moved[0][:2] == ("84000", "Muar (Johor)") and [m[0] for m in moved[1:]] == [f"300{i:02d}" for i in range(12, 3, -1)]
    assert [float(m[2]) for m in moved] == sorted((float(m[2]) for m in moved), reverse=True)
    d = bd.km(6.12, 100.37, *jb)
    assert f"  set aside 79570 Nusajaya (Johor): {d:.1f} km from the median of the 3 other postcodes of its place" in text
    assert re.findall(r"^  kept .*$", text, flags=re.M) == [
        "  kept 31000 Batu Gajah (Perak) at its GeoNames point: its 2 shops lie 12.6 km apart and one of them "
        "is 3.7 km from that point"]

    # check_data reports the counts; 1 of 18 GeoNames postcodes set aside is more than 5%.
    res = cd.check(out)
    assert ("Postcode check: 13 postcode(s) placed at the shops OpenStreetMap found there (13 moved more than "
            "2 km), 1 set aside as far from the other postcodes of their place.") in res["notes"]
    assert any("set aside 1 of 18 GeoNames postcodes (5.6%, more than 5%)" in w for w in res["warns"])


def test_build_checks_the_table_against_the_place_list(tmp_path, capsys, monkeypatch):
    """The build reads the place list (etl/geo/places_my.csv unless --places names another; '' for none)
    and the shops' addresses from PriceCatcher's premise list, and says what moved to the place list's
    points: the 10 largest moves, largest first."""
    raw = te.write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")],
                       [(1, "Sabah", "Penampang"), (2, "Sabah", "Penampang"), (3, "Johor", "Muar")],
                       {"2026-10": [("2026-10-10", 1, 1, 3.0), ("2026-10-10", 2, 1, 3.1), ("2026-10-10", 3, 1, 3.2)]})
    (raw / "lookup_premise.csv").write_text(
        "premise_code,premise,address,premise_type,state,district\n"
        '1,Kedai 1,"LOT 1, GRAND PLAZA PUTATAN",Pasar Mini,Sabah,Penampang\n'
        '2,Kedai 2,"88200 PUTATAN, SABAH",Pasar Mini,Sabah,Penampang\n'
        '3,Kedai 3,"84000 MUAR, JOHOR",Pasar Mini,Johor,Muar\n', encoding="utf-8")
    town = (5.9258, 116.0609)
    boxes = [f"887{i:02d}" for i in range(1, 13)]
    gn = [("88200", "Putatan", "Sabah", *north(town, 1))]
    gn += [(pc, "Putatan", "Sabah", *north(town, -(90 + i))) for i, pc in enumerate(boxes, 1)]   # 91-102 km out
    pcs = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = write_cache(tmp_path / "premise_geo.csv", [(1, *as_read(north(town, 0.5)), "name", ""),
                                                     (2, *as_read(north(town, -0.5)), "address", "")])
    # No population: only the shops whose address names Putatan make it Putatan's point.
    places = write_places(tmp_path / "places_my.csv", [("Putatan", "", *town, "PPL", "Sabah", 0)])
    args = ["--raw", str(raw), "--geo", str(geo), "--postcodes", str(pcs), "--district-geo", ""]
    out = tmp_path / "out"
    assert bd.main(args + ["--out", str(out), "--places", str(places)]) == 0
    text = capsys.readouterr().out
    g = te.read_json(out / "meta.json")["geo"]
    assert g["postcodes"] == {"88200": r4(north(town, 1)), **{pc: r4(town) for pc in boxes}}
    assert g["postcode_checks"] == {"from_shops": 0, "moved": 0, "to_place": 12, "set_aside": 0}
    assert g["postcode_source"] == ("GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap and the "
                                    "GeoNames place list")
    assert g["postcode_source_url"] == "https://www.geonames.org/"
    assert ("postcode table: 13 postcodes from GeoNames, checked against shops found by OpenStreetMap and the GeoNames "
            "place list: 0 placed at those shops (0 moved more than 2 km), 12 moved to their place's point in the "
            "place list, 0 set aside") in text
    lines = re.findall(r"^  to place point (\d{5}) Putatan \(Sabah\): ([\d.]+) km \((.+)\)$", text, flags=re.M)
    assert [m[0] for m in lines] == boxes[::-1][:10]
    assert lines[0][1:] == ("102.0", "more than 10 km from each of the 1 other postcodes of its place near that point")
    assert ("Postcode check: 0 postcode(s) placed at the shops OpenStreetMap found there (0 moved more than 2 km), "
            "12 moved to their place's point in the GeoNames place list, 0 set aside as far from the other "
            "postcodes of their place.") in cd.check(out)["notes"]
    # A build of one state reads the addresses of every shop: the 2 Sabah shops name Putatan in a Johor build too.
    assert bd.main(args + ["--out", str(tmp_path / "johor"), "--places", str(places), "--state", "Johor"]) == 0
    capsys.readouterr()
    johor = te.read_json(tmp_path / "johor" / "meta.json")
    assert [a["district"] for a in johor["areas"]["Johor"]] == ["Muar"] and list(johor["areas"]) == ["Johor"]
    assert {k: johor["geo"][k] for k in ("postcodes", "postcode_checks")} == {k: g[k] for k in ("postcodes",
                                                                                                "postcode_checks")}
    # The default place list is read when --places is not given. It holds no shops, so a synthetic sample
    # with a postcode table reads it too.
    monkeypatch.setattr(bd, "PLACES_DEFAULT", places)
    assert bd.main(args + ["--out", str(tmp_path / "default")]) == 0
    assert te.read_json(tmp_path / "default" / "meta.json")["geo"] == g
    (raw / "SYNTHETIC.txt").write_text("x", encoding="utf-8")
    assert bd.main(args + ["--out", str(tmp_path / "synthetic")]) == 0
    assert te.read_json(tmp_path / "synthetic" / "meta.json")["geo"] == g
    (raw / "SYNTHETIC.txt").unlink()
    capsys.readouterr()
    # No place list, or a broken one (with a note): the shops-only check, which sets 88200 aside, since
    # its 12 other postcodes share one point 97.5 km away.
    broken = tmp_path / "broken.csv"
    broken.write_bytes(b"\xff\xfe\x00")
    for name, value in (("none", ""), ("broken", str(broken))):
        assert bd.main(args + ["--out", str(tmp_path / name), "--places", value]) == 0
        noted = "Note: no usable rows in broken.csv, so the postcode table is checked against shops only."
        assert (noted in capsys.readouterr().out) == (name == "broken")
        g = te.read_json(tmp_path / name / "meta.json")["geo"]
        assert g["postcode_checks"] == {"from_shops": 0, "moved": 0, "set_aside": 1} and "88200" not in g["postcodes"]
        assert g["postcode_source"] == "GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap"


def test_build_counts_and_prints_the_postcodes_set_aside_by_their_first_digits(tmp_path, capsys):
    """The postcodes set aside as far from the confirmed postcodes that share their first 3 digits count in
    set_aside and, only when there are any, in far_from_prefix. The build prints the count and each of them
    with its reason, and check_data notes them."""
    raw = te.write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")], [(1, "Sarawak", "Miri")],
                       {"2026-10": [("2026-10-10", 1, 1, 3.0)]})
    miri = (4.4, 114.0)
    gn = [("98000", "Miri", "Sarawak", *miri), ("98007", "Miri", "Sarawak", *miri),
          ("98057", "Baram", "Sarawak", *north(miri, -431))]
    shops = [(1, *miri, "name", "98000"), (2, *miri, "address", "98000"), (3, *miri, "name", "98007"),
             (4, *miri, "name", "98007")]
    pcs = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = write_cache(tmp_path / "premise_geo.csv", shops)
    out = tmp_path / "out"
    args = ["--raw", str(raw), "--geo", str(geo), "--postcodes", str(pcs), "--district-geo", "", "--places", ""]
    assert bd.main(args + ["--out", str(out)]) == 0
    text = capsys.readouterr().out
    g = te.read_json(out / "meta.json")["geo"]
    assert g["postcodes"] == {"98000": list(miri), "98007": list(miri)}
    assert g["postcode_checks"] == {"from_shops": 2, "moved": 0, "set_aside": 1, "far_from_prefix": 1}
    assert ("postcode table: 2 postcodes from GeoNames, checked against shops found by OpenStreetMap: 2 placed at "
            "those shops (0 moved more than 2 km), 1 set aside (1 of them as far from the confirmed postcodes that "
            "share their first 3 digits)") in text
    assert ("  set aside 98057 Baram (Sarawak): 431.0 km from the nearest of the 2 confirmed postcodes that start 980"
            in text)
    assert ("Postcode check: 2 postcode(s) placed at the shops OpenStreetMap found there (0 moved more than 2 km), "
            "1 set aside as far from the other postcodes of their place or, for 1 of them, from the confirmed "
            "postcodes that share their first 3 digits.") in cd.check(out)["notes"]
    # With 1 confirmed postcode in Miri, nothing judges 98057: the counts keep their older shape.
    geo1 = write_cache(tmp_path / "one.csv", shops[:2])
    assert bd.main(args[:2] + ["--geo", str(geo1), "--postcodes", str(pcs), "--district-geo", "", "--places", "",
                               "--out", str(tmp_path / "one")]) == 0
    assert "far_from_prefix" not in capsys.readouterr().out
    assert te.read_json(tmp_path / "one" / "meta.json")["geo"]["postcode_checks"] == {
        "from_shops": 1, "moved": 0, "set_aside": 0}


def test_build_prints_the_far_rows_and_the_postcodes_whose_rows_lie_far_apart(tmp_path, capsys):
    """With the place list, the build says which postcodes it placed without a GeoNames row far from its
    place's point, and why. The postcodes set aside as far from each of their own rows count in set_aside
    and, only when there are any, in far_from_rows; the build prints each with its reason, and check_data
    notes them."""
    raw = te.write_raw(tmp_path / "raw", [(1, "GULA CONTOH", "1kg")], [(1, "Sarawak", "Miri")],
                       {"2026-10": [("2026-10-10", 1, 1, 3.0)]})
    miri, port, kl, indah, e = (4.4, 114.0), (2.99959, 101.39287), (3.1373, 101.7471), (2.9662, 101.3768), (1.72, 103.92)
    gn = [("98000", "Miri", "Sarawak", *miri), ("98007", "Miri", "Sarawak", *miri),
          ("98057", "Baram", "Sarawak", *north(miri, -431)),
          ("42000", "Pelabuhan Klang", "Selangor", *north(port, 2)), ("42009", "Pelabuhan Klang", "Selangor", *port),
          ("42920", "Pelabuhan Klang", "Selangor", *kl), ("42920", "Pulau Indah", "Selangor", *indah),
          ("81920", "Ayer Tawar 2", "Johor", *north(e, 2)), ("81920", "Ayer Tawar 3", "Johor", *e),
          ("81920", "Ayer Tawar 4", "Johor", *north(e, -2)), ("81920", "Ayer Tawar 5", "Johor", *east(e, -129))]
    shops = [(1, *miri, "name", "98000"), (2, *miri, "address", "98000"), (3, *miri, "name", "98007"),
             (4, *miri, "name", "98007")]
    pcs = write_geonames(tmp_path / "postcodes_my.csv", gn)
    geo = write_cache(tmp_path / "premise_geo.csv", shops)
    places = write_places(tmp_path / "places_my.csv", [("Pelabuhan Klang", "", *port, "PPL", "Selangor", 20000)])
    out = tmp_path / "out"
    assert bd.main(["--raw", str(raw), "--geo", str(geo), "--postcodes", str(pcs), "--district-geo", "",
                    "--places", str(places), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    g = te.read_json(out / "meta.json")["geo"]
    assert g["postcodes"] == {"42000": r4(north(port, 2)), "42009": r4(port), "42920": list(indah),
                              "98000": list(miri), "98007": list(miri)}
    assert g["postcode_checks"] == {"from_shops": 2, "moved": 0, "to_place": 0, "set_aside": 2, "far_from_prefix": 1,
                                    "far_from_rows": 1}
    assert ("postcode table: 5 postcodes from GeoNames, checked against shops found by OpenStreetMap and the GeoNames "
            "place list: 2 placed at those shops (0 moved more than 2 km), 0 moved to their place's point in the place "
            "list, 1 placed without a GeoNames row far from its place's point, 2 set aside (1 of them as far from the "
            "confirmed postcodes that share their first 3 digits, 1 as far from each of their own GeoNames rows)") in text
    d = bd.km(*bd._mean_point([kl, indah]), *indah)
    assert (f"  without a far row 42920 Pelabuhan Klang (Selangor) / Pulau Indah (Selangor): {d:.1f} km (its row of "
            f"Pelabuhan Klang (Selangor) lies {bd.km(*kl, *port):.1f} km from that place's point)") in text
    rows = bd.load_postcode_rows(pcs)["81920"]
    d = min(bd.km(*bd._mean_point([r[2] for r in rows]), *r[2]) for r in rows)
    assert re.findall(r"^  set aside .*$", text, flags=re.M) == [
        "  set aside 81920 Ayer Tawar 2 (Johor) / Ayer Tawar 3 (Johor) / Ayer Tawar 4 (Johor) / Ayer Tawar 5 (Johor): "
        f"{d:.1f} km from the nearest of its 4 GeoNames rows, whose average it is",
        "  set aside 98057 Baram (Sarawak): 431.0 km from the nearest of the 2 confirmed postcodes that start 980"]
    assert ("Postcode check: 2 postcode(s) placed at the shops OpenStreetMap found there (0 moved more than 2 km), "
            "0 moved to their place's point in the GeoNames place list, 2 set aside as far from the other postcodes "
            "of their place or, for 1 of them, from the confirmed postcodes that share their first 3 digits or, for "
            "1 of them, from each of their own GeoNames rows.") in cd.check(out)["notes"]


# ---------- check_data ----------

def checked_build(root, checks="absent", geo=None, **kw):
    """A passing build (test_check.make_build) with geo.postcode_checks set to `checks`."""
    root = tc.make_build(root, **kw)

    def change(meta):
        if geo is not None:
            meta["geo"] = geo
        elif checks != "absent":
            meta["geo"]["postcode_checks"] = checks

    tc.edit_json(root / "meta.json", change)
    return root


def test_check_data_notes_the_postcode_check(tmp_path, capsys):
    root = checked_build(tmp_path / "b", {"from_shops": 86, "moved": 68, "set_aside": 40})
    res = cd.check(root)
    assert res["fails"] == [] and res["warns"] == []
    note = ("Postcode check: 86 postcode(s) placed at the shops OpenStreetMap found there (68 moved more than "
            "2 km), 40 set aside as far from the other postcodes of their place.")
    assert note in res["notes"] and res["facts"]["postcode_checks"] == {"from_shops": 86, "moved": 68, "set_aside": 40}
    assert cd.main(["--data", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## Data check: PASS\n") and f"- {note}" in out


@pytest.mark.parametrize("postcodes, aside, warned", [(2090, 110, False),   # exactly 5% of 2,200
                                                      (2089, 111, True),    # 5.05%
                                                      (2000, 120, True), (2000, 0, False)])
def test_check_data_warns_when_more_than_5_percent_is_set_aside(tmp_path, postcodes, aside, warned):
    root = checked_build(tmp_path / "b", {"from_shops": 80, "moved": 60, "set_aside": aside}, postcodes=postcodes)
    res = cd.check(root)
    warns = [w for w in res["warns"] if "postcode check set aside" in w]
    assert res["fails"] == [] and bool(warns) == warned and len(res["warns"]) == len(warns)
    if warned:
        table = postcodes + aside
        assert f"set aside {aside:,} of {table:,} GeoNames postcodes ({aside / table:.1%}, more than 5%)" in warns[0]
        assert "shop location cache may be broken" in warns[0]


@pytest.mark.parametrize("checks, geo, warned", [
    ("absent", None, None),                                   # an older build: no note, no warning
    (None, None, None),
    (None, [1, 2], None),                                     # geo itself is not an object
    ([86, 68, 40], None, "not an object"),
    ("86", None, "not an object"),
    (7, None, "not an object"),
    ({}, None, "from_shops, moved, set_aside"),
    ({"from_shops": "86", "moved": None, "set_aside": True}, None, "from_shops, moved, set_aside"),
    ({"from_shops": -1, "moved": 1.5, "set_aside": 40}, None, "from_shops, moved"),
    ({"from_shops": 86, "moved": 68, "set_aside": [40]}, None, "set_aside"),
])
def test_check_data_reads_any_postcode_checks_without_a_crash(tmp_path, capsys, checks, geo, warned):
    root = checked_build(tmp_path / "b", checks, geo=geo)
    res = cd.check(root)
    assert res["fails"] == []
    mine = [w for w in res["warns"] if "postcode_checks" in w]
    if warned is None:   # a build without the check: nothing is said about it
        assert mine == [] and res["notes"] == []
        empty = tmp_path / "prev.json"
        empty.write_text("", encoding="utf-8")
        notes = cd.check(root, empty)["notes"]   # an empty live meta.json: its note is the only one
        assert len(notes) == 1 and notes[0].startswith("No live build to compare with")
    else:
        assert len(mine) == 1 and warned in mine[0]
        if isinstance(checks, dict):
            assert any(n.startswith("Postcode check: ") and "?" in n for n in res["notes"])
    assert cd.main(["--data", str(root)]) == 0
    assert capsys.readouterr().out.startswith("## Data check: PASS")


def test_check_data_says_a_negative_count_is_not_a_count(tmp_path):
    """-1 is a whole number, so the warning says what a count must be: a whole number of 0 or more."""
    root = checked_build(tmp_path / "b", {"from_shops": 84, "moved": 66, "set_aside": -1})
    res = cd.check(root)
    assert [w for w in res["warns"] if "postcode_checks" in w] == [
        "geo.postcode_checks in meta.json has counts that are not whole numbers of 0 or more: set_aside."]
    assert ("Postcode check: 84 postcode(s) placed at the shops OpenStreetMap found there (66 moved more than "
            "2 km), ? set aside as far from the other postcodes of their place.") in res["notes"]


@pytest.mark.parametrize("to_place, shown", [(12, "12"), (0, "0"), (1234, "1,234"), (None, "?"), ("12", "?"),
                                             (-1, "?"), (1.5, "?"), (True, "?"), ([12], "?")])
def test_check_data_notes_the_moves_to_place_points(tmp_path, to_place, shown):
    """A build that used the place list counts to_place. An odd value shows as ? with a warning, and never
    stops the report; a build without it (older, or no place list) is noted as before."""
    root = checked_build(tmp_path / "b", {"from_shops": 84, "moved": 66, "to_place": to_place, "set_aside": 5})
    res = cd.check(root)
    assert res["fails"] == []
    assert ("Postcode check: 84 postcode(s) placed at the shops OpenStreetMap found there (66 moved more than 2 km), "
            f"{shown} moved to their place's point in the GeoNames place list, 5 set aside as far from the other "
            "postcodes of their place.") in res["notes"]
    assert [w for w in res["warns"] if "postcode_checks" in w] == ([] if shown != "?" else [
        "geo.postcode_checks in meta.json has counts that are not whole numbers of 0 or more: to_place."])
    assert res["facts"]["postcode_checks"]["to_place"] == (None if shown == "?" else to_place)
    assert cd.main(["--data", str(root)]) == 0


@pytest.mark.parametrize("rows_apart, shown", [(2, "2"), (0, "0"), (1234, "1,234"), (None, "?"), ("2", "?"), (-1, "?"),
                                               (1.5, "?"), (True, "?"), ([2], "?")])
def test_check_data_notes_the_postcodes_set_aside_as_far_from_their_own_rows(tmp_path, rows_apart, shown):
    """A build that set aside postcodes whose point, the average of their GeoNames rows, lies more than 30 km
    from each of those rows counts them in far_from_rows, part of set_aside. An odd value shows as ? with a
    warning, and never stops the report; a build without it is noted as before."""
    root = checked_build(tmp_path / "b", {"from_shops": 81, "moved": 63, "to_place": 310, "set_aside": 10,
                                          "far_from_prefix": 6, "far_from_rows": rows_apart})
    res = cd.check(root)
    assert res["fails"] == []
    assert ("Postcode check: 81 postcode(s) placed at the shops OpenStreetMap found there (63 moved more than 2 km), "
            "310 moved to their place's point in the GeoNames place list, 10 set aside as far from the other "
            "postcodes of their place or, for 6 of them, from the confirmed postcodes that share their first 3 digits "
            f"or, for {shown} of them, from each of their own GeoNames rows.") in res["notes"]
    assert [w for w in res["warns"] if "postcode_checks" in w] == ([] if shown != "?" else [
        "geo.postcode_checks in meta.json has counts that are not whole numbers of 0 or more: far_from_rows."])
    assert res["facts"]["postcode_checks"]["far_from_rows"] == (None if shown == "?" else rows_apart)
    assert cd.main(["--data", str(root)]) == 0
    # Without far_from_prefix, only the rows' count is said.
    root = checked_build(tmp_path / "c", {"from_shops": 1, "moved": 0, "set_aside": 2, "far_from_rows": 2})
    assert ("Postcode check: 1 postcode(s) placed at the shops OpenStreetMap found there (0 moved more than 2 km), "
            "2 set aside as far from the other postcodes of their place or, for 2 of them, from each of their own "
            "GeoNames rows.") in cd.check(root)["notes"]


@pytest.mark.parametrize("far, shown", [(4, "4"), (0, "0"), (1234, "1,234"), (None, "?"), ("4", "?"), (-1, "?"),
                                        (1.5, "?"), (True, "?"), ([4], "?")])
def test_check_data_notes_the_postcodes_set_aside_by_their_first_digits(tmp_path, far, shown):
    """A build that set aside postcodes as far from the confirmed postcodes that share their first 3 digits
    counts them in far_from_prefix, part of set_aside. An odd value shows as ? with a warning, and never stops
    the report; a build without it is noted as before (see above)."""
    root = checked_build(tmp_path / "b", {"from_shops": 81, "moved": 63, "to_place": 310, "set_aside": 8,
                                          "far_from_prefix": far})
    res = cd.check(root)
    assert res["fails"] == []
    assert ("Postcode check: 81 postcode(s) placed at the shops OpenStreetMap found there (63 moved more than 2 km), "
            "310 moved to their place's point in the GeoNames place list, 8 set aside as far from the other "
            f"postcodes of their place or, for {shown} of them, from the confirmed postcodes that share their first "
            "3 digits.") in res["notes"]
    assert [w for w in res["warns"] if "postcode_checks" in w] == ([] if shown != "?" else [
        "geo.postcode_checks in meta.json has counts that are not whole numbers of 0 or more: far_from_prefix."])
    assert res["facts"]["postcode_checks"]["far_from_prefix"] == (None if shown == "?" else far)
    assert cd.main(["--data", str(root)]) == 0
