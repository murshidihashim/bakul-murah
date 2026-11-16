"""Give every shop a map point, so the app can show shops near the user.

PriceCatcher's premise table has addresses but no coordinates. This looks each shop up with
OpenStreetMap's Nominatim service and keeps the answers in etl/geo/premise_geo.csv, so later
runs only look up new shops and retry weak answers. Shops come from the built district files
(web/data/d/*.json). etl/geo/README.md describes the files and their licences.

Each shop first gets an anchor: where we expect it to be, without asking Nominatim.
  postcode in the address, found in etl/geo/postcodes_my.csv (GeoNames)  -> postcode point
  else a GeoNames town name written in the address (same state)          -> town point
  else the district centre (etl/geo/district_geo.csv)                    -> district centre
Lot, title and box numbers ("LOT 52100", "PTD 43282", "P.O. BOX 80838") are not postcodes, a
town named in a road ("JALAN KUALA KRAI", "JALAN KUCHING-SERIAN") is not where the shop is, and
the state's own name ("..., JASIN, MELAKA") is not a town, except in a district named after it.
A postcode or town counts only if GeoNames has it in the shop's state (for W.P. Kuala Lumpur and
W.P. Putrajaya shops also Selangor's, as strong evidence: Cyberjaya is 63000, Selangor) and it lies
near where the district's shops are: the district centre (when trusted: not shared with another
district, and not 35 km away from the shops OpenStreetMap found) or the median of those shops.
Near means 35 km. Only when nothing in the address is that near may strong evidence lie up to
80 km away: the postcode's own GeoNames place right after it ("91000 TAWAU"), or a town named
after the district ("KUALA ROMPIN" in district Rompin). Else the district centre.

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
DFIELDS = ["key", "lat", "lon", "tries", "shared"]

ANCHOR_KM = 10.0       # a hit must be this close to a postcode or town anchor
DISTRICT_KM = 25.0     # ... or this close to a district centre
MAX_KM = DISTRICT_KM   # old name, kept for older callers
CHECK_KM = 35.0        # a postcode or town further than this from where the district is, is wrong
STRONG_KM = 80.0       # ... unless the evidence is strong ("91000 TAWAU", "KUALA ROMPIN" in Rompin)
STATE_KM = 50.0        # a new district centre must lie this close to a GeoNames place of its state
BOX_NEAR = 0.15        # search box half-size (degrees) around a postcode or town anchor
BOX_DISTRICT = 0.3     # ... and around a district centre
SHARED_KM = 0.5        # district centres this close together are a fallback answer, not real
TOWN_SPREAD_KM = 20.0  # a town name with two points further apart than this names several places
MAX_ERRORS = 10        # errors in a row before the run stops calling Nominatim
MAX_TRIES = 2          # lookups per shop before we stop hoping for a better answer
RETRY_DAYS = 20        # days between two lookups of the same shop
RETRY_AFTER_CAP = 120  # longest wait (seconds) we accept from a 429/503 Retry-After
RETRY_AFTER_DEFAULT = 30
EXACT = ("name", "address")                   # found by OpenStreetMap
LOCATED = EXACT + ("postcode", "town")        # any real point
RANK = {"name": 0, "address": 0, "postcode": 1, "town": 2, "district": 3}  # lower is better
RETRY_ORDER = {"district": 0, "town": 1, "postcode": 2}
# Spellings of the same state in PriceCatcher and GeoNames, after state_key().
STATE_ALIASES = {"penang": "pulau pinang", "malacca": "melaka", "negri sembilan": "negeri sembilan",
                 "johore": "johor", "trengganu": "terengganu"}
# Shops of these states may also have the other state's postcodes and towns, as strong evidence
# only: Cyberjaya (63000, Selangor in GeoNames) is filed under W.P. Putrajaya in PriceCatcher.
# Distance checks still apply.
NEIGHBOURS = {"putrajaya": ("selangor",), "kuala lumpur": ("selangor",)}
# Other ways the state itself is written ("..., JASIN, MELAKA"): a place with such a name is the
# state, not the shop's town.
STATE_SPELLINGS = {"pulau pinang": ("PENANG",), "melaka": ("MALACCA",),
                   "negeri sembilan": ("N SEMBILAN", "NEGRI SEMBILAN"), "johor": ("JOHORE",),
                   "terengganu": ("TRENGGANU",)}
# A town name right after one of these words is a road name: "JALAN KUALA KRAI".
STREET_WORDS = {"JALAN", "JLN", "LORONG", "LRG", "PERSIARAN", "LEBUH", "LEBUHRAYA"}
DASH = r"\s*-\s*"
# Lot, title and box numbers are not postcodes: the word plus its run of numbers, e.g.
# "LOT 12081-12085", "PT 35345 & PT 35346", "HS(D) 4471", "P.O. BOX 80838", "PETI SURAT 61758".
_LOT = r"(?:LOTS?|PLOT|PTD|PTB|PT|NO|HS\s*\(\s*[DM]\s*\)|HS[DM]|GRN|GERAN|GM|BOX|SURAT|P\s*/\s*S|TB|LEASE)"
_NUM = r"\d[\dA-Z/]*"
NOT_POSTCODE = re.compile(rf"\b{_LOT}[\s.:\-]*{_NUM}(?:\s*(?:-|&|\bAND\b)\s*(?:{_LOT}[\s.:\-]*)?{_NUM})*")
# A 5-digit group: not part of a longer number or code; a town may follow with no space ("91000TAWAU").
FIVE = re.compile(r"(?<![0-9A-Z])\d{5}(?![0-9])")


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


def anchor_states(state: str) -> tuple[str, ...]:
    """State keys whose GeoNames rows a shop of this state may use: its own, then NEIGHBOURS."""
    sk = state_key(state)
    return (sk,) + NEIGHBOURS.get(sk, ())


def words(text: str) -> str:
    """Upper case, punctuation to single spaces, for whole-word matching."""
    return re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip()


def state_spellings(state: str) -> set[str]:
    """The shop's state (and neighbours) as written in an address: 'PULAU PINANG', 'PENANG'."""
    return {s for sk in anchor_states(state) for s in (words(sk),) + STATE_SPELLINGS.get(sk, ())}


def _words_after(text: str, end: int) -> list[str]:
    """The words right after position `end` (spaces, commas, dots or a dash between), up to the
    next comma, digit or other mark: '91000 TAWAU, SABAH' and '91000TAWAU' -> ['TAWAU']."""
    m = re.match(r"[\s,.\-]*([A-Z][A-Z \t.'\-]*)", text[end:])
    return words(m.group(1)).split() if m else []


def postcode_of(address: str) -> str:
    """The last 5-digit group (old rule, kept for older callers; see postcodes_in)."""
    m = re.findall(r"\b(\d{5})\b", address or "")
    return m[-1] if m else ""


def _masked(address: str, places=None, state: str = "") -> str:
    """The address in upper case with lot, title and box numbers blanked out. With the GeoNames
    table, a 5-digit group in such a run is kept when Places.rescues() says it is a postcode
    ("NO 10 - 81100 JOHOR BAHRU"); without the table nothing in a run is kept."""
    a = (address or "").upper()
    out = list(a)
    for m in NOT_POSTCODE.finditer(a):
        cut = m.end()
        if places is not None:
            cut = next((g.start() for g in FIVE.finditer(a, m.start(), m.end())
                        if places.rescues(a, g.start(), g.end(), state)), cut)
        out[m.start():cut] = " " * (cut - m.start())
    return "".join(out)


def postcodes_in(address: str, places=None, state: str = "") -> list[str]:
    """5-digit groups that may be the address's postcode, best first. Lot, title and box numbers
    are left out (see _masked); a group followed by a place name ("47500 SUBANG JAYA") comes before
    one followed by a street or nothing; then later groups first."""
    a = _masked(address, places, state)
    found = []
    for m in FIVE.finditer(a):
        nxt = re.match(r"[\s,.]*([A-Z]+)", a[m.end():])
        placed = bool(nxt) and nxt.group(1) not in STREET_WORDS
        found.append((not placed, -m.start(), m.group()))
    return list(dict.fromkeys(pc for *_, pc in sorted(found)))


def best_postcode(address: str, places=None, state: str = "") -> str:
    """The most likely postcode of an address, or '': never a lot, title or box number. With the
    table and the shop's state, a postcode GeoNames has in that state comes first."""
    pcs = postcodes_in(address, places, state)
    if places is not None and state and places.knows(state):
        mine = set(anchor_states(state))
        pcs = sorted(pcs, key=lambda pc: not places.postcode_states.get(pc, set()) & mine)
    return pcs[0] if pcs else ""


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

def _far(point, near, limit: float = CHECK_KM) -> bool:
    """True when there are reference points and the point is more than `limit` km from all of them."""
    return bool(near) and all(km(*point, *r) > limit for r in near)


def _spread_over(points, limit: float) -> bool:
    """True when two of the points are more than `limit` km apart."""
    pts = list(set(points))
    if len(pts) < 2:
        return False
    la, lo = [p[0] for p in pts], [p[1] for p in pts]
    if km(min(la), min(lo), max(la), max(lo)) <= limit:  # the whole box is small enough
        return False
    return any(km(*p, *q) > limit for i, p in enumerate(pts) for q in pts[i + 1:])


class Places:
    """Postcode and town points from etl/geo/postcodes_my.csv (postcode, place, state, lat, lon)."""

    def __init__(self, rows):
        pcs, pc_names, pc_states, towns = {}, {}, {}, {}
        self.states: set[str] = set()                # state keys the table knows
        self.state_points: dict[str, list] = {}      # state key -> points of its rows
        self.state_names: dict[str, set] = {}        # state key -> place names (WORDS)
        self.names: set[str] = set()                 # every place name (WORDS), any state
        self.postcode_places: dict[str, list] = {}   # postcode -> its place names, as word lists
        for r in rows:
            p = _point(r)
            if not p:
                continue
            pc = str(r.get("postcode") or "").strip().zfill(5)
            place = (r.get("place") or "").strip()
            sk = state_key(r.get("state") or "")
            w = words(place)
            self.states.add(sk)
            self.state_points.setdefault(sk, []).append(p)
            if w:
                self.names.add(w)
                self.state_names.setdefault(sk, set()).add(w)
            if re.fullmatch(r"\d{5}", pc):
                pcs.setdefault(pc, []).append(p)
                pc_states.setdefault(pc, set()).add(sk)
                if place:
                    pc_names.setdefault(pc, Counter())[place] += 1
                    own = self.postcode_places.setdefault(pc, [])
                    if w and w.split() not in own:
                        own.append(w.split())
            if sum(c.isalpha() for c in w) >= 4 and w.split()[0] not in STREET_WORDS:  # "Jalan ...": a road
                t = towns.setdefault((sk, w), {"name": place, "points": []})
                t["points"].append(p)
        self.longest = max((len(n.split()) for n in self.names), default=1)  # words in a name
        self.postcodes = {pc: _mean(v) for pc, v in pcs.items()}
        self.postcode_states = pc_states
        self.postcode_names = {pc: sorted(c.items(), key=lambda x: (-x[1], x[0]))[0][0]
                               for pc, c in pc_names.items()}
        self.towns: dict[str, list] = {}  # state key -> [(WORDS, name, point)], longest name first
        for (sk, w), t in towns.items():
            if _spread_over(t["points"], TOWN_SPREAD_KM):
                continue  # one name for places far apart: its average would be nowhere
            self.towns.setdefault(sk, []).append((w, t["name"], _mean(t["points"])))
        for v in self.towns.values():
            v.sort(key=lambda t: (-len(t[0]), t[0]))
        self._for_state: dict[str, list] = {}

    @classmethod
    def load(cls, path) -> "Places | None":
        if not path or not Path(path).exists():
            return None
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def postcode(self, pc: str):
        return self.postcodes.get(pc) if pc else None

    def knows(self, state: str) -> bool:
        """True when the table has rows for this state (under a spelling state_key understands)."""
        return state_key(state) in self.states

    def in_state(self, pc: str, state: str) -> bool:
        """True when GeoNames has the postcode in the shop's state or a NEIGHBOURS state."""
        return bool(self.postcode_states.get(pc, set()) & set(anchor_states(state)))

    def near_state(self, point, state: str, limit: float = STATE_KM) -> bool | None:
        """True when the point lies within `limit` km of a GeoNames place of the state (or a
        NEIGHBOURS state); None when the table has no rows for the state."""
        if not self.knows(state):
            return None
        return any(km(*point, *p) <= limit
                   for sk in anchor_states(state) for p in self.state_points.get(sk, ()))

    def named_after(self, pc: str, after: list) -> bool:
        """True when the words right after a postcode begin with one of its own GeoNames place
        names: '91000 TAWAU', '26800 KUALA ROMPIN'. Strong evidence the postcode is right.
        Words that start with a street word ('JALAN ...') are a road, never a place."""
        if after[:1] and after[0] in STREET_WORDS:
            return False
        return any(after[:len(p)] == p for p in self.postcode_places.get(pc, ()))

    def rescues(self, text: str, start: int, end: int, state: str = "") -> bool:
        """True when the 5-digit group text[start:end], caught in a run of lot or house numbers
        ("NO 10 - 81100 JOHOR BAHRU", "LOT 5 & 47500 SUBANG JAYA"), is really a postcode: GeoNames
        has it (in the shop's state or a NEIGHBOURS state, when the table knows the state) and
        a GeoNames place of that state follows it directly (its own place or another one)."""
        pc = text[start:end]
        states = set(self.postcode_states.get(pc, ()))
        if state and self.knows(state):
            states &= set(anchor_states(state))
        if not states:
            return False
        after = _words_after(text, end)
        if after[:1] and after[0] in STREET_WORDS:
            return False  # "LOT 11570 JALAN ...": a road follows, not a place
        names = set().union(*(self.state_names.get(sk, set()) for sk in states))
        return self.named_after(pc, after) or any(" ".join(after[:n]) in names
                                                  for n in range(1, len(after) + 1))

    def postcode_anchor(self, candidates, state: str, near=(), address: str = "",
                        reach: float = STRONG_KM):
        """(postcode, point, strong) of the first candidate in the table that GeoNames puts in the
        shop's state (a NEIGHBOURS state's only when strong) and that lies near one of the `near`
        points (none given: no distance check): within `reach` km (default STRONG_KM) when it is
        strong (the address names its own GeoNames place right after it, see named_after), else
        within CHECK_KM. None otherwise. For a state the table does not know at all (a spelling we
        do not handle) the distance check alone decides, so that state's postcodes are not all
        thrown away; with no `near` points either, nothing can vouch for a postcode, so none counts."""
        known = self.knows(state)
        text = (address or "").upper()
        for pc in candidates:
            point = self.postcodes.get(pc)
            if point is None or (not known and not near):
                continue
            if known and not self.in_state(pc, state):
                continue  # another state's postcode: a lot number or a typo
            strong = any(self.named_after(pc, _words_after(text, m.end()))
                         for m in FIVE.finditer(text) if m.group() == pc)
            if known and not strong and state_key(state) not in self.postcode_states[pc]:
                continue  # a NEIGHBOURS postcode counts only as strong evidence
            if _far(point, near, reach if strong else CHECK_KM):
                continue
            return pc, point, strong
        return None

    def _towns_for(self, state: str) -> list:
        """[(WORDS, name, point, neighbour)] of the shop's state and its NEIGHBOURS (neighbour:
        True), longest name first."""
        sk = state_key(state)
        if sk not in self._for_state:
            seen, out = set(), []
            for rank, s in enumerate(anchor_states(state)):
                for w, name, point in self.towns.get(s, []):
                    if w not in seen:  # the shop's own state first
                        seen.add(w)
                        out.append((len(w), w, rank, name, point))
            out.sort(key=lambda t: (-t[0], t[1], t[2]))
            self._for_state[sk] = [(w, name, point, rank > 0) for _, w, rank, name, point in out]
        return self._for_state[sk]

    def _place_at(self, text: str, toks: list, k: int, step: int) -> bool:
        """True when a GeoNames place name (any state) ends (step -1) or starts (step 1) at word k."""
        for n in range(1, self.longest + 1):
            a, b = (k - n + 1, k + 1) if step < 0 else (k, k + n)
            if a < 0 or b > len(toks):
                break
            if any(not re.fullmatch(r"[ \t]+", text[toks[x][2]:toks[x + 1][1]]) for x in range(a, b - 1)):
                break  # the words of one name are written next to each other
            if " ".join(t[0] for t in toks[a:b]) in self.names:
                return True
        return False

    def _after_street(self, text: str, toks: list, k: int) -> bool:
        """True when the words ending at word k (written next to each other, at most as many as
        the longest place name) come right after a street word: "JALAN KCH" in "JALAN KCH-SERIAN"."""
        for a in range(k, max(0, k - self.longest), -1):  # a: first word of the run (a >= 1)
            gap = text[toks[a - 1][2]:toks[a][1]]
            if toks[a - 1][0] in STREET_WORDS and re.fullmatch(r"[\s.]*", gap):
                return True
            if not re.fullmatch(r"[ \t]+", gap):
                return False
        return False

    def _on_road(self, text: str, toks: list, i: int, j: int) -> bool:
        """True when words i..j of the address are part of a road name: right after a street word
        ("JALAN KUALA KRAI"), or joined by a dash to another GeoNames place (the route
        "KUCHING-SERIAN") or to words right after a street word ("JALAN KCH-SERIAN").
        "CYBERSQUARE - CYBERJAYA" is not a route: CYBERSQUARE is no place."""
        if i > 0:
            word, _, end = toks[i - 1]
            gap = text[end:toks[i][1]]
            if word in STREET_WORDS and re.fullmatch(r"[\s.]*", gap):
                return True
            if re.fullmatch(DASH, gap) and (self._place_at(text, toks, i - 1, -1)
                                            or self._after_street(text, toks, i - 1)):
                return True
        if j + 1 < len(toks) and re.fullmatch(DASH, text[toks[j][2]:toks[j + 1][1]]):
            return self._place_at(text, toks, j + 1, 1)
        return False

    def town_hits(self, address: str, state: str, district: str = "") -> list:
        """[(place name, point, strong)] of the GeoNames places of the shop's state (and its
        NEIGHBOURS) written in the address as whole words, longest name first. strong: the name is
        or contains the district's name ('KUALA ROMPIN' in district Rompin). Left out: a name that
        is part of a road name (see _on_road); the state's own name ('MELAKA', 'PENANG') unless the
        district is named after it (Melaka Tengah); a NEIGHBOURS place that is not strong (KLANG
        in "JALAN GENTING KLANG", a KL road)."""
        text = (address or "").upper()
        spaced = f" {words(text)} "
        dist = f" {words(plain_name(district))} " if words(plain_name(district)) else ""
        state_names = state_spellings(state)
        toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"[A-Z0-9]+", text)]
        seq = [t[0] for t in toks]
        taken = [False] * len(toks)  # words already part of a longer name
        out = []
        for w, name, point, neighbour in self._towns_for(state):
            if f" {w} " not in spaced:
                continue
            parts, ok = w.split(), False
            for i in range(len(toks) - len(parts) + 1):
                j = i + len(parts) - 1
                if seq[i:j + 1] != parts or any(taken[i:j + 1]):
                    continue
                taken[i:j + 1] = [True] * len(parts)
                ok = ok or not self._on_road(text, toks, i, j)
            strong = bool(dist) and dist in f" {w} "
            if not ok or (w in state_names and f" {w} " not in dist) or (neighbour and not strong):
                continue  # a road, the state itself, or a weak NEIGHBOURS town ("JALAN GENTING KLANG")
            out.append((name, point, strong))
        return out

    def town_anchors(self, address: str, state: str, near=(), district: str = "",
                     reach: float = STRONG_KM) -> list:
        """town_hits() that lie near one of the `near` points: within `reach` km (default
        STRONG_KM) for a strong one, else CHECK_KM (none given: no distance check)."""
        return [t for t in self.town_hits(address, state, district)
                if not _far(t[1], near, reach if t[2] else CHECK_KM)]

    def towns_in(self, address: str, state: str, near=(), district: str = "") -> list:
        """[(place name, point)] of town_anchors()."""
        return [(name, point) for name, point, _ in self.town_anchors(address, state, near, district)]

    def town(self, address: str, state: str, district: str = ""):
        """(place name, point) of the longest town name in the address that is not a road name,
        or None."""
        found = self.towns_in(address, state, (), district)
        return found[0] if found else None


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
    # utf-8-sig: a file saved by a spreadsheet may start with a byte-order mark, which would
    # otherwise become part of the first column's name.
    with path.open(encoding="utf-8-sig", newline="") as f:
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


def shop_medians(cache: dict, kinds=EXACT) -> dict[str, tuple[float, float]]:
    """'State|District' -> median point of its shops with a precision in `kinds` (default: found by
    OpenStreetMap, name or address), when there are at least 2."""
    pts: dict[str, list] = {}
    for row in cache.values():
        p = _point(row)
        if p and row.get("precision") in kinds:
            pts.setdefault(f"{row.get('state', '')}|{row.get('district', '')}", []).append(p)
    return {k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
            for k, v in pts.items() if len(v) >= 2}


class Districts:
    """District centres from etl/geo/district_geo.csv, looked up when missing. A centre shared by
    two or more districts of a state is marked shared=1 in the file and asked again once. The mark
    stays until an answer moves the point away, so a district left alone on the old shared point
    (when the others got their own) is still asked again and still not trusted. With the GeoNames
    table, a new answer more than STATE_KM from every GeoNames place of the district's state is
    not used (the old point stays).

    Checks use only the shops OpenStreetMap found (name or address): a postcode or town point from
    an earlier run must not vouch for itself. Where to search a shop with no postcode or town uses
    the centre, or, while it is not trusted (shared, missing, or far from the district's shops),
    the median of the district's located shops, if it has 2."""

    def __init__(self, dcache: dict, geo: Geocoder, cache: dict, places: Places | None = None):
        self.dcache, self.geo, self.places = dcache, geo, places
        self.medians = shop_medians(cache)            # name or address: a reference for checks
        self.located = shop_medians(cache, LOCATED)   # any real point: only to place a search
        self._mark(shared_keys(dcache))
        self.lookups = self.requeried = self.rejected = 0

    def _mark(self, keys) -> None:
        for key in keys:
            self.dcache[key]["shared"] = "1"

    def is_shared(self, key: str) -> bool:
        return str((self.dcache.get(key) or {}).get("shared") or "").strip() not in ("", "0")

    @property
    def shared(self) -> set[str]:
        return {k for k in self.dcache if self.is_shared(k)}

    def prepare(self, pairs) -> None:
        """Make sure every (state, district) in pairs has had its chance at a centre."""
        for st, dist in sorted(set(pairs)):
            key = f"{st}|{dist}"
            row = self.dcache.get(key)
            if row is None:
                self._ask(key, st, dist, None)
            elif self.is_shared(key) and _int(row.get("tries")) < 1 and self._ask(key, st, dist, row):
                self.requeried += 1
        self._mark(shared_keys(self.dcache))

    def _ask(self, key: str, st: str, dist: str, row: dict | None) -> bool:
        try:
            hit = self.geo.search(q=district_query(st, dist))
        except GeocodeError:
            return False  # not cached: asked again next run
        self.lookups += 1
        if hit and self.places is not None and self.places.near_state(hit, st) is False:
            self.rejected += 1  # not in the district's state: nothing better found
            print(f"  {key}: answer {hit[0]:.4f},{hit[1]:.4f} is more than {STATE_KM:g} km from every"
                  f" GeoNames place of {st}; not used", file=sys.stderr)
            hit = None
        tries = str(_int((row or {}).get("tries")) + 1)
        old = _point(row or {})
        if hit:
            new = {**(row or {}), "key": key, "lat": f"{hit[0]:.6f}", "lon": f"{hit[1]:.6f}", "tries": tries}
            if old is None or km(*hit, *old) > SHARED_KM:
                new["shared"] = ""  # moved off the old point (marked again if it is still shared)
            self.dcache[key] = new
        elif row:
            self.dcache[key] = {**row, "tries": tries}  # nothing better found: keep the old point
        else:
            self.dcache[key] = {"key": key, "lat": "", "lon": "", "tries": tries}
        return True

    def _own(self, key: str, median):
        """The stored centre, unless it is missing, shared, or more than CHECK_KM from `median`."""
        p = _point(self.dcache.get(key) or {})
        return p if p and not self.is_shared(key) and not _far(p, [median] if median else []) else None

    def trusted(self, st: str, dist: str):
        """The district's own centre, or None when it is missing, shared, or more than CHECK_KM
        from the median of the district's shops found by OpenStreetMap (then the centre is the
        middle of a large district far from its town, or a wrong answer)."""
        key = f"{st}|{dist}"
        return self._own(key, self.medians.get(key))

    def references(self, st: str, dist: str) -> list:
        """Where the district's shops are known to be: its trusted centre and the median of its
        shops found by OpenStreetMap (name or address, when it has 2). A postcode or town anchor
        must lie within CHECK_KM (STRONG_KM for strong evidence) of one."""
        return [p for p in (self.trusted(st, dist), self.medians.get(f"{st}|{dist}")) if p]

    def centre(self, st: str, dist: str):
        """Where to search for a shop with no postcode or town: the centre, unless it is missing,
        shared, or more than CHECK_KM from the median of the district's shops (found by
        OpenStreetMap; else any located ones, as Limbang's postcode points at its town); then
        that median; else the stored point."""
        key = f"{st}|{dist}"
        m = self.medians.get(key) or self.located.get(key)
        return self._own(key, m) or m or _point(self.dcache.get(key) or {})


# ---------- one shop ----------

def anchor_for(shop: dict, places: Places | None, districts: Districts) -> dict | None:
    """Where we expect the shop: kind (postcode, town or district), point, label, strong (see
    Places.postcode_anchor and town_hits), plus the town name for the search text and the shop's
    postcode for the cache. None when there is nothing to go on. A postcode or town must be in
    the shop's state and near the trusted district centre or the median of the district's shops
    found by OpenStreetMap (Districts.references); else the next one in the address is tried,
    then the district centre."""
    st, dist, addr = shop["state"], shop["district"], shop["address"]
    near = districts.references(st, dist)
    candidates = postcodes_in(addr, places, st)
    pc = town = None
    # Evidence within CHECK_KM first ("18000 KUALA KRAI, KETEREH" in Kota Bharu: Ketereh); only
    # then may strong evidence lie up to STRONG_KM away (Kuala Rompin, 43 km from Rompin's centre).
    for reach in ((CHECK_KM, STRONG_KM) if places else ()):
        pc = places.postcode_anchor(candidates, st, near, addr, reach)
        towns = places.town_anchors(addr, st, near, dist, reach)
        town = towns[0] if towns else None
        if pc or town:
            break
    context = (town[0] if town else None) or (places.postcode_names.get(pc[0]) if pc else None)
    a = {"town": town[0] if town else None, "context": context or plain_name(dist),
         "postcode": best_postcode(addr, places, st), "strong": False}
    if pc:
        return {**a, "kind": "postcode", "point": pc[1], "label": pc[0], "strong": pc[2]}
    if town:
        return {**a, "kind": "town", "point": town[1], "label": town[0], "strong": town[2]}
    centre = districts.centre(st, dist)
    if centre:
        return {**a, "kind": "district", "point": centre, "label": f"{dist}, {st}"}
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


def make_row(shop: dict, prec: str, point, query: str, tries: int, last_tried: str,
             postcode: str | None = None) -> dict:
    """A premise_geo.csv row. postcode defaults to best_postcode(address): never a lot number."""
    return {"premise_code": shop["premise_code"], "lat": f"{point[0]:.6f}", "lon": f"{point[1]:.6f}",
            "precision": prec, "query": query, "district": shop["district"], "state": shop["state"],
            "postcode": best_postcode(shop["address"]) if postcode is None else postcode,
            "tries": str(tries), "last_tried": last_tried}


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
    unknown = sorted({s["state"] for s in shops if not places.knows(s["state"])}) if places else []
    if unknown:
        print(f"  postcode table has no rows for {', '.join(unknown)}: postcodes there are not checked"
              " against the state", file=sys.stderr)

    new_all = [s for s in shops if s["premise_code"] not in cache]
    new = new_all[:max(0, max_new)]
    again = retry_queue(shops, cache, today, retry)
    # looked_up / retried: shops that reached Nominatim (even if a call then failed);
    # skipped: shops not sent because calls had stopped (breaker or time budget).
    stats = {"shops": len(shops), "cached": len(shops) - len(new_all), "new": len(new_all),
             "looked_up": 0, "retried": 0, "skipped": 0, "name": 0, "address": 0, "postcode": 0,
             "town": 0, "district": 0, "offline": 0, "kept": 0, "missed": 0, "district_lookups": 0,
             "district_requeried": 0, "district_rejected": 0}
    districts = Districts(dcache, geo, cache, places)
    if new or again:
        districts.prepare((s["state"], s["district"]) for s in new + again)
    # The postcode column is worked out again from the address on every write, so a row kept
    # from an older run never holds a lot, title or box number.
    pc_column = {s["premise_code"]: best_postcode(s["address"], places, s["state"]) for s in shops}

    def tidy_postcodes():
        for code, row in cache.items():
            if code in pc_column:
                row["postcode"] = pc_column[code]
            elif places is not None and places.knows(row.get("state") or ""):
                # No address to read (not in this build): keep only a postcode of the shop's state.
                pc = str(row.get("postcode") or "").strip()
                pc = pc.zfill(5) if pc.isdigit() and len(pc) == 4 else pc
                row["postcode"] = pc if places.in_state(pc, row.get("state") or "") else ""

    def save():
        tidy_postcodes()
        write_cache(cache_path, cache, FIELDS)
        write_cache(dcache_path, dcache, dfields)

    def handle(s: dict, old: dict | None) -> bool:
        """Look one shop up and update the cache. True when its lookup ran (it reached Nominatim)."""
        a = anchor_for(s, places, districts)
        if a is None:
            stats["missed"] += 1
            return False
        calls = geo.calls
        try:
            if geo.stopped:
                raise GeocodeStopped("not calling Nominatim any more this run")
            found = lookup(geo, s, a)
        except GeocodeError:
            reached = geo.calls > calls
            if not reached:
                stats["skipped"] += 1
            if old is not None:
                stats["kept"] += 1  # an error is not an answer: keep the row we had
                return reached
            cache[s["premise_code"]] = make_row(s, a["kind"], a["point"], a["label"], 0, "", a["postcode"])
            stats["offline"] += 1
            stats[a["kind"]] += 1
            return reached
        prec, point, query = found or (a["kind"], a["point"], a["label"])
        tries = _int(old.get("tries")) + 1 if old else 1
        # Never swap a point for a less precise one, unless the old one is a postcode or town
        # point more than CHECK_KM from where the district's shops are (e.g. a lot number read
        # as another state's postcode). A good far point is found again by today's anchor.
        old_pt = _point(old) if old else None
        wrong = bool(old_pt and old.get("precision") in ("postcode", "town")
                     and _far(old_pt, districts.references(s["state"], s["district"])))
        if old_pt and RANK[prec] > RANK.get(old.get("precision"), 3) and not wrong:
            cache[s["premise_code"]] = {**old, "tries": str(tries), "last_tried": today}  # no worse point
            prec = old["precision"]
        else:
            cache[s["premise_code"]] = make_row(s, prec, point, query, tries, today, a["postcode"])
        stats[prec] += 1
        return True

    done = 0
    for s in new:
        stats["looked_up"] += handle(s, None)
        done += 1
        if done % save_every == 0:
            save()
            print(f"  {done} shops done, {geo.calls} requests, {geo.minutes():.1f} min", flush=True)
    for i, s in enumerate(again):
        if geo.stopped:
            stats["skipped"] += len(again) - i  # never sent: calls had stopped
            break
        stats["retried"] += handle(s, cache[s["premise_code"]])
        done += 1
        if done % save_every == 0:
            save()
            print(f"  {done} shops done, {geo.calls} requests, {geo.minutes():.1f} min", flush=True)
    save()
    stats.update({"district_lookups": districts.lookups, "district_requeried": districts.requeried,
                  "district_rejected": districts.rejected,
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
