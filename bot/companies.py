"""Company share data: listed shares, promoter (locked) shares, public float, listing date.

Source: ShareSansar company list (public), one request per sector.   python -m bot.companies

Used to rebuild each stock's market capitalisation and free-float value through
history: today's share count x the bonus/right-adjusted price series. (Rights issues
and FPOs bring in new money, so this is an approximation, but a close one for ranking.)
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .fetch import BASE, SECTOR_IDS, Client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "companies.csv"
FIELDS = ["symbol", "sector", "listed", "promoter", "public", "paidup", "listing_date"]


def _n(v) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", str(v)) or 0)
    except ValueError:
        return 0.0


def update() -> int:
    c = Client()
    rows = {}
    for sid, name in SECTOR_IDS.items():
        start = 0
        while True:
            js = c._get(f"{BASE}/company-list", params={"sector": sid, "draw": 1, "start": start, "length": 50},
                        headers={"X-Requested-With": "XMLHttpRequest"}).json()
            data = js.get("data", [])
            for d in data:
                sym = re.sub(r"<[^>]+>", "", d.get("symbol", "")).strip()
                if sym:
                    rows[sym] = {"symbol": sym, "sector": name, "listed": _n(d.get("shares")),
                                 "promoter": _n(d.get("prom_share")), "public": _n(d.get("public_share")),
                                 "paidup": _n(d.get("paidup")), "listing_date": (d.get("listing_date") or "")[:10]}
            start += 50
            if not data or start >= js.get("recordsFiltered", 0):
                break
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r["symbol"]))
    print(f"companies: {len(rows)} saved")
    return len(rows)


if __name__ == "__main__":
    update()
