"""Give every shop a map point, so the app can show shops near the user.

PriceCatcher's premise table has addresses but no coordinates. This looks each shop up once
with OpenStreetMap's Nominatim service and keeps the answers in etl/geo/premise_geo.csv, so
later runs only look up new shops. Shops come from the built district files (web/data/d/*.json).

For each shop, in order, inside a box around its district:
  1. the shop name ("MYDIN MART SEKSYEN 18")          -> precision "name"
  2. the cleaned address + district                    -> precision "address"
  3. the postcode in the address + district            -> precision "postcode"
  4. the district centre                               -> precision "district"
A hit must be within 40 km of the district centre, or it is ignored.

Nominatim usage policy: at most 1 request a second, a clear User-Agent, cache results,
credit OpenStreetMap (ODbL). This script does all four. Standard library only.

Run (GitHub Actions does this before the build):
  python etl/geocode_premises.py --max-new 3000
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / "geo" / "premise_geo.csv"
DCACHE = HERE / "geo" / "district_geo.csv"
URL = "https://nominatim.openstreetmap.org/search"
UA = "BakulMurah/1.0 (grocery price prototype; github.com/murshidihashim)"
MAX_KM = 40.0
FIELDS = ["premise_code", "lat", "lon", "precision", "query", "district", "state", "postcode"]


def km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Geocoder:
    """Nominatim client with a 1.1 s gap between calls. `fetch` can be swapped in tests."""

    def __init__(self, fetch=None, delay: float = 1.1):
        self.fetch = fetch or self._fetch
        self.delay = delay
        self._last = 0.0
        self.calls = 0

    def _fetch(self, params: dict) -> list:
        q = urllib.parse.urlencode({**params, "format": "jsonv2", "limit": 1, "countrycodes": "my"})
        req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": UA, "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def search(self, **params):
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        try:
            res = self.fetch(params)
        except Exception as e:  # one failed lookup must not stop the run
            print(f"  lookup failed ({type(e).__name__}): {params}", file=sys.stderr)
            return None
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"])
        return None


def postcode_of(address: str) -> str:
    m = re.findall(r"\b(\d{5})\b", address or "")
    return m[-1] if m else ""


def clean_address(address: str) -> str:
    """Drop unit and lot numbers that confuse the search; keep street, area, town."""
    a = (address or "").upper()
    # "NO. 3", "LOT 4,5,6", "NO 1216-1220", "LOT 533, 535-538": the word plus its list of numbers.
    a = re.sub(r"\b(NO|LOT|UNIT|PT|PTD)\b\.?\s*\d[\w/\-]*(\s*[,&]\s*\d[\w/\-]*)*", " ", a)
    a = re.sub(r"\b(GROUND FLOOR|FIRST FLOOR|G\.?F\.?|LEVEL \w+|TINGKAT \w+|ARAS \w+|BLOK \w+|BLOCK \w+)\b", " ", a)
    a = re.sub(r"\b\d{5}\b", " ", a)
    parts = [re.sub(r"\s+", " ", p).strip(" .-&") for p in a.split(",")]
    return ", ".join(p for p in parts if re.search(r"[A-Z]{3}", p))


def viewbox(lat: float, lon: float, deg: float = 0.35) -> str:
    return f"{lon - deg},{lat + deg},{lon + deg},{lat - deg}"


def read_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {row[next(iter(row))]: row for row in csv.DictReader(f)}


def write_cache(path: Path, rows: dict, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(rows, key=lambda x: (str(x).zfill(12))):
            w.writerow({c: rows[k].get(c, "") for c in fields})
    tmp.replace(path)
    # Verify the write: same number of rows back.
    with path.open(encoding="utf-8", newline="") as f:
        n = sum(1 for _ in csv.DictReader(f))
    if n != len(rows):
        raise SystemExit(f"cache write check failed: {n} rows on disk, {len(rows)} expected")


def shops_from_build(data_dir: Path) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(data_dir / "d" / "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for p in d["premises"]:
            out.append({"premise_code": str(p[0]), "premise": p[1], "address": p[3],
                        "district": d["district"], "state": d["state"]})
    return out


def run(data_dir: Path, cache_path: Path, dcache_path: Path, geo: Geocoder, max_new: int,
        save_every: int = 50) -> dict:
    shops = shops_from_build(data_dir)
    cache = read_cache(cache_path)
    dcache = read_cache(dcache_path)
    todo = [s for s in shops if s["premise_code"] not in cache][:max_new]
    stats = {"shops": len(shops), "cached": len(shops) - len([s for s in shops if s["premise_code"] not in cache]),
             "looked_up": 0, "name": 0, "address": 0, "postcode": 0, "district": 0, "missed": 0}

    def district_centre(st: str, dist: str):
        key = f"{st}|{dist}"
        if key not in dcache:
            hit = geo.search(q=f"{dist}, {st}, Malaysia") or geo.search(q=f"{st}, Malaysia")
            dcache[key] = {"key": key, "lat": f"{hit[0]:.6f}" if hit else "", "lon": f"{hit[1]:.6f}" if hit else ""}
        r = dcache[key]
        return (float(r["lat"]), float(r["lon"])) if r["lat"] else None

    for i, s in enumerate(todo, 1):
        centre = district_centre(s["state"], s["district"])
        pc = postcode_of(s["address"])
        tries = []
        if centre:
            vb = {"viewbox": viewbox(*centre), "bounded": 1}
            tries.append(("name", {"q": s["premise"], **vb}))
            ca = clean_address(s["address"])
            if ca:
                tries.append(("address", {"q": f"{ca}, {s['district']}", **vb}))
            if pc:
                tries.append(("postcode", {"postalcode": pc, **vb}))
        hit, prec, query = None, "", ""
        for prec, params in tries:
            r = geo.search(**params)
            if r and (not centre or km(*r, *centre) <= MAX_KM):
                hit, query = r, params.get("q") or params.get("postalcode", "")
                break
        if not hit and centre:
            hit, prec, query = centre, "district", f"{s['district']}, {s['state']}"
        if hit:
            cache[s["premise_code"]] = {"premise_code": s["premise_code"], "lat": f"{hit[0]:.6f}", "lon": f"{hit[1]:.6f}",
                                        "precision": prec, "query": query, "district": s["district"],
                                        "state": s["state"], "postcode": pc}
            stats[prec] += 1
        else:
            stats["missed"] += 1
        stats["looked_up"] += 1
        if i % save_every == 0:
            write_cache(cache_path, cache, FIELDS)
            write_cache(dcache_path, dcache, ["key", "lat", "lon"])
            print(f"  {i}/{len(todo)} done, {geo.calls} requests", flush=True)
    write_cache(cache_path, cache, FIELDS)
    write_cache(dcache_path, dcache, ["key", "lat", "lon"])
    stats["requests"] = geo.calls
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "web" / "data"))
    ap.add_argument("--max-new", type=int, default=3000, help="look up at most this many new shops")
    args = ap.parse_args(argv)
    stats = run(Path(args.data), CACHE, DCACHE, Geocoder(), args.max_new)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
