"""Technical-chart "swing picks" for the next 2-3 weeks, with an honest backtest.

    python -m bot.technical        # prints the study, writes docs/data/technical.json

A chart analyst's toolkit, written as exact rules on adjusted daily prices:
moving averages (20/50/200), MACD, RSI, ADX/DMI, Bollinger squeeze breakouts,
pullbacks in uptrends, higher lows, 52-week highs, relative strength vs NEPSE,
volume confirmation.

Backtest = a real retail trade: signal at Friday's (last weekly) close, buy at the
next session's VWAP (impossible if locked at the upper circuit), sell at VWAP 10 or
15 trading days later (2 / 3 weeks), ~1.4% round-trip costs + 7.5% tax on profit.
"Correct" is reported three ways: the stock went up after costs; it beat NEPSE;
and how that compares with picking liquid stocks at random on the same days.
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import research

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "technical.json"
LOG = Path(__file__).resolve().parent.parent / "data" / "swing_log.json"   # live, forward-tested predictions
LOG_HOLD = 15                                                              # each prediction is judged after 3 weeks
SPLIT = research.DESIGN_END
COST, CGT = 0.014, 0.075
HORIZONS = (10, 15)
TOP_N = 5


# ----------------------------------------------------------------- indicators
def indicators(M):
    adj = np.exp(M["lp"]) / M["close"]
    traded = M["traded"]
    C = (M["close"] * adj).where(traded).ffill()
    H = (M["high"] * adj).where(traded).ffill()
    L = (M["low"] * adj).where(traded).ffill()
    V = M["turnover"].fillna(0)
    I = {"C": C, "H": H, "L": L}
    for n in (20, 50, 200):
        I[f"sma{n}"] = C.rolling(n, min_periods=int(n * 0.8)).mean()
    ema = lambda x, n: x.ewm(span=n, adjust=False, min_periods=n).mean()
    I["macd"] = ema(C, 12) - ema(C, 26)
    I["macds"] = ema(I["macd"], 9)
    d = C.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    rs = up.ewm(alpha=1 / 14, adjust=False).mean() / dn.ewm(alpha=1 / 14, adjust=False).mean()
    I["rsi"] = 100 - 100 / (1 + rs)
    tr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max().reindex(C.index)
    I["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdm = (H.diff()).where((H.diff() > -L.diff()) & (H.diff() > 0), 0.0)
    ndm = (-L.diff()).where((-L.diff() > H.diff()) & (-L.diff() > 0), 0.0)
    atr = I["atr"].replace(0, np.nan)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    I["pdi"], I["ndi"] = pdi, ndi
    I["adx"] = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)).ewm(alpha=1 / 14, adjust=False).mean()
    bbw = 4 * C.rolling(20).std() / I["sma20"]
    I["squeeze"] = bbw <= bbw.rolling(120, min_periods=60).quantile(0.25)
    I["hi20"] = H.shift(1).rolling(20).max()
    I["hi252"] = H.rolling(250, min_periods=120).max()
    I["v5"], I["v20"] = V.rolling(5).mean(), V.rolling(20).mean()
    nep = np.log(M["nepse"])
    lc = np.log(C)
    I["rs60"] = (lc - lc.shift(60)).sub(nep - nep.shift(60), axis=0)
    I["rs20"] = (lc - lc.shift(20)).sub(nep - nep.shift(20), axis=0)
    lo10 = L.rolling(10).min()
    I["higher_lows"] = (lo10 > lo10.shift(10)) & (lo10.shift(10) > lo10.shift(20))
    return I


def setups(I, uni):
    C, R = I["C"], I["rsi"]
    up_trend = (I["sma50"] > I["sma200"])
    rs_rank = I["rs60"].where(uni).rank(axis=1, pct=True)
    vol_up = I["v5"] > I["v20"]
    S = {
        "stack": ("Moving averages stacked", "Price > 20-day > 50-day > 200-day average: a textbook uptrend.",
                  (C > I["sma20"]) & (I["sma20"] > I["sma50"]) & up_trend),
        "golden": ("Golden cross", "50-day average crossed above the 200-day in the last 5 days.",
                   up_trend & ~(I["sma50"].shift(5) > I["sma200"].shift(5))),
        "macd": ("MACD bullish cross", "MACD crossed above its signal line in the last 3 days, price above the 50-day.",
                 (I["macd"] > I["macds"]) & ~(I["macd"].shift(3) > I["macds"].shift(3)) & (C > I["sma50"])),
        "rsi": ("RSI momentum zone", "RSI between 55 and 70: strong but not overbought, in an uptrend.",
                (R > 55) & (R < 70) & up_trend),
        "oversold": ("RSI oversold bounce", "RSI climbed back above 30 after being oversold.",
                     (R > 30) & (R.shift(1) <= 30)),
        "squeeze": ("Squeeze breakout", "Broke above its 20-day high after a tight, quiet range, on rising volume.",
                    (C > I["hi20"]) & I["squeeze"].shift(1).fillna(False) & vol_up),
        "pullback": ("Pullback in uptrend", "Uptrend intact, price dipped to the 20-day average and held (RSI 40-55).",
                     up_trend & (C > I["sma50"]) & (I["L"] <= I["sma20"]) & (C >= I["sma20"]) & (R > 40) & (R < 55)),
        "higherlows": ("Higher lows", "Each 10-day low higher than the one before, price above the 50-day.",
                       I["higher_lows"] & (C > I["sma50"])),
        "adx": ("Strong trend (ADX)", "ADX above 25 with buyers in control (+DI > -DI).",
                (I["adx"] > 25) & (I["pdi"] > I["ndi"])),
        "high52": ("Near 52-week high", "Within 3% of its 1-year high, above the 200-day average.",
                   (C >= 0.97 * I["hi252"]) & (C > I["sma200"])),
        "relstr": ("Relative strength leader", "Top 20% vs NEPSE over 3 months, above the 50-day average.",
                   (rs_rank >= 0.8) & (C > I["sma50"])),
    }
    # The "chart expert" checklist: one point per bullish condition, picks need 6+ of 8
    pts = sum(x.astype(int) for x in [C > I["sma50"], up_trend, C > I["sma20"], I["macd"] > I["macds"],
                                      (R > 50) & (R < 70), (I["adx"] > 20) & (I["pdi"] > I["ndi"]),
                                      rs_rank >= 0.7, vol_up])
    return S, pts, rs_rank


# ----------------------------------------------------------------- backtest helpers
def net(g):
    after = (1 + g) * (1 - COST) - 1
    return after - CGT * after.clip(lower=0)


def forward(M, h):
    adj = np.exp(M["lp"]) / M["close"]
    av = (M["vwap"] * adj).where(M["traded"])
    locked = (M["ret_raw"] >= 0.095) & (M["close"] >= M["high"] * 0.999)
    g = av.shift(-(1 + h)) / av.shift(-1) - 1
    return g.where(~locked.shift(-1, fill_value=False))


def week_ends(dates):
    """Last trading day of each calendar week (signal days)."""
    s = pd.Series(pd.to_datetime(dates), index=dates)
    wk = s.dt.isocalendar().week.astype(str) + "-" + s.dt.isocalendar().year.astype(str)
    return list(s.groupby(wk.values).apply(lambda x: x.index[-1]).sort_values())


def score_frame(I, pts, rs_rank):
    """Rank for picking: checklist points, then relative strength."""
    return pts + rs_rank.fillna(0)


def evaluate(events, gross, base_up, base_beat, base_net, nepse_ret, days):
    """events: boolean frame of picks on signal days. -> stats dict per period."""
    out = {}
    for per, sl in (("design", lambda d: d <= SPLIT), ("holdout", lambda d: d > SPLIT)):
        rows = []
        for d in days:
            if not sl(d):
                continue
            e = events.loc[d]
            syms = list(e[e].index)
            g = gross.loc[d, syms].dropna()
            if g.empty:
                continue
            for s, x in g.items():
                rows.append((d, s, x, nepse_ret.get(d, np.nan)))
        if len(rows) < 20:
            out[per] = {"n": len(rows)}
            continue
        T = pd.DataFrame(rows, columns=["d", "s", "g", "nep"]).dropna()
        nr = net(T.g)
        beat = (T.g > T.nep)
        dd = T.assign(nr=nr).groupby("d").nr.mean()
        bb = pd.Series({d: base_net.get(d) for d in dd.index}).astype(float)
        diff = (dd - bb).dropna()
        t = diff.mean() / (diff.std() / math.sqrt(len(diff))) if len(diff) > 5 and diff.std() > 0 else 0
        out[per] = {"n": int(len(T)), "weeks": int(T.d.nunique()),
                    "up_pct": round(float((nr > 0).mean()) * 100, 1),
                    "beat_pct": round(float(beat.mean()) * 100, 1),
                    "avg_net": round(float(nr.mean()) * 100, 2), "median_net": round(float(nr.median()) * 100, 2),
                    "big_win_pct": round(float((nr > 0.10).mean()) * 100, 1),
                    "big_loss_pct": round(float((nr < -0.10).mean()) * 100, 1),
                    "base_up_pct": round(float(np.nanmean([base_up.get(d) for d in T.d.unique()])) * 100, 1),
                    "base_beat_pct": round(float(np.nanmean([base_beat.get(d) for d in T.d.unique()])) * 100, 1),
                    "base_net": round(float(np.nanmean([base_net.get(d) for d in T.d.unique()])) * 100, 2),
                    "vs_random_t": round(float(t), 2)}
    return out


def verdict(r):
    d, o = r.get("design", {}), r.get("holdout", {})
    if d.get("n", 0) < 50 or o.get("n", 0) < 20:
        return "too few"
    dd, oo = d["avg_net"] - d["base_net"], o["avg_net"] - o["base_net"]
    if dd > 0.5 and oo > 0.5 and d["vs_random_t"] > 2:
        return "edge"
    if dd < -0.5 and oo < -0.5:
        return "worse than random"
    return "same as random"


def update_log(M, picks_date, picks):
    """Forward test. On the first trading day of each new week, save the top-5 picks from the
    previous close. Each prediction is scored after LOG_HOLD trading days: buy at the next
    session's VWAP, sell at VWAP 15 trading days later, after costs and tax. Never edited
    afterwards, so the scoreboard cannot be cherry-picked."""
    log = json.loads(LOG.read_text()) if LOG.exists() else {"weeks": []}
    dates = M["dates"]
    pos = {d: i for i, d in enumerate(dates)}
    wk = lambda d: tuple(pd.Timestamp(d).isocalendar()[:2])
    if picks and (not log["weeks"] or wk(log["weeks"][-1]["signal"]) != wk(picks_date)):
        log["weeks"].append({"signal": picks_date, "picks": [{"sym": p["sym"], "close": p["close"],
                                                               "points": p["points"]} for p in picks[:TOP_N]]})
    adj = np.exp(M["lp"]) / M["close"]
    av = (M["vwap"] * adj).where(M["traded"])
    nep = M["nepse"]
    for w in log["weeks"]:
        i = pos.get(w["signal"])
        if i is None or w.get("done"):
            continue
        ie, ix = i + 1, i + 1 + LOG_HOLD
        if ix >= len(dates):
            w["days_left"] = ix - (len(dates) - 1)
            w["status"] = f"open: judged in {w['days_left']} trading day{'s' if w['days_left'] != 1 else ''}"
            continue
        nret = float(nep.iloc[ix] / nep.iloc[ie] - 1)
        for p in w["picks"]:
            a, b = av[p["sym"]].iloc[ie], av[p["sym"]].iloc[ix:ix + 3].dropna()
            if pd.isna(a) or b.empty:
                p["result"] = None
                continue
            g = float(b.iloc[0] / a - 1)
            p["gross"] = round(g * 100, 2)
            p["net"] = round(float(net(pd.Series([g]))[0]) * 100, 2)
            p["beat_nepse"] = bool(g > nret)
        w.update(done=True, entry=dates[ie], exit=dates[ix], nepse=round(nret * 100, 2), status="judged")
    LOG.write_text(json.dumps(log, indent=1))
    judged = [p for w in log["weeks"] if w.get("done") for p in w["picks"] if p.get("net") is not None]
    return {"weeks": log["weeks"][-26:], "total_weeks": len(log["weeks"]), "judged": len(judged),
            "up": sum(p["net"] > 0 for p in judged), "beat": sum(p["beat_nepse"] for p in judged),
            "avg_net": round(float(np.mean([p["net"] for p in judged])), 2) if judged else None}


def main():
    M = research.load()
    uni = research.universe_mask(M)
    I = indicators(M)
    S, pts, rs_rank = setups(I, uni)
    score = score_frame(I, pts, rs_rank)
    days = [d for d in week_ends(M["dates"]) if d >= "2019-10-01"]
    nep = M["nepse"]
    res = {"as_of": M["dates"][-1], "split": SPLIT, "top_n": TOP_N, "cost_pct": COST * 100, "horizons": {}}
    for h in HORIZONS:
        g = forward(M, h)
        nep_fwd = (nep.shift(-(1 + h)) / nep.shift(-1) - 1)
        gu = g.where(uni)
        base_up = (net(gu) > 0).where(gu.notna()).mean(axis=1)
        base_beat = gu.gt(nep_fwd, axis=0).where(gu.notna()).mean(axis=1)
        base_net = net(gu).mean(axis=1)
        nf = nep_fwd.to_dict()
        rows = {}
        # 1) each classic setup: all liquid stocks that showed it
        for k, (label, desc, ev) in S.items():
            e = ev.fillna(False) & uni
            r = evaluate(e, g, base_up, base_beat, base_net, nf, days)
            r.update(label=label, desc=desc, kind="setup")
            rows[k] = r
        # 2) the chart-expert checklist: top 5 per week with 6+ of 8 points
        for k, label, desc, cond in (
            ("expert", "Chart-expert top 5", "Each week, the 5 stocks scoring best on an 8-point bullish checklist "
             "(6+ points needed), ties broken by relative strength.", pts >= 6),
            ("expert_quiet", "Chart-expert top 5, quiet stocks only", "Same checklist, limited to the less-crowded half "
             "of the market (the main model's strongest finding).", (pts >= 6) &
             (M["turnover"].rolling(60, min_periods=30).median().where(uni).rank(axis=1, pct=True) <= 0.5)),
        ):
            sc = score.where(cond & uni)
            top = sc.rank(axis=1, ascending=False, method="first") <= TOP_N
            r = evaluate(top, g, base_up, base_beat, base_net, nf, days)
            r.update(label=label, desc=desc, kind="picks")
            rows[k] = r
        for k in rows:
            rows[k]["verdict"] = verdict(rows[k])
        res["horizons"][f"h{h}"] = rows

    # ---------------- this week's picks (latest close), with chart levels
    d = M["dates"][-1]
    sc = score.loc[d].where((pts.loc[d] >= 6) & uni.loc[d])
    best = sc.dropna().sort_values(ascending=False).head(10).index
    picks = []
    for s in best:
        c, atr = float(I["C"].at[d, s]), float(I["atr"].at[d, s])
        raw = float(M["close"].at[d, s])
        k = raw / c if c else 1.0
        tags = [S[x][0] for x in S if bool(S[x][2].at[d, s]) and x not in ("oversold",)]
        picks.append({"sym": s, "sector": M["sector"].get(s), "close": raw, "points": int(pts.at[d, s]),
                      "rsi": round(float(I["rsi"].at[d, s]), 1), "adx": round(float(I["adx"].at[d, s]), 1),
                      "rs60": round(float(I["rs60"].at[d, s]) * 100, 1),
                      "sma50": round(float(I["sma50"].at[d, s]) * k, 1), "sma200": round(float(I["sma200"].at[d, s]) * k, 1),
                      "stop": round((c - 2 * atr) * k, 1), "target": round((c + 3 * atr) * k, 1),
                      "atr_pct": round(atr / c * 100, 1), "tags": tags[:5]})
    res["picks"] = {"date": d, "list": picks}
    res["live"] = update_log(M, d, picks)
    OUT.write_text(json.dumps(res, separators=(",", ":"), default=float))

    for hk, rows in res["horizons"].items():
        print(f"\n=== hold {hk[1:]} trading days (net of ~1.4% costs + CGT) ===")
        for k, r in rows.items():
            line = f"{r['label']:38s} {r['verdict']:18s}"
            for per in ("design", "holdout"):
                x = r.get(per, {})
                if "up_pct" in x:
                    line += (f" | {per[0]} n{x['n']:6d} up {x['up_pct']:4.1f}% (rand {x['base_up_pct']:4.1f}) "
                             f"beatN {x['beat_pct']:4.1f}% (rand {x['base_beat_pct']:4.1f}) avg {x['avg_net']:+5.2f} (rand {x['base_net']:+5.2f}) t{x['vs_random_t']:+.1f}")
            print(line)
    print("\npicks", d, [(p["sym"], p["points"]) for p in picks])
    print("live log:", {k: v for k, v in res["live"].items() if k != "weeks"})


if __name__ == "__main__":
    main()
