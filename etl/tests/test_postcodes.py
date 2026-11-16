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
    """GeoNames often lists several place names for one postcode, all on one point. Their average can
    differ from that point in the last bit, but it is the same point, so 98050 is still not judged."""
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
                    "--district-geo", ""]) == 0
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
