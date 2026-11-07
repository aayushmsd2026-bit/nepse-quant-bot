"""Market data collector for NEPSE (Nepal Stock Exchange).

Source: ShareSansar public pages (end-of-day prices for every listed stock,
and the NEPSE index history). No login, no API key.

Usage:
    python -m bot.fetch backfill 2025-05-01      # download history
    python -m bot.fetch latest                   # download any new trading days
    python -m bot.fetch sectors                  # refresh symbol -> sector map
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRICES = DATA / "prices.csv"
INDEX = DATA / "nepse_index.csv"
SECTORS = DATA / "sectors.json"

BASE = "https://www.sharesansar.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) nepse-quant-bot (paper trading research)"

SECTOR_IDS = {
    "1": "Commercial Bank", "2": "Corporate Debentures", "3": "Development Bank",
    "4": "Finance", "5": "Government Bonds", "6": "Hotel & Tourism", "7": "Hydropower",
    "20": "Investment", "8": "Life Insurance", "9": "Manufacturing and Processing",
    "12": "Microfinance", "13": "Mutual Fund", "15": "Non-Life Insurance", "16": "Others",
    "17": "Preference Share", "18": "Promoter Share", "19": "Trading",
}

FIELDS = ["date", "symbol", "open", "high", "low", "close", "vwap", "volume",
          "prev_close", "turnover", "trans"]
# header text on the site -> our column name
HEADER_MAP = {"Symbol": "symbol", "Open": "open", "High": "high", "Low": "low",
              "Close": "close", "VWAP": "vwap", "Vol": "volume",
              "Prev. Close": "prev_close", "Turnover": "turnover", "Trans.": "trans"}


class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.token = None

    def _refresh_token(self):
        r = self._get(f"{BASE}/today-share-price")
        m = re.search(r'name="_token" content="([^"]+)"', r.text)
        if not m:
            raise RuntimeError("could not read ShareSansar session token")
        self.token = m.group(1)
        return r.text

    def _get(self, url, **kw):
        return self._retry(lambda: self.s.get(url, timeout=40, **kw))

    def _post(self, url, data):
        if self.token is None:
            self._refresh_token()
        data = {"_token": self.token, **data}
        hdr = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/today-share-price"}
        return self._retry(lambda: self.s.post(url, data=data, headers=hdr, timeout=40))

    @staticmethod
    def _retry(fn, tries=5):
        err = None
        for i in range(tries):
            try:
                r = fn()
                if r.status_code == 200:
                    return r
                err = RuntimeError(f"HTTP {r.status_code}")
            except requests.RequestException as e:  # network hiccup
                err = e
            time.sleep(2 ** i)
        raise RuntimeError(f"request failed after {tries} tries: {err}")

    def day(self, date: str, sector: str = "all_sec") -> list[dict]:
        r = self._post(f"{BASE}/ajaxtodayshareprice", {"sector": sector, "date": date})
        return parse_price_table(r.text, date)

    def index_history(self, start: str, end: str, index_id: int = 12) -> list[dict]:
        rows, offset = [], 0
        while True:
            r = self._get(f"{BASE}/index-history-data", params={
                "index_id": index_id, "from": start, "to": end,
                "draw": 1, "start": offset, "length": 50},
                headers={"X-Requested-With": "XMLHttpRequest"})
            js = r.json()
            rows += js["data"]
            offset += 50
            if offset >= js["recordsFiltered"]:
                break
        return [{"date": d["published_date"], "open": float(d["open"]),
                 "high": float(d["high"]), "low": float(d["low"]),
                 "close": float(d["current"]), "turnover": float(d["turnover"])}
                for d in rows]


def _num(s: str) -> float:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_price_table(text: str, date: str) -> list[dict]:
    m = re.search(r'As of : <span class="text-org">([0-9-]+)', text)
    if not m or m.group(1) != date:
        return []
    heads = [html.unescape(re.sub(r"<[^>]+>", "", h)).strip()
             for h in re.findall(r"<th[^>]*>(.*?)</th>", text, re.S)]
    body = text.split("<tbody>", 1)[-1]
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) != len(heads):
            continue
        row = {"date": date}
        for h, c in zip(heads, cells):
            k = HEADER_MAP.get(h)
            if k == "symbol":
                row[k] = c
            elif k:
                row[k] = _num(c)
        if row.get("close", 0) > 0 and row.get("symbol"):
            out.append(row)
    return out


def load_dates() -> set[str]:
    if not PRICES.exists():
        return set()
    with PRICES.open() as f:
        return {r["date"] for r in csv.DictReader(f)}


def append_rows(rows: list[dict]):
    new = not PRICES.exists()
    with PRICES.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in sorted(rows, key=lambda r: (r["date"], r["symbol"])):
            w.writerow(r)


def fetch_range(start: dt.date, end: dt.date, workers: int = 4) -> list[str]:
    """Download every trading day in [start, end] not already stored."""
    have = load_dates()
    days = [(start + dt.timedelta(d)).isoformat() for d in range((end - start).days + 1)]
    todo = [d for d in days if d not in have]
    if not todo:
        return []
    clients = [Client() for _ in range(workers)]

    def job(i_d):
        i, d = i_d
        return d, clients[i % workers].day(d)

    got = []
    with ThreadPoolExecutor(workers) as ex:
        for d, rows in ex.map(job, enumerate(todo)):
            if rows:
                got.append((d, rows))
                print(f"  {d}: {len(rows)} stocks", flush=True)
    for d, rows in sorted(got):
        append_rows(rows)
    return [d for d, _ in sorted(got)]


def fetch_sectors():
    c = Client()
    latest = max(load_dates()) if PRICES.exists() else dt.date.today().isoformat()
    mapping = {}
    for sid, name in SECTOR_IDS.items():
        for r in c.day(latest, sid):
            mapping[r["symbol"]] = name
    SECTORS.write_text(json.dumps(dict(sorted(mapping.items())), indent=1))
    print(f"sectors: {len(mapping)} symbols mapped")


def fetch_index(start: str):
    rows = Client().index_history(start, (dt.date.today() + dt.timedelta(1)).isoformat())
    rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
    with INDEX.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "turnover"])
        w.writeheader()
        w.writerows(rows)
    print(f"index: {len(rows)} days")


def update_latest() -> list[str]:
    """Fetch any trading days after the last stored one (used by the daily job)."""
    have = load_dates()
    last = dt.date.fromisoformat(max(have)) if have else dt.date.today() - dt.timedelta(30)
    new = fetch_range(last + dt.timedelta(1), dt.date.today(), workers=2)
    first = min(have) if have else last.isoformat()
    fetch_index(first)
    if new and (not SECTORS.exists() or dt.date.today().weekday() == 6):
        fetch_sectors()
    return new


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "latest"
    if cmd == "backfill":
        start = dt.date.fromisoformat(sys.argv[2])
        fetch_range(start, dt.date.today())
        fetch_index(start.isoformat())
        fetch_sectors()
    elif cmd == "sectors":
        fetch_sectors()
    else:
        print("new days:", update_latest())
