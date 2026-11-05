"""Turn raw PriceCatcher files into small JSON files for the web app.

Input folder (default data/raw) holds files named as on data.gov.my:
  lookup_item.(parquet|csv), lookup_premise.(parquet|csv), pricecatcher_YYYY-MM.(parquet|csv)

Steps:
  1. Clean prices: fix types, drop zero/blank/unknown codes, merge same-day
     duplicates (median), drop outliers (more than 4x away from that item's
     monthly median).
  2. Join item and premise lookups.
  3. Per premise and item: latest price (newest date in the last 2 months).
  4. Per premise, item and month: median price.
  5. Monthly medians per item for each district and for Malaysia.
  5b. Per shop, the monthly median for the last 3 months (for "cheapest on average").
  6. Month-on-month change, like-for-like: only shops priced in both months
     count, and the item's change is the median of those shops' changes.
     The newest month is usually partial (month to date).
  6b. If etl/geo/premise_geo.csv exists (made by geocode_premises.py), add each shop's map
      point, a centre and radius per district, and a postcode -> location table for "near me".
  7. Write web/data/meta.json plus one web/data/d/<state>--<district>.json per
     district. Each JSON also gets a .js twin so the app works from file://.

Run:
  python etl/build_dataset.py                        # real data in data/raw
  python etl/build_dataset.py --raw data/sample      # synthetic sample
  python etl/build_dataset.py --state Selangor       # one state only
  python etl/build_dataset.py --top-items 300        # keep most-covered items
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
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
MONTH_RE = re.compile(r"^pricecatcher_(\d{4}-\d{2})$")


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
    p = prices.assign(month=prices["date"].dt.strftime("%Y-%m"))
    return p.groupby(["month", "premise_code", "item_code"], as_index=False)["price"].median()


def latest_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Newest observation per premise and item: premise_code, item_code, price, date."""
    if prices.empty:
        return pd.DataFrame(columns=["premise_code", "item_code", "price", "date"])
    last = prices.groupby(["premise_code", "item_code"])["date"].transform("max")
    p = prices[prices["date"] == last]
    return p.groupby(["premise_code", "item_code"], as_index=False).agg(price=("price", "median"),
                                                                       date=("date", "max"))


def mom_changes(pm: pd.DataFrame, cur: str, prev: str, min_n: int = 3) -> pd.DataFrame:
    """Like-for-like month-on-month change per item.

    pm: month, premise_code, item_code, price (plus optional group columns).
    Returns item_code, pct (median of per-shop % change), n (shops),
    prev_price, cur_price (medians over the matched shops).
    """
    a = pm[pm["month"] == prev][["premise_code", "item_code", "price"]]
    b = pm[pm["month"] == cur][["premise_code", "item_code", "price"]]
    j = a.merge(b, on=["premise_code", "item_code"], suffixes=("_prev", "_cur"))
    if j.empty:
        return pd.DataFrame(columns=["item_code", "pct", "n", "prev_price", "cur_price"])
    j["chg"] = j["price_cur"] / j["price_prev"] - 1
    g = j.groupby("item_code").agg(pct=("chg", "median"), n=("chg", "size"),
                                   prev_price=("price_prev", "median"),
                                   cur_price=("price_cur", "median")).reset_index()
    g = g[g["n"] >= min_n]
    g["pct"] = (g["pct"] * 100).round(1)
    return g.sort_values("pct", ascending=False).reset_index(drop=True)


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
    down = ch[ch["pct"] < 0].sort_values("pct").head(top)
    return {"cols": ["item_code", "pct", "n_shops", "prev_median", "cur_median"],
            "rises": rows(up), "falls": rows(down)}


# ---------- shop locations ----------

GEO_DEFAULT = HERE / "geo" / "premise_geo.csv"


def load_geo(path: Path | None) -> dict[int, tuple[float, float, str, str]]:
    """premise_code -> (lat, lon, precision, postcode). Empty when there is no cache yet."""
    if not path or not Path(path).exists():
        return {}
    out = {}
    with Path(path).open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out[int(r["premise_code"])] = (round(float(r["lat"]), 5), round(float(r["lon"]), 5),
                                               r.get("precision", ""), r.get("postcode", ""))
            except (ValueError, KeyError):
                continue
    return out


def km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def district_geo(points: list[tuple[float, float]]) -> list[float] | None:
    """[lat, lon, radius_km]: centre of the shops and the distance to the furthest one."""
    if not points:
        return None
    la = sum(p[0] for p in points) / len(points)
    lo = sum(p[1] for p in points) / len(points)
    r = max(km(la, lo, p[0], p[1]) for p in points)
    return [round(la, 5), round(lo, 5), round(r + 0.5, 1)]


def postcode_table(geo: dict) -> dict[str, list[float]]:
    """Postcode -> average location of shops found at street or name level with that postcode."""
    acc: dict[str, list] = {}
    for lat, lon, prec, pc in geo.values():
        if pc and prec in ("name", "address", "postcode"):
            acc.setdefault(pc, []).append((lat, lon))
    return {pc: [round(sum(p[0] for p in v) / len(v), 4), round(sum(p[1] for p in v) / len(v), 4)]
            for pc, v in sorted(acc.items())}


# ---------- writing ----------

def write_json(path: Path, key: str, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    js = f'(window.PCDATA=window.PCDATA||{{}})[{json.dumps(key)}]={text};\n'
    path.with_suffix(".js").write_text(js, encoding="utf-8")
    # Verify the write: re-read and parse.
    json.loads(path.read_text(encoding="utf-8"))
    return len(text.encode("utf-8"))


# ---------- main build ----------

def build(raw: Path, out: Path, months_keep: int = 12, state: str | None = None,
          district: str | None = None, top_items: int | None = None,
          feed_top: int = 25, min_n_national: int = 3, min_n_district: int = 2,
          geo_path: Path | None = GEO_DEFAULT) -> dict:
    geo = load_geo(geo_path)
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

    pm_parts, recent = [], []
    rows_in = rows_kept = 0
    for i, ym in enumerate(months):
        df = load_table(raw, f"pricecatcher_{ym}")
        rows_in += len(df)
        c = clean_prices(df, items["item_code"], prem["premise_code"])
        rows_kept += len(c)
        pm_parts.append(monthly_premise_median(c))
        if i >= len(months) - 2:
            recent.append(c)
        del df
    pm = pd.concat(pm_parts, ignore_index=True)
    pm = pm[pm["month"].isin(months)]  # guard against stray dates in a file
    rec = pd.concat(recent, ignore_index=True)
    data_date = rec["date"].max()
    latest = latest_prices(rec)

    if top_items:
        cover = latest.groupby("item_code")["premise_code"].nunique().sort_values(ascending=False)
        keep = set(cover.head(top_items).index)
        pm = pm[pm["item_code"].isin(keep)]
        latest = latest[latest["item_code"].isin(keep)]

    present = set(pm["item_code"].unique())
    items = items[items["item_code"].isin(present)].sort_values("item").reset_index(drop=True)

    cur, prev = months[-1], (months[-2] if len(months) > 1 else None)
    synthetic = (raw / "SYNTHETIC.txt").exists()

    # National (or filtered area) history and feed.
    nat_hist = history_table(pm, months)
    nat_feed = feed_rows(mom_changes(pm, cur, prev, min_n_national), feed_top) if prev else None

    pm_d = pm.merge(prem[["premise_code", "state", "district"]], on="premise_code")
    latest_d = latest.merge(prem, on="premise_code")
    areas, sizes = {}, {}
    for (st, dist), lp in latest_d.groupby(["state", "district"]):
        slug = slugify(st, dist)
        pcodes = sorted(lp["premise_code"].unique())
        pidx = {c: i for i, c in enumerate(pcodes)}
        pinfo = prem.set_index("premise_code").loc[pcodes]
        premises = []
        for c, r in pinfo.iterrows():
            g = geo.get(int(c))
            premises.append([int(c), r["premise"], r["premise_type"], r["address"],
                             g[0] if g else None, g[1] if g else None, g[2] if g else None])
        prices = {}
        for code, g in lp.groupby("item_code"):
            prices[int(code)] = [[pidx[int(r.premise_code)], round(float(r.price), 2),
                                  int((data_date - r.date).days)] for r in g.itertuples()]
        sub = pm_d[(pm_d["state"] == st) & (pm_d["district"] == dist)]
        avg_months = months[-3:]
        dfile = {
            "state": st, "district": dist, "months": months,
            "data_date": data_date.strftime("%Y-%m-%d"), "synthetic": synthetic,
            "premise_cols": ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision"],
            "premises": premises,
            "price_cols": ["premise_index", "price", "days_old"],
            "prices": prices,
            "history": history_table(sub, months),
            "avg_months": avg_months,
            "shop_months": shop_months_table(sub, pidx, avg_months),
            "feed": feed_rows(mom_changes(sub, cur, prev, min_n_district), feed_top) if prev else None,
        }
        sizes[slug] = write_json(out / "d" / f"{slug}.json", f"d/{slug}", dfile)
        area = {"district": dist, "slug": slug, "premises": len(pcodes), "items": len(prices)}
        dg = district_geo([(p[4], p[5]) for p in premises if p[4] is not None])
        if dg:
            area["geo"] = dg
        areas.setdefault(st, []).append(area)

    # Remove district files left over from an earlier build (e.g. the synthetic
    # sample) so the app never shows a district that is not in this build.
    for old in (out / "d").glob("*.js*"):
        if old.name.split(".")[0] not in sizes:
            old.unlink()

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_date": data_date.strftime("%Y-%m-%d"),
        "months": months,
        "current_month_partial": True,
        "synthetic": synthetic,
        "source": SOURCE, "source_url": SOURCE_URL,
        "licence": LICENCE, "licence_url": LICENCE_URL,
        "item_cols": ["item_code", "item", "unit", "item_group", "item_category"],
        "items": items[["item_code", "item", "unit", "item_group", "item_category"]].values.tolist(),
        "areas": {st: sorted(v, key=lambda d: d["district"]) for st, v in sorted(areas.items())},
        "geo": {"shops_located": sum(1 for c in latest_d["premise_code"].unique() if int(c) in geo),
                "source": "© OpenStreetMap contributors (ODbL), via Nominatim",
                "source_url": "https://www.openstreetmap.org/copyright",
                "postcodes": postcode_table({k: v for k, v in geo.items()
                                             if k in set(int(x) for x in latest_d["premise_code"].unique())})} if geo else None,
        "national_history": nat_hist,
        "national_feed": nat_feed,
        "stats": {"rows_in": rows_in, "rows_kept": rows_kept, "premises": int(latest_d["premise_code"].nunique()),
                  "items": len(items), "districts": len(sizes)},
    }
    meta["items"] = [[int(r[0]), *r[1:]] for r in meta["items"]]
    sizes["meta"] = write_json(out / "meta.json", "meta", meta)
    return {"sizes": sizes, "stats": meta["stats"], "data_date": meta["data_date"],
            "months": months, "synthetic": synthetic}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "web" / "data"))
    ap.add_argument("--months", type=int, default=12, help="months of history to keep")
    ap.add_argument("--state", help="only this state, e.g. Selangor")
    ap.add_argument("--district", help="only this district, e.g. Petaling")
    ap.add_argument("--top-items", type=int, help="keep only the N most widely priced items")
    ap.add_argument("--feed-top", type=int, default=25)
    ap.add_argument("--max-kb", type=int, default=1500, help="warn if a district file is bigger")
    ap.add_argument("--geo", default=str(GEO_DEFAULT), help="shop locations from geocode_premises.py ('' for none)")
    args = ap.parse_args(argv)

    res = build(Path(args.raw), Path(args.out), args.months, args.state, args.district,
                args.top_items, args.feed_top, geo_path=Path(args.geo) if args.geo else None)
    s = res["stats"]
    print(f"{'SYNTHETIC' if res['synthetic'] else 'REAL'} data, months {res['months'][0]}..{res['months'][-1]}, "
          f"data date {res['data_date']}")
    print(f"rows in {s['rows_in']:,}, kept {s['rows_kept']:,}; {s['premises']} premises, "
          f"{s['items']} items, {s['districts']} districts")
    big = {k: v for k, v in res["sizes"].items() if v > args.max_kb * 1024}
    total = sum(res["sizes"].values())
    print(f"wrote {len(res['sizes'])} JSON files, {total/1024:.0f} KB total, "
          f"largest {max(res['sizes'].values())/1024:.0f} KB")
    if big:
        print(f"WARNING: {len(big)} file(s) over {args.max_kb} KB. Try --top-items 300.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
