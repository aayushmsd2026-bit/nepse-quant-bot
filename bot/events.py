"""Corporate-event calendar: dividend / bonus announcements and book-closure dates.

Source: ShareSansar "proposed dividend" table (public), one request per Nepali fiscal year.

    python -m bot.events          # refresh data/dividends.csv

Why it matters: in the price data alone we only see a bonus share AFTER the book
closure (when the exchange cuts the base price). Knowing the ANNOUNCEMENT date and
the BOOK-CLOSURE date in advance is what turns the pre-book-closure run-up into
something you could act on.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from .fetch import BASE, Client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dividends.csv"
FIELDS = ["symbol", "fiscal_year", "bonus_pct", "cash_pct", "total_pct", "announced", "book_close",
          "distribution", "bonus_listing"]


def _clean(v) -> str:
    v = re.sub(r"<[^>]+>", "", str(v or "")).strip()
    return "" if v in ("-", "0000-00-00", "None", "null") else v


def _pct(v) -> float:
    try:
        return float(_clean(v).replace(",", "").replace("%", "") or 0)
    except ValueError:
        return 0.0


def year_options(c: Client) -> dict[str, str]:
    html = c._get(f"{BASE}/proposed-dividend").text
    sel = re.search(r'<select name="year".*?</select>', html, re.S)
    return {v: lab.strip() for v, lab in re.findall(r'<option[^>]*value="(\d+)"[^>]*>\s*([0-9/]+)', sel.group(0))} if sel else {}


def fetch_year(c: Client, year_id: str) -> list[dict]:
    rows, start = [], 0
    while True:
        r = c._get(f"{BASE}/proposed-dividend", params={
            "type": "YEARWISE", "year": year_id, "sector": 0, "draw": 1, "start": start, "length": 50},
            headers={"X-Requested-With": "XMLHttpRequest"})
        js = r.json()
        rows += js.get("data", [])
        start += 50
        if start >= js.get("recordsFiltered", 0) or not js.get("data"):
            break
    return rows


def update(min_year_label: str = "2075/2076") -> int:
    c = Client()
    years = {k: v for k, v in year_options(c).items() if v >= min_year_label}
    out = {}
    for yid, label in sorted(years.items(), key=lambda kv: kv[1]):
        for d in fetch_year(c, yid):
            sym = _clean(d.get("symbol"))
            if not sym:
                continue
            row = {"symbol": sym, "fiscal_year": _clean(d.get("year")) or label,
                   "bonus_pct": _pct(d.get("bonus_share")), "cash_pct": _pct(d.get("cash_dividend")),
                   "total_pct": _pct(d.get("total_dividend")), "announced": _clean(d.get("announcement_date"))[:10],
                   "book_close": _clean(d.get("bookclose_date"))[:10], "distribution": _clean(d.get("distribution_date"))[:10],
                   "bonus_listing": _clean(d.get("bonus_listing_date"))[:10]}
            out[(sym, row["fiscal_year"])] = row
        print(f"  {label}: {sum(1 for k in out if k[1] == label or out[k]['fiscal_year'] == label)} rows", flush=True)
    rows = sorted(out.values(), key=lambda r: (r["announced"] or r["book_close"], r["symbol"]))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"dividends: {len(rows)} announcements saved")
    return len(rows)


if __name__ == "__main__":
    update(sys.argv[1] if len(sys.argv) > 1 else "2075/2076")
