"""Download real PriceCatcher files from OpenDOSM / data.gov.my.

What it gets:
  - lookup_item.parquet and lookup_premise.parquet (always refreshed)
  - pricecatcher_YYYY-MM.parquet for the last N months (default 12)
    Older months are skipped if already on disk. The current and previous
    month are always re-downloaded, because DOSM updates them daily.

Source: https://open.dosm.gov.my/data-catalogue/pricecatcher
Licence: CC BY 4.0. Credit "PriceCatcher, KPDN via OpenDOSM".

Run on Windows (Command Prompt or PowerShell), from the grocery-prototype folder:
    py -m pip install pandas pyarrow
    py etl\\fetch_pricecatcher.py --months 12
    py etl\\build_dataset.py
Or just double-click scripts\\refresh_windows.bat.

Run on Mac/Linux:
    python3 etl/fetch_pricecatcher.py --months 12

Uses only the Python standard library for downloading. OpenDOSM says each
monthly file has over a million records. Parquet is much smaller than CSV.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

BASE_URL = "https://storage.data.gov.my/pricecatcher/"
LOOKUPS = ["lookup_item", "lookup_premise"]
USER_AGENT = "grocery-prototype-fetch/1.0 (personal, non-commercial prototype)"
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "data" / "raw"


def last_n_months(n: int, today: date | None = None) -> list[str]:
    """Return ['YYYY-MM', ...] for the last n months, oldest first, ending this month."""
    today = today or date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def file_url(stem: str, fmt: str = "parquet") -> str:
    return f"{BASE_URL}{stem}.{fmt}"


def download(url: str, dest: Path, retries: int = 3, timeout: int = 120) -> int:
    """Download url to dest atomically. Returns bytes written. Raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                total = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            if total == 0:
                raise IOError("empty response")
            tmp.replace(dest)
            return total
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                break  # month not published yet; no point retrying
        except Exception as e:  # network hiccup
            last_err = e
        time.sleep(2 * attempt)
    if tmp.exists():
        tmp.unlink()
    raise RuntimeError(f"failed to download {url}: {last_err}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", type=int, default=12, help="how many recent months (default 12)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="folder for raw files (default data/raw)")
    ap.add_argument("--format", default="parquet", choices=["parquet", "csv"],
                    help="parquet is much smaller; csv needs no pyarrow to read")
    ap.add_argument("--force", action="store_true", help="re-download every month")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "SYNTHETIC.txt").exists():
        print("This folder is marked SYNTHETIC. Use a different --out folder for real data.")
        return 2

    months = last_n_months(args.months)
    always = set(months[-2:])
    log = {"started": datetime.now(timezone.utc).isoformat(), "files": []}
    failures = 0

    for stem in LOOKUPS:
        dest = out / f"{stem}.{args.format}"
        try:
            n = download(file_url(stem, args.format), dest)
            print(f"OK   {dest.name}  {n/1e6:.1f} MB")
            log["files"].append({"file": dest.name, "bytes": n})
        except RuntimeError as e:
            print(f"FAIL {e}")
            failures += 1

    for ym in months:
        stem = f"pricecatcher_{ym}"
        dest = out / f"{stem}.{args.format}"
        if dest.exists() and ym not in always and not args.force:
            print(f"skip {dest.name} (already have it)")
            continue
        try:
            n = download(file_url(stem, args.format), dest)
            print(f"OK   {dest.name}  {n/1e6:.1f} MB")
            log["files"].append({"file": dest.name, "bytes": n})
        except RuntimeError as e:
            # The newest month may not exist on day 1 of a month. That is fine.
            print(f"FAIL {e}")
            if ym != months[-1]:
                failures += 1

    log["finished"] = datetime.now(timezone.utc).isoformat()
    (out / "fetch_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print("Done." if failures == 0 else f"Done with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
