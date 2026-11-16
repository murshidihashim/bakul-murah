"""Tests for the GeoNames place list download (etl/fetch_places.py). No network.

Run from the repo root:  python -m pytest etl/tests -q
"""
import csv
import io
import json
import sys
import urllib.error
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_places as fpl  # noqa: E402


def row(gid, name, ascii_name, alt, lat, lon, fclass, code, cc="MY", admin1="08", pop="0"):
    """One line of a GeoNames country dump (19 tab-separated columns)."""
    return "\t".join([str(gid), name, ascii_name, alt, str(lat), str(lon), fclass, code, cc, "", admin1,
                      "", "", "", pop, "", "10", "Asia/Kuala_Lumpur", "2024-01-01"])


DUMP = "\n".join([
    row(1733037, "Perak", "Perak", "", 4.75, 101.0, "A", "ADM1"),
    row(1733039, "Penang", "Penang", "Pulau Pinang", 5.4, 100.3, "A", "ADM1", admin1="09"),
    row(1734759, "Seri Manjung", "Seri Manjung", "Sri Manjung,سري منجوڠ,斯里曼绒,SRI MANJUNG,2",
        4.2, 100.66, "P", "PPL", pop="20000"),
    row(1734760, "Bagan Serai", "Bagan Serai", "", 5.0108, 100.541, "P", "PPLA3", pop="12000"),
    row(1734761, "Kampung Lama", "Kampung Lama", "", 4.3, 100.9, "P", "PPLQ"),   # abandoned
    row(1734762, "Old Town", "Old Town", "", 4.31, 100.91, "P", "PPLH"),         # historical
    row(1734763, "Bukit Merah", "Bukit Merah", "", 4.99, 100.66, "T", "HLL"),     # a hill
    row(1734764, "Far Away", "Far Away", "", 12.5, 100.66, "P", "PPL"),          # outside Malaysia
    row(1734765, "Bad Point", "Bad Point", "", "x", 100.66, "P", "PPL"),
    row(1734766, "Elsewhere", "Elsewhere", "", 4.2, 100.66, "P", "PPL", cc="TH"),
    row(1734767, "No State", "No State", "", 4.2, 100.66, "P", "PPL", admin1="99"),
    row(1734768, "George Town", "George Town", "Georgetown,Tanjung", 5.4141, 100.3288, "P", "PPLA", admin1="09",
        pop="300000"),
    "short\tline",
])


def zip_of(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "GeoNames")
        z.writestr("MY.txt", text)
    return buf.getvalue()


def test_parse_keeps_living_populated_places_with_their_state():
    rows = fpl.parse("﻿" + DUMP)
    assert [r["name"] for r in rows] == ["George Town", "Bagan Serai", "Seri Manjung"]
    gt, bs, sm = rows
    assert gt["state"] == "Penang" and gt["code"] == "PPLA" and gt["population"] == "300000"
    assert gt["alt"] == "Georgetown|Tanjung"
    assert bs == {"geonameid": "1734760", "name": "Bagan Serai", "asciiname": "Bagan Serai", "alt": "",
                  "lat": "5.01080", "lon": "100.54100", "code": "PPLA3", "state": "Perak", "population": "12000"}
    # Latin-script names only, not the name itself again (any case), no numbers.
    assert sm["alt"] == "Sri Manjung"


def test_fetch_places_writes_list_then_skips_while_fresh(tmp_path):
    out = tmp_path / "places_my.csv"
    calls = []

    def fetch():
        calls.append(1)
        return zip_of(DUMP)

    assert fpl.update(out, fetch=fetch, today=date(2026, 11, 16), min_rows=1) == "updated"
    with out.open(encoding="utf-8", newline="") as f:
        got = list(csv.DictReader(f))
    assert list(got[0].keys()) == fpl.FIELDS and len(got) == 3
    info = json.loads((tmp_path / "places_my.json").read_text(encoding="utf-8"))
    assert info["fetched"] == "2026-11-16" and info["rows"] == 3 and info["states"] == 2
    assert info["licence"] == "CC BY 4.0" and info["download_url"] == fpl.URL
    assert fpl.update(out, fetch=fetch, today=date(2027, 2, 13), min_rows=1) == "fresh"   # 89 days
    assert len(calls) == 1
    assert fpl.update(out, fetch=fetch, today=date(2027, 2, 14), min_rows=1) == "updated"  # 90 days
    assert fpl.update(out, fetch=fetch, today=date(2027, 2, 14), force=True, min_rows=1) == "updated"
    assert len(calls) == 3
    # A list without its .json, or with another header, is fetched again.
    (tmp_path / "places_my.json").unlink()
    assert fpl.age_days(out, date(2027, 2, 14)) is None


def test_fetch_places_failure_keeps_old_list_and_exits_0(tmp_path, monkeypatch, capsys):
    out = tmp_path / "places_my.csv"
    assert fpl.update(out, fetch=lambda: zip_of(DUMP), today=date(2026, 1, 1), min_rows=1) == "updated"
    before = out.read_bytes()

    def down():
        raise urllib.error.URLError("blocked")

    for fetch in (down, lambda: b"not a zip", lambda: zip_of(DUMP)):  # error, bad zip, too few rows
        assert fpl.update(out, fetch=fetch, today=date(2026, 11, 16)) == "failed"
        assert out.read_bytes() == before
        assert not (tmp_path / "places_my.csv.tmp").exists()
    assert "WARNING" in capsys.readouterr().out
    monkeypatch.setattr(fpl.fpc, "download", lambda *a, **k: down())
    assert fpl.main(["--out", str(out), "--force"]) == 0
    assert out.read_bytes() == before
    assert fpl.main(["--out", str(tmp_path / "none.csv")]) == 0
    assert not (tmp_path / "none.csv").exists()


def test_default_download_uses_the_gazetteer_url_and_its_size_limit(tmp_path, monkeypatch):
    seen = {}

    def fake(url, max_bytes=None, **k):
        seen.update(url=url, max_bytes=max_bytes)
        return zip_of(DUMP)

    monkeypatch.setattr(fpl.fpc, "download", fake)
    assert fpl.update(tmp_path / "places_my.csv", today=date(2026, 11, 16), min_rows=1) == "updated"
    assert seen == {"url": "https://download.geonames.org/export/dump/MY.zip", "max_bytes": fpl.MAX_BYTES}
    assert fpl.MAX_BYTES > fpl.fpc.MAX_BYTES  # the gazetteer is bigger than the postcode table


def test_postcode_download_still_enforces_its_own_limit(monkeypatch):
    class Resp:
        def __init__(self):
            self.left = 3

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read1(self, n):
            self.left -= 1
            return b"x" * 10 if self.left >= 0 else b""

    monkeypatch.setattr(fpl.fpc.urllib.request, "urlopen", lambda req, timeout=None: Resp())
    assert fpl.fpc.download("https://example.invalid/", max_bytes=30) == b"x" * 30
    try:
        fpl.fpc.download("https://example.invalid/", max_bytes=29)
    except ValueError as e:
        assert "bigger" in str(e)
    else:
        raise AssertionError("an oversized download must fail")
