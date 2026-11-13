"""Daily job: fetch new NEPSE data, advance the live paper portfolio,
rebuild the historical simulation, and publish JSON for the dashboard.

    python -m bot.run            # normal daily run (fetch + trade)
    python -m bot.run --no-fetch # use stored data only
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import fetch, research
from .engine import SIGNALS, Config, Engine, Market, metrics

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "live_state.json"
OUT = ROOT / "docs" / "data"
NPT = dt.timezone(dt.timedelta(hours=5, minutes=45))
BACKTEST_START = "2019-10-01"
SPLIT = research.DESIGN_END

# Measured once on identical data (2019-10 .. 2026-11), see the README research notes.
MODEL_HISTORY = [
    {"version": "v1", "date": "2026-11-07",
     "summary": "8 generic signals (momentum, sector rotation, ...), 10 stocks, 20-day review, NEPSE 50-day trend filter.",
     "design_pct": -30.2, "holdout_pct": -4.7},
    {"version": "v2", "date": "2026-11-08",
     "summary": "Rebuilt from 8 years of NEPSE research: anti closing-pressure, calm and non-spiky stocks, "
                "never-buy circuit filters, 12 stocks, 40-day review, bear-market-only exposure cut.",
     "design_pct": 59.5, "holdout_pct": 17.9},
]


def now_npt() -> str:
    return dt.datetime.now(NPT).strftime("%Y-%m-%d %H:%M NPT")


def dump(path: Path, obj):
    path.write_text(json.dumps(obj, separators=(",", ":"), default=float))


def public(state: dict, mkt: Market, log_days=None) -> dict:
    s = dict(state)
    s["metrics"] = metrics(state, SPLIT)
    s["positions"] = [{"sym": k, **v, "value": round(v["qty"] * v["last"], 2),
                       "pnl": round(v["qty"] * v["last"] - v["cost"], 2),
                       "pnl_pct": round((v["qty"] * v["last"] / v["cost"] - 1) * 100, 2),
                       "hist": [round(float(x), 2) for x in
                                mkt.close[k].iloc[:mkt.didx[state["last_date"]] + 1].dropna().tail(30)]}
                      for k, v in state["positions"].items()]
    if log_days:
        s["days"] = s["days"][-log_days:]
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    run_log = [f"{now_npt()} — job started"]
    new_days = []
    if "--no-fetch" not in sys.argv:
        try:
            new_days = fetch.update_latest()
            run_log.append(f"fetched {len(new_days)} new trading day(s): {', '.join(new_days) or '—'}")
        except Exception as e:  # keep the site alive even if the source is down
            run_log.append(f"data fetch failed: {e}")

    mkt = Market()
    latest = mkt.dates[-1]

    # ---------------- live paper portfolio
    if STATE.exists():
        state = json.loads(STATE.read_text())
    else:
        state = None
    start = None
    if state is not None and state.get("model") != "v2":
        if not any(t.get("status") == "FILLED" for t in state["trades"]):
            # upgrade before any real (paper) trade happened: restart v2 from the same day
            run_log.append(f"model upgraded to v2; live portfolio restarted from {state['inception']} "
                           "(no trades had executed yet)")
            start, state = state["inception"], None
        else:
            state["model"] = "v2"
            run_log.append("model upgraded to v2; existing live holdings kept")
    live = Engine(mkt, Config(), state)
    if state is None and start:
        live.s["started_at"] = now_npt()
        todo = [d for d in mkt.dates if d >= start]
    elif state is None:
        # the live portfolio starts on the most recent trading day
        live.s["started_at"] = now_npt()
        todo = [latest]
    else:
        todo = [d for d in mkt.dates if d > live.s["last_date"]]
    for d in todo:
        live.run_day(d)
        run_log.append(f"live portfolio advanced to {d}")
    if not todo:
        run_log.append(f"no new trading day since {latest} (weekend / holiday / market not closed yet)")
    STATE.write_text(json.dumps(live.s, separators=(",", ":"), default=float))

    # ---------------- historical simulation (same engine, walk-forward)
    bt = Engine(mkt, Config(), log_days=90)
    for d in mkt.dates:
        if d >= BACKTEST_START:
            bt.run_day(d)
    bts = public(bt.s, mkt)
    bts["weight_history"] = bt.s.get("weight_history", [])[::5]
    bts["equity"] = bt.s["equity"][::2] + ([bt.s["equity"][-1]] if len(bt.s["equity"]) % 2 == 0 else [])

    rpath = OUT / "research.json"
    # Age = days between the data the research covers and the newest market data.
    # (File modification times are useless here: a fresh git checkout resets them.)
    try:
        covered_to = json.loads(rpath.read_text())["period"][1]
        age = (dt.date.fromisoformat(latest) - dt.date.fromisoformat(covered_to)).days
    except (OSError, ValueError, KeyError, IndexError):
        age = 99
    if new_days and age > 6 or not rpath.exists():  # weekly refresh of the pattern research
        research.main()
        run_log.append("NEPSE pattern research refreshed")

    dump(OUT / "live.json", public(live.s, mkt))
    dump(OUT / "backtest.json", bts)
    dump(OUT / "meta.json", {
        "updated": now_npt(), "latest_market_date": latest, "first_data_date": mkt.dates[0],
        "trading_days_stored": len(mkt.dates), "symbols": len(mkt.symbols),
        "signals": {k: {"name": v[0], "desc": v[1]} for k, v in SIGNALS.items()},
        "config": Config().__dict__, "run_log": run_log, "backtest_start": BACKTEST_START,
        "split": SPLIT, "model_history": MODEL_HISTORY,
    })
    print("\n".join(run_log))
    lm = live.s and metrics(live.s)
    print("live:", lm.get("equity"), "| backtest:", bts["metrics"])


if __name__ == "__main__":
    main()
