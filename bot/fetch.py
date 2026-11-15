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
PRICES_DIR = DATA / "prices"          # one CSV per calendar year: small daily git diffs
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
        hdr = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/today-share-price"}
        for attempt in range(3):
            r = self._retry(lambda: self.s.post(url, data={"_token": self.token, **data},
                                                headers=hdr, timeout=40), ok=(200, 419))
            if r.status_code == 200:
                return r
            self._refresh_token()  # 419 = session/CSRF token expired
        raise RuntimeError("session token keeps expiring")

    @staticmethod
    def _retry(fn, tries=5, ok=(200,)):
        err = None
        for i in range(tries):
            try:
                r = fn()
                if r.status_code in ok:
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


def year_files() -> list[Path]:
    return sorted(PRICES_DIR.glob("*.csv"))


def load_dates() -> set[str]:
    out = set()
    for f in year_files():
        with f.open() as fh:
            out |= {r["date"] for r in csv.DictReader(fh)}
    return out


def append_rows(rows: list[dict]):
    """Merge rows into the per-year files, keeping each file sorted by (date, symbol)."""
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    by_year: dict[str, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["date"][:4], []).append(r)
    for y, new in by_year.items():
        f = PRICES_DIR / f"{y}.csv"
        old = []
        if f.exists():
            with f.open() as fh:
                old = list(csv.DictReader(fh))
        merged = {(r["date"], r["symbol"]): r for r in old}
        merged.update({(r["date"], r["symbol"]): r for r in new})
        with f.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for k in sorted(merged):
                w.writerow(merged[k])


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

    saved = []
    with ThreadPoolExecutor(workers) as ex:
        for c0 in range(0, len(todo), 90):          # save progress every ~3 months
            got = []
            for d, rows in ex.map(job, enumerate(todo[c0:c0 + 90])):
                if rows:
                    got.append((d, rows))
                    print(f"  {d}: {len(rows)} stocks", flush=True)
            append_rows([r for _, rows in got for r in rows])
            saved += [d for d, _ in got]
    return sorted(saved)


def fetch_sectors(history: bool = False):
    """Map symbol -> sector. With history=True also sample one day per quarter in
    the past, so stocks that later merged or delisted still get a sector
    (reduces survivorship bias in the historical simulation)."""
    c = Client()
    dates = sorted(load_dates())
    sample = [dates[-1]]
    if history:
        seen = set()
        for d in dates:
            q = (d[:4], (int(d[5:7]) - 1) // 3)
            if q not in seen:
                seen.add(q)
                sample.append(d)
    mapping = json.loads(SECTORS.read_text()) if SECTORS.exists() else {}
    for d in sorted(set(sample)):          # oldest first, so the latest date wins
        for sid, name in SECTOR_IDS.items():
            for r in c.day(d, sid):
                mapping[r["symbol"]] = name
    SECTORS.write_text(json.dumps(dict(sorted(mapping.items())), indent=1))
    print(f"sectors: {len(mapping)} symbols mapped")


def fetch_index(start: str):
    """Download NEPSE index history from `start` and merge it into the stored file."""
    rows = Client().index_history(start, (dt.date.today() + dt.timedelta(1)).isoformat())
    old = list(csv.DictReader(INDEX.open())) if INDEX.exists() else []
    merged = {r["date"]: r for r in old}
    merged.update({r["date"]: r for r in rows})
    with INDEX.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "turnover"])
        w.writeheader()
        w.writerows(merged[d] for d in sorted(merged))
    print(f"index: {len(merged)} days ({len(rows)} fetched)")


NPT = dt.timezone(dt.timedelta(hours=5, minutes=45))
MARKET_CLOSE = dt.time(15, 10)      # NEPSE closes at 15:00; allow 10 minutes for the final prices


def day_counts(n: int = 10) -> list[int]:
    """Number of stocks stored for each of the last n trading days."""
    files = year_files()[-2:]
    counts: dict[str, int] = {}
    for f in files:
        with f.open() as fh:
            for r in csv.DictReader(fh):
                counts[r["date"]] = counts.get(r["date"], 0) + 1
    return [counts[d] for d in sorted(counts)[-n:]]


def validate_day(rows: list[dict], typical: float) -> str | None:
    """Return a reason to REJECT a freshly downloaded day, or None if it looks sane."""
    if len(rows) < max(50, 0.6 * typical):
        return f"only {len(rows)} stocks (normally ~{typical:.0f}): looks incomplete"
    bad = [r["symbol"] for r in rows if not (r.get("close", 0) > 0 and r.get("vwap", 0) > 0 and r.get("volume", 0) > 0)]
    if len(bad) > 0.05 * len(rows):
        return f"{len(bad)} stocks have missing price/volume fields"
    moves = [abs(r["close"] / r["prev_close"] - 1) for r in rows if r.get("prev_close", 0) > 0]
    wild = sum(m > 0.12 for m in moves)       # beyond the 10% circuit: only new listings / adjustments
    if wild > 0.1 * len(rows):
        return f"{wild} stocks moved more than the 10% circuit allows"
    return None


def update_latest(now: dt.datetime | None = None) -> tuple[list[str], list[str]]:
    """Fetch trading days after the last stored one (used by the daily job).

    Safety: today's prices are only accepted after the market has closed (otherwise a
    run during trading hours would store half-day prices as the final close), and each
    new day must pass `validate_day`. Returns (stored_dates, notes)."""
    now = now or dt.datetime.now(NPT)
    have = load_dates()
    last = dt.date.fromisoformat(max(have)) if have else now.date() - dt.timedelta(30)
    counts = day_counts()
    typical = sorted(counts)[len(counts) // 2] if counts else 0
    client, stored, notes = Client(), [], []
    d = last + dt.timedelta(1)
    while d <= now.date():
        if d == now.date() and now.time() < MARKET_CLOSE:
            notes.append(f"{d}: market still open ({now:%H:%M} NPT), not fetching today's prices yet")
            break
        rows = client.day(d.isoformat())
        if rows:
            why = validate_day(rows, typical)
            if why:
                notes.append(f"{d}: data rejected, {why}; will retry on the next run")
                break                      # never skip a day: later days depend on it
            append_rows(rows)
            stored.append(d.isoformat())
            print(f"  {d}: {len(rows)} stocks", flush=True)
        d += dt.timedelta(1)
    fetch_index((last - dt.timedelta(10)).isoformat())
    if stored and (not SECTORS.exists() or now.weekday() == 6):  # refresh on Sundays
        fetch_sectors()
    return stored, notes


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "latest"
    if cmd == "backfill":
        start = dt.date.fromisoformat(sys.argv[2])
        fetch_range(start, dt.date.today())
        fetch_index(start.isoformat())
        fetch_sectors(history=True)
    elif cmd == "sectors":
        fetch_sectors(history=True)
    else:
        print("new days: %s, notes: %s" % update_latest())
