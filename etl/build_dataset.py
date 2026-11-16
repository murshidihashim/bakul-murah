"""Turn raw PriceCatcher files into small JSON files for the web app.

Input folder (default data/raw) holds files named as on data.gov.my:
  lookup_item.(parquet|csv), lookup_premise.(parquet|csv), pricecatcher_YYYY-MM.(parquet|csv)

Steps:
  1. Clean prices: fix types, drop zero/blank/unknown codes, merge same-day
     duplicates (median), drop outliers (more than 4x away from that item's
     monthly median).
  2. Join item and premise lookups.
  3. Months with data: a month counts only if its cleaned rows are at least 5% of the
     median of the other months. A new month file often holds just a day or two.
  4. Per premise and item: latest price (newest date). It is shown only if it is at most
     30 days older than the data date, and it is set aside if it is below 0.4x or above
     2.5x the item's median across all shops (usually a different pack size).
     Shops left with no price are not listed.
  5. Per premise, item and month: median price (all prices, for history).
  6. Monthly medians per item for each district and for Malaysia.
  6b. Per shop, the monthly median for the last 3 months with data (for "cheapest on average").
     The pack-size check applies here too: any monthly price below 0.4x or above 2.5x that
     month's median across all shops is left out, and so is the month of a latest price set
     aside in step 4. That shop's other months for the item stay only within 0.8x-1.25x of
     the month's median.
  7. Month-on-month change, like-for-like: only shops priced in both months count.
     The change is the median of their newest-month prices over the median of their
     previous-month prices, so the % matches the RM figures shown.
  8. Proof figure for the home screen: what a typical weekly list costs at the
     cheapest shop and at a typical shop, per district. Added up in whole sen as the app
     adds up a list, so the figures match the app's for the same list.
  9. Shop locations from etl/geo/ (made by geocode_premises.py): each shop's map point,
     a centre and radius per district, and a postcode -> location table for "near me": the GeoNames
     points checked against the shops OpenStreetMap found (see check_postcodes).
     A district point shared with another district of the state is never a centre.
 10. Write web/data/meta.json plus one web/data/d/<state>--<district>.json per
     district. Each JSON also gets a .js twin so the app works from file://.

Run:
  python etl/build_dataset.py                        # real data in data/raw
  python etl/build_dataset.py --raw data/sample      # synthetic sample (etl/geo/ files not used)
  python etl/build_dataset.py --state Selangor       # one state only (other district files are kept)
  python etl/build_dataset.py --top-items 300        # keep most-covered items
  python etl/build_dataset.py --months 12            # months of history (at least 3)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = "PriceCatcher, KPDN via OpenDOSM"
SOURCE_URL = "https://open.dosm.gov.my/data-catalogue/pricecatcher"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
OUTLIER_FACTOR = 4.0
MAX_PRICE_AGE_DAYS = 30          # older latest prices are not shown
MIN_MONTHS = 3                   # a price up to 30 days old can be in any of the 3 newest month files
PACK_LOW, PACK_HIGH = 0.4, 2.5   # a price vs the item's median across all shops
ASIDE_LOW, ASIDE_HIGH = 0.8, 1.25  # other months of a shop and item whose latest price was set aside
MIN_MONTH_SHARE = 0.05           # share of a usual month's rows for a month to count
MONTH_RE = re.compile(r"^pricecatcher_(\d{4}-\d{2})$")

# Home-screen proof: a typical weekly list as (label, item name pattern, unit or None for any).
# Each group uses the matching item that is priced at the most shops.
PROOF_GROUPS = [
    ("ayam bersih standard", r"^AYAM BERSIH - STANDARD", None),
    ("telur gred A 30 biji", r"^TELUR AYAM GRED A$", "30 biji"),
    ("beras 10 kg", r"^BERAS (?!PULUT)", "10 kg"),
    ("gula putih kasar", r"^GULA PUTIH BERTAPIS KASAR", None),
    ("minyak masak 5 kg", r"^MINYAK MASAK (SEBATIAN|TULEN)", "5 kg"),
    ("bawang besar", r"^BAWANG BESAR", None),
    ("kangkung", r"^KANGKUNG", None),
    ("tomato", r"^TOMATO$", None),
    ("ikan kembung", r"^IKAN KEMBUNG \(", None),
    ("susu pekat", r"^KRIMER MANIS", None),
]
PROOF_MIN_GROUPS = 8   # fewer groups found: no proof figure
PROOF_MIN_SHOPS = 3    # a district needs this many shops that price the whole list


# ---------- loading ----------

def _has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def load_table(raw: Path, stem: str) -> pd.DataFrame:
    pq, csv = raw / f"{stem}.parquet", raw / f"{stem}.csv"
    if pq.exists() and _has_pyarrow():
        return pd.read_parquet(pq)
    if csv.exists():
        return pd.read_csv(csv)
    if pq.exists():
        raise SystemExit(f"{pq.name} needs pyarrow. Run: pip install pyarrow")
    raise FileNotFoundError(f"missing {stem} in {raw}")


def find_months(raw: Path) -> list[str]:
    months = set()
    for p in raw.glob("pricecatcher_*.*"):
        m = MONTH_RE.match(p.stem)
        if m and p.suffix in (".parquet", ".csv"):
            months.add(m.group(1))
    return sorted(months)


# ---------- cleaning ----------

def clean_items(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["item_code"] = pd.to_numeric(df["item_code"], errors="coerce")
    df = df.dropna(subset=["item_code", "item"])
    df = df[df["item_code"] > 0]
    df["item_code"] = df["item_code"].astype("int64")
    for c in ["item", "unit", "item_group", "item_category"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    return df.drop_duplicates("item_code").reset_index(drop=True)


def clean_premises(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["premise_code"] = pd.to_numeric(df["premise_code"], errors="coerce")
    df = df.dropna(subset=["premise_code", "premise", "state", "district"])
    df["premise_code"] = df["premise_code"].astype("int64")
    for c in ["premise", "address", "premise_type", "state", "district"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df = df[(df["state"] != "") & (df["district"] != "")]
    return df.drop_duplicates("premise_code").reset_index(drop=True)


def clean_prices(df: pd.DataFrame, item_codes=None, premise_codes=None) -> pd.DataFrame:
    """Return date (datetime64), premise_code, item_code (int64), price (float)."""
    df = df[["date", "premise_code", "item_code", "price"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["premise_code", "item_code"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna()
    df = df[df["price"] > 0]
    df["premise_code"] = df["premise_code"].astype("int64")
    df["item_code"] = df["item_code"].astype("int64")
    if item_codes is not None:
        df = df[df["item_code"].isin(set(item_codes))]
    if premise_codes is not None:
        df = df[df["premise_code"].isin(set(premise_codes))]
    # Same shop, item and day reported more than once: take the median.
    df = df.groupby(["date", "premise_code", "item_code"], as_index=False)["price"].median()
    # Outliers: far from the item's median in this file (usually one month).
    med = df.groupby("item_code")["price"].transform("median")
    ok = (df["price"] <= med * OUTLIER_FACTOR) & (df["price"] >= med / OUTLIER_FACTOR)
    return df[ok].reset_index(drop=True)


# ---------- metrics ----------

def monthly_premise_median(prices: pd.DataFrame) -> pd.DataFrame:
    """month (YYYY-MM), premise_code, item_code, price (median of the month)."""
    p = prices if "month" in prices.columns else prices.assign(month=prices["date"].dt.strftime("%Y-%m"))
    return p.groupby(["month", "premise_code", "item_code"], as_index=False)["price"].median()


def months_with_data(rows: dict[str, int], months: list[str], share: float = MIN_MONTH_SHARE) -> list[str]:
    """The months whose cleaned rows are at least `share` of the median of the other months (and 1+)."""
    out = []
    for m in months:
        n = rows.get(m, 0)
        others = [rows.get(o, 0) for o in months if o != m]
        if n >= 1 and (not others or n >= share * statistics.median(others)):
            out.append(m)
    return out


def latest_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Newest observation per premise and item: premise_code, item_code, price, date."""
    if prices.empty:
        return pd.DataFrame(columns=["premise_code", "item_code", "price", "date"])
    last = prices.groupby(["premise_code", "item_code"])["date"].transform("max")
    p = prices[prices["date"] == last]
    return p.groupby(["premise_code", "item_code"], as_index=False).agg(price=("price", "median"),
                                                                       date=("date", "max"))


def fresh_only(latest: pd.DataFrame, data_date, max_days: int = MAX_PRICE_AGE_DAYS) -> pd.DataFrame:
    """Add days_old (days before data_date) and keep only prices at most max_days old."""
    days = (data_date - latest["date"]).dt.days
    return latest.assign(days_old=days)[days <= max_days].reset_index(drop=True)


def pack_ok(price, median, low: float = PACK_LOW, high: float = PACK_HIGH):
    """True where price is from low x to high x the median, both limits included.

    The tiny tolerance keeps prices that are exactly on a limit: in floating point
    1.20 / 3.00 is 0.39999999999999997 and 2.35 / 0.94 is 2.5000000000000004.
    """
    ratio = price / median
    return (ratio >= low - 1e-9) & (ratio <= high + 1e-9)


def set_aside_pack_errors(latest: pd.DataFrame, low: float = PACK_LOW,
                          high: float = PACK_HIGH) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split latest prices into (kept, set aside).

    A price below low x or above high x the item's median across all shops is usually
    a different pack size reported under the item, so it is not compared.
    """
    ok = pack_ok(latest["price"], latest.groupby("item_code")["price"].transform("median"), low, high)
    return latest[ok].reset_index(drop=True), latest[~ok].reset_index(drop=True)


def same_pack_months(pm: pd.DataFrame, aside: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    """The monthly shop prices that "cheapest on average" may compare, for the given months.

    pm: month, premise_code, item_code, price; aside: premise_code, item_code, date of the
    latest prices set aside. Each monthly price is compared with that month's median for the
    item across all shops, and must be within PACK_LOW..PACK_HIGH x that median. For a shop and
    item whose latest price was set aside, the month that holds that price is left out (it may
    mix in the other pack), and its other months stay only within ASIDE_LOW..ASIDE_HIGH x the
    median: a shop that reported another pack once often did so in other months too, at a price
    the wide limits still let through.
    """
    p = pm[pm["month"].isin(months)]
    med = p.groupby(["month", "item_code"])["price"].transform("median")
    keep = pack_ok(p["price"], med)
    if len(aside):
        bad = pd.MultiIndex.from_arrays([aside["date"].dt.strftime("%Y-%m"), aside["premise_code"],
                                         aside["item_code"]])
        keep = keep & ~pd.MultiIndex.from_frame(p[["month", "premise_code", "item_code"]]).isin(bad)
        was_aside = pd.MultiIndex.from_frame(p[["premise_code", "item_code"]]).isin(
            pd.MultiIndex.from_frame(aside[["premise_code", "item_code"]]))
        keep = keep & (~was_aside | pack_ok(p["price"], med, ASIDE_LOW, ASIDE_HIGH))
    return p[keep]


def mom_changes(pm: pd.DataFrame, cur: str, prev: str, min_n: int = 3) -> pd.DataFrame:
    """Like-for-like month-on-month change per item.

    pm: month, premise_code, item_code, price (plus optional group columns).
    Only shops priced in both months count. Returns item_code, pct, n (shops),
    prev_price, cur_price: the medians over those shops (to the sen), and
    pct = (cur_price / prev_price - 1) * 100 to 1 decimal, so it matches the RM figures.
    """
    cols = ["item_code", "pct", "n", "prev_price", "cur_price"]
    a = pm[pm["month"] == prev][["premise_code", "item_code", "price"]]
    b = pm[pm["month"] == cur][["premise_code", "item_code", "price"]]
    j = a.merge(b, on=["premise_code", "item_code"], suffixes=("_prev", "_cur"))
    if j.empty:
        return pd.DataFrame(columns=cols)
    g = j.groupby("item_code").agg(n=("price_cur", "size"), prev_price=("price_prev", "median"),
                                   cur_price=("price_cur", "median")).reset_index()
    g = g[g["n"] >= min_n]
    g = g.assign(prev_price=[round(float(v), 2) for v in g["prev_price"]],
                 cur_price=[round(float(v), 2) for v in g["cur_price"]])
    g = g[g["prev_price"] > 0]
    g = g.assign(pct=[round((c / p - 1) * 100, 1) for c, p in zip(g["cur_price"], g["prev_price"])])
    g = g.sort_values(["pct", "n", "item_code"], ascending=[False, False, True])
    return g[cols].reset_index(drop=True)


def slugify(state: str, district: str) -> str:
    def s(x: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")
    return f"{s(state)}--{s(district)}"


def history_table(pm: pd.DataFrame, months: list[str]) -> dict[int, list]:
    """item_code -> list of monthly median price (None when no data), aligned to months."""
    med = pm.groupby(["item_code", "month"])["price"].median().unstack("month")
    med = med.reindex(columns=months)
    out = {}
    for code, row in med.iterrows():
        out[int(code)] = [None if pd.isna(v) else round(float(v), 2) for v in row.tolist()]
    return out


def shop_months_table(sub: pd.DataFrame, pidx: dict, months: list[str]) -> dict[int, list]:
    """item_code -> [[premise_index, price month 1, month 2, ...]] for the given months.

    sub: month, premise_code, item_code, price for one district. Only shops in pidx
    (shops with a recent price) are kept. None marks a month with no price.
    """
    s = sub[sub["month"].isin(months) & sub["premise_code"].isin(pidx.keys())]
    out: dict[int, list] = {}
    if s.empty:
        return out
    wide = s.pivot_table(index=["item_code", "premise_code"], columns="month", values="price", aggfunc="median")
    wide = wide.reindex(columns=months)
    for (code, pc), row in wide.iterrows():
        vals = [None if pd.isna(v) else round(float(v), 2) for v in row.tolist()]
        out.setdefault(int(code), []).append([pidx[int(pc)], *vals])
    for rows in out.values():
        rows.sort(key=lambda r: r[0])
    return out


def feed_rows(ch: pd.DataFrame, top: int) -> dict[str, list]:
    def rows(df):
        return [[int(r.item_code), float(r.pct), int(r.n), round(float(r.prev_price), 2),
                 round(float(r.cur_price), 2)] for r in df.itertuples()]
    up = ch[ch["pct"] > 0].head(top)
    down = ch[ch["pct"] < 0].sort_values("pct", kind="stable").head(top)
    return {"cols": ["item_code", "pct", "n_shops", "prev_median", "cur_median"],
            "rises": rows(up), "falls": rows(down)}


# ---------- proof figure ----------

def _unit_key(unit) -> str:
    """'10 kg', '10kg' and '10 KG' all become '10kg'."""
    return re.sub(r"\s+", "", str(unit or "")).lower()


def proof_items(items: pd.DataFrame, shops: pd.Series) -> tuple[list[int], list[str]]:
    """For each PROOF_GROUPS entry, the matching item priced at the most shops (ties: lower code)."""
    codes, labels = [], []
    for label, pattern, unit in PROOF_GROUPS:
        rx = re.compile(pattern, re.IGNORECASE)
        best = None
        for r in items.itertuples():
            if not rx.search(r.item) or (unit and _unit_key(r.unit) != _unit_key(unit)):
                continue
            n = int(shops.get(r.item_code, 0))
            if n and (best is None or (n, -r.item_code) > (best[0], -best[1])):
                best = (n, int(r.item_code))
        if best:
            codes.append(best[1])
            labels.append(label)
    return codes, labels


def to_sen(price: float) -> int:
    """RM to whole sen, half up, as the app rounds (Math.round(price * 100))."""
    return math.floor(price * 100 + 0.5)


def median_sen(totals: list[int]) -> int:
    """Median of totals in whole sen, as the app gives it: with an even count, the mean of the two
    middle totals, rounded half up to the sen."""
    s = sorted(totals)
    h = len(s) // 2
    return s[h] if len(s) % 2 else (s[h - 1] + s[h] + 1) // 2


def proof_figure(latest_d: pd.DataFrame, items: pd.DataFrame, shops: pd.Series) -> dict | None:
    """What a typical weekly list costs per district: [cheapest, typical (median), shops].

    Only shops that price every item on the list count, and a district needs
    PROOF_MIN_SHOPS of them. None when fewer than PROOF_MIN_GROUPS items are found.
    Totals are in whole sen, each price to the sen, as the app adds up the same list, so the
    home screen shows the same figures as the app's results for that list.
    """
    codes, labels = proof_items(items, shops)
    if len(codes) < PROOF_MIN_GROUPS:
        return None
    sub = latest_d[latest_d["item_code"].isin(codes)]
    sub = sub.assign(sen=[to_sen(float(p)) for p in sub["price"]])
    by_district, saving = {}, {}
    for (st, dist), g in sub.groupby(["state", "district"]):
        per_shop = g.groupby("premise_code").agg(n=("item_code", "nunique"), total=("sen", "sum"))
        full = [int(t) for t in per_shop.loc[per_shop["n"] == len(codes), "total"]]
        if len(full) >= PROOF_MIN_SHOPS:
            slug, cheapest, typical = slugify(st, dist), min(full), median_sen(full)
            by_district[slug] = [cheapest / 100, typical / 100, len(full)]
            saving[slug] = typical - cheapest
    if not by_district:
        return None
    # Example district: the most shops with the whole list; ties: the larger saving (in whole sen).
    example = min(by_district, key=lambda s: (-by_district[s][2], -saving[s], s))
    return {"items": codes, "labels": labels, "by_district": dict(sorted(by_district.items())),
            "example": example}


# ---------- shop locations ----------

GEO_DEFAULT = HERE / "geo" / "premise_geo.csv"
DISTRICT_GEO_DEFAULT = HERE / "geo" / "district_geo.csv"
POSTCODES_DEFAULT = HERE / "geo" / "postcodes_my.csv"
# A location path the caller did not give: the etl/geo/ file, except for a synthetic sample,
# whose made-up shops share codes with real shops in premise_geo.csv.
DEFAULT = "default"
LOCATED = ("name", "address", "postcode", "town")   # a real map point: exact or approximate
PRECISIONS = LOCATED + ("district",)               # district: location unknown
SHARED_KM = 0.5   # district points this close in one state are a fallback guess, not a centre


def location_file(given, default: Path, synthetic: bool) -> Path | str | None:
    """The location file to read: `given` (a path, or None for none), or for DEFAULT the
    etl/geo/ file, which a synthetic sample does not use."""
    if given == DEFAULT:
        return None if synthetic else default
    return given


def _point(lat, lon) -> tuple[float, float] | None:
    """(lat, lon) from two CSV cells, or None when blank or not a real coordinate."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def _postcode(s) -> str:
    """Five-digit postcode as text, or ''. '1000' (a spreadsheet dropped the 0) becomes '01000'."""
    s = str(s or "").strip()
    if s.isdigit() and len(s) == 4:
        s = s.zfill(5)
    return s if re.fullmatch(r"\d{5}", s) else ""


def _rows(path: Path | None):
    if not path or not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_geo(path: Path | None) -> dict[int, tuple[float, float, str, str]]:
    """premise_code -> (lat, lon, precision, postcode) from premise_geo.csv. Empty when there is no file.

    Rows without a usable point are skipped: those shops count as location unknown.
    A precision other than name, address, postcode or town is read as 'district'.
    Extra columns (tries, last_tried, ...) are ignored.
    """
    out = {}
    for r in _rows(path):
        try:
            code = int(float(r.get("premise_code") or ""))
        except (ValueError, OverflowError):
            continue
        pt = _point(r.get("lat"), r.get("lon"))
        if pt is None:
            continue
        prec = (r.get("precision") or "").strip().lower()
        out[code] = (round(pt[0], 5), round(pt[1], 5), prec if prec in LOCATED else "district",
                     _postcode(r.get("postcode")))
    return out


def district_key(state: str, district: str) -> str:
    return f"{state.strip().lower()}|{district.strip().lower()}"


def load_district_points(path: Path | None) -> dict[str, tuple[float, float]]:
    """district_key -> (lat, lon) from district_geo.csv (key 'State|District', lat, lon, shared).

    A point shared with another district of the same state is left out: the geocoder falls
    back to the state centre when it cannot find a district. Shared means marked in the
    'shared' column (any value but blank or 0, as the geocoder reads it; the mark stays on a
    district left alone on an old shared point) or within 0.5 km of another district's point
    in the same state (the only test for older files without the column; the geocoder marks
    these too). Extra columns (tries, ...) are ignored.
    """
    pts, marked = {}, set()
    for r in _rows(path):
        key, pt = r.get("key") or "", _point(r.get("lat"), r.get("lon"))
        if "|" in key and pt:
            k = district_key(*key.split("|", 1))
            pts[k] = pt
            if str(r.get("shared") or "").strip() not in ("", "0"):
                marked.add(k)
    out = {}
    for k, p in pts.items():
        st = k.split("|", 1)[0]
        if k not in marked and not any(o != k and o.split("|", 1)[0] == st and km(*p, *q) <= SHARED_KM
                                       for o, q in pts.items()):
            out[k] = p
    return out


def load_postcode_rows(path: Path | None) -> dict[str, list[tuple[str, str, tuple[float, float]]]]:
    """Postcode -> its rows in postcodes_my.csv (GeoNames) that have a usable point, as
    (place key, place name, (lat, lon)), sorted by postcode. The place key is 'state|place' in lower
    case ('' when the row has no place name); the place name reads 'Place (State)'. No file: {}."""
    out: dict[str, list] = {}
    for r in _rows(path):
        pc, pt = _postcode(r.get("postcode")), _point(r.get("lat"), r.get("lon"))
        if not (pc and pt):
            continue
        place, state = (" ".join(str(r.get(c) or "").split()) for c in ("place", "state"))
        name = f"{place} ({state})" if place and state else place or state
        out.setdefault(pc, []).append((f"{state}|{place}".lower() if place else "", name, pt))
    return dict(sorted(out.items()))


def load_postcodes(path: Path | None) -> dict[str, list[float]]:
    """Postcode -> [lat, lon] from postcodes_my.csv (GeoNames): the average of that postcode's rows.
    These are the GeoNames points before check_postcodes."""
    return {pc: [round(p[0], 4), round(p[1], 4)]
            for pc, p in ((pc, _mean_point([r[2] for r in v])) for pc, v in load_postcode_rows(path).items())}


def km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def district_centre(located: list, fallback: tuple | None = None) -> tuple | None:
    """(lat, lon) for a district, or None.

    located: points of shops with a real location (precision name, address, postcode or town);
    fallback: the district's district_geo.csv point when it is not shared (load_district_points).
    The median of 2+ located shops, else the fallback, else the one located shop, else None.
    Points of shops at district precision are never used: they are the geocoder's own fallback,
    often a state centre shared by several districts.
    """
    if len(located) >= 2 or (located and not fallback):
        return statistics.median(p[0] for p in located), statistics.median(p[1] for p in located)
    return fallback or None


def district_radius(centre, located: list) -> float:
    """km from the centre to the furthest located shop, plus 0.5 (at least 1). 3 when none is located."""
    if not located:
        return 3.0
    return max(1.0, round(max(km(*centre, *p) for p in located) + 0.5, 1))


def postcode_table(geo: dict) -> dict[str, list[float]]:
    """Postcode -> median location of shops found by name or street address with that postcode.

    Only used when there is no postcodes_my.csv.
    """
    acc: dict[str, list] = {}
    for lat, lon, prec, pc in geo.values():
        if pc and prec in ("name", "address"):
            acc.setdefault(pc, []).append((lat, lon))
    return {pc: [round(statistics.median(p[0] for p in v), 4), round(statistics.median(p[1] for p in v), 4)]
            for pc, v in sorted(acc.items())}


# ---------- the shoppers' postcode table ----------

ANCHOR_KM = 10.0   # shops that agree lie this close to the median of them all (as in the geocoder)
MOVED_KM = 2.0     # a postcode placed at its shops this far from its GeoNames point counts as moved
BACKED_KM = 5.0    # only 2 shops agree, more than ANCHOR_KM apart: one this close to the GeoNames point backs it
PLACE_KM = 30.0    # a GeoNames point further than this from the other postcodes of its place is set aside
PLACE_MIN = 3      # ... judged only when the place has at least this many other postcodes
PLACE_POINTS = 2   # ... on at least this many different points (postcodes on one shared point are one guess)
COMPACT_MIN = 10       # a place with at least this many other postcodes ...
COMPACT_SHARE = 0.9    # ... this share of them within COMPACT_KM / 2 of their median is compact:
COMPACT_KM = 15.0      # ... there a point further than this from that median is set aside too
POSTCODE_SOURCE = "GeoNames (CC BY 4.0), checked against shops found by OpenStreetMap"


def _mean_point(points) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _median_point(points) -> tuple[float, float]:
    return statistics.median(p[0] for p in points), statistics.median(p[1] for p in points)


def _agreeing(points) -> list | None:
    """The points within ANCHOR_KM of the median of them all, when at least 2 and more than half of
    them are that close (they agree); else None."""
    if len(points) < 2:
        return None
    mid = _median_point(points)
    close = [p for p in points if km(*p, *mid) <= ANCHOR_KM]
    return close if len(close) >= 2 and 2 * len(close) > len(points) else None


def agreed_point(points) -> tuple[float, float] | None:
    """The median of the points that agree (_agreeing), else None. The rule the shop geocoder uses
    for its own postcode anchors (_agreed in etl/geocode_premises.py); the build keeps its own copy
    so that the two scripts stay separate."""
    close = _agreeing(points)
    return _median_point(close) if close else None


def _compact_core(points, mid) -> float | None:
    """The distance from mid within which COMPACT_SHARE of the points lie, when the place is compact:
    at least COMPACT_MIN points, and that distance at most COMPACT_KM / 2. Else None."""
    if len(points) < COMPACT_MIN:
        return None
    ds = sorted(km(*p, *mid) for p in points)
    core = ds[math.ceil(round(COMPACT_SHARE * len(ds), 6)) - 1]
    return core if core <= COMPACT_KM / 2 else None


def check_postcodes(rows: dict, geo: dict) -> dict:
    """The postcode table the app places a shopper at: GeoNames points checked against the shops
    OpenStreetMap found.

    rows: load_postcode_rows(); geo: load_geo(), every row of the shop location cache, not only the
    shops in this build.
      1. From shops: a postcode with 2 or more shops found by name or address (the cache's postcode
         column) that agree (agreed_point) is placed at the median of those that agree.
         Except when only 2 shops agree and they lie more than ANCHOR_KM apart: their middle can be
         in neither town. If one of the 2 lies within BACKED_KM of the GeoNames point, that shop
         backs the point, and the postcode keeps it and is never set aside ("kept").
      2. Set aside: any other postcode is left out when its point (the average of its rows) is more
         than PLACE_KM from the median of the other postcodes of its place (same state and place
         name; at least PLACE_MIN others, on at least PLACE_POINTS different points at 4 decimals,
         each at its point after step 1). Others that all share one point are one guess, not a
         place to judge by. In a compact place (at least COMPACT_MIN others, COMPACT_SHARE of them
         within COMPACT_KM / 2 of their median) a point more than COMPACT_KM from that median is
         left out too. The app then places a postcode left out from the postcodes that share its
         first 4, 3 or 2 digits.
      3. Every other postcode keeps the average of its rows, also when the rows lie far apart.
    No postcode is added: a shop's postcode that GeoNames does not have is not used.
    In the 2026-11-16 GeoNames table, 66 of the 84 postcodes placed from shops lay more than 2 km
    from their shops (Muar 84000 by 29 km, Bintulu 97000 by 120 km), and 79570 Nusajaya (Johor)
    lay in Kedah. Kept: 30200 Ipoh, whose 2 shops (Ipoh, Batu Gajah) would have put it about 10 km
    from Ipoh, and 98107 Lutong. Not judged: 98050 Baram, whose 3 other postcodes share one point
    445 km from Marudi, where its shops are. Compact: 88000 Kota Kinabalu, the city centre, lay
    18.4 km from the median of the city's 205 other postcodes, 90% of which lie within 4.5 km of
    it, and the app showed shoppers there the shops of Putatan.
    Returns {"postcodes": {postcode: [lat, lon]} (4 decimals, sorted), "checks": {"from_shops",
    "moved" (more than MOVED_KM), "set_aside"}, "moves": [(km, postcode, place)] largest first,
    "set_aside": [(postcode, place, reason)], "kept": [(postcode, place, reason)]}.
    """
    found: dict[str, list] = {}
    for lat, lon, prec, pc in geo.values():
        if prec in ("name", "address") and pc in rows:
            found.setdefault(pc, []).append((lat, lon))
    average = {pc: _mean_point([r[2] for r in v]) for pc, v in rows.items()}
    shops, backed = {}, {}
    for pc, v in found.items():
        close = _agreeing(v)
        if not close:
            continue
        apart = km(*close[0], *close[1]) if len(close) == 2 else 0.0
        near = min(km(*p, *average[pc]) for p in close)
        if apart > ANCHOR_KM and near <= BACKED_KM:
            backed[pc] = (f"its 2 shops lie {apart:.1f} km apart and one of them is {near:.1f} km "
                          f"from that point")
        else:
            shops[pc] = _median_point(close)
    point = {pc: shops.get(pc, average[pc]) for pc in rows}
    places: dict[str, set] = {}
    for pc, v in rows.items():
        for key, _, _ in v:
            if key:
                places.setdefault(key, set()).add(pc)
    out, moves, aside, kept = {}, [], [], []
    for pc, v in sorted(rows.items()):
        name = " / ".join(sorted({r[1] for r in v if r[1]}))
        if pc in shops:
            d = km(*average[pc], *shops[pc])
            if d > MOVED_KM:
                moves.append((d, pc, name))
        elif pc in backed:
            kept.append((pc, name, backed[pc]))
        else:
            others = set().union(*(places[r[0]] for r in v if r[0])) - {pc}
            pts = [point[o] for o in sorted(others)]
            # Different points are counted at the table's 4 decimals: the average of rows on one point
            # can differ from it in the last bit (3 x 112.3837 / 3 = 112.38370000000002).
            if len(others) >= PLACE_MIN and len({(round(p[0], 4), round(p[1], 4)) for p in pts}) >= PLACE_POINTS:
                mid = _median_point(pts)
                d = km(*average[pc], *mid)
                why = f"{d:.1f} km from the median of the {len(others)} other postcodes of its place"
                core = _compact_core(pts, mid)
                if d > PLACE_KM:
                    aside.append((pc, name, why))
                    continue
                if core is not None and d > COMPACT_KM:
                    aside.append((pc, name, f"{why}, {COMPACT_SHARE:.0%} of which lie within {core:.1f} km of it"))
                    continue
        out[pc] = [round(point[pc][0], 4), round(point[pc][1], 4)]
    moves.sort(key=lambda m: (-m[0], m[1]))
    return {"postcodes": dict(sorted(out.items())),
            "checks": {"from_shops": len(shops), "moved": len(moves), "set_aside": len(aside)},
            "moves": moves, "set_aside": aside, "kept": kept}


def geo_summary(geo: dict, listed: set, precision: dict, postcodes: dict | None) -> dict | None:
    """meta.geo: located shop counts, the postcode table and credits. None without shop locations.

    postcodes: check_postcodes() of the GeoNames table. When it holds no postcode (no table), the
    table is made from the shops of this build found by name or address (postcode_table).
    """
    if not geo:
        return None
    if postcodes and postcodes["postcodes"]:
        pcs = {"postcodes": postcodes["postcodes"], "postcode_source": POSTCODE_SOURCE,
               "postcode_source_url": "https://www.geonames.org/", "postcode_checks": postcodes["checks"]}
    else:
        pcs = {"postcodes": postcode_table({c: g for c, g in geo.items() if c in listed}),
               "postcode_source": "shop addresses"}
    return {"shops_located": sum(precision[p] for p in LOCATED), "precision": precision, **pcs,
            "source": "© OpenStreetMap contributors (ODbL), via Nominatim",
            "source_url": "https://www.openstreetmap.org/copyright"}


# ---------- writing ----------

def write_json(path: Path, key: str, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: a NaN would make the file unreadable in the browser, so fail the build instead.
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path.write_text(text, encoding="utf-8")
    js = f'(window.PCDATA=window.PCDATA||{{}})[{json.dumps(key)}]={text};\n'
    path.with_suffix(".js").write_text(js, encoding="utf-8")
    # Verify the write: re-read both files and parse the JSON.
    back = path.read_text(encoding="utf-8")
    if back != text or path.with_suffix(".js").read_text(encoding="utf-8") != js:
        raise SystemExit(f"write check failed: {path}")
    json.loads(back)
    return len(text.encode("utf-8"))


# ---------- main build ----------

def build(raw: Path, out: Path, months_keep: int = 12, state: str | None = None,
          district: str | None = None, top_items: int | None = None,
          feed_top: int = 25, min_n_national: int = 3, min_n_district: int = 2,
          geo_path: Path | str | None = DEFAULT, postcodes_path: Path | str | None = DEFAULT,
          district_geo_path: Path | str | None = DEFAULT) -> dict:
    """Build the web data from the raw files. Returns sizes, stats and a short summary.

    months_keep: months of history, at least MIN_MONTHS. geo_path, postcodes_path and
    district_geo_path: a location file, None for none, or DEFAULT for the etl/geo/ file.
    A synthetic sample (raw/SYNTHETIC.txt) does not read the DEFAULT files.
    """
    # Fresh prices are read from the 3 newest month files, so fewer months would miss some.
    if months_keep < MIN_MONTHS:
        raise SystemExit(f"--months must be at least {MIN_MONTHS} (got {months_keep}): a price up to "
                         f"{MAX_PRICE_AGE_DAYS} days old can be in any of the {MIN_MONTHS} newest month files.")
    synthetic = (raw / "SYNTHETIC.txt").exists()
    if synthetic and DEFAULT in (geo_path, postcodes_path, district_geo_path):
        print("Note: synthetic sample, so the shop location files in etl/geo/ are not used (their real "
              "shops share codes with the made-up ones). Name them with --geo, --postcodes or "
              "--district-geo to use them.")
    geo = load_geo(location_file(geo_path, GEO_DEFAULT, synthetic))
    dpoints = load_district_points(location_file(district_geo_path, DISTRICT_GEO_DEFAULT, synthetic))
    postcodes_path = location_file(postcodes_path, POSTCODES_DEFAULT, synthetic)
    items = clean_items(load_table(raw, "lookup_item"))
    prem = clean_premises(load_table(raw, "lookup_premise"))
    if state:
        prem = prem[prem["state"].str.lower() == state.lower()]
    if district:
        prem = prem[prem["district"].str.lower() == district.lower()]
    if prem.empty:
        raise SystemExit("No premises left after the state/district filter.")

    months = find_months(raw)[-months_keep:]
    if not months:
        raise SystemExit(f"No pricecatcher_YYYY-MM files in {raw}")

    # One month at a time to keep memory low: monthly medians for history, cleaned rows
    # per month, and for the last 3 months each shop's newest price per item.
    pm_parts, recent = [], []
    month_rows = dict.fromkeys(months, 0)
    rows_in = rows_kept = 0
    for i, ym in enumerate(months):
        df = load_table(raw, f"pricecatcher_{ym}")
        rows_in += len(df)
        c = clean_prices(df, items["item_code"], prem["premise_code"])
        del df
        rows_kept += len(c)
        if c.empty:
            continue
        c = c.assign(month=c["date"].dt.strftime("%Y-%m"))
        c = c[c["month"].isin(months)]  # guard against stray dates in a file
        if c.empty:
            continue
        for m, n in c["month"].value_counts().items():
            month_rows[m] += int(n)
        pm_parts.append(monthly_premise_median(c))
        if i >= len(months) - 3:  # enough to cover MAX_PRICE_AGE_DAYS before the data date
            recent.append(latest_prices(c))
    if not recent:
        raise SystemExit("No prices in the newest months after cleaning.")
    pm = pd.concat(pm_parts, ignore_index=True)
    rec = pd.concat(recent, ignore_index=True)
    data_date = rec["date"].max()
    latest, aside = set_aside_pack_errors(fresh_only(latest_prices(rec), data_date))
    # To the sen, as shown, so the proof totals add up the same prices the app does.
    latest = latest.assign(price=[round(float(p), 2) for p in latest["price"]])

    if top_items:
        cover = latest.groupby("item_code")["premise_code"].nunique().sort_values(ascending=False, kind="stable")
        keep = set(cover.head(top_items).index)
        pm = pm[pm["item_code"].isin(keep)]
        latest = latest[latest["item_code"].isin(keep)]
        aside = aside[aside["item_code"].isin(keep)]

    present = set(pm["item_code"].unique())
    items = items[items["item_code"].isin(present)].sort_values("item", kind="stable").reset_index(drop=True)
    shops = latest.groupby("item_code")["premise_code"].nunique()

    with_data = months_with_data(month_rows, months)
    cur = with_data[-1]
    prev = with_data[-2] if len(with_data) > 1 else None
    avg_months = with_data[-3:]

    # National (or filtered area) history and feed.
    nat_hist = history_table(pm, months)
    nat_feed = feed_rows(mom_changes(pm, cur, prev, min_n_national), feed_top) if prev else None

    pm_d = pm.merge(prem[["premise_code", "state", "district"]], on="premise_code")
    # "Cheapest on average" must not compare another pack size either; history and feed use every price.
    pm_avg = same_pack_months(pm, aside, avg_months).merge(prem[["premise_code", "state", "district"]],
                                                           on="premise_code")
    latest_d = latest.merge(prem, on="premise_code")
    pinfo = prem.set_index("premise_code")
    precision = dict.fromkeys(PRECISIONS, 0)
    areas, sizes = {}, {}
    for (st, dist), lp in latest_d.groupby(["state", "district"]):
        slug = slugify(st, dist)
        pcodes = sorted(int(c) for c in lp["premise_code"].unique())
        pidx = {c: i for i, c in enumerate(pcodes)}
        located = {c: geo[c] for c in pcodes if c in geo and geo[c][2] in LOCATED}
        # dpoints has no shared points, so a shared point never becomes a centre.
        centre = district_centre([g[:2] for g in located.values()], dpoints.get(district_key(st, dist)))
        centre = [round(centre[0], 5), round(centre[1], 5)] if centre else None
        premises = []
        for c in pcodes:
            r = pinfo.loc[c]
            # No real location (precision "district", or no usable row in premise_geo.csv): as the data
            # contract says, lat/lon is the district centre, and the app says "location unknown".
            # Without a centre the point stays empty, never the shop's own district-level guess.
            lat, lon, prec = located[c][:3] if c in located else (*(centre or (None, None)), "district")
            precision[prec] += 1
            premises.append([c, r["premise"], r["premise_type"], r["address"], lat, lon, prec])
        prices = {}
        for code, g in lp.groupby("item_code"):
            prices[int(code)] = sorted([pidx[int(r.premise_code)], round(float(r.price), 2), int(r.days_old)]
                                       for r in g.itertuples())
        sub = pm_d[(pm_d["state"] == st) & (pm_d["district"] == dist)]
        sub_avg = pm_avg[(pm_avg["state"] == st) & (pm_avg["district"] == dist)]
        dfile = {
            "state": st, "district": dist, "months": months,
            "data_date": data_date.strftime("%Y-%m-%d"), "synthetic": synthetic,
            "premise_cols": ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision"],
            "premises": premises,
            "centre": centre,
            "price_cols": ["premise_index", "price", "days_old"],
            "prices": prices,
            "history": history_table(sub, months),
            "avg_months": avg_months,
            "shop_months": shop_months_table(sub_avg, pidx, avg_months),
            "feed": feed_rows(mom_changes(sub, cur, prev, min_n_district), feed_top) if prev else None,
        }
        sizes[slug] = write_json(out / "d" / f"{slug}.json", f"d/{slug}", dfile)
        area = {"district": dist, "slug": slug, "premises": len(pcodes), "items": len(prices)}
        if centre:
            area["geo"] = [*centre, district_radius(centre, [g[:2] for g in located.values()])]
        areas.setdefault(st, []).append(area)

    # A full build removes district files left over from an earlier build (e.g. the synthetic
    # sample) so the app never shows a district that is not in this build. A --state or
    # --district build only rewrites its own districts and keeps the other files.
    if not state and not district:
        for old in (out / "d").glob("*.js*"):
            if old.name.split(".")[0] not in sizes:
                old.unlink()

    listed = {int(c) for c in latest_d["premise_code"].unique()}
    # Without shop locations there is no meta.geo, so no postcode table to check.
    checked = check_postcodes(load_postcode_rows(postcodes_path), geo) if geo else None
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_date": data_date.strftime("%Y-%m-%d"),
        "months": months,
        "months_with_data": with_data,
        "current_month_partial": True,
        "synthetic": synthetic,
        "source": SOURCE, "source_url": SOURCE_URL,
        "licence": LICENCE, "licence_url": LICENCE_URL,
        "item_cols": ["item_code", "item", "unit", "item_group", "item_category", "shops"],
        "items": [[int(r.item_code), r.item, r.unit, r.item_group, r.item_category, int(shops.get(r.item_code, 0))]
                  for r in items.itertuples()],
        "areas": {st: sorted(v, key=lambda d: d["district"]) for st, v in sorted(areas.items())},
        "geo": geo_summary(geo, listed, precision, checked),
        "proof": proof_figure(latest_d, items, shops),
        "national_history": nat_hist,
        "national_feed": nat_feed,
        "stats": {"rows_in": rows_in, "rows_kept": rows_kept, "premises": len(listed),
                  "items": len(items), "districts": len(sizes), "prices_set_aside": int(len(aside))},
    }
    sizes["meta"] = write_json(out / "meta.json", "meta", meta)
    return {"sizes": sizes, "stats": meta["stats"], "data_date": meta["data_date"],
            "months": months, "months_with_data": with_data, "proof": meta["proof"],
            "synthetic": synthetic, "postcode_check": checked if checked and checked["postcodes"] else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "web" / "data"))
    ap.add_argument("--months", type=int, default=12, help=f"months of history to keep (at least {MIN_MONTHS})")
    ap.add_argument("--state", help="only this state, e.g. Selangor")
    ap.add_argument("--district", help="only this district, e.g. Petaling")
    ap.add_argument("--top-items", type=int, help="keep only the N most widely priced items")
    ap.add_argument("--feed-top", type=int, default=25)
    ap.add_argument("--max-kb", type=int, default=1500, help="warn if a district file is bigger")
    # Location files: not given means the etl/geo/ file (not used for a synthetic sample); '' means none.
    ap.add_argument("--geo", help="shop locations from geocode_premises.py (default etl/geo/premise_geo.csv; "
                                  "'' for none)")
    ap.add_argument("--postcodes", help="postcode places from GeoNames (default etl/geo/postcodes_my.csv; "
                                        "'' for none: use shop addresses)")
    ap.add_argument("--district-geo", help="district centres (default etl/geo/district_geo.csv; '' for none)")
    args = ap.parse_args(argv)

    def location_arg(p: str | None) -> Path | str | None:
        return DEFAULT if p is None else Path(p) if p else None

    res = build(Path(args.raw), Path(args.out), months_keep=args.months, state=args.state,
                district=args.district, top_items=args.top_items, feed_top=args.feed_top,
                geo_path=location_arg(args.geo), postcodes_path=location_arg(args.postcodes),
                district_geo_path=location_arg(args.district_geo))
    s = res["stats"]
    print(f"{'SYNTHETIC' if res['synthetic'] else 'REAL'} data, months {res['months'][0]}..{res['months'][-1]} "
          f"({len(res['months_with_data'])} with data), data date {res['data_date']}")
    print(f"rows in {s['rows_in']:,}, kept {s['rows_kept']:,}; {s['premises']} premises, "
          f"{s['items']} items, {s['districts']} districts; {s['prices_set_aside']} prices set aside")
    p = res["proof"]
    print(f"proof figure: {len(p['items'])} items, example {p['example']}" if p else "proof figure: none")
    pc = res["postcode_check"]
    if pc:
        c = pc["checks"]
        print(f"postcode table: {len(pc['postcodes']):,} postcodes from GeoNames, checked against shops found by "
              f"OpenStreetMap: {c['from_shops']} placed at those shops ({c['moved']} moved more than "
              f"{MOVED_KM:g} km), {c['set_aside']} set aside")
        for d, code, name in pc["moves"][:10]:
            print(f"  moved {code} {name}: {d:.1f} km")
        for code, name, why in pc["set_aside"]:
            print(f"  set aside {code} {name}: {why}")
        for code, name, why in pc["kept"]:
            print(f"  kept {code} {name} at its GeoNames point: {why}")
    big = {k: v for k, v in res["sizes"].items() if v > args.max_kb * 1024}
    total = sum(res["sizes"].values())
    print(f"wrote {len(res['sizes'])} JSON files, {total/1024:.0f} KB total, "
          f"largest {max(res['sizes'].values())/1024:.0f} KB")
    if big:
        print(f"WARNING: {len(big)} file(s) over {args.max_kb} KB. Try --top-items 300.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
