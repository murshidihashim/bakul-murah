"""Sanity checks on a built web/data folder, run after the build and before the deploy.

Fails (exit 1), so the live site keeps its last good data, when:
  - meta.json is missing, unreadable or the wrong shape, has no data date, or is marked synthetic
  - meta.json or a district file holds NaN or Infinity (not JSON: browsers cannot read the file)
  - there are fewer than 150 districts, 1,900 shops (stats.premises) or 300 items
  - a district in meta.json has no d/<slug>.json, or a district file cannot be read (or has the wrong shape)
  - a price row points at a shop index that is not in its file (every file is checked)
  - a shop's latitude is outside -90..90 or its longitude outside -180..180
  - with --previous (the live meta.json; skipped when missing, empty or unreadable): shops down more than
    15%, kept price rows down more than 25%, or a data date older than the live one
Warns (exit 0) when:
  - more than 30% of shops have no real location (precision "district", or none at all)
  - a shop's location precision is not name, address, postcode, town or district
  - a located shop lies outside Malaysia (latitude 0..8, longitude 99..120)
  - the postcode table has fewer than 2,000 postcodes
  - the build's postcode check set aside more than 5% of the GeoNames postcodes (a broken postcode
    table or shop location cache), or meta.json's geo.postcode_checks cannot be read
  - the data date is more than 10 days before the build date
  - fewer than 10 located shops lie within 5 km of one of the towns in TOWNS
  - price rows are older than 30 days

Notes say what the build's postcode check did (geo.postcode_checks in meta.json), when it ran: postcodes
placed at shops, moved to their place's point in the GeoNames place list (to_place, when the build used the
place list) and set aside (far_from_prefix of them as far from the confirmed postcodes that share their first
3 digits, and far_from_rows as far from each of their own GeoNames rows, when there are any).
Prints a short markdown report; the workflow adds it to the run summary.

Run:  python etl/check_data.py [--data web/data] [--previous prev_meta.json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

MIN_DISTRICTS, MIN_PREMISES, MIN_ITEMS = 150, 1900, 300
MAX_PREMISE_DROP = 0.15     # vs the live build
MAX_ROWS_DROP = 0.25
MAX_UNLOCATED_SHARE = 0.30
MIN_POSTCODES = 2000
MAX_ASIDE_SHARE = 0.05      # GeoNames postcodes the build set aside as far from their place
MAX_DATA_AGE_DAYS = 10      # data date vs build date
FRESH_DAYS = 30             # price rows older than this should not be in the build
TOWN_KM, MIN_TOWN_SHOPS = 5.0, 10
# Big towns where "near me" must work: (name, lat, lon).
TOWNS = [("Kuala Lumpur", 3.139, 101.687), ("Shah Alam", 3.073, 101.518),
         ("Petaling Jaya", 3.107, 101.606), ("Kepong", 3.212, 101.636), ("Cheras", 3.106, 101.738),
         ("Johor Bahru", 1.492, 103.741), ("Kota Kinabalu", 5.980, 116.073), ("Kuching", 1.553, 110.344),
         ("Ipoh", 4.597, 101.090), ("Kuantan", 3.808, 103.326), ("Melaka", 2.196, 102.248),
         ("Seremban", 2.726, 101.938), ("George Town", 5.414, 100.329), ("Kota Bharu", 6.125, 102.238),
         ("Alor Setar", 6.121, 100.367), ("Kuala Terengganu", 5.330, 103.137)]
EXACT, APPROX = ("name", "address"), ("postcode", "town")
PRECISIONS = EXACT + APPROX + ("district",)
MALAYSIA = (0.0, 8.0, 99.0, 120.0)   # a generous box: lat from, lat to, lon from, lon to


def km(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class NotFinite(ValueError):
    """The file holds NaN, Infinity or -Infinity: Python reads them, browsers do not."""


def _no_constant(name: str):
    raise NotFinite(name)


NOT_FINITE = object()   # what _read_json returns for such a file


def _read_json(path: Path):
    """The parsed file; None when it is missing or unreadable; NOT_FINITE when it holds NaN or Infinity."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_no_constant)
    except NotFinite:
        return NOT_FINITE
    except (OSError, ValueError):
        return None


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _num(x):
    """A finite number, else None (bools, text, NaN and infinities are not numbers here, nor an
    integer too big for a float: browsers read it as Infinity)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return x if math.isfinite(float(x)) else None
    except OverflowError:
        return None


def _date(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _count(x):
    """A whole number of 0 or more, else None."""
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else None


def _col(cols: list, name: str, default: int) -> int:
    return cols.index(name) if name in cols else default


def _bad_point(lat, lon) -> bool:
    """True when a shop's lat or lon is a number but no place on Earth: NaN or infinite, a
    latitude outside -90..90 or a longitude outside -180..180 (a missing or text value is not this)."""
    for v, limit in ((lat, 90), (lon, 180)):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and (_num(v) is None or not -limit <= v <= limit):
            return True
    return False


def _row_ok(r, n_premises: int, n_min: int) -> bool:
    """A row that starts with a valid shop index, with at least n_min values."""
    return (isinstance(r, list) and len(r) >= n_min and isinstance(r[0], int) and not isinstance(r[0], bool)
            and 0 <= r[0] < n_premises)


def check(data_dir: Path, previous: Path | None = None, today: date | None = None,
          min_districts: int = MIN_DISTRICTS, min_premises: int = MIN_PREMISES,
          min_items: int = MIN_ITEMS) -> dict:
    """{'fails', 'warns', 'notes': [str], 'facts': {...}, 'locations': Counter, 'towns': [(name, n)]}"""
    res = {"fails": [], "warns": [], "notes": [], "facts": {}, "locations": Counter(), "towns": []}
    fail, warn, note = res["fails"].append, res["warns"].append, res["notes"].append
    meta = _read_json(data_dir / "meta.json")
    if meta is NOT_FINITE:
        fail(f"meta.json in {data_dir} holds NaN or Infinity, which browsers cannot read.")
        return res
    if not isinstance(meta, dict):
        fail(f"meta.json is missing or unreadable in {data_dir}.")
        return res

    # Wrong types are failures with a message, never a crash: the report must reach the summary.
    if not isinstance(meta.get("areas"), dict):
        fail("meta.json has no areas object ({state: [districts]}).")
    if meta.get("stats") is not None and not isinstance(meta.get("stats"), dict):
        fail("meta.json stats is not an object.")
    areas = [(st, a) for st, v in _dict(meta.get("areas")).items() if isinstance(v, list)
             for a in v if isinstance(a, dict)]
    stats = _dict(meta.get("stats"))
    premises = _num(stats.get("premises"))
    items = len(meta["items"]) if isinstance(meta.get("items"), list) else _num(stats.get("items"))
    data_date = _date(meta.get("data_date"))
    built = _date(meta.get("generated_at")) or today or date.today()
    f = res["facts"]
    f.update(data_date=meta.get("data_date"), built=built.isoformat(), districts=len(areas),
             premises=premises, items=items, rows_kept=_num(stats.get("rows_kept")))

    if meta.get("synthetic"):
        fail("The build is marked synthetic (made-up sample data). It must not be published.")
    if data_date is None:
        fail("meta.json has no valid data_date.")
    if len(areas) < min_districts:
        fail(f"Only {len(areas)} districts (expected at least {min_districts}).")
    if premises is None:
        fail("meta.json has no shop count (stats.premises).")
    elif premises < min_premises:
        fail(f"Only {premises} shops in stats.premises (expected at least {min_premises:,}).")
    if items is None:
        fail("meta.json has no item list.")
    elif items < min_items:
        fail(f"Only {items} items (expected at least {min_items}).")

    # District files: present, readable, price rows pointing at real shops.
    missing, unreadable, not_finite, bad_points, abroad = [], [], [], [], []
    bad_rows = stale_rows = n_rows = 0
    located = []
    odd = Counter()  # precision values outside the contract, as text
    for st, a in areas:
        slug = a.get("slug") if isinstance(a.get("slug"), str) and re.fullmatch(r"[\w-]+", a["slug"]) else ""
        d = _read_json(data_dir / "d" / f"{slug}.json") if slug else None
        if d is NOT_FINITE:
            not_finite.append(f"d/{slug}.json")
            continue
        if d is None:
            (missing if not slug or not (data_dir / "d" / f"{slug}.json").exists() else unreadable).append(
                slug or f"{st} / {a.get('district')}")
            continue
        if (not isinstance(d, dict) or not isinstance(d.get("premises"), list) or not isinstance(d.get("prices"), dict)
                or not isinstance(d.get("shop_months") or {}, dict) or not isinstance(d.get("premise_cols") or [], list)):
            unreadable.append(slug)
            continue
        n = len(d["premises"])
        for rows in d["prices"].values():
            for r in rows if isinstance(rows, list) else [None]:
                n_rows += 1
                if not _row_ok(r, n, 3) or _num(r[1]) is None or r[1] <= 0:
                    bad_rows += 1
                elif _num(r[2]) is not None and r[2] > FRESH_DAYS:
                    stale_rows += 1
        for rows in (d.get("shop_months") or {}).values():
            for r in rows if isinstance(rows, list) else [None]:
                if not _row_ok(r, n, 2):
                    bad_rows += 1
        cols = d.get("premise_cols") or []
        icode = _col(cols, "premise_code", 0)
        ilat, ilon, iprec = _col(cols, "lat", 4), _col(cols, "lon", 5), _col(cols, "geo_precision", 6)
        for p in d["premises"]:
            get = (lambda i: p[i] if isinstance(p, list) and len(p) > i else None)
            lat, lon, prec = _num(get(ilat)), _num(get(ilon)), get(iprec)
            if _bad_point(get(ilat), get(ilon)):
                bad_points.append(f"d/{slug}.json shop {get(icode)} ({get(ilat)}, {get(ilon)})")
                lat = lon = None
            if prec not in PRECISIONS:
                odd[str(prec)] += 1
            if prec == "district":
                res["locations"]["district"] += 1
            elif lat is None or lon is None or not prec:
                res["locations"]["none"] += 1
            else:
                # str(): a number or other odd value must not break sorting in report()
                kind = "exact" if prec in EXACT else "approximate" if prec in APPROX else str(prec)
                res["locations"][kind] += 1
                located.append((lat, lon))
                if not (MALAYSIA[0] <= lat <= MALAYSIA[1] and MALAYSIA[2] <= lon <= MALAYSIA[3]):
                    abroad.append(f"d/{slug}.json shop {get(icode)} ({lat:.4f}, {lon:.4f})")
    f.update(price_rows=n_rows, located=len(located))
    if not_finite:
        fail(f"{len(not_finite)} district file(s) hold NaN or Infinity, which browsers cannot read: "
             f"{', '.join(not_finite[:5])}{' ...' if len(not_finite) > 5 else ''}")
    if bad_points:
        fail(f"{len(bad_points)} shop(s) have a latitude outside -90..90 or a longitude outside -180..180: "
             f"{', '.join(bad_points[:5])}{' ...' if len(bad_points) > 5 else ''}")
    if missing:
        fail(f"{len(missing)} district file(s) missing: {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}")
    if unreadable:
        fail(f"{len(unreadable)} district file(s) unreadable: {', '.join(unreadable[:5])}"
             f"{' ...' if len(unreadable) > 5 else ''}")
    if bad_rows:
        fail(f"{bad_rows:,} price row(s) point at a shop that is not in their file, or are malformed.")

    # Compared with the live site.
    if previous is not None:
        prev = _read_json(previous) if previous.exists() and previous.stat().st_size > 0 else None
        if prev is NOT_FINITE:
            note("No live build to compare with (previous meta.json holds NaN or Infinity).")
        elif not isinstance(prev, dict):
            note("No live build to compare with (previous meta.json missing, empty or unreadable).")
        else:
            ps = _dict(prev.get("stats"))
            pp, pr, pd_ = _num(ps.get("premises")), _num(ps.get("rows_kept")), _date(prev.get("data_date"))
            f["previous"] = {"data_date": prev.get("data_date"), "premises": pp, "rows_kept": pr}
            if pp and premises is not None and premises < pp * (1 - MAX_PREMISE_DROP):
                fail(f"Shops fell from {pp:,} to {premises:,} ({premises / pp - 1:+.0%}) since the live build.")
            if pr and f["rows_kept"] is not None and f["rows_kept"] < pr * (1 - MAX_ROWS_DROP):
                fail(f"Kept price rows fell from {pr:,} to {f['rows_kept']:,} ({f['rows_kept'] / pr - 1:+.0%}) "
                     "since the live build.")
            if pd_ and data_date and data_date < pd_:
                fail(f"Data date {data_date} is older than the live build's {pd_}.")

    # Warnings.
    total = sum(res["locations"].values())
    unlocated = res["locations"]["district"] + res["locations"]["none"]
    if total and unlocated / total > MAX_UNLOCATED_SHARE:
        warn(f"{unlocated / total:.0%} of shops ({unlocated:,} of {total:,}) have no real location "
             f"(more than {MAX_UNLOCATED_SHARE:.0%}).")
    if odd:
        warn(f"{sum(odd.values()):,} shop(s) have a location precision that is not name, address, postcode, "
             f"town or district: {', '.join(sorted(odd)[:5])}{' ...' if len(odd) > 5 else ''}.")
    if abroad:
        warn(f"{len(abroad):,} located shop(s) lie outside Malaysia (latitude {MALAYSIA[0]:g} to {MALAYSIA[1]:g}, "
             f"longitude {MALAYSIA[2]:g} to {MALAYSIA[3]:g}): {', '.join(abroad[:5])}{' ...' if len(abroad) > 5 else ''}.")
    pcs = _dict(meta.get("geo")).get("postcodes")
    n_pc = len(pcs) if isinstance(pcs, (dict, list)) else 0
    f["postcodes"] = n_pc
    if n_pc < MIN_POSTCODES:
        warn(f"The postcode table has {n_pc:,} postcodes (expected at least {MIN_POSTCODES:,}).")
    # The build checks the GeoNames points against shops found by OpenStreetMap (postcode_checks).
    # Any shape is read without a crash: the report must reach the summary. A build without the check
    # (no GeoNames table or no shop locations, or an older build) gets no note.
    checks = (meta.get("geo") if isinstance(meta.get("geo"), dict) else {}).get("postcode_checks")
    if checks is not None and not isinstance(checks, dict):
        warn("geo.postcode_checks in meta.json is not an object, so the postcode check cannot be read.")
    elif checks is not None:
        # to_place is there only when the build also checked against the GeoNames place list, far_from_prefix
        # (part of set_aside) only when the build set aside a postcode as far from those sharing its first digits,
        # and far_from_rows (part of set_aside too) only when it set aside one as far from each of its own rows.
        keys = ("from_shops", "moved", "to_place", "set_aside") if "to_place" in checks else (
            "from_shops", "moved", "set_aside")
        keys += tuple(k for k in ("far_from_prefix", "far_from_rows") if k in checks)
        c = {k: _count(checks.get(k)) for k in keys}
        f["postcode_checks"] = c
        shown = {k: "?" if v is None else f"{v:,}" for k, v in c.items()}
        to_place = (f"{shown['to_place']} moved to their place's point in the GeoNames place list, "
                    if "to_place" in c else "")
        prefix = (f" or, for {shown['far_from_prefix']} of them, from the confirmed postcodes that share their first "
                  "3 digits" if "far_from_prefix" in c else "")
        prefix += (f" or, for {shown['far_from_rows']} of them, from each of their own GeoNames rows"
                   if "far_from_rows" in c else "")
        note(f"Postcode check: {shown['from_shops']} postcode(s) placed at the shops OpenStreetMap found there "
             f"({shown['moved']} moved more than 2 km), {to_place}{shown['set_aside']} set aside as far from the "
             f"other postcodes of their place{prefix}.")
        bad = [k for k in keys if c[k] is None]
        if bad:
            warn(f"geo.postcode_checks in meta.json has counts that are not whole numbers of 0 or more: "
                 f"{', '.join(bad)}.")
        aside, table = c["set_aside"], n_pc + (c["set_aside"] or 0)
        if aside and aside / table > MAX_ASIDE_SHARE:
            warn(f"The postcode check set aside {aside:,} of {table:,} GeoNames postcodes ({aside / table:.1%}, "
                 f"more than {MAX_ASIDE_SHARE:.0%}): the postcode table or the shop location cache may be broken.")
    if data_date and (built - data_date).days > MAX_DATA_AGE_DAYS:
        warn(f"The data date {data_date} is {(built - data_date).days} days before the build ({built}).")
    if stale_rows:
        warn(f"{stale_rows:,} price row(s) are older than {FRESH_DAYS} days.")
    res["towns"] = [(name, sum(1 for p in located if km(lat, lon, *p) <= TOWN_KM)) for name, lat, lon in TOWNS]
    weak = [f"{name} ({n})" for name, n in res["towns"] if n < MIN_TOWN_SHOPS]
    if weak:
        warn(f"Fewer than {MIN_TOWN_SHOPS} located shops within {TOWN_KM:g} km of: {', '.join(weak)}.")
    return res


def report(res: dict) -> str:
    f = res["facts"]
    head = "FAIL" if res["fails"] else "PASS"
    if res["warns"]:
        head += f" ({len(res['warns'])} warning{'s' if len(res['warns']) > 1 else ''})"
    out = [f"## Data check: {head}", ""]

    def n(x):
        return f"{x:,}" if isinstance(x, (int, float)) else "?"

    if f:
        out.append(f"Data date {f.get('data_date') or '?'} (built {f.get('built')}): {n(f.get('districts'))} districts, "
                   f"{n(f.get('premises'))} shops, {n(f.get('items'))} items, {n(f.get('price_rows'))} price rows.")
    prev = f.get("previous")
    if prev:
        out.append(f"Live site: data date {prev.get('data_date') or '?'}, {n(prev.get('premises'))} shops, "
                   f"{n(prev.get('rows_kept'))} kept price rows (now {n(f.get('rows_kept'))}).")
    total = sum(res["locations"].values())
    if total:
        out += ["", "| Shop location | Shops | Share |", "|---|---:|---:|"]
        labels = [("exact", "exact (name or address)"), ("approximate", "approximate (postcode or town)"),
                  ("district", "district only"), ("none", "none")]
        known = {k for k, _ in labels}
        labels += [(k, str(k)) for k in sorted(res["locations"], key=str) if k not in known]
        for key, label in labels:
            if res["locations"][key]:
                out.append(f"| {label} | {res['locations'][key]:,} | {res['locations'][key] / total:.0%} |")
    if res["fails"]:
        out += ["", "**Problems (deploy stopped):**"] + [f"- {x}" for x in res["fails"]]
    if res["warns"]:
        out += ["", "**Warnings:**"] + [f"- {x}" for x in res["warns"]]
    if res["notes"]:
        out += [""] + [f"- {x}" for x in res["notes"]]
    if res["towns"]:
        out += ["", f"<details><summary>Located shops within {TOWN_KM:g} km of big towns</summary>", "",
                "| Town | Shops |", "|---|---:|"]
        out += [f"| {name} | {k} |" for name, k in res["towns"]] + ["", "</details>"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "web" / "data"))
    ap.add_argument("--previous", help="an older meta.json, e.g. the live site's; skipped when missing, empty "
                    "or unreadable")
    ap.add_argument("--min-districts", type=int, default=MIN_DISTRICTS)
    ap.add_argument("--min-premises", type=int, default=MIN_PREMISES)
    ap.add_argument("--min-items", type=int, default=MIN_ITEMS)
    args = ap.parse_args(argv)
    try:
        res = check(Path(args.data), Path(args.previous) if args.previous else None,
                    min_districts=args.min_districts, min_premises=args.min_premises, min_items=args.min_items)
        text = report(res)
    except Exception as e:  # a shape we did not foresee: still a failure with a report, not a traceback
        res = {"fails": [f"The check could not finish ({type(e).__name__}: {e})."], "warns": [],
               "notes": [], "facts": {}, "locations": Counter(), "towns": []}
        text = report(res)
    print(text, end="")
    return 1 if res["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
