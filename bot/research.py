"""NEPSE pattern research: what actually predicts returns on the Nepal Stock Exchange?

Run:  python -m bot.research          (prints a report, writes docs/data/research.json)

Methodology
-----------
* DESIGN period (2019-01 .. 2024-06): used to discover patterns and choose the model.
* HOLD-OUT period (2024-07 .. today): never used for design; only to check whether a
  pattern discovered in the design period kept working afterwards.
* IC = Spearman rank correlation between a factor today and each stock's return over
  the next H trading days (entry at the next session). |IC| of 0.03+ that keeps the same
  sign year after year is useful in practice; 0.10+ is very strong.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "data" / "research.json"
DESIGN_END = "2024-06-30"
EXCLUDED = {"Corporate Debentures", "Government Bonds", "Mutual Fund", "Preference Share",
            "Promoter Share", "Unknown"}


def load():
    df = pd.concat([pd.read_csv(f) for f in sorted((DATA / "prices").glob("*.csv"))])
    df = df.drop_duplicates(["date", "symbol"], keep="last")
    sectors = json.loads((DATA / "sectors.json").read_text())
    dates = sorted(df["date"].unique())
    p = lambda c: df.pivot(index="date", columns="symbol", values=c).reindex(dates)
    M = {c: p(c) for c in ["close", "high", "low", "vwap", "volume", "turnover", "prev_close"]}
    traded = M["close"].notna() & (M["volume"] > 0)
    ret = (M["close"] / M["prev_close"] - 1).where(traded).clip(-0.11, 0.11)
    M["traded"], M["ret_raw"], M["ret"] = traded, ret, ret.fillna(0.0)
    M["lp"] = np.log1p(M["ret"]).cumsum()
    M["sector"] = pd.Series({s: sectors.get(s, "Unknown") for s in M["close"].columns})
    idx = pd.read_csv(DATA / "nepse_index.csv").set_index("date")
    M["nepse"] = idx["close"].reindex(dates).ffill()
    M["mkt_turn"] = idx["turnover"].reindex(dates)
    M["dates"] = dates
    return M


def universe_mask(M, min_turn=1_000_000, top_frac=0.6):
    """Liquid, tradable common stocks. Relative filter so it works in 2019 (thin market)
    and 2026 alike: top 60% by 20-day median turnover AND at least Rs 10 lakh/day
    (so a Rs 40k order is under 5% of a day's trading)."""
    tr60 = M["traded"].rolling(60).sum()
    med = M["turnover"].rolling(20, min_periods=10).median()
    rank = med.rank(axis=1, pct=True, ascending=False)
    ok_sector = ~M["sector"].reindex(M["close"].columns).isin(EXCLUDED)
    mask = (tr60 >= 50) & (med >= min_turn) & (rank <= top_frac) & M["traded"]
    return mask & ok_sector.values[None, :]


def factors(M):
    lp, ret, close = M["lp"], M["ret"], M["close"].ffill()
    sec = M["sector"]
    smean = lambda m: m.T.groupby(sec).transform("mean").T
    r = lambda n: lp - lp.shift(n)
    vol20, vol60 = ret.rolling(20).std(), ret.rolling(60).std()
    ma20 = close.rolling(20).mean()
    v = M["volume"].fillna(0)
    t = M["turnover"].fillna(0)
    F = {
        "reversal": -(r(5) - smean(r(5))),
        "rev20": -r(20),
        "momentum": lp.shift(5) - lp.shift(65),
        "mom120": lp.shift(5) - lp.shift(125),
        "mom250": lp.shift(5) - lp.shift(250),
        "meanrev": -((close - ma20) / ma20) / vol20.replace(0, np.nan),
        "pressure": ((M["close"] - M["vwap"]) / M["vwap"]).where(M["traded"]).rolling(5, min_periods=3).mean(),
        "volume": np.log((v.rolling(5).mean() + 1) / (v.rolling(60).mean() + 1)) * np.sign(r(5)),
        "calm": -vol20,
        "calm60": -vol60,
        "quality": r(20) / (vol20 * math.sqrt(20)).replace(0, np.nan),
        "sector": smean(r(20)),
        "sector60": smean(r(60)),
        "high52": lp - lp.rolling(250, min_periods=120).max(),
        "maxret": -M["ret_raw"].rolling(20, min_periods=10).max(),
        "turntrend": np.log((t.rolling(20).mean() + 1) / (t.rolling(120).mean() + 1)),
        "size": np.log(t.rolling(60, min_periods=30).median() + 1),
        "upcirc": (M["ret_raw"] >= 0.095).astype(float).rolling(20).sum(),
    }
    return F


def rank_ic(F, fwd, mask):
    """Row-wise Spearman correlation, vectorised."""
    a = F.where(mask & fwd.notna() & F.notna())
    b = fwd.where(a.notna())
    ra, rb = a.rank(axis=1), b.rank(axis=1)
    ra = ra.sub(ra.mean(axis=1), axis=0)
    rb = rb.sub(rb.mean(axis=1), axis=0)
    ic = (ra * rb).sum(axis=1) / np.sqrt((ra ** 2).sum(axis=1) * (rb ** 2).sum(axis=1))
    return ic.where(a.notna().sum(axis=1) >= 30)


def top_excess(F, fwd, mask, q=0.1):
    a = F.where(mask & fwd.notna() & F.notna())
    pct = a.rank(axis=1, pct=True, ascending=False)
    top = fwd.where(pct <= q).mean(axis=1)
    allm = fwd.where(a.notna()).mean(axis=1)
    return (top - allm).where(a.notna().sum(axis=1) >= 30)


def stats(series, step):
    s = series.dropna().iloc[::step]
    if len(s) < 5:
        return None
    t = s.mean() / (s.std() / math.sqrt(len(s))) if s.std() > 0 else 0
    return round(float(s.mean()), 4), round(float(t), 2), len(s)


def market_patterns(M, uni):
    n = M["nepse"]
    r = n.pct_change()
    dates = pd.to_datetime(pd.Series(M["dates"], index=M["dates"]))
    res = {}
    # autocorrelation of index returns
    res["autocorr"] = {f"lag{k}": round(float(r.autocorr(k)), 3) for k in (1, 2, 3, 5)}
    wk = np.log(n).diff(5).iloc[::5]
    res["autocorr"]["weekly"] = round(float(wk.autocorr(1)), 3)
    # trend following overlays
    fwd20 = n.shift(-20) / n - 1
    trend = {}
    for N in (20, 50, 100, 200):
        above = n > n.rolling(N).mean()
        row = {}
        for name, sl in (("design", slice(None, DESIGN_END)), ("holdout", slice(DESIGN_END, None))):
            ab, f = above[sl], fwd20[sl]
            row[name] = {"above": round(float(f[ab].mean() * 100), 2), "below": round(float(f[~ab].mean() * 100), 2),
                         "pct_above": round(float(ab.mean() * 100), 1)}
            # overlay equity: hold index when above SMA (decided at close, earn next day's return), 0.5% per switch
            pos = ab.shift(1).fillna(False).astype(float)
            rr = r[sl].fillna(0) * pos[sl] - pos[sl].diff().abs().fillna(0) * 0.005
            eq = (1 + rr).cumprod()
            bh = (1 + r[sl].fillna(0)).cumprod()
            row[name]["overlay_ret"] = round(float(eq.iloc[-1] - 1) * 100, 1)
            row[name]["buyhold_ret"] = round(float(bh.iloc[-1] - 1) * 100, 1)
            row[name]["overlay_dd"] = round(float((eq / eq.cummax() - 1).min()) * 100, 1)
            row[name]["buyhold_dd"] = round(float((bh / bh.cummax() - 1).min()) * 100, 1)
        trend[f"sma{N}"] = row
    res["trend"] = trend
    # breadth: % of universe above its own 50d MA
    close = M["close"].ffill()
    above50 = ((close > close.rolling(50).mean()) & uni).sum(axis=1).astype(float)
    breadth = above50 / uni.sum(axis=1).astype(float).replace(0, np.nan)
    res["breadth_q"] = quintile_table(breadth, fwd20)
    tt = M["mkt_turn"].rolling(20).mean() / M["mkt_turn"].rolling(120).mean()
    res["turnover_q"] = quintile_table(tt, fwd20)
    # calendar
    dow = dates.dt.day_name()
    res["weekday"] = {d: round(float(r[dow == d].mean() * 100), 3) for d in
                      ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"] if (dow == d).sum() > 30}
    mon = dates.dt.month
    mret = n.groupby(dates.dt.to_period("M").values).last().pct_change()
    res["month"] = {int(m): round(float(mret[mret.index.month == m].mean() * 100), 2) for m in range(1, 13)}
    res["month_n"] = {int(m): int((mret.index.month == m).sum()) for m in range(1, 13)}
    # long holidays (gap >= 5 calendar days): returns before and after
    gaps = dates.diff().dt.days
    hol = [i for i in range(1, len(dates)) if gaps.iloc[i] >= 5]
    pre = [n.iloc[i - 1] / n.iloc[i - 6] - 1 for i in hol if i >= 6]
    post = [n.iloc[i + 4] / n.iloc[i - 1] - 1 for i in hol if i + 4 < len(n)]
    res["holidays"] = {"count": len(hol), "pre5_avg": round(float(np.mean(pre) * 100), 2),
                       "post5_avg": round(float(np.mean(post) * 100), 2),
                       "any5_avg": round(float((n.shift(-5) / n - 1).mean() * 100), 2),
                       "dates": [M["dates"][i] for i in hol]}
    # yearly NEPSE
    yr = n.groupby(dates.dt.year.values).agg(["first", "last"])
    res["nepse_years"] = {int(y): round(float(v["last"] / v["first"] - 1) * 100, 1) for y, v in yr.iterrows()}
    return res, breadth


def quintile_table(x, fwd):
    out = {}
    for name, sl in (("design", slice(None, DESIGN_END)), ("holdout", slice(DESIGN_END, None))):
        xs, fs = x[sl], fwd[sl]
        ok = xs.notna() & fs.notna()
        q = pd.qcut(xs[ok], 5, labels=False)
        out[name] = [round(float(fs[ok][q == k].mean() * 100), 2) for k in range(5)]
    return out


def stock_patterns(M, uni, F):
    out = {}
    lp = M["lp"]
    for H in (5, 20):
        fwd = (lp.shift(-(1 + H)) - lp.shift(-1)).where(M["traded"].shift(-1, fill_value=False))
        rows = {}
        for k, f in F.items():
            ic = rank_ic(f, fwd, uni)
            ex = top_excess(f, fwd, uni)
            yrs = ic.groupby(pd.to_datetime(ic.index).year).mean()
            rows[k] = {
                "ic_design": stats(ic[:DESIGN_END], H), "ic_holdout": stats(ic[DESIGN_END:], H),
                "top10_excess_design": stats(ex[:DESIGN_END], H), "top10_excess_holdout": stats(ex[DESIGN_END:], H),
                "ic_by_year": {int(y): round(float(v), 3) for y, v in yrs.items() if not np.isnan(v)},
            }
        out[f"h{H}"] = rows
    return out


def circuit_behaviour(M, uni):
    r = M["ret_raw"]
    nxt = r.shift(-1)
    up = (r >= 0.095) & uni
    dn = (r <= -0.095) & uni
    fwd5 = (M["lp"].shift(-6) - M["lp"].shift(-1))
    m = lambda frame, sel: float(np.nanmean(frame.where(sel).values)) * 100
    return {"after_upper_next_day": round(m(nxt, up), 2), "after_upper_next5": round(m(fwd5, up), 2),
            "after_lower_next_day": round(m(nxt, dn), 2), "after_lower_next5": round(m(fwd5, dn), 2),
            "avg_stock_next_day": round(m(nxt, uni), 3), "avg_stock_next5": round(m(fwd5, uni), 3),
            "n_upper": int(up.values.sum()), "n_lower": int(dn.values.sum())}


def main():
    M = load()
    uni = universe_mask(M)
    F = factors(M)
    mk, breadth = market_patterns(M, uni)
    sp = stock_patterns(M, uni, F)
    cb = circuit_behaviour(M, uni)
    res = {"period": [M["dates"][0], M["dates"][-1]], "design_end": DESIGN_END,
           "universe_avg": round(float(uni.sum(axis=1).iloc[250:].mean()), 1),
           "market": mk, "stocks": sp, "circuits": cb}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":")))
    # ---- console report
    print(f"Data {res['period']}, avg universe {res['universe_avg']} stocks")
    print("NEPSE by year:", mk["nepse_years"])
    print("Index autocorr:", mk["autocorr"])
    for k, v in mk["trend"].items():
        print(f"  {k}: {v}")
    print("Breadth quintiles -> next 20d NEPSE %:", mk["breadth_q"])
    print("Turnover-ratio quintiles -> next 20d %:", mk["turnover_q"])
    print("Weekday:", mk["weekday"])
    print("Month:", mk["month"])
    print("Holidays:", {k: v for k, v in mk["holidays"].items() if k != "dates"})
    print("Circuits:", cb)
    for H, rows in sp.items():
        print(f"\n=== Stock factors, horizon {H} (IC mean, t, n | top-decile excess) ===")
        for k, v in sorted(rows.items(), key=lambda kv: -abs(kv[1]["ic_design"][0] if kv[1]["ic_design"] else 0)):
            print(f"{k:10s} D:{v['ic_design']} H:{v['ic_holdout']} | exD:{v['top10_excess_design']} exH:{v['top10_excess_holdout']} | {v['ic_by_year']}")


if __name__ == "__main__":
    main()
