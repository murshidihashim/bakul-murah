"""Make a SYNTHETIC PriceCatcher-shaped sample for offline development.

The files match the documented OpenDOSM schema:
  lookup_item:    item_code (int), item, unit, item_group, item_category
  lookup_premise: premise_code (int), premise, address, premise_type, state, district
  pricecatcher_YYYY-MM: date (YYYY-MM-DD), premise_code (int), item_code (int), price (float, RM)

The prices, shops and addresses are made up. Shop names contain "(SAMPLE)".
A SYNTHETIC.txt marker is written so build_dataset.py labels the output.

Run:  python etl/make_synthetic.py --out data/sample
"""
from __future__ import annotations

import argparse
import calendar
import random
from datetime import date
from pathlib import Path

import pandas as pd

# (code, item, unit, item_group, item_category, base price RM, fresh?)
# Names and units are written in the style of the real lookup table.
# Codes and category labels are illustrative only.
ITEMS = [
    (1, "AYAM BERSIH - STANDARD", "1kg", "BARANGAN SEGAR", "AYAM", 9.9, True),
    (2, "AYAM BERSIH - SUPER", "1kg", "BARANGAN SEGAR", "AYAM", 10.9, True),
    (3, "PAHA AYAM", "1kg", "BARANGAN SEGAR", "AYAM", 12.5, True),
    (4, "DADA AYAM", "1kg", "BARANGAN SEGAR", "AYAM", 13.0, True),
    (5, "SAYAP AYAM", "1kg", "BARANGAN SEGAR", "AYAM", 14.5, True),
    (6, "DAGING LEMBU TEMPATAN (KAWAH)", "1kg", "BARANGAN SEGAR", "DAGING", 38.0, True),
    (7, "DAGING LEMBU IMPORT INDIA (KERBAU)", "1kg", "BARANGAN SEGAR", "DAGING", 25.0, True),
    (8, "DAGING KAMBING BEBIRI IMPORT", "1kg", "BARANGAN SEGAR", "DAGING", 42.0, True),
    (9, "IKAN KEMBUNG (ANTARA 8 HINGGA 12 EKOR SEKILOGRAM)", "1kg", "BARANGAN SEGAR", "IKAN", 16.0, True),
    (10, "IKAN SELAYANG", "1kg", "BARANGAN SEGAR", "IKAN", 10.0, True),
    (11, "IKAN TONGKOL", "1kg", "BARANGAN SEGAR", "IKAN", 14.0, True),
    (12, "IKAN CENCARU", "1kg", "BARANGAN SEGAR", "IKAN", 12.0, True),
    (13, "IKAN TILAPIA MERAH", "1kg", "BARANGAN SEGAR", "IKAN", 15.0, True),
    (14, "IKAN SIAKAP", "1kg", "BARANGAN SEGAR", "IKAN", 26.0, True),
    (15, "IKAN BAWAL HITAM", "1kg", "BARANGAN SEGAR", "IKAN", 32.0, True),
    (16, "UDANG PUTIH (ANTARA 40 HINGGA 60 EKOR SEKILOGRAM)", "1kg", "BARANGAN SEGAR", "MAKANAN LAUT", 38.0, True),
    (17, "SOTONG", "1kg", "BARANGAN SEGAR", "MAKANAN LAUT", 28.0, True),
    (18, "TELUR AYAM GRED A", "10 biji", "BARANGAN SEGAR", "TELUR", 4.5, True),
    (19, "TELUR AYAM GRED B", "10 biji", "BARANGAN SEGAR", "TELUR", 4.2, True),
    (20, "TELUR AYAM GRED C", "10 biji", "BARANGAN SEGAR", "TELUR", 3.9, True),
    (21, "TELUR AYAM GRED A", "30 biji", "BARANGAN SEGAR", "TELUR", 13.0, True),
    (22, "BAWANG BESAR IMPORT (INDIA)", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 5.5, True),
    (23, "BAWANG MERAH IMPORT (INDIA)", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 8.0, True),
    (24, "BAWANG PUTIH IMPORT (CHINA)", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 11.0, True),
    (25, "HALIA TUA", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 9.0, True),
    (26, "CILI MERAH", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 14.0, True),
    (27, "CILI API / PADI HIJAU", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 18.0, True),
    (28, "TOMATO", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 6.0, True),
    (29, "KOBIS BULAT (TEMPATAN)", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 4.5, True),
    (30, "KANGKUNG", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 4.0, True),
    (31, "SAWI HIJAU", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 5.0, True),
    (32, "BAYAM HIJAU", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 5.5, True),
    (33, "KACANG PANJANG", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 7.0, True),
    (34, "TIMUN", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 3.5, True),
    (35, "TERUNG PANJANG", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 6.0, True),
    (36, "KENTANG IMPORT (CHINA)", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 4.5, True),
    (37, "LOBAK MERAH", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN", 4.0, True),
    (38, "PISANG BERANGAN", "1kg", "BARANGAN SEGAR", "BUAH-BUAHAN", 6.0, True),
    (39, "BETIK BIASA", "1kg", "BARANGAN SEGAR", "BUAH-BUAHAN", 4.5, True),
    (40, "TEMBIKAI MERAH TANPA BIJI", "1kg", "BARANGAN SEGAR", "BUAH-BUAHAN", 3.5, True),
    (41, "LIMAU NIPIS", "1kg", "BARANGAN SEGAR", "BUAH-BUAHAN", 8.0, True),
    (42, "KELAPA PARUT", "1kg", "BARANGAN SEGAR", "BUAH-BUAHAN", 9.0, True),
    (101, "BERAS TEMPATAN SUPER 5% HANCUR", "5kg", "BARANGAN KERING", "BERAS", 13.0, False),
    (102, "BERAS TEMPATAN SUPER 5% HANCUR", "10kg", "BARANGAN KERING", "BERAS", 26.0, False),
    (103, "BERAS IMPORT WANGI THAI", "5kg", "BARANGAN KERING", "BERAS", 31.0, False),
    (104, "BERAS BASMATHI", "1kg", "BARANGAN KERING", "BERAS", 9.5, False),
    (105, "GULA PASIR KASAR", "1kg", "BARANGAN KERING", "GULA", 2.85, False),
    (106, "GULA PASIR HALUS", "1kg", "BARANGAN KERING", "GULA", 2.95, False),
    (107, "GULA PERANG", "1kg", "BARANGAN KERING", "GULA", 4.2, False),
    (108, "TEPUNG GANDUM SERBAGUNA", "1kg", "BARANGAN KERING", "TEPUNG", 2.7, False),
    (109, "TEPUNG BERAS", "500g", "BARANGAN KERING", "TEPUNG", 2.3, False),
    (110, "TEPUNG JAGUNG", "400g", "BARANGAN KERING", "TEPUNG", 3.2, False),
    (111, "MINYAK MASAK TULEN CAP A (PAKET)", "1kg", "BARANGAN KERING", "MINYAK MASAK", 2.5, False),
    (112, "MINYAK MASAK SAWIT (BOTOL)", "2kg", "BARANGAN KERING", "MINYAK MASAK", 13.5, False),
    (113, "MINYAK MASAK SAWIT (BOTOL)", "5kg", "BARANGAN KERING", "MINYAK MASAK", 30.0, False),
    (114, "GARAM HALUS", "500g", "BARANGAN KERING", "BAHAN MASAKAN", 1.2, False),
    (115, "KICAP MANIS", "335ml", "BARANGAN KERING", "BAHAN MASAKAN", 3.6, False),
    (116, "SOS CILI", "340g", "BARANGAN KERING", "BAHAN MASAKAN", 3.8, False),
    (117, "SOS TOMATO", "325g", "BARANGAN KERING", "BAHAN MASAKAN", 3.6, False),
    (118, "SERBUK KARI DAGING", "250g", "BARANGAN KERING", "BAHAN MASAKAN", 6.2, False),
    (119, "SERBUK CILI", "250g", "BARANGAN KERING", "BAHAN MASAKAN", 6.5, False),
    (120, "IKAN BILIS GRED A", "1kg", "BARANGAN KERING", "IKAN KERING", 38.0, False),
    (121, "IKAN MASIN GELAMA", "1kg", "BARANGAN KERING", "IKAN KERING", 30.0, False),
    (122, "KACANG DAL", "1kg", "BARANGAN KERING", "KEKACANG", 7.5, False),
    (123, "KACANG HIJAU", "1kg", "BARANGAN KERING", "KEKACANG", 8.5, False),
    (124, "SANTAN KOTAK", "200ml", "BARANGAN KERING", "BAHAN MASAKAN", 2.4, False),
    (125, "MI KUNING", "1kg", "BARANGAN KERING", "MI DAN BIHUN", 3.5, False),
    (126, "BIHUN BERAS", "400g", "BARANGAN KERING", "MI DAN BIHUN", 3.3, False),
    (127, "MI SEGERA PERISA KARI (5 X 75G)", "5 paket", "BARANGAN BERBUNGKUS", "MI DAN BIHUN", 5.2, False),
    (128, "SARDIN DALAM SOS TOMATO", "425g", "BARANGAN BERBUNGKUS", "MAKANAN DALAM TIN", 7.5, False),
    (129, "TUNA DALAM AIR GARAM", "185g", "BARANGAN BERBUNGKUS", "MAKANAN DALAM TIN", 6.2, False),
    (130, "ROTI PUTIH (BUKU)", "400g", "BARANGAN BERBUNGKUS", "ROTI", 3.9, False),
    (131, "ROTI GANDUM PENUH", "400g", "BARANGAN BERBUNGKUS", "ROTI", 4.6, False),
    (132, "BISKUT KRIM KRAKER", "428g", "BARANGAN BERBUNGKUS", "BISKUT", 6.0, False),
    (133, "MARJERIN", "480g", "BARANGAN BERBUNGKUS", "BAHAN MASAKAN", 6.8, False),
    (134, "MENTEGA KACANG", "340g", "BARANGAN BERBUNGKUS", "SAPUAN", 9.5, False),
    (135, "KAYA", "400g", "BARANGAN BERBUNGKUS", "SAPUAN", 5.8, False),
    (201, "SUSU PEKAT MANIS", "500g", "MINUMAN", "SUSU", 3.8, False),
    (202, "SUSU SEGAR PENUH KRIM", "1 liter", "MINUMAN", "SUSU", 7.9, False),
    (203, "SUSU TEPUNG PENUH KRIM", "1kg", "MINUMAN", "SUSU", 23.0, False),
    (204, "SUSU SEJAT", "390g", "MINUMAN", "SUSU", 3.4, False),
    (205, "SERBUK KOKO MINUMAN COKLAT MALT", "1kg", "MINUMAN", "MINUMAN SERBUK", 21.0, False),
    (206, "KOPI SERBUK O", "200g", "MINUMAN", "KOPI DAN TEH", 6.5, False),
    (207, "KOPI 3 DALAM 1 (20 X 30G)", "20 paket", "MINUMAN", "KOPI DAN TEH", 13.5, False),
    (208, "SERBUK TEH", "200g", "MINUMAN", "KOPI DAN TEH", 6.0, False),
    (209, "TEH UNCANG (100 UNCANG)", "200g", "MINUMAN", "KOPI DAN TEH", 9.5, False),
    (210, "AIR MINERAL", "1.5 liter", "MINUMAN", "MINUMAN BOTOL", 1.6, False),
    (211, "AIR MINERAL (6 X 1.5L)", "6 botol", "MINUMAN", "MINUMAN BOTOL", 8.5, False),
    (212, "MINUMAN BERKARBONAT (TIN)", "325ml", "MINUMAN", "MINUMAN BOTOL", 2.2, False),
    (213, "JUS OREN", "1 liter", "MINUMAN", "MINUMAN BOTOL", 6.9, False),
    (301, "SERBUK PENCUCI PAKAIAN", "2kg", "BARANGAN KEBERSIHAN", "PENCUCI", 14.0, False),
    (302, "CECAIR PENCUCI PAKAIAN", "3kg", "BARANGAN KEBERSIHAN", "PENCUCI", 23.0, False),
    (303, "CECAIR PENCUCI PINGGAN", "900ml", "BARANGAN KEBERSIHAN", "PENCUCI", 5.5, False),
    (304, "PELEMBUT FABRIK", "1.6 liter", "BARANGAN KEBERSIHAN", "PENCUCI", 12.5, False),
    (305, "SABUN MANDI CECAIR", "900ml", "BARANGAN KEBERSIHAN", "PENJAGAAN DIRI", 14.0, False),
    (306, "UBAT GIGI", "175g", "BARANGAN KEBERSIHAN", "PENJAGAAN DIRI", 7.5, False),
    (307, "SYAMPU", "400ml", "BARANGAN KEBERSIHAN", "PENJAGAAN DIRI", 16.0, False),
    (308, "TISU TANDAS (10 GULUNG)", "10 gulung", "BARANGAN KEBERSIHAN", "TISU", 12.5, False),
    (309, "TISU MUKA (4 KOTAK)", "4 kotak", "BARANGAN KEBERSIHAN", "TISU", 10.5, False),
    (310, "LAMPIN PAKAI BUANG BAYI (SAIZ M)", "40 keping", "BARANGAN KEBERSIHAN", "BAYI", 33.0, False),
    (311, "PENCUCI LANTAI", "2 liter", "BARANGAN KEBERSIHAN", "PENCUCI", 9.5, False),
    (312, "BEG SAMPAH HITAM (SAIZ BESAR)", "10 keping", "BARANGAN KEBERSIHAN", "PERALATAN RUMAH", 4.5, False),
]

# (state, district, number of premises). State and district spellings follow
# the style of the real lookup table. Counts are small on purpose.
AREAS = [
    ("Selangor", "Petaling", 18),
    ("Selangor", "Klang", 12),
    ("Selangor", "Hulu Langat", 12),
    ("W.P. Kuala Lumpur", "Kuala Lumpur", 18),
    ("Johor", "Johor Bahru", 14),
    ("Pulau Pinang", "Timur Laut", 10),
    ("Sabah", "Kota Kinabalu", 8),
]

# premise_type: (share, price multiplier, carries fresh, carries packaged, carry rate)
PREMISE_TYPES = {
    "Hypermarket": (0.15, 0.95, True, True, 0.98),
    "Pasar Raya / Supermarket": (0.30, 1.00, True, True, 0.95),
    "Pasar Mini": (0.20, 1.05, False, True, 0.70),
    "Kedai Runcit": (0.20, 1.07, False, True, 0.50),
    "Pasar Basah": (0.15, 0.96, True, False, 0.75),
}

NAME_STEMS = ["Maju", "Jaya", "Sentosa", "Harmoni", "Bestari", "Indah", "Murni",
              "Setia", "Ceria", "Damai", "Gemilang", "Sinar", "Mutiara", "Seri"]
TYPE_PREFIX = {
    "Hypermarket": "Hiper Contoh",
    "Pasar Raya / Supermarket": "Pasaraya Contoh",
    "Pasar Mini": "Pasar Mini Contoh",
    "Kedai Runcit": "Kedai Runcit Contoh",
    "Pasar Basah": "Pasar Awam Contoh",
}


def month_list(end_year: int, end_month: int, n: int) -> list[tuple[int, int]]:
    out = []
    y, m = end_year, end_month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def make(out: Path, months: int = 12, end: str = "2026-10", last_day: int = 27,
         seed: int = 7, fmt: str = "both") -> dict:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    items = pd.DataFrame(
        [(c, n, u, g, cat) for c, n, u, g, cat, _, _ in ITEMS],
        columns=["item_code", "item", "unit", "item_group", "item_category"],
    )

    premises = []
    code = 1000
    for state, district, n in AREAS:
        types = list(PREMISE_TYPES)
        weights = [PREMISE_TYPES[t][0] for t in types]
        for i in range(n):
            ptype = types[i] if i < len(types) else rng.choices(types, weights)[0]
            code += rng.randint(1, 9)
            stem = rng.choice(NAME_STEMS)
            name = f"{TYPE_PREFIX[ptype]} {stem} {i + 1} (SAMPLE)"
            addr = f"No. {rng.randint(1, 120)}, Jalan Contoh {rng.randint(1, 30)}, {district}, {state}"
            premises.append((code, name, addr, ptype, state, district))
    premise_df = pd.DataFrame(
        premises, columns=["premise_code", "premise", "address", "premise_type", "state", "district"]
    )
    # The real table has a few rows with missing fields. Mimic that.
    premise_df = pd.concat([premise_df, pd.DataFrame([{
        "premise_code": 999999, "premise": None, "address": None,
        "premise_type": None, "state": None, "district": None}])], ignore_index=True)

    ey, em = (int(x) for x in end.split("-"))
    mlist = month_list(ey, em, months)

    # Item price paths: gentle random walk, plus a few clear jumps in the last month.
    base = {c: p for c, _, _, _, _, p, _ in ITEMS}
    fresh = {c: f for c, _, _, _, _, _, f in ITEMS}
    jumpers = set(rng.sample(list(base), 10))
    fallers = set(rng.sample([c for c in base if c not in jumpers], 5))
    path = {}
    for c, p in base.items():
        level = p * rng.uniform(0.9, 1.0)
        series = []
        for k in range(months):
            vol = 0.04 if fresh[c] else 0.012
            level *= 1 + rng.gauss(0.003, vol)
            if k == months - 1 and c in jumpers:
                level *= rng.uniform(1.08, 1.22)
            if k == months - 1 and c in fallers:
                level *= rng.uniform(0.85, 0.94)
            series.append(level)
        path[c] = series

    # Premise factors and assortments.
    pfactor, assortment = {}, {}
    for code, _, _, ptype, *_ in premises:
        _, mult, f_ok, pk_ok, rate = PREMISE_TYPES[ptype]
        pfactor[code] = mult * rng.gauss(1.0, 0.035)
        assortment[code] = [c for c in base
                            if ((fresh[c] and f_ok) or (not fresh[c] and pk_ok)) and rng.random() < rate]

    files = []
    for k, (y, m) in enumerate(mlist):
        ndays = calendar.monthrange(y, m)[1]
        if k == months - 1:
            ndays = min(ndays, last_day)
        rows = []
        for code, *_ in premises:
            for c in assortment[code]:
                # Not every item is seen every month at every shop.
                if rng.random() < 0.04:
                    continue
                item_factor = rng.gauss(1.0, 0.02)
                days = [rng.randint(1, ndays) for _ in range(rng.randint(1, 3))]
                if rng.random() < 0.8:  # most shops are re-checked near the month end
                    days.append(rng.randint(max(1, ndays - 9), ndays))
                for d in days:
                    p = path[c][k] * pfactor[code] * item_factor * rng.gauss(1.0, 0.01)
                    p = round(round(p / 0.05) * 0.05, 2)
                    rows.append((date(y, m, d).isoformat(), code, c, p))
        # Dirty rows the cleaner must handle: zero, absurd, duplicate, unknown codes.
        if rows:
            r = rows[0]
            rows += [(r[0], r[1], r[2], 0.0), (r[0], r[1], r[2], r[3] * 40),
                     r, (r[0], 424242, r[2], r[3]), (r[0], r[1], 77777, 5.0)]
        df = pd.DataFrame(rows, columns=["date", "premise_code", "item_code", "price"])
        df = df.sort_values(["date", "premise_code", "item_code"]).reset_index(drop=True)
        stem = f"pricecatcher_{y:04d}-{m:02d}"
        _write(df, out / stem, fmt)
        files.append(stem)

    _write(items, out / "lookup_item", fmt)
    _write(premise_df, out / "lookup_premise", fmt)
    (out / "SYNTHETIC.txt").write_text(
        "This folder holds SYNTHETIC sample data made by etl/make_synthetic.py.\n"
        "Prices, shops and addresses are made up. Do not publish as real prices.\n"
        "Delete this file only after replacing the data with real downloads.\n",
        encoding="utf-8",
    )
    return {"months": files, "items": len(items), "premises": len(premise_df)}


def _write(df: pd.DataFrame, stem: Path, fmt: str) -> None:
    if fmt in ("csv", "both"):
        df.to_csv(stem.with_suffix(".csv"), index=False)
    if fmt in ("parquet", "both"):
        try:
            df.to_parquet(stem.with_suffix(".parquet"), index=False)
        except ImportError:
            pass  # pyarrow not installed; CSV is enough


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/sample")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--end", default="2026-10", help="last month, YYYY-MM")
    ap.add_argument("--last-day", type=int, default=27, help="last day of data in the last month")
    ap.add_argument("--format", default="csv", choices=["csv", "parquet", "both"])
    args = ap.parse_args()
    info = make(Path(args.out), args.months, args.end, args.last_day, fmt=args.format)
    print(f"Wrote SYNTHETIC sample to {args.out}: {info}")


if __name__ == "__main__":
    main()
