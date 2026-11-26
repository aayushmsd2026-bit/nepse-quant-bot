"""Daily job: fetch new NEPSE data, advance the live paper portfolio,
rebuild the historical simulation, and publish JSON for the dashboard.

    python -m bot.run            # normal daily run (fetch + trade)
    python -m bot.run --no-fetch # use stored data only

Reliability rules: the live state is only replaced after the whole day has been
processed successfully (written to a temp file first), and any crash exits
non-zero so the GitHub Actions run shows up red instead of failing silently.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import warnings
from pathlib import Path

from . import events, fetch, floorsheet, research, shortterm
from .engine import MODEL_VERSION, PORTFOLIOS, SIGNALS, Config, Engine, Market, config_for, metrics

warnings.filterwarnings("ignore", category=RuntimeWarning)
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "live_state.json"          # Rs 5 lakh portfolio (original file names kept)


def files_for(pid: str) -> tuple[Path, str, str]:
    """(state file, live json name, backtest json name) for a portfolio id."""
    if pid == "500k":
        return STATE, "live.json", "backtest.json"
    return ROOT / "data" / f"live_state_{pid}.json", f"live_{pid}.json", f"backtest_{pid}.json"
OUT = ROOT / "docs" / "data"
NPT = dt.timezone(dt.timedelta(hours=5, minutes=45))
BACKTEST_START = "2019-10-01"
SPLIT = research.DESIGN_END

# Earlier versions, measured on the same 7 years of data. The current version's
# numbers are filled in from the live historical simulation on every run.
MODEL_HISTORY = [
    {"version": "v1", "date": "2026-11-07",
     "summary": "8 generic signals (momentum, sector rotation, ...), 10 stocks, 20-day review, NEPSE 50-day trend filter.",
     "design_pct": -30.2, "holdout_pct": -4.7},
    {"version": "v2", "date": "2026-11-08",
     "summary": "First NEPSE-specific model: anti closing-pressure, calm stocks, circuit filters, 12 stocks, "
                "40-day review, stop-losses, bear-market cash filter.",
     "design_pct": 59.5, "holdout_pct": 17.9},
    {"version": "v3", "date": "2026-11-15",
     "summary": "Built around NEPSE's strongest pattern: crowded, heavily traded stocks lag and quiet ones lead. "
                "16 stocks, each reviewed every 60 days, no stop-losses (they destroyed value), no market timing, "
                "realistic market-impact costs, fixed research weights.",
     "design_pct": None, "holdout_pct": None},
]


def now_npt() -> str:
    return dt.datetime.now(NPT).strftime("%Y-%m-%d %H:%M NPT")


def dump(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=float))
    os.replace(tmp, path)               # atomic: never leaves a half-written file


def public(state: dict, mkt: Market, log_days=None) -> dict:
    s = dict(state)
    s["metrics"] = metrics(state, SPLIT)
    s["positions"] = [{"sym": k, **v, "value": round(v["qty"] * v["last"], 2),
                       "pnl": round(v["qty"] * v["last"] - v["cost"], 2),
                       "pnl_pct": round((v["qty"] * v["last"] / v["cost"] - 1) * 100, 2),
                       "review_in": max(0, v["review_on"] - mkt.didx[state["last_date"]]) if "review_on" in v else None,
                       "hist": [round(float(x), 2) for x in
                                mkt.close[k].iloc[:mkt.didx[state["last_date"]] + 1].dropna().tail(30)]}
                      for k, v in state["positions"].items()]
    s["pending"] = [o for o in state["pending"] if not o.get("quiet")]
    s.pop("weight_history", None)
    if log_days:
        s["days"] = s["days"][-log_days:]
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    run_log = [f"{now_npt()} — job started"]
    new_days = []
    if "--no-fetch" not in sys.argv:
        try:
            new_days, notes = fetch.update_latest()
            run_log.append(f"fetched {len(new_days)} new trading day(s): {', '.join(new_days) or '—'}")
            run_log += notes
        except Exception as e:  # keep the site alive even if the source is down
            run_log.append(f"data fetch failed: {e}")

    mkt = Market()
    latest = mkt.dates[-1]

    summary, bms = {}, {}
    for pid in PORTFOLIOS:
        st_path, live_name, bt_name = files_for(pid)
        cfg = config_for(pid)
        tag = PORTFOLIOS[pid]["name"]

        # ---------------- live paper portfolio
        state = json.loads(st_path.read_text()) if st_path.exists() else None
        if state is not None and state.get("model") != MODEL_VERSION:
            old = state.get("model", "v1")
            state.setdefault("model_log", []).append(
                {"date": latest, "from": old, "to": MODEL_VERSION,
                 "note": "existing holdings kept and reviewed gradually under the new rules"})
            state["model"] = MODEL_VERSION
            run_log.append(f"{tag}: model upgraded {old} → {MODEL_VERSION}; existing live holdings kept")
        live = Engine(mkt, cfg, state)
        if state is None:
            live.s["started_at"] = now_npt()   # a brand-new portfolio starts on the latest trading day
            todo = [latest]
            run_log.append(f"{tag}: new live portfolio started on {latest}")
        else:
            todo = [d for d in mkt.dates if d > live.s["last_date"]]
        for d in todo:
            live.run_day(d)
            run_log.append(f"{tag} live portfolio advanced to {d}")
        if not todo:
            run_log.append(f"{tag}: no new trading day since {latest} (weekend / holiday / market not closed yet)")
        live.s["health_history"] = live.s.get("health_history", [])[-400:]
        live.s["portfolio"] = {"id": pid, "name": tag}
        dump(st_path, live.s)

        # ---------------- historical simulation (same engine, walk-forward)
        bt = Engine(mkt, cfg, log_days=90)
        for d in mkt.dates:
            if d >= BACKTEST_START:
                bt.run_day(d)
        bts = public(bt.s, mkt)
        bts["portfolio"] = {"id": pid, "name": tag}
        bts["health_history"] = bt.s.get("health_history", [])[::5]
        bts["equity"] = bt.s["equity"][::2] + ([bt.s["equity"][-1]] if len(bt.s["equity"]) % 2 == 0 else [])
        dump(OUT / live_name, public(live.s, mkt))
        dump(OUT / bt_name, bts)
        bms[pid] = bts["metrics"]
        lm = metrics(live.s)
        summary[pid] = (f"{tag}: live Rs {lm.get('equity')}, {len(live.s['positions'])} holdings | historical "
                        f"{bms[pid]['return_pct']}% (NEPSE {bms[pid]['nepse_return_pct']}%), design "
                        f"{bms[pid].get('design', {}).get('return_pct')}%, hold-out {bms[pid].get('holdout', {}).get('return_pct')}%")

    # ---------------- extra data + short-term study (never allowed to break the daily run)
    if new_days and latest in new_days:
        try:
            d = latest
            syms = [x for x in mkt.symbols if bool(mkt.traded.at[d, x])]
            run_log.append(floorsheet.collect(d, syms))
        except Exception as e:
            run_log.append(f"floorsheet collection failed: {e}")
    try:
        div = events.OUT
        stale = not div.exists() or (dt.datetime.now(NPT).weekday() == 6 and new_days)   # refresh weekly (Sundays)
        if stale and "--no-fetch" not in sys.argv:
            events.update()
            run_log.append("dividend / book-closure calendar refreshed")
    except Exception as e:
        run_log.append(f"dividend calendar refresh failed: {e}")
    try:
        shortterm.main()
        run_log.append("short-term radar updated")
    except Exception as e:
        run_log.append(f"short-term study failed: {e}")

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

    history = [dict(h) for h in MODEL_HISTORY]
    bm = bms["500k"]
    history[-1]["design_pct"] = bm.get("design", {}).get("return_pct")
    history[-1]["holdout_pct"] = bm.get("holdout", {}).get("return_pct")
    dump(OUT / "meta.json", {
        "updated": now_npt(), "latest_market_date": latest, "first_data_date": mkt.dates[0],
        "trading_days_stored": len(mkt.dates), "symbols": len(mkt.symbols), "model": MODEL_VERSION,
        "signals": {k: {"name": v[0], "desc": v[1], "prior": v[3]} for k, v in SIGNALS.items()},
        "config": Config().__dict__, "run_log": run_log, "backtest_start": BACKTEST_START,
        "split": SPLIT, "model_history": history,
        "portfolios": [{"id": pid, "name": v["name"], "files": list(files_for(pid)[1:]),
                        "config": config_for(pid).__dict__} for pid, v in PORTFOLIOS.items()],
    })
    print("\n".join(run_log))
    for line in summary.values():
        print(line)


if __name__ == "__main__":
    main()
