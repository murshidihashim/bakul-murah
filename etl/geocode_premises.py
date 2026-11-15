"""Give every shop a map point, so the app can show shops near the user.

PriceCatcher's premise table has addresses but no coordinates. This looks each shop up with
OpenStreetMap's Nominatim service and keeps the answers in etl/geo/premise_geo.csv, so later
runs only look up new shops and retry weak answers. Shops come from the built district files
(web/data/d/*.json). etl/geo/README.md describes the files and their licences.

Each shop first gets an anchor: where we expect it to be, without asking Nominatim.
  postcode in the address, found in etl/geo/postcodes_my.csv (GeoNames)  -> postcode point
  else a GeoNames town name written in the address (same state)          -> town point
  else the district centre (etl/geo/district_geo.csv)                    -> district centre

Then at most 3 searches in a box around the anchor, stopping at the first good hit:
  1. "<shop name>, <town or district>"   -> precision "name"
  2. the cleaned address                  -> precision "address"
  3. the shop name alone                  -> precision "name"
A hit counts only within 10 km of a postcode or town anchor, or 25 km of a district centre.
With no good hit the shop gets the anchor itself: precision "postcode", "town" or "district".

Errors are not answers. If a search for a shop fails (network, timeout, HTTP error, bad reply),
the shop keeps its old row; a new shop gets its anchor with tries=0, so the next run tries again.
After 10 errors in a row, or when --max-minutes is used up, the run stops calling Nominatim.

After the new shops, shops at district, then town, then postcode precision are retried
(at most 2 lookups per shop, at least 20 days apart) until the time is used up.

Nominatim usage policy: at most 1 request a second, a clear User-Agent, cache results,
credit OpenStreetMap (ODbL). This script does all four. Standard library only.

Run (GitHub Actions does this between the two builds):
  python etl/geocode_premises.py --max-minutes 40
"""
from __future__ import annotations

import argparse
import csv
import email.utils
import glob
import json
import math
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / "geo" / "premise_geo.csv"
DCACHE = HERE / "geo" / "district_geo.csv"
POSTCODES = HERE / "geo" / "postcodes_my.csv"
URL = "https://nominatim.openstreetmap.org/search"
UA = "BakulMurah/1.1 (+https://github.com/murshidihashim/bakul-murah)"
FIELDS = ["premise_code", "lat", "lon", "precision", "query", "district", "state", "postcode",
          "tries", "last_tried"]
DFIELDS = ["key", "lat", "lon", "tries"]

ANCHOR_KM = 10.0       # a hit must be this close to a postcode or town anchor
DISTRICT_KM = 25.0     # ... or this close to a district centre
MAX_KM = DISTRICT_KM   # old name, kept for older callers
BOX_NEAR = 0.15        # search box half-size (degrees) around a postcode or town anchor
BOX_DISTRICT = 0.3     # ... and around a district centre
SHARED_KM = 0.5        # district centres this close together are a fallback answer, not real
TOWN_SPREAD_KM = 20.0  # a town name whose points spread wider than this is ambiguous
MAX_ERRORS = 10        # errors in a row before the run stops calling Nominatim
MAX_TRIES = 2          # lookups per shop before we stop hoping for a better answer
RETRY_DAYS = 20        # days between two lookups of the same shop
RETRY_AFTER_CAP = 120  # longest wait (seconds) we accept from a 429/503 Retry-After
RETRY_AFTER_DEFAULT = 30
LOCATED = ("name", "address", "postcode", "town")
RANK = {"name": 0, "address": 0, "postcode": 1, "town": 2, "district": 3}  # lower is better
RETRY_ORDER = {"district": 0, "town": 1, "postcode": 2}
# Spellings of the same state in PriceCatcher and GeoNames, after state_key().
STATE_ALIASES = {"penang": "pulau pinang", "malacca": "melaka", "negri sembilan": "negeri sembilan",
                 "johore": "johor", "trengganu": "terengganu"}


def km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _mean(points) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _int(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError, OverflowError):
        return 0


def _point(row) -> tuple[float, float] | None:
    try:
        return float(row["lat"]), float(row["lon"])
    except (TypeError, KeyError, ValueError):
        return None


# ---------- names ----------

def plain_name(name: str) -> str:
    """Drop the federal territory prefix: 'W.P. Kuala Lumpur', 'WP Kuala Lumpur' -> 'Kuala Lumpur'."""
    s = re.sub(r"\b(wilayah\s+persekutuan|w\s*\.?\s*p)\b\.?", " ", name or "", flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    return s or (name or "").strip()


def state_key(name: str) -> str:
    """Compare states across sources: 'W.P. Kuala Lumpur' and 'Kuala Lumpur' -> 'kuala lumpur'."""
    s = plain_name(name).lower()
    return STATE_ALIASES.get(s, s)


def words(text: str) -> str:
    """Upper case, punctuation to single spaces, for whole-word matching."""
    return re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip()


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


# ---------- offline anchors (GeoNames postcodes) ----------

class Places:
    """Postcode and town points from etl/geo/postcodes_my.csv (postcode, place, state, lat, lon)."""

    def __init__(self, rows):
        pcs, pc_names, towns = {}, {}, {}
        for r in rows:
            p = _point(r)
            if not p:
                continue
            pc = str(r.get("postcode") or "").strip().zfill(5)
            place = (r.get("place") or "").strip()
            if re.fullmatch(r"\d{5}", pc):
                pcs.setdefault(pc, []).append(p)
                if place:
                    pc_names.setdefault(pc, Counter())[place] += 1
            w = words(place)
            if sum(c.isalpha() for c in w) >= 4:
                t = towns.setdefault((state_key(r.get("state", "")), w), {"name": place, "points": []})
                t["points"].append(p)
        self.postcodes = {pc: _mean(v) for pc, v in pcs.items()}
        self.postcode_names = {pc: sorted(c.items(), key=lambda x: (-x[1], x[0]))[0][0]
                               for pc, c in pc_names.items()}
        self.towns: dict[str, list] = {}  # state key -> [(WORDS, name, point)], longest name first
        for (sk, w), t in towns.items():
            centre = _mean(t["points"])
            if max(km(*centre, *p) for p in t["points"]) > TOWN_SPREAD_KM:
                continue  # one name for places far apart: its average would be nowhere
            self.towns.setdefault(sk, []).append((w, t["name"], centre))
        for v in self.towns.values():
            v.sort(key=lambda t: (-len(t[0]), t[0]))

    @classmethod
    def load(cls, path) -> "Places | None":
        if not path or not Path(path).exists():
            return None
        with Path(path).open(encoding="utf-8", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def postcode(self, pc: str):
        return self.postcodes.get(pc) if pc else None

    def town(self, address: str, state: str):
        """(place name, point) of the longest town name in the address, or None."""
        text = f" {words(address)} "
        for w, name, point in self.towns.get(state_key(state), []):
            if f" {w} " in text:
                return name, point
        return None


# ---------- Nominatim ----------

class GeocodeError(Exception):
    """A lookup that failed: network error, timeout, HTTP error or a reply we cannot read.
    Not the same as Nominatim answering 'nothing found'."""


class GeocodeStopped(GeocodeError):
    """The run has stopped calling Nominatim (too many errors in a row, or out of time)."""


def retry_after(headers, default: float = RETRY_AFTER_DEFAULT, cap: float = RETRY_AFTER_CAP) -> float:
    """Seconds to wait from a Retry-After header (seconds or an HTTP date), at most `cap`."""
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return min(default, cap)
    try:
        secs = float(str(raw).strip())
    except ValueError:
        try:
            secs = (email.utils.parsedate_to_datetime(str(raw)) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, IndexError, OverflowError):
            secs = default
    return max(0.0, min(secs, cap))


def _first_hit(res):
    if not isinstance(res, list):
        raise ValueError(f"unexpected reply: {str(res)[:80]}")
    if not res:
        return None
    lat, lon = float(res[0]["lat"]), float(res[0]["lon"])
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("reply has no usable coordinates")
    return lat, lon


class Geocoder:
    """Nominatim client: 1.1 s between calls; errors are raised, never returned as 'no result';
    stops after `max_errors` errors in a row, or after `max_minutes` (None = no limit).
    `fetch(params) -> list`, `clock` and `sleep` can be swapped in tests."""

    def __init__(self, fetch=None, delay: float = 1.1, max_minutes: float | None = None,
                 max_errors: int = MAX_ERRORS, clock=None, sleep=None):
        self.fetch = fetch or self._fetch
        self.delay = delay
        self.max_minutes = max_minutes
        self.max_errors = max_errors
        self.clock = clock or time.monotonic
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.start = self.clock()
        self._last = None
        self.calls = 0
        self.errors = 0
        self.in_a_row = 0
        self.breaker_tripped = False
        self.out_of_time = False

    def _fetch(self, params: dict) -> list:
        q = urllib.parse.urlencode({**params, "format": "jsonv2", "limit": 1, "countrycodes": "my"})
        req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": UA, "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def minutes(self) -> float:
        return (self.clock() - self.start) / 60

    def seconds_left(self) -> float:
        if self.max_minutes is None:
            return math.inf
        return max(0.0, self.max_minutes * 60 - (self.clock() - self.start))

    @property
    def stopped(self) -> bool:
        if not self.out_of_time and self.seconds_left() <= 0:
            self.out_of_time = True
        return self.breaker_tripped or self.out_of_time

    def search(self, **params):
        """(lat, lon) of the top hit, or None when nothing was found. Raises GeocodeError."""
        if self.stopped:
            raise GeocodeStopped("not calling Nominatim any more this run")
        if self._last is not None:
            wait = self._last + self.delay - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.calls += 1
        try:
            hit = _first_hit(self.fetch(params))
        except Exception as e:  # network, timeout, HTTP error or bad JSON: an error, not "nothing found"
            self._failed(e, params)
            raise GeocodeError(f"{type(e).__name__}: {e}") from e
        self.in_a_row = 0
        return hit

    def _failed(self, e: Exception, params: dict) -> None:
        self.errors += 1
        self.in_a_row += 1
        print(f"  lookup error ({type(e).__name__}: {str(e)[:120]}): {params.get('q', params)}", file=sys.stderr)
        if getattr(e, "code", None) in (429, 503):
            wait = min(retry_after(getattr(e, "headers", None)), self.seconds_left())
            if wait > 0:
                print(f"  Nominatim asked us to wait; waiting {wait:.0f} s", file=sys.stderr)
                self.sleep(wait)
        if self.in_a_row >= self.max_errors and not self.breaker_tripped:
            self.breaker_tripped = True
            print(f"  {self.in_a_row} errors in a row: no more Nominatim calls this run", file=sys.stderr)


# ---------- caches ----------

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
    out, seen = [], set()
    for f in sorted(glob.glob(str(data_dir / "d" / "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        cols = d.get("premise_cols") or []
        i_code, i_name, i_addr = (cols.index(c) if c in cols else j
                                  for c, j in (("premise_code", 0), ("premise", 1), ("address", 3)))
        for p in d["premises"]:
            code = str(p[i_code])
            if code in seen:
                continue
            seen.add(code)
            out.append({"premise_code": code, "premise": p[i_name] or "", "address": p[i_addr] or "",
                        "district": d["district"], "state": d["state"]})
    return out


# ---------- district centres ----------

def district_query(state: str, district: str) -> str:
    return f"{plain_name(district)}, {plain_name(state)}, Malaysia"


def shared_keys(dcache: dict) -> set[str]:
    """Keys whose centre is within SHARED_KM of another district's centre in the same state.
    A shared point is usually a fallback answer (the state centre), not the district."""
    by_state: dict[str, list] = {}
    for key, row in dcache.items():
        p = _point(row)
        if p:
            by_state.setdefault(key.split("|")[0], []).append((key, p))
    out = set()
    for rows in by_state.values():
        for i, (k1, p1) in enumerate(rows):
            for k2, p2 in rows[i + 1:]:
                if km(*p1, *p2) <= SHARED_KM:
                    out.update((k1, k2))
    return out


def shop_medians(cache: dict) -> dict[str, tuple[float, float]]:
    """'State|District' -> median point of its located shops, when there are at least 2."""
    pts: dict[str, list] = {}
    for row in cache.values():
        p = _point(row)
        if p and row.get("precision") in LOCATED:
            pts.setdefault(f"{row.get('state', '')}|{row.get('district', '')}", []).append(p)
    return {k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
            for k, v in pts.items() if len(v) >= 2}


class Districts:
    """District centres from etl/geo/district_geo.csv, looked up when missing. Centres shared by
    two or more districts of a state are asked again once; while still shared (or missing), the
    median of the district's located shops is used instead, when there are at least 2."""

    def __init__(self, dcache: dict, geo: Geocoder, cache: dict):
        self.dcache, self.geo = dcache, geo
        self.medians = shop_medians(cache)
        self.shared: set[str] = shared_keys(dcache)
        self.lookups = self.requeried = 0

    def prepare(self, pairs) -> None:
        """Make sure every (state, district) in pairs has had its chance at a centre."""
        before = shared_keys(self.dcache)
        for st, dist in sorted(set(pairs)):
            key = f"{st}|{dist}"
            row = self.dcache.get(key)
            if row is None:
                self._ask(key, st, dist, None)
            elif key in before and _int(row.get("tries")) < 1 and self._ask(key, st, dist, row):
                self.requeried += 1
        self.shared = shared_keys(self.dcache)

    def _ask(self, key: str, st: str, dist: str, row: dict | None) -> bool:
        try:
            hit = self.geo.search(q=district_query(st, dist))
        except GeocodeError:
            return False  # not cached: asked again next run
        self.lookups += 1
        tries = str(_int((row or {}).get("tries")) + 1)
        if hit:
            self.dcache[key] = {**(row or {}), "key": key, "lat": f"{hit[0]:.6f}", "lon": f"{hit[1]:.6f}",
                                "tries": tries}
        elif row:
            self.dcache[key] = {**row, "tries": tries}  # nothing better found: keep the old point
        else:
            self.dcache[key] = {"key": key, "lat": "", "lon": "", "tries": tries}
        return True

    def centre(self, st: str, dist: str):
        key = f"{st}|{dist}"
        p = _point(self.dcache.get(key) or {})
        if p and key not in self.shared:
            return p
        return self.medians.get(key) or p


# ---------- one shop ----------

def anchor_for(shop: dict, places: Places | None, districts: Districts) -> dict | None:
    """Where we expect the shop: kind (postcode, town or district), point, label, plus the town
    name for the search text. None when there is nothing to go on."""
    pc = postcode_of(shop["address"])
    town = places.town(shop["address"], shop["state"]) if places else None
    pc_point = places.postcode(pc) if places else None
    context = (town[0] if town else None) or (places.postcode_names.get(pc) if places and pc_point else None)
    a = {"town": town[0] if town else None, "context": context or plain_name(shop["district"])}
    if pc_point:
        return {**a, "kind": "postcode", "point": pc_point, "label": pc}
    if town:
        return {**a, "kind": "town", "point": town[1], "label": town[0]}
    centre = districts.centre(shop["state"], shop["district"])
    if centre:
        return {**a, "kind": "district", "point": centre, "label": f"{shop['district']}, {shop['state']}"}
    return None


def lookup(geo: Geocoder, shop: dict, a: dict):
    """(precision, point, query) of the first good hit near the anchor, or None.
    Raises GeocodeError (the caller then keeps what it had)."""
    near = a["kind"] in ("postcode", "town")
    box = {"viewbox": viewbox(*a["point"], BOX_NEAR if near else BOX_DISTRICT), "bounded": 1}
    limit = ANCHOR_KM if near else DISTRICT_KM
    name = (shop["premise"] or "").strip()
    queries, seen = [], set()
    for prec, q in (("name", f"{name}, {a['context']}" if name else ""),
                    ("address", clean_address(shop["address"])),
                    ("name", name)):
        if q and q.upper() not in seen:
            seen.add(q.upper())
            queries.append((prec, q))
    for prec, q in queries[:3]:
        hit = geo.search(q=q, **box)
        if hit and km(*hit, *a["point"]) <= limit:
            return prec, hit, q
    return None


def make_row(shop: dict, prec: str, point, query: str, tries: int, last_tried: str) -> dict:
    return {"premise_code": shop["premise_code"], "lat": f"{point[0]:.6f}", "lon": f"{point[1]:.6f}",
            "precision": prec, "query": query, "district": shop["district"], "state": shop["state"],
            "postcode": postcode_of(shop["address"]), "tries": str(tries), "last_tried": last_tried}


def retry_queue(shops: list[dict], cache: dict, today: str, limit: int) -> list[dict]:
    """Cached shops worth another lookup: district first, then town, then postcode precision;
    fewer than MAX_TRIES lookups, and none in the last RETRY_DAYS days."""
    cutoff = (date.fromisoformat(today) - timedelta(days=RETRY_DAYS)).isoformat()
    out = []
    for s in shops:
        row = cache.get(s["premise_code"])
        if not row or row.get("precision") not in RETRY_ORDER or _int(row.get("tries")) >= MAX_TRIES:
            continue
        last = (row.get("last_tried") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", last):
            last = ""
        if last and last >= cutoff:
            continue
        out.append((RETRY_ORDER[row["precision"]], _int(row.get("tries")), last, s["premise_code"].zfill(12), s))
    out.sort(key=lambda t: t[:4])
    return [t[-1] for t in out[:max(0, limit)]]


# ---------- the run ----------

def run(data_dir: Path, cache_path: Path, dcache_path: Path, geo: Geocoder, max_new: int = 3000,
        retry: int = 3000, postcodes_path: Path | None = None, today=None, save_every: int = 50) -> dict:
    today = str(today or date.today().isoformat())
    shops = shops_from_build(data_dir)
    cache = read_cache(cache_path)
    for row in cache.values():  # older files have no tries/last_tried: missing means 0 / never
        row["tries"] = str(_int(row.get("tries")))
        row["last_tried"] = row.get("last_tried") or ""
    dcache = read_cache(dcache_path)
    for row in dcache.values():
        row["tries"] = str(_int(row.get("tries")))
    dfields = DFIELDS + [c for c in dict.fromkeys(c for r in dcache.values() for c in r) if c and c not in DFIELDS]
    places = Places.load(postcodes_path)
    if postcodes_path and places is None:
        print(f"  no postcode table at {postcodes_path}; using district centres only", file=sys.stderr)

    new_all = [s for s in shops if s["premise_code"] not in cache]
    new = new_all[:max(0, max_new)]
    again = retry_queue(shops, cache, today, retry)
    stats = {"shops": len(shops), "cached": len(shops) - len(new_all), "new": len(new_all),
             "looked_up": 0, "retried": 0, "name": 0, "address": 0, "postcode": 0, "town": 0,
             "district": 0, "offline": 0, "kept": 0, "missed": 0, "district_lookups": 0,
             "district_requeried": 0}
    districts = Districts(dcache, geo, cache)
    if new or again:
        districts.prepare((s["state"], s["district"]) for s in new + again)

    def save():
        write_cache(cache_path, cache, FIELDS)
        write_cache(dcache_path, dcache, dfields)

    def handle(s: dict, old: dict | None) -> None:
        a = anchor_for(s, places, districts)
        if a is None:
            stats["missed"] += 1
            return
        try:
            if geo.stopped:
                raise GeocodeStopped("not calling Nominatim any more this run")
            found = lookup(geo, s, a)
        except GeocodeError:
            if old is not None:
                stats["kept"] += 1  # an error is not an answer: keep the row we had
                return
            cache[s["premise_code"]] = make_row(s, a["kind"], a["point"], a["label"], 0, "")
            stats["offline"] += 1
            stats[a["kind"]] += 1
            return
        prec, point, query = found or (a["kind"], a["point"], a["label"])
        tries = _int(old.get("tries")) + 1 if old else 1
        if old and _point(old) and RANK[prec] > RANK.get(old.get("precision"), 3):
            cache[s["premise_code"]] = {**old, "tries": str(tries), "last_tried": today}  # no worse point
            prec = old["precision"]
        else:
            cache[s["premise_code"]] = make_row(s, prec, point, query, tries, today)
        stats[prec] += 1

    done = 0
    for s in new:
        handle(s, None)
        stats["looked_up"] += 1
        done += 1
        if done % save_every == 0:
            save()
            print(f"  {done} shops done, {geo.calls} requests, {geo.minutes():.1f} min", flush=True)
    for s in again:
        if geo.stopped:
            break
        handle(s, cache[s["premise_code"]])
        stats["retried"] += 1
        done += 1
        if done % save_every == 0:
            save()
            print(f"  {done} shops done, {geo.calls} requests, {geo.minutes():.1f} min", flush=True)
    save()
    stats.update({"district_lookups": districts.lookups, "district_requeried": districts.requeried,
                  "retry_waiting": len(retry_queue(shops, cache, today, 10 ** 9)),
                  "errors": geo.errors, "breaker_tripped": geo.breaker_tripped,
                  "out_of_time": geo.out_of_time, "requests": geo.calls, "minutes": round(geo.minutes(), 1)})
    located = Counter(cache[s["premise_code"]].get("precision") for s in shops if s["premise_code"] in cache)
    stats["all_shops"] = {p: located.get(p, 0) for p in RANK} | {"none": len(shops) - sum(located.values())}
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "web" / "data"))
    ap.add_argument("--max-new", type=int, default=3000, help="look up at most this many new shops")
    ap.add_argument("--retry", type=int, default=3000, help="retry at most this many weak answers")
    ap.add_argument("--max-minutes", type=float, default=40, help="stop calling Nominatim after this")
    ap.add_argument("--postcodes", default=str(POSTCODES), help="GeoNames table ('' for none)")
    args = ap.parse_args(argv)
    geo = Geocoder(max_minutes=args.max_minutes)
    stats = run(Path(args.data), CACHE, DCACHE, geo, args.max_new, args.retry,
                Path(args.postcodes) if args.postcodes else None)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
