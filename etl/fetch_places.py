"""Download the GeoNames list of Malaysian towns and villages, to check town points.

The GeoNames postcode table (fetch_postcodes.py) gives each postcode an estimated point, and those
points are often 10-50 km from the town they are named after. GeoNames also publishes its full
gazetteer, where each populated place has its own point:
  https://download.geonames.org/export/dump/MY.zip  (MY.txt inside)
Licence CC BY 4.0. Credit "GeoNames, https://www.geonames.org/".

This keeps only populated places (feature class P, not abandoned, historical or destroyed) and
writes etl/geo/places_my.csv with the columns
  geonameid, name, asciiname, alt, lat, lon, code, state, population
sorted by state, asciiname, geonameid. alt holds the place's other Latin-script names (for
example "Sri Manjung"), joined by "|". state is the GeoNames name of the state (the ADM1 row in
the same file, for example "Penang"); compare states with geocode_premises.state_key().
etl/geo/places_my.json records when it was fetched, row count, source and licence.

The list changes rarely, so the download is skipped while it is younger than 90 days (--force
fetches anyway); its age comes from places_my.json, as for the postcode table. A failed download
never fails the run: the old list is kept and the script still exits 0. Standard library only.

Run:  python etl/fetch_places.py [--force]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

import fetch_postcodes as fpc

HERE = Path(__file__).resolve().parent
OUT = HERE / "geo" / "places_my.csv"
URL = "https://download.geonames.org/export/dump/MY.zip"
FIELDS = ["geonameid", "name", "asciiname", "alt", "lat", "lon", "code", "state", "population"]
SOURCE = {"source": "GeoNames gazetteer (populated places)", "source_url": "https://www.geonames.org/",
          "download_url": URL, "licence": "CC BY 4.0",
          "licence_url": "https://creativecommons.org/licenses/by/4.0/"}
MAX_AGE_DAYS = 90
MIN_ROWS = 2000           # Malaysia has many thousands of populated places; fewer means a broken download
MAX_BYTES = 60 << 20      # MY.zip is a few MB
MAX_ALT = 10
# Populated places that exist today: abandoned (PPLQ), historical (PPLH, PPLCH) and destroyed (PPLW)
# places are left out.
KEEP = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLG", "PPLL", "PPLS", "PPLX", "PPLF", "PPLR"}
LAT, LON = fpc.LAT, fpc.LON


def _alt_names(field: str, name: str, ascii_name: str) -> str:
    """Other Latin-script names of the place (letters, spaces, dots, dashes, apostrophes), at most
    MAX_ALT, without the name itself, in the order GeoNames gives them."""
    seen = {name.casefold(), ascii_name.casefold()}
    out = []
    for a in field.split(","):
        a = re.sub(r"\s+", " ", a).strip()
        if not (3 <= len(a) <= 60) or not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", a):
            continue  # other scripts, codes, links and numbers
        if a.casefold() in seen:
            continue
        seen.add(a.casefold())
        out.append(a)
        if len(out) == MAX_ALT:
            break
    return "|".join(out)


def parse(text: str) -> list[dict]:
    """Populated places of a GeoNames country dump (tab-separated: geonameid, name, asciiname,
    alternatenames, latitude, longitude, feature class, feature code, country code, cc2, admin1
    code, admin2 code, admin3 code, admin4 code, population, elevation, dem, timezone, modification
    date) as dicts with FIELDS. The state is the name of the ADM1 row with the same admin1 code.
    Bad rows, other countries and places outside Malaysia's box are skipped."""
    if text.startswith("﻿"):
        text = text[1:]
    states, rows = {}, []
    for line in text.splitlines():
        f = [x.strip() for x in line.split("\t")]
        if len(f) < 15 or f[8].upper() != "MY":
            continue
        if f[6] == "A" and f[7] == "ADM1" and f[10]:
            states[f[10]] = f[2] or f[1]
            continue
        if f[6] != "P" or f[7] not in KEEP or not f[0].isdigit() or not (f[1] or f[2]):
            continue
        try:
            lat, lon = float(f[4]), float(f[5])
        except ValueError:
            continue
        if not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
            continue
        pop = f[14] if f[14].isdigit() else "0"
        rows.append({"geonameid": f[0], "name": f[1] or f[2], "asciiname": f[2] or f[1],
                     "alt": _alt_names(f[3], f[1], f[2]), "lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                     "code": f[7], "state": f[10], "population": str(int(pop))})
    out, seen = [], set()
    for r in rows:
        r["state"] = states.get(r["state"], "")
        if not r["state"] or r["geonameid"] in seen:
            continue  # a place without a known state cannot be checked against a shop's state
        seen.add(r["geonameid"])
        out.append(r)
    out.sort(key=lambda r: (r["state"], r["asciiname"].casefold(), int(r["geonameid"])))
    return out


def _header_and_rows(path: Path) -> tuple[list[str], int]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        return next(r, []), sum(1 for _ in r)


def age_days(out: Path, today: date) -> float | None:
    """Age of the list in days, from the fetch date in places_my.json; None means fetch it."""
    if not out.exists():
        return None
    try:
        fetched = date.fromisoformat(json.loads(out.with_suffix(".json").read_text(encoding="utf-8-sig"))["fetched"])
        header, _ = _header_and_rows(out)
    except (OSError, ValueError, KeyError, TypeError, csv.Error):
        return None
    age = float((today - fetched).days)
    if header != FIELDS or age < -1:
        return None
    return age


def write(out: Path, rows: list[dict], today: date) -> int:
    """Write the list safely: a temp file, checked, then swapped in. Returns rows on disk."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    if _header_and_rows(tmp) != (FIELDS, len(rows)):
        tmp.unlink()  # our own temp file; the old list is untouched
        raise OSError("write check failed for the new list")
    tmp.replace(out)
    header, n = _header_and_rows(out)
    if (header, n) != (FIELDS, len(rows)):
        raise OSError(f"write check failed: {n} rows on disk, {len(rows)} expected")
    info = {"fetched": today.isoformat(), "rows": n, "states": len({r["state"] for r in rows}), **SOURCE}
    out.with_suffix(".json").write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    return n


def update(out: Path = OUT, fetch=None, force: bool = False, today: date | None = None,
           max_age_days: float = MAX_AGE_DAYS, min_rows: int = MIN_ROWS) -> str:
    """'fresh' (kept, young enough), 'updated' or 'failed' (old list kept).
    fetch() returns the zip's bytes; the default downloads it."""
    today = today or date.today()
    fetch = fetch or (lambda: fpc.download(URL, max_bytes=MAX_BYTES))
    age = age_days(out, today)
    if not force and age is not None and age < max_age_days:
        print(f"skip {out.name}: {age:.0f} days old (refreshed after {max_age_days:.0f}; --force to fetch now)")
        return "fresh"
    try:
        rows = parse(fpc.read_zip(fetch()))
        if len(rows) < min_rows:
            raise ValueError(f"only {len(rows)} usable rows, expected at least {min_rows}")
        n = write(out, rows, today)
    except Exception as e:  # any failure: warn, keep the old list, carry on
        keep = "keeping the old list" if out.exists() else "no list yet (town points are not checked)"
        print(f"WARNING: place list not updated ({type(e).__name__}: {e}); {keep}.")
        return "failed"
    print(f"OK   {out.name}: {n} places in {len({r['state'] for r in rows})} states")
    return "updated"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT), help="where to write the list (default etl/geo/places_my.csv)")
    ap.add_argument("--force", action="store_true", help="download even if the list is recent")
    ap.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS)
    args = ap.parse_args(argv)
    try:
        update(Path(args.out), force=args.force, max_age_days=args.max_age_days)
    except Exception as e:  # never fail the workflow over the place list
        print(f"WARNING: place list step failed ({type(e).__name__}: {e}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
