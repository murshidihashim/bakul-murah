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
    (30200 Ipoh: a shop in Ipoh and one in Batu Gajah). When one of them is within 5 km of the
    GeoNames point, the postcode keeps that point; else it is placed at their middle."""
    g = {pc: (2.0 + 0.5 * i, 102.0) for i, pc in enumerate(["30200", "30210", "30220", "30230", "30240",
                                                             "30250", "30260"])}
    km_north = {"30200": (3.7, -8.9), "30210": (4.9, 15.8), "30220": (5.1, 16.0), "30230": (1, 10.9),
                "30240": (1, 11.1), "30250": (1, 8, 12), "30260": (28, 41)}
    at = {pc: [as_read(north(g[pc], d)) for d in ds] for pc, ds in km_north.items()}
    shops = [(10 * i + k, *p, "name", pc) for i, (pc, ps) in enumerate(at.items()) for k, p in enumerate(ps)]
    gn = [(pc, f"Town {pc}", "Perak", *p) for pc, p in g.items()]
    res = run_check(tmp_path, gn, shops)
    pts = res["postcodes"]

    def middle(pc):   # the median of pc's shops, as the build reads them
        return r4((statistics.median(p[0] for p in at[pc]), statistics.median(p[1] for p in at[pc])))

    assert pts["30200"] == r4(g["30200"])                       # 12.6 km apart, one 3.7 km away: kept
    assert pts["30210"] == r4(g["30210"])                       # one 4.9 km away: kept
    assert pts["30220"] == middle("30220")                      # 5.1 km: not close enough, their middle
    assert pts["30230"] == middle("30230")                      # 9.9 km apart: they agree closely enough
    assert pts["30240"] == r4(g["30240"])                       # 10.1 km apart: kept
    assert pts["30250"] == middle("30250") == r4(at["30250"][1])   # 3 shops: their median, however wide
    assert pts["30260"] == middle("30260")                      # neither near the GeoNames point: their middle
    assert res["checks"] == {"from_shops": 4, "moved": 4, "set_aside": 0}
    assert res["kept"] == [
        ("30200", "Town 30200 (Perak)", "its 2 shops lie 12.6 km apart and one of them is 3.7 km from that point"),
        ("30210", "Town 30210 (Perak)", "its 2 shops lie 10.9 km apart and one of them is 4.9 km from that point"),
        ("30240", "Town 30240 (Perak)", "its 2 shops lie 10.1 km apart and one of them is 1.0 km from that point")]
    assert not {m[1] for m in res["moves"]} & {"30200", "30210", "30240"}
    assert bd.BACKED_KM == 5.0
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
    res = bd.check_postcodes(rows, geo)
    assert res["kept"] == [] and res["checks"]["from_shops"] == 1
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
    agreed, backed = {}, set()
    for pc in before:
        found = [g[:2] for g in geo.values() if g[2] in ("name", "address") and g[3] == pc]
        if bd.agreed_point(found):
            agreed[pc] = bd.agreed_point(found)
            mid = statistics.median(p[0] for p in found), statistics.median(p[1] for p in found)
            close = [p for p in found if bd.km(*p, *mid) <= 10]
            average = (statistics.mean(r[2][0] for r in rows[pc]), statistics.mean(r[2][1] for r in rows[pc]))
            if len(close) == 2 and bd.km(*close[0], *close[1]) > 10 and min(
                    bd.km(*p, *average) for p in close) <= 5:   # 2 far apart, one backs GeoNames
                backed.add(pc)
    assert {pc for pc, _, _ in res["kept"]} == backed
    for pc, v in out.items():   # each kept postcode: its GeoNames point, or where its shops agree
        assert v == (r4(agreed[pc]) if pc in agreed and pc not in backed else before[pc])
        assert all(isinstance(x, float) and round(x, 4) == x for x in v) and len(v) == 2
    assert not removed & set(agreed)                         # a postcode placed from shops or backed is kept
    assert c["from_shops"] == len(agreed) - len(backed) and c["moved"] == len(res["moves"]) <= c["from_shops"]
    assert all(d > bd.MOVED_KM for d, _, _ in res["moves"])
    assert [m[0] for m in res["moves"]] == sorted((m[0] for m in res["moves"]), reverse=True)
    assert 0 < c["set_aside"] and 0 < c["from_shops"] and 0 < c["moved"]   # the data tries every rule
    assert 0 < len(backed)
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
    other names count only when no name matches. Without one: a single entry by name that has a
    population or a seat-of-government code, unless an entry with that name as another name lies more
    than ANCHOR_KM from it. Entries are ((lat, lon), code, population)."""
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
    assert bd.place_point([e(town, 33000), e(far, 9)], [], None) is None   # 2 by name: which one?
    assert bd.place_point([], [e(town, 33000)], None) is None             # another name only
    assert bd.place_point([e(far, 49000)], [e(town)], None) is None       # Sungai Siput
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
    by name 20 km apart; its PO box postcodes share a point 50 km south of the town."""
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
    # Shops of another state, a street of that name, or shops that do not agree: no reference, and with
    # 2 entries by name, no place point.
    assert run(near_b, {10: ("Perlis", "SIMPANG EMPAT"), 11: ("Perlis", "SIMPANG EMPAT")}, "state") == r4(north(a, -50))
    assert run(near_b, {10: ("Kedah", "TAMAN SIMPANG EMPATJAYA"), 11: named_b[11]}, "one") == r4(north(a, -50))
    apart = [(10, *as_read(north(b, 12)), "name", ""), (11, *as_read(north(b, -12)), "address", "")]
    assert run(apart, named_b, "apart") == r4(north(a, -50))
    anchor = [near_b[0], (11, *as_read(north(b, -1)), "town", "")]   # a town-level point is no shop found by OSM
    assert run(anchor, named_b, "anchor") == r4(north(a, -50))
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
    res = run_places(tmp_path, gn, places + [("Jerantut", "", 3.3, 101.6, "PPL", "Selangor", 900)], name="both")
    assert res["postcodes"]["27000"] == r4(far) and res["to_place"] == []
    # The place list has Jerantut in Selangor twice, with no population and no shops: no point there, but the
    # list has the name in both states, so both count and 27000 keeps its point.
    selangor = [("Jerantut", "", 3.3, 101.6, "PPL", "Selangor", 0), ("Jerantut", "", 3.5, 101.4, "PPL", "Selangor", 0)]
    res = run_places(tmp_path, gn, places + selangor, name="no_point")
    assert res["postcodes"]["27000"] == r4(far) and res["to_place"] == []
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
    place_points = {tuple(r4(p[3])) for p in places}
    for pc, v in out.items():
        if pc in moved:   # moved to a place-list point more than ANCHOR_KM from its GeoNames point
            assert tuple(v) in place_points and bd.km(*bd._mean_point([r[2] for r in rows[pc]]), *v) > bd.ANCHOR_KM
        else:             # as without the place list: its GeoNames point or where its shops agree
            assert v == plain["postcodes"].get(pc, before[pc])
        assert all(isinstance(x, float) and round(x, 4) == x for x in v) and len(v) == 2
    assert (c["from_shops"], c["moved"], res["moves"], res["kept"]) == (
        plain["checks"]["from_shops"], plain["checks"]["moved"], plain["moves"], plain["kept"])
    assert [m[0] for m in res["to_place"]] == sorted((m[0] for m in res["to_place"]), reverse=True)
    # Deterministic: the same answer from every input in the opposite order.
    assert bd.check_postcodes(dict(reversed(list(rows.items()))), dict(reversed(list(geo.items()))),
                              list(reversed(places)), dict(reversed(list(addresses.items())))) == res


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
