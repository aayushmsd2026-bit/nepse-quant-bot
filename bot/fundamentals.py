"""Point-in-time fundamentals collector (weekly).

ShareSansar shows each company's LATEST quarterly report only (EPS, net worth per
share, P/E, P/B, ROE, ROA; for banks also NPL, cost of funds, base rate, credit-to-
deposit ratio). History is not published, so the bot saves a snapshot every week and
records the first date it saw each new quarter. That date is when the information was
public, so future backtests of value / quality / earnings-surprise signals will be
free of look-ahead bias. Bank cost-of-funds and base rates also build an interest-rate
series for NEPSE's famous rate sensitivity.

    python -m bot.fundamentals          # take a snapshot now
"""
from __future__ import annotations

import csv
import datetime as dt
import html as H
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .fetch import BASE, Client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fundamentals.csv"
CHECKED = ROOT / "data" / "fundamentals_checked.txt"   # date of the last full check
NPT = dt.timezone(dt.timedelta(hours=5, minutes=45))
# (column, regex on the lower-cased label); labels differ by sector, so match patterns.
RULES = [
    ("eps_annualized", r"(earning[s]? per share|\beps\b)(?!.*diluted)"),
    ("networth_ps", r"net ?worth per share"),
    ("pe", r"p/e|price to earning|price earning"),
    ("pb", r"price to book|\bp/b\b|pb ratio"),
    ("roe", r"return on equity"),
    ("roa", r"return on asset"),
    ("npl", r"non-performing"),
    ("cost_of_funds", r"cost of fund"),
    ("base_rate", r"base rate"),
    ("cd_ratio", r"credit to deposit"),
    ("spread", r"spread"),
    ("capital_rwa", r"capital fund to rwa"),
    ("shares", r"outstanding shares"),
]
METRICS = {k: k for k, _ in RULES}
FIELDS = ["symbol", "quarter", "first_seen", *dict.fromkeys(METRICS.values())]


def _num(v: str):
    v = re.sub(r"[^0-9.\-]", "", v or "")
    try:
        return float(v) if v not in ("", "-", ".") else None
    except ValueError:
        return None


def snapshot(c: Client, sym: str) -> dict | None:
    page = c._get(f"{BASE}/company/{sym.lower()}").text
    cid = re.search(r'id="companyid"[^>]*>(\d+)<', page)
    sec = re.search(r'id="sector"[^>]*>([^<]*)<', page)
    tok = re.search(r'name="_token" content="([^"]+)"', page)
    if not (cid and tok):
        return None
    r = c.s.post(f"{BASE}/company-quarterly-report",
                 data={"company": cid.group(1), "symbol": sym, "sector": sec.group(1) if sec else ""},
                 headers={"X-CSRF-Token": tok.group(1), "X-Requested-With": "XMLHttpRequest",
                          "Referer": f"{BASE}/company/{sym.lower()}"}, timeout=40)
    if r.status_code != 200 or "Particulars" not in r.text:
        return None
    cells = [H.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r.text, re.S)]
    q = next((x for x in cells if re.search(r"Quarter\s+\d{4}/\d{4}", x)), None)
    row = {"symbol": sym, "quarter": q}
    for i, x in enumerate(cells[:-1]):
        lab = re.sub(r"\s+", " ", x).lower()
        if "diluted" in lab or "retained" in lab or len(lab) > 90:
            continue
        for key, rx in RULES:
            if row.get(key) is None and re.search(rx, lab):
                v = _num(cells[i + 1])
                if v is not None:
                    row[key] = v
                break
    return row if q and any(row.get(k) is not None for k in ("eps_annualized", "networth_ps", "pe")) else None


def load() -> list[dict]:
    if not OUT.exists():
        return []
    with OUT.open() as f:
        return list(csv.DictReader(f))


def update(symbols: list[str], workers: int = 3) -> str:
    """Append a row whenever a company shows a quarter we have not stored yet."""
    seen = {(r["symbol"], r["quarter"]) for r in load()}
    today = dt.datetime.now(NPT).date().isoformat()
    clients = [Client() for _ in range(workers)]

    def job(a):
        try:
            return snapshot(clients[a[0] % workers], a[1])
        except Exception:
            return None

    with ThreadPoolExecutor(workers) as ex:
        rows = [r for r in ex.map(job, enumerate(symbols)) if r]
    new = [dict(r, first_seen=today) for r in rows if (r["symbol"], r["quarter"]) not in seen]
    exists = OUT.exists()
    with OUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerows(sorted(new, key=lambda r: r["symbol"]))
    CHECKED.write_text(today + "\n")
    return f"fundamentals: {len(rows)} companies checked, {len(new)} new quarterly reports saved"


def last_snapshot_date() -> str | None:
    if CHECKED.exists():
        return CHECKED.read_text().strip() or None
    rows = load()
    return max((r["first_seen"] for r in rows), default=None)


if __name__ == "__main__":
    from . import research
    M = research.load()
    d = M["dates"][-1]
    uni = research.universe_mask(M).loc[d]
    syms = [s for s in M["close"].columns if bool(M["traded"].at[d, s])]
    print(update(syms))
