"""Daily floorsheet collector: who bought and who sold each stock (broker numbers).

ShareSansar only serves the LATEST day's floorsheet, so history cannot be downloaded
later. The daily job calls `collect()` after each close and stores one compact summary
row per stock in data/floorsheet/<year>.csv. After 6-12 months there is enough history
to test broker-accumulation signals (e.g. one broker quietly buying for days before
news comes out, the likely source of the pre-announcement run-up).

    python -m bot.floorsheet      # collect today's floorsheet now
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .fetch import BASE, Client

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "floorsheet"
FIELDS = ["date", "symbol", "trades", "qty", "amount", "buyers", "sellers", "avg_trade",
          "top_buyer", "top_buyer_pct", "top3_buy_pct", "top_seller", "top_seller_pct", "top3_sell_pct",
          "top_net_broker", "top_net_pct", "buy_hhi"]


def _rows(c: Client, sym: str) -> list[dict]:
    out, start = [], 0
    while True:
        js = c._get(f"{BASE}/floorsheet", params={"draw": 1, "start": start, "length": 500, "company": sym},
                    headers={"X-Requested-With": "XMLHttpRequest"}).json()
        data = js.get("data", [])
        out += data
        start += 500
        if not data or start >= js.get("recordsFiltered", 0):
            return out


def summarise(sym: str, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    buy, sell = defaultdict(float), defaultdict(float)
    qty = amt = 0.0
    for r in rows:
        q, a = float(r["quantity"]), float(r["amount"])
        buy[str(r["buyer"])] += q
        sell[str(r["seller"])] += q
        qty += q
        amt += a
    if qty <= 0:
        return None
    sb = sorted(buy.items(), key=lambda kv: -kv[1])
    ss = sorted(sell.items(), key=lambda kv: -kv[1])
    net = {b: buy.get(b, 0) - sell.get(b, 0) for b in set(buy) | set(sell)}
    nb = max(net.items(), key=lambda kv: kv[1])
    return {"date": rows[0]["date_"], "symbol": sym, "trades": len(rows), "qty": round(qty), "amount": round(amt),
            "buyers": len(buy), "sellers": len(sell), "avg_trade": round(amt / len(rows)),
            "top_buyer": sb[0][0], "top_buyer_pct": round(sb[0][1] / qty * 100, 1),
            "top3_buy_pct": round(sum(v for _, v in sb[:3]) / qty * 100, 1),
            "top_seller": ss[0][0], "top_seller_pct": round(ss[0][1] / qty * 100, 1),
            "top3_sell_pct": round(sum(v for _, v in ss[:3]) / qty * 100, 1),
            "top_net_broker": nb[0], "top_net_pct": round(nb[1] / qty * 100, 1),
            "buy_hhi": round(sum((v / qty) ** 2 for v in buy.values()), 4)}


def stored_dates() -> set[str]:
    out = set()
    for f in DIR.glob("*.csv"):
        with f.open() as fh:
            out |= {r["date"] for r in csv.DictReader(fh)}
    return out


def collect(expected_date: str, symbols: list[str], workers: int = 3) -> str:
    """Download and summarise the floorsheet if it is for `expected_date` and not stored yet."""
    if expected_date in stored_dates():
        return f"floorsheet for {expected_date} already stored"
    probe = _rows(Client(), symbols[0]) if symbols else []
    served = probe[0]["date_"] if probe else None
    if served != expected_date:
        return f"floorsheet not collected: source shows {served}, expected {expected_date}"
    clients = [Client() for _ in range(workers)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda a: summarise(a[1], _rows(clients[a[0] % workers], a[1])), enumerate(symbols)))
    rows = [r for r in res if r and r["date"] == expected_date]
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / f"{expected_date[:4]}.csv"
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["symbol"]))
    return f"floorsheet for {expected_date}: {len(rows)} stocks, {sum(r['trades'] for r in rows):,} trades summarised"


if __name__ == "__main__":
    import pandas as pd
    from . import research
    M = research.load()
    d = M["dates"][-1]
    syms = [s for s in M["close"].columns if bool(M["traded"].at[d, s])]
    print(collect(d, syms))
