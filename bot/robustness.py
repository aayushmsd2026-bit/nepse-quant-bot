"""Robustness report for the current model:  python -m bot.robustness   (~10 minutes)

1. Start-date luck: the same model started on 4 different days.
2. Sensitivity: every important setting nudged, on the DESIGN period only
   (this is how the settings were chosen; a good model sits on a plateau).
3. Hold-out: the frozen model on data it never saw, for each start date.
4. Significance: is the monthly edge over NEPSE distinguishable from luck?

Writes docs/data/robustness.json for the dashboard.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import warnings
from pathlib import Path

import pandas as pd

from . import engine as E
from . import research

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "robustness.json"
STARTS = ["2019-10-01", "2019-10-24", "2019-11-17", "2019-12-10"]
MID, SPLIT = "2021-12-31", research.DESIGN_END
M: E.Market | None = None

SENSITIVITY = [
    ("Chosen settings", {}),
    ("Review every 40 days", {"review_every": 40}),
    ("Review every 90 days", {"review_every": 90}),
    ("Review every 120 days", {"review_every": 120}),
    ("12 stocks", {"max_positions": 12, "max_weight": 0.11}),
    ("20 stocks", {"max_positions": 20, "max_weight": 0.065}),
    ("Keep only top 35%", {"keep_top_pct": 0.35}),
    ("Keep top 65%", {"keep_top_pct": 0.65}),
    ("Buy from top 10%", {"buy_top_pct": 0.10}),
    ("Buy from top 25%", {"buy_top_pct": 0.25}),
    ("Trading costs doubled", {"half_spread": 0.003, "impact_coef": 2.0}),
    ("With 15% stop-loss + 22% trailing", {"stop_loss": 0.15, "trailing_stop": 0.22}),
    ("With bear-market cash filter", {"bear_gross": 0.60}),
    ("Weights half re-learned each day", {"prior_mix": 0.5}),
    ("No lower-circuit exit", {"lower_circuit_exit": False}),
    ("No post-bonus veto", {"postadj_veto_days": 0}),
]


def bootstrap_ci(x, n=3000, block=3, seed=7):
    """90% confidence interval for the yearly edge (mean monthly excess x 12), by moving-block
    bootstrap (blocks of 3 months keep short-run streaks together)."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    if len(x) < 12:
        return None
    rng = np.random.default_rng(seed)
    k = int(np.ceil(len(x) / block))
    starts = rng.integers(0, len(x) - block + 1, size=(n, k))
    sims = np.array([np.concatenate([x[s:s + block] for s in row])[:len(x)].mean() for row in starts]) * 12 * 100
    lo, hi = np.percentile(sims, [5, 95])
    return {"edge_yr": round(float(x.mean() * 12 * 100), 1), "lo": round(float(lo), 1), "hi": round(float(hi), 1),
            "p_positive": round(float((sims > 0).mean()) * 100), "months": int(len(x))}


def _run(args):
    label, kw, start, end = args
    e = E.Engine(M, E.Config(**kw), log_days=2)
    for d in M.dates:
        if start <= d <= end:
            e.run_day(d)
    eq = pd.DataFrame(e.s["equity"])
    seg = lambda a, b: E._seg(eq[(eq.date >= a) & (eq.date <= b)], 5e5)
    if start > SPLIT:                      # a fresh portfolio started inside the hold-out
        H = seg(start, end)
        return {"label": label, "start": start, "holdout": H["return_pct"], "holdout_nepse": H["nepse_return_pct"],
                "holdout_ew": H["ew_return_pct"], "holdout_dd": H["max_drawdown_pct"],
                "holdout_nepse_dd": H["nepse_drawdown_pct"], "holdout_sharpe": H["sharpe"],
                "holdout_nepse_sharpe": H["nepse_sharpe"],
                "invested": round(float((eq.invested / eq.equity).iloc[30:].mean()) * 100),
                "costs": round(sum(e.s["fees"].values()))}
    D, A, B = seg(start, SPLIT), seg(start, MID), seg(MID, SPLIT)
    sells = [t for t in e.s["trades"] if t.get("status") == "FILLED" and t["side"] == "SELL"]
    out = {"label": label, "start": start, "design": D["return_pct"], "design_nepse": D["nepse_return_pct"],
           "design_ew": D["ew_return_pct"], "dd": D["max_drawdown_pct"], "sharpe": D["sharpe"],
           "h1x": round(A["return_pct"] - A["nepse_return_pct"], 1),
           "h2x": round(B["return_pct"] - B["nepse_return_pct"], 1),
           "trades": len(sells), "costs": round(sum(e.s["fees"].values()))}
    if end > SPLIT:
        H = seg(SPLIT, end)
        out.update(holdout=H["return_pct"], holdout_nepse=H["nepse_return_pct"], holdout_ew=H["ew_return_pct"],
                   holdout_dd=H["max_drawdown_pct"], holdout_nepse_dd=H["nepse_drawdown_pct"],
                   holdout_sharpe=H["sharpe"], holdout_nepse_sharpe=H["nepse_sharpe"])
        m = eq.assign(month=eq.date.str[:7]).groupby("month").agg(e=("equity", "last"), n=("nepse", "last"))
        x = (m.e.pct_change() - m.n.pct_change()).dropna()
        mm = eq.assign(month=eq.date.str[:7]).groupby("month").agg(e=("equity", "last"), n=("nepse", "last"), w=("ew", "last"))
        for bench, col in (("nepse", "n"), ("avg_stock", "w")):
            xs = (mm.e.pct_change() - mm[col].pct_change()).dropna()
            for name, sl in (("all", xs), ("holdout", xs[xs.index > SPLIT[:7]])):
                out[f"ci_{bench}_{name}"] = bootstrap_ci(sl.values)
        for name, sl in (("all", x), ("holdout", x[x.index > SPLIT[:7]])):
            t = sl.mean() / (sl.std() / math.sqrt(len(sl))) if len(sl) > 5 and sl.std() > 0 else 0
            out[f"edge_{name}"] = {"monthly_pct": round(float(sl.mean()) * 100, 2), "t": round(float(t), 2),
                                   "months": int(len(sl)), "win_months_pct": round(float((sl > 0).mean()) * 100)}
    return out


def main(procs: int = 4):
    global M
    M = E.Market()
    last = M.dates[-1]
    jobs = [(l, k, st, SPLIT) for l, k in SENSITIVITY for st in STARTS]
    jobs += [("Frozen model", {}, st, last) for st in STARTS]
    fresh_start = next(d for d in M.dates if d > SPLIT)
    jobs += [("Fresh start", {}, fresh_start, last)]
    with mp.get_context("fork").Pool(procs) as pool:
        res = pool.map(_run, jobs)
    fresh = next(r for r in res if r["label"] == "Fresh start")
    res = [r for r in res if r["label"] != "Fresh start"]
    df = pd.DataFrame([r for r in res if r["label"] != "Frozen model"])
    sens = []
    for label, _ in SENSITIVITY:
        x = df[df.label == label]
        sens.append({"label": label, "avg": round(x.design.mean(), 1), "min": round(x.design.min(), 1),
                     "max": round(x.design.max(), 1), "dd": round(x.dd.mean(), 1), "sharpe": round(x.sharpe.mean(), 2),
                     "h1x": round(x.h1x.mean(), 1), "h2x": round(x.h2x.mean(), 1),
                     "trades": round(x.trades.mean()), "costs": round(x.costs.mean())})
    frozen = [r for r in res if r["label"] == "Frozen model"]
    out = {"model": E.MODEL_VERSION, "as_of": last, "split": SPLIT, "starts": frozen, "sensitivity": sens,
           "design_nepse": round(df.design_nepse.mean(), 1), "design_ew": round(df.design_ew.mean(), 1),
           "fresh": fresh}
    print("fresh Rs 5 lakh start on the hold-out:", fresh)
    for r in frozen:
        print(r["start"], "CI vs NEPSE hold-out", r.get("ci_nepse_holdout"), "| vs avg stock hold-out", r.get("ci_avg_stock_holdout"),
              "| vs NEPSE all", r.get("ci_nepse_all"))
    OUT.write_text(json.dumps(out, separators=(",", ":"), default=float))
    print(f"NEPSE design {out['design_nepse']}%  avg stock {out['design_ew']}%")
    for s in sens:
        print(f"{s['label']:36s} design avg {s['avg']:6.1f} ({s['min']:.0f}..{s['max']:.0f}) dd {s['dd']:6.1f} sharpe {s['sharpe']}")
    for r in frozen:
        print(f"start {r['start']}: design {r['design']:.1f} | hold-out {r['holdout']:.1f} vs NEPSE {r['holdout_nepse']:.1f} "
              f"avg stock {r['holdout_ew']:.1f} | edge/month all {r['edge_all']} holdout {r['edge_holdout']}")


if __name__ == "__main__":
    main()
