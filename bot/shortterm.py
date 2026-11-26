"""Short-term (3-10 trading day) setups on NEPSE: what does history say?

    python -m bot.shortterm        # prints the study, writes docs/data/shortterm.json

Each "setup" is an event seen at the close of day t. The study assumes a real
retail trade around it:

  * buy at the next session's VWAP (t+1); impossible if that day is locked at the
    upper circuit (counted as "could not buy")
  * T+2 settlement: the earliest you can sell is about 3 trading days after buying,
    so the horizons tested are 3, 5 and 10 trading days
  * sell at that day's VWAP
  * costs: broker commission + SEBON fee both ways, DP charge, slippage, and 7.5%
    capital-gains tax on any profit (~1.4% round trip for a Rs 10,000 trade)

"Excess" = the trade's return minus the average liquid stock's return over the
same days, so a setup that only works because the whole market rose does not count.
Significance uses one average per event DAY (events on the same day are not
independent), and every setup is checked separately on the design period
(2019-01..2024-06) and the hold-out (after).
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
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "shortterm.json"
SPLIT = research.DESIGN_END
HORIZONS = (3, 5, 10)
COST = 0.014        # round trip for a ~Rs 10,000 order: 2x(0.36% + 0.015%) + Rs 25 DP + ~2x0.3% slippage
CGT = 0.075


def panel():
    M = research.load()
    uni = research.universe_mask(M)
    lp, rr = M["lp"], M["ret_raw"]
    traded = M["traded"]
    adj = np.exp(lp) / M["close"]                   # cumulative bonus/right adjustment factor
    avwap = (M["vwap"] * adj).where(traded)         # VWAP on the adjusted scale
    locked_up = (rr >= 0.095) & (M["close"] >= M["high"] * 0.999)
    locked_dn = (rr <= -0.095) & (M["close"] <= M["low"] * 1.001)
    return M, uni, avwap, locked_up, locked_dn


def setups(M):
    """name -> (label, plain-English description, boolean frame of events at close of day t)."""
    lp, rr, c, hi, lo = M["lp"], M["ret_raw"], M["close"], M["high"], M["low"]
    turn = M["turnover"].fillna(0)
    t20 = turn.shift(1).rolling(20, min_periods=10).mean()
    near_high = c >= hi * 0.995
    pressure = (c - M["vwap"]) / M["vwap"]
    nep = M["nepse"]
    mkt_dn = (nep.pct_change() <= -0.02)
    S = {
        "uc": ("Upper-circuit hit", "Closed up ~10%, the daily maximum.", rr >= 0.095),
        "lc": ("Lower-circuit hit", "Closed down ~10%, the daily maximum fall.", rr <= -0.095),
        "volbreak": ("Volume breakout", "Traded 3x its normal amount, rose over 4% and closed at the day's high.",
                     (turn > 3 * t20) & (rr > 0.04) & near_high & (rr < 0.095)),
        "high20": ("20-day breakout", "Closed above its highest close of the last 20 days, on 1.5x normal volume.",
                   (lp > lp.shift(1).rolling(20).max()) & (turn > 1.5 * t20) & (rr < 0.095)),
        "strong": ("Strong close", "Up over 3% and closed at the day's high (not a circuit).",
                   (rr > 0.03) & near_high & (rr < 0.095)),
        "upstreak": ("3-day rally", "Rose more than 2% on each of the last 3 days.",
                     (rr > 0.02) & (rr.shift(1) > 0.02) & (rr.shift(2) > 0.02)),
        "oversold": ("Sharp 5-day drop", "Fell more than 12% in 5 trading days (no circuit today).",
                     ((lp - lp.shift(5)) < -0.12) & (rr > -0.095)),
        "weakclose": ("Weak close", "Closed more than 2% below the day's average price (late selling).",
                      pressure < -0.02),
        "pushclose": ("Late push", "Closed more than 2% above the day's average price (late buying).",
                      pressure > 0.02),
        "postadj": ("Bonus/right book closure", "The exchange cut the base price today for a bonus or right share.",
                    research.adjustment_events(M)),
        "mktdip": ("Market dip day", "NEPSE fell 2% or more today (applies to every liquid stock).",
                   pd.DataFrame(np.repeat(mkt_dn.values[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)),
    }
    return S


def trade_returns(avwap, locked_up, h):
    """Gross return of buying at VWAP on t+1 and selling at VWAP on t+1+h, indexed by signal day t."""
    entry = avwap.shift(-1)
    exit_ = avwap.shift(-(1 + h))
    blocked = locked_up.shift(-1, fill_value=False)
    return (exit_ / entry - 1).where(~blocked), blocked


def net(g):
    after = (1 + g) * (1 - COST) - 1
    return after - CGT * after.clip(lower=0)


def study(M, uni, avwap, locked_up, S):
    out = {}
    for h in HORIZONS:
        gross, blocked = trade_returns(avwap, locked_up, h)
        base = gross.where(uni).mean(axis=1)                  # average liquid stock, same days
        for k, (label, desc, ev) in S.items():
            e = ev.fillna(False) & uni
            g = gross.where(e)
            nr = net(g)
            ex = g.sub(base, axis=0).where(e)
            row = out.setdefault(k, {"label": label, "desc": desc})
            for name, sl in (("design", slice(None, SPLIT)), ("holdout", slice(SPLIT, None)), ("all", slice(None, None))):
                gs, ns, xs = g[sl].stack().dropna(), nr[sl].stack().dropna(), ex[sl].stack().dropna()
                n_ev = int(e[sl].values.sum())
                if len(gs) < 15:
                    row.setdefault(name, {})[f"h{h}"] = {"n": len(gs)}
                    continue
                daily = ex[sl].mean(axis=1).dropna()        # cluster by event day
                t = daily.mean() / (daily.std() / math.sqrt(len(daily))) if len(daily) > 5 and daily.std() > 0 else 0
                row.setdefault(name, {})[f"h{h}"] = {
                    "n": int(len(gs)), "days": int(len(daily)),
                    "net": round(float(ns.mean()) * 100, 2), "median_net": round(float(ns.median()) * 100, 2),
                    "win": round(float((ns > 0).mean()) * 100, 1),
                    "excess": round(float(xs.mean()) * 100, 2), "t": round(float(t), 2),
                    "big_win": round(float((ns > 0.10).mean()) * 100, 1), "big_loss": round(float((ns < -0.10).mean()) * 100, 1),
                    "blocked": round(float(blocked[sl].values[e[sl].values].mean()) * 100, 1) if n_ev else 0,
                }
    return out


def event_path(M, uni, events, before=20, after=20):
    """Average cumulative return vs the average liquid stock, from `before` trading days
    before an event to `after` days after it, zeroed on the day before the event.
    `events` = list of (symbol, trading-day index of the event)."""
    r = M["lp"].diff()
    ex = r.sub(r.where(uni).mean(axis=1), axis=0)
    n = len(M["dates"])
    rows = []
    for sym, i in events:
        if sym not in ex.columns or i - before < 1 or i + after >= n:
            continue
        seg = ex[sym].iloc[i - before:i + after + 1].values
        if np.isnan(seg).sum() > (before + after) / 4:
            continue
        rows.append(np.nan_to_num(seg))
    if not rows:
        return {}
    A = np.array(rows)
    cum = np.cumsum(A.mean(axis=0)) * 100
    cum -= cum[before - 1]
    return {"n": len(rows), "days": list(range(-before, after + 1)), "cum_excess": [round(float(v), 2) for v in cum],
            "pre": round(float(A[:, :before].sum(axis=1).mean()) * 100, 2),
            "post": round(float(A[:, before + 1:].sum(axis=1).mean()) * 100, 2),
            "day0": round(float(A[:, before].mean()) * 100, 2)}


def around_adjustments(M, uni):
    """Excess return around the bonus/right BOOK-CLOSURE day (seen in prices as the base-price cut)."""
    ev = research.adjustment_events(M)
    pos = {d: i for i, d in enumerate(M["dates"])}
    events = [(sym, pos[d]) for sym in ev.columns for d in ev.index[ev[sym].fillna(False)]]
    return event_path(M, uni, events)


def around_announcements(M, uni):
    """Excess return around the dividend/bonus ANNOUNCEMENT (first trading day after it = day 0)."""
    D = load_dividends()
    if D is None:
        return {}
    dates = pd.Index(M["dates"])
    events = [(r.symbol, int(dates.searchsorted(str(r.announced), side="right"))) for r in D.itertuples()]
    out = event_path(M, uni, [(s, i) for s, i in events if i < len(dates)])
    bonus = [(r.symbol, int(dates.searchsorted(str(r.announced), side="right"))) for r in D.itertuples() if r.bonus_pct >= 10]
    out["bonus10"] = event_path(M, uni, [(s, i) for s, i in bonus if i < len(dates)])
    return out


DIV_FILE = Path(__file__).resolve().parent.parent / "data" / "dividends.csv"


def load_dividends():
    if not DIV_FILE.exists():
        return None
    d = pd.read_csv(DIV_FILE)
    return d.dropna(subset=["announced", "book_close"])


def dividend_study(M, uni, avwap, locked_up):
    """Trade the dividend/bonus calendar, as a retail investor could:
      * BUY at VWAP on the first trading day AFTER the announcement date
      * 'sell_before': sell at VWAP 3 trading days before the book-closure date
        (a pure price play; you give up the dividend/bonus)
      * 'hold_through': keep it until 10 trading days after the book closure and collect
        the bonus shares (in the adjusted prices) and cash dividend (5% tax, Rs 100 face value)
    Costs: 1.4% round trip + 7.5% CGT on profit. Excess = minus the average stock, same days."""
    D = load_dividends()
    if D is None:
        return None
    dates = pd.Index(M["dates"])
    lp = M["lp"]
    base = np.exp(lp.where(uni).diff()).sub(1).mean(axis=1).fillna(0)     # avg liquid-stock daily return
    base_idx = (1 + base).cumprod()
    rows = []
    for _, r in D.iterrows():
        sym = r["symbol"]
        if sym not in avwap.columns:
            continue
        ia = dates.searchsorted(str(r["announced"]), side="right")          # first trading day AFTER announcement
        ib = dates.searchsorted(str(r["book_close"]), side="left")          # book-closure day (or next trading day)
        if ia >= len(dates) or ib >= len(dates) or ib - ia < 5:
            continue
        if not bool(uni.iloc[ia - 1].get(sym, False)) and not bool(uni.iloc[ia].get(sym, False)):
            continue                                                         # not liquid enough to trade
        if bool(locked_up.iloc[ia].get(sym, False)):
            continue                                                         # could not buy: locked upper circuit
        e = avwap[sym].iloc[ia]
        if pd.isna(e):
            continue
        cash = r["cash_pct"] / 100 * 100 * 0.95 / float(M["vwap"][sym].iloc[ia]) if r["cash_pct"] > 0 else 0.0
        out = {"sym": sym, "announced": str(r["announced"]), "book_close": str(r["book_close"]),
               "bonus": float(r["bonus_pct"]), "cash_pct": float(r["cash_pct"]), "gap": int(ib - ia),
               "date": dates[ia]}
        for name, ix, extra in (("sell_before", ib - 3, 0.0), ("hold_through", ib + 10, cash)):
            if ix <= ia + 2 or ix >= len(dates):
                continue
            x = avwap[sym].iloc[ix:ix + 3].dropna()
            if x.empty:
                continue
            g = float(x.iloc[0] / e - 1 + extra)
            out[name] = g
            out[name + "_ex"] = g - float(base_idx.iloc[ix] / base_idx.iloc[ia] - 1)
            out[name + "_days"] = int(ix - ia)
        rows.append(out)
    T = pd.DataFrame(rows)
    if T.empty:
        return None

    def summarise(sub):
        res = {}
        for k in ("sell_before", "hold_through"):
            g = sub[k].dropna() if k in sub else pd.Series(dtype=float)
            if len(g) < 10:
                res[k] = {"n": int(len(g))}
                continue
            nr = net(g)
            ex = sub[k + "_ex"].dropna()
            t = ex.mean() / (ex.std() / math.sqrt(len(ex))) if ex.std() > 0 else 0
            res[k] = {"n": int(len(g)), "net": round(float(nr.mean()) * 100, 2), "median_net": round(float(nr.median()) * 100, 2),
                      "win": round(float((nr > 0).mean()) * 100, 1), "excess": round(float(ex.mean()) * 100, 2),
                      "t": round(float(t), 2), "days": round(float(sub[k + "_days"].mean()), 1)}
        return res

    groups = {"All announcements": T, "Bonus share 10%+": T[T.bonus >= 10], "Cash dividend only": T[T.bonus == 0]}
    out = {}
    for gname, sub in groups.items():
        out[gname] = {per: summarise(sub[sub.date <= SPLIT] if per == "design" else sub[sub.date > SPLIT])
                      for per in ("design", "holdout")}
    return {"n_announcements": int(len(D)), "n_tradable": int(len(T)), "median_gap_days": int(T.gap.median()),
            "groups": out}


def verdict(row, h="h5"):
    """A setup counts as a short-term edge only if, after costs and tax, it made money on
    average AND beat the average stock in BOTH periods, with day-clustered t > 2 in design."""
    d, o = row.get("design", {}).get(h, {}), row.get("holdout", {}).get(h, {})
    if d.get("n", 0) < 30 or o.get("n", 0) < 15:
        return "too few"
    if d["net"] > 0 and o["net"] > 0 and d["excess"] > 0 and o["excess"] > 0 and d["t"] > 2:
        return "edge"
    if d["excess"] < 0 and o["excess"] < 0 and d["t"] < -2:
        return "avoid"
    return "noise"


def today(M, uni, S, stats):
    """Stocks that triggered a setup at the latest close, with the setup's history."""
    d = M["dates"][-1]
    out = []
    for k, (label, _, ev) in S.items():
        if k == "mktdip":
            continue
        row = ev.loc[d].fillna(False) & uni.loc[d]
        syms = list(row[row].index)
        if not syms:
            continue
        out.append({"setup": k, "label": label, "verdict": stats[k]["verdict"],
                    "stocks": [{"sym": s, "sector": M["sector"].get(s), "close": float(M["close"].at[d, s]),
                                "chg": round(float(M["ret_raw"].at[d, s]) * 100, 2)} for s in syms[:40]],
                    "count": len(syms)})
    mk = S["mktdip"][2].loc[d].iloc[0] if len(S["mktdip"][2].columns) else False
    return {"date": d, "triggers": out, "market_dip": bool(mk)}


def main():
    M, uni, avwap, lu, ld = panel()
    S = setups(M)
    st = study(M, uni, avwap, lu, S)
    for k in st:
        st[k]["verdict"] = verdict(st[k])
    res = {"as_of": M["dates"][-1], "split": SPLIT, "cost_pct": COST * 100, "cgt_pct": CGT * 100,
           "horizons": list(HORIZONS), "setups": st, "book_closure": around_adjustments(M, uni),
           "dividends": dividend_study(M, uni, avwap, lu),
           "announcement": around_announcements(M, uni),
           "today": today(M, uni, S, st)}
    try:
        from .floorsheet import stored_dates
        fs = sorted(stored_dates())
        res["floorsheet"] = {"days": len(fs), "first": fs[0] if fs else None, "last": fs[-1] if fs else None}
    except Exception:
        res["floorsheet"] = {"days": 0}
    OUT.write_text(json.dumps(res, separators=(",", ":"), default=float))
    print(f"as of {res['as_of']}; costs {COST * 100:.1f}% round trip + {CGT * 100:.1f}% CGT on profit")
    for k, r in st.items():
        line = f"{r['label']:24s} {r['verdict']:7s}"
        for h in HORIZONS:
            for per in ("design", "holdout"):
                x = r.get(per, {}).get(f"h{h}", {})
                if "net" in x:
                    line += f" | {per[0]}{h}: n{x['n']:5d} net{x['net']:+6.2f} win{x['win']:5.1f} ex{x['excess']:+6.2f} t{x['t']:+5.1f}"
                    if h == 5 and per == "design":
                        line += f" blk{x['blocked']:4.1f}"
        print(line)
    bc = res["book_closure"]
    print("book closure: n", bc.get("n"), "excess 20d before", bc.get("pre"), "day0", bc.get("day0"), "20d after", bc.get("post"))
    an = res["announcement"]
    print("announcement: n", an.get("n"), "20d before", an.get("pre"), "day0", an.get("day0"), "20d after", an.get("post"),
          "| bonus>=10%:", {k: an.get("bonus10", {}).get(k) for k in ("n", "pre", "day0", "post")})
    dv = res["dividends"]
    if dv:
        print(f"dividend calendar: {dv['n_announcements']} announcements, {dv['n_tradable']} tradable, "
              f"median {dv['median_gap_days']} trading days from announcement to book closure")
        for gname, per in dv["groups"].items():
            for p_, x in per.items():
                print(f"  {gname:22s} {p_:7s} " + " | ".join(
                    f"{k}: n{v.get('n')} net{v.get('net', 0):+.2f} win{v.get('win', 0):.0f} ex{v.get('excess', 0):+.2f} t{v.get('t', 0):+.1f} ({v.get('days', 0)}d)"
                    for k, v in x.items()))
    print("today:", [(t["label"], t["count"]) for t in res["today"]["triggers"]])


if __name__ == "__main__":
    main()
