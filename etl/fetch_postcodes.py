"""Download the Malaysian postcode table from GeoNames, for offline shop locations.

GeoNames publishes every postcode with a place name, state and an estimated point:
  https://download.geonames.org/export/zip/MY.zip  (MY.txt inside)
Licence CC BY 4.0. Credit "GeoNames, https://www.geonames.org/".

This writes etl/geo/postcodes_my.csv with the columns
  postcode, place, state, lat, lon, accuracy
sorted by postcode then place, and etl/geo/postcodes_my.json (when it was fetched, row count,
source, licence). geocode_premises.py and build_dataset.py read the table.

The table changes rarely, so the download is skipped while it is younger than 90 days
(--force fetches anyway). Its age comes from postcodes_my.json only: file times are no use,
because a git checkout resets them. Without a readable postcodes_my.json the table is fetched
again. A failed download never fails the run: the old table is kept and the script still
exits 0. Standard library only.

Run:  python etl/fetch_postcodes.py [--force]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "geo" / "postcodes_my.csv"
URL = "https://download.geonames.org/export/zip/MY.zip"
UA = "BakulMurah/1.1 (+https://github.com/murshidihashim/bakul-murah)"
FIELDS = ["postcode", "place", "state", "lat", "lon", "accuracy"]
SOURCE = {"source": "GeoNames postal codes", "source_url": "https://www.geonames.org/",
          "download_url": URL, "licence": "CC BY 4.0",
          "licence_url": "https://creativecommons.org/licenses/by/4.0/"}
MAX_AGE_DAYS = 90
MIN_ROWS = 1000           # the real table has about 2,800 rows; fewer means a broken download
MAX_BYTES = 20 << 20      # MY.zip is about 100 KB
LAT, LON = (0.5, 7.6), (99.5, 119.5)  # Malaysia, with a margin


def parse(text: str) -> list[dict]:
    """Rows of a GeoNames postal code file (tab-separated: country, postal code, place name,
    admin name1, admin code1, admin name2, admin code2, admin name3, admin code3, latitude,
    longitude, accuracy) as dicts with FIELDS, sorted by postcode then place. Bad rows are skipped."""
    rows, seen = [], set()
    if text.startswith("\ufeff"):  # a byte-order mark would hide the first row's country code
        text = text[1:]
    for line in text.splitlines():
        f = [x.strip() for x in line.split("\t")]
        if len(f) < 11 or f[0].upper() != "MY" or not re.fullmatch(r"\d{4,5}", f[1]):
            continue
        try:
            lat, lon = float(f[9]), float(f[10])
        except ValueError:
            continue
        if not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
            continue
        row = {"postcode": f[1].zfill(5), "place": f[2], "state": f[3],
               "lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "accuracy": f[11] if len(f) > 11 else ""}
        key = tuple(row.values())
        if key not in seen:
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda r: (r["postcode"], r["place"], r["state"], r["lat"], r["lon"]))
    return rows


def read_zip(data: bytes, member: str = "MY.txt") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.split("/")[-1].lower() == member.lower()]
        if not names:
            raise ValueError(f"{member} not found in the zip")
        return z.read(names[0]).decode("utf-8-sig")


def download(url: str = URL, timeout: int = 60, deadline: float = 180.0) -> bytes:
    """The file's bytes. Raises on a network error, a slow download or an oversized file."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    end = time.monotonic() + deadline
    parts, size = [], 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        read = getattr(r, "read1", None) or r.read
        while True:
            if time.monotonic() > end:
                raise TimeoutError(f"download took longer than {deadline:.0f} s")
            chunk = read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("download is far bigger than expected")
            parts.append(chunk)
    return b"".join(parts)


def info_path(out: Path) -> Path:
    return out.with_suffix(".json")


def age_days(out: Path, today: date) -> float | None:
    """Age of the table in days, from the fetch date in postcodes_my.json. None means fetch it:
    no table, no readable postcodes_my.json (it is rewritten on every good fetch), a fetch date
    in the future, or a table without our header. File times are not used: in GitHub Actions
    every file is as new as the checkout, so the table would never look old."""
    if not out.exists():
        return None
    try:
        fetched = date.fromisoformat(json.loads(info_path(out).read_text(encoding="utf-8-sig"))["fetched"])
        header, _ = _header_and_rows(out)
    except (OSError, ValueError, KeyError, TypeError, csv.Error):
        return None
    age = float((today - fetched).days)
    if header != FIELDS or age < -1:  # -1: fetched "tomorrow" in another time zone is fine
        return None
    return age


def _header_and_rows(path: Path) -> tuple[list[str], int]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        return next(r, []), sum(1 for _ in r)


def write(out: Path, rows: list[dict], today: date) -> int:
    """Write the table safely: a temp file, checked, then swapped in. Returns rows on disk."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    # Verify before swapping in: same header and row count back.
    if _header_and_rows(tmp) != (FIELDS, len(rows)):
        tmp.unlink()  # our own temp file; the old table is untouched
        raise OSError("write check failed for the new table")
    tmp.replace(out)
    header, n = _header_and_rows(out)
    if (header, n) != (FIELDS, len(rows)):
        raise OSError(f"write check failed: {n} rows on disk, {len(rows)} expected")
    info = {"fetched": today.isoformat(), "rows": n, "postcodes": len({r["postcode"] for r in rows}), **SOURCE}
    info_path(out).write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    return n


def update(out: Path = OUT, fetch=None, force: bool = False, today: date | None = None,
           max_age_days: float = MAX_AGE_DAYS, min_rows: int = MIN_ROWS) -> str:
    """'fresh' (kept, young enough), 'updated' or 'failed' (old table kept).
    fetch() returns the zip's bytes; the default downloads it."""
    today = today or date.today()
    fetch = fetch or download
    age = age_days(out, today)
    if not force and age is not None and age < max_age_days:
        print(f"skip {out.name}: {age:.0f} days old (refreshed after {max_age_days:.0f}; --force to fetch now)")
        return "fresh"
    try:
        rows = parse(read_zip(fetch()))
        if len(rows) < min_rows:
            raise ValueError(f"only {len(rows)} usable rows, expected at least {min_rows}")
        n = write(out, rows, today)
    except Exception as e:  # any failure: warn, keep the old table, carry on
        keep = "keeping the old table" if out.exists() else "no table yet (shops fall back to district centres)"
        print(f"WARNING: postcode table not updated ({type(e).__name__}: {e}); {keep}.")
        return "failed"
    print(f"OK   {out.name}: {n} rows, {len({r['postcode'] for r in rows})} postcodes")
    return "updated"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT), help="where to write the table (default etl/geo/postcodes_my.csv)")
    ap.add_argument("--force", action="store_true", help="download even if the table is recent")
    ap.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS)
    args = ap.parse_args(argv)
    try:
        update(Path(args.out), force=args.force, max_age_days=args.max_age_days)
    except Exception as e:  # never fail the workflow over the postcode table
        print(f"WARNING: postcode step failed ({type(e).__name__}: {e}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
