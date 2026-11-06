"""Sanity checks on a built web/data folder, run after the build and before the deploy.

Fails (exit 1), so the live site keeps its last good data, when:
  - meta.json is missing or unreadable, has no data date, or is marked synthetic
  - there are fewer than 150 districts, 1,900 shops (stats.premises) or 300 items
  - a district in meta.json has no d/<slug>.json, or a district file cannot be read
  - a price row points at a shop index that is not in its file (every file is checked)
  - with --previous (the live meta.json; skipped when missing or empty): shops down more than
    15%, kept price rows down more than 25%, or a data date older than the live one
Warns (exit 0) when:
  - more than 30% of shops have no real location (precision "district", or none at all)
  - the postcode table has fewer than 2,000 postcodes
  - the data date is more than 10 days before the build date
  - fewer than 10 located shops lie within 5 km of one of the towns in TOWNS
  - price rows are older than 30 days

Prints a short markdown report; the workflow adds it to the run summary.

Run:  python etl/check_data.py [--data web/data] [--previous prev_meta.json]
"""
from __future__ import annotations

import argparse
import json
import math
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


def km(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _date(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _col(cols: list, name: str, default: int) -> int:
    return cols.index(name) if name in cols else default


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
    if not isinstance(meta, dict):
        fail(f"meta.json is missing or unreadable in {data_dir}.")
        return res

    areas = [(st, a) for st, v in (meta.get("areas") or {}).items() for a in (v or []) if isinstance(a, dict)]
    stats = meta.get("stats") or {}
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
    missing, unreadable = [], []
    bad_rows = stale_rows = n_rows = 0
    located = []
    for st, a in areas:
        slug = a.get("slug") or ""
        d = _read_json(data_dir / "d" / f"{slug}.json") if slug else None
        if d is None:
            (missing if not slug or not (data_dir / "d" / f"{slug}.json").exists() else unreadable).append(
                slug or f"{st} / {a.get('district')}")
            continue
        if not isinstance(d, dict) or not isinstance(d.get("premises"), list) or not isinstance(d.get("prices"), dict):
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
        ilat, ilon, iprec = _col(cols, "lat", 4), _col(cols, "lon", 5), _col(cols, "geo_precision", 6)
        for p in d["premises"]:
            get = (lambda i: p[i] if isinstance(p, list) and len(p) > i else None)
            lat, lon, prec = _num(get(ilat)), _num(get(ilon)), get(iprec)
            if prec == "district":
                res["locations"]["district"] += 1
            elif lat is None or lon is None or not prec:
                res["locations"]["none"] += 1
            else:
                res["locations"]["exact" if prec in EXACT else "approximate" if prec in APPROX else prec] += 1
                located.append((lat, lon))
    f.update(price_rows=n_rows, located=len(located))
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
        if not isinstance(prev, dict):
            note("No live build to compare with (previous meta.json missing or empty).")
        else:
            ps = prev.get("stats") or {}
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
    geo = meta.get("geo") if isinstance(meta.get("geo"), dict) else {}
    n_pc = len(geo.get("postcodes") or {})
    f["postcodes"] = n_pc
    if n_pc < MIN_POSTCODES:
        warn(f"The postcode table has {n_pc:,} postcodes (expected at least {MIN_POSTCODES:,}).")
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
        labels += [(k, k) for k in sorted(res["locations"]) if k not in known]
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
    ap.add_argument("--previous", help="an older meta.json, e.g. the live site's; skipped when missing or empty")
    ap.add_argument("--min-districts", type=int, default=MIN_DISTRICTS)
    ap.add_argument("--min-premises", type=int, default=MIN_PREMISES)
    ap.add_argument("--min-items", type=int, default=MIN_ITEMS)
    args = ap.parse_args(argv)
    res = check(Path(args.data), Path(args.previous) if args.previous else None,
                min_districts=args.min_districts, min_premises=args.min_premises, min_items=args.min_items)
    print(report(res), end="")
    return 1 if res["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
