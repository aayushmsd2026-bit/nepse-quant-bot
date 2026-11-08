"""Sagarmatha Signal Engine v2: a paper-trading quant model built for NEPSE.

Inspired by the publicly known *principles* of Renaissance Technologies
(their real models are secret): many weak statistical signals, weights learned
from data walk-forward, small diversified positions, strict cost and risk control.

v2 is rebuilt from 8 years of NEPSE research (see bot/research.py). Findings that
shaped it, all confirmed on a hold-out period the design never saw:
  * Closing pressure (close above VWAP) predicts *lower* future returns, in every year.
  * "Boring beats exciting": calm stocks without lottery-like spikes outperform,
    except in euphoric bull runs (2020-21), so its weight must adapt.
  * After a lower-circuit day a stock keeps falling (~-2.5% next week); after an
    upper-circuit day there is no further gain on average. Chasing doesn't pay.
  * 3/6/12-month momentum, sector rotation and calendar months: no reliable edge.
  * The signals are better at telling you what to AVOID than what to buy, and
    NEPSE round-trip costs (~0.85% + 7.5% CGT) punish turnover. Hence: a
    diversified 12-stock portfolio, reviewed every 40 trading days, hard
    "never buy" filters, and holdings kept while they stay in the top third.

Adapted to NEPSE reality: long-only, T+2 settlement, ±10% circuits, thin
liquidity, broker commission, SEBON fee, DP charge and capital-gains tax.
Everything here is a SIMULATION with virtual money.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import research

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ------------------------------------------------------------------ the signals
# key: (display name, plain-English description, research factor, prior weight)
# Prior weights come from the 2019-2024 design-period research (sign and rough size).
SIGNALS = {
    "pressure": ("Closing pressure",
                 "Closing above the day's average price (VWAP). On NEPSE this predicts LOWER returns: "
                 "late-day pushes tend to unwind.", "pressure", -0.30),
    "calm": ("Low volatility",
             "Calm, steady stocks (60-day volatility). Works in normal and falling markets; "
             "fails in euphoric bull runs.", "calm60", 0.22),
    "nolottery": ("No lottery spikes",
                  "Stocks WITHOUT a recent huge one-day jump. Spiky 'lottery' stocks tend to disappoint later.",
                  "maxret", 0.18),
    "bounce": ("Short-term bounce",
               "Stocks that fell more than their sector over the last 5 days tend to recover a little.",
               "reversal", 0.10),
    "high52": ("Near 52-week high",
               "Stocks close to their 1-year high tend to keep doing slightly better.", "high52", 0.10),
    "chasing": ("Circuit chasing",
                "How often the stock hit the +10% upper circuit in the last 20 days. Frequent "
                "circuit-hitters tend to fall back later.", "upcirc", -0.10),
    "momentum": ("3-month momentum",
                 "The global classic. No reliable edge on NEPSE, so it starts at zero weight; "
                 "the bot will use it only if it starts working.", "momentum", 0.0),
    "size": ("Company size",
             "Bigger, more-traded companies vs smaller ones. Direction flips by year, so it starts at zero.",
             "size", 0.0),
}
SIG_KEYS = list(SIGNALS)


@dataclass
class Config:
    start_capital: float = 500_000.0
    max_positions: int = 12
    max_weight: float = 0.12          # per stock
    max_sector_weight: float = 0.35
    min_order: float = 10_000.0
    review_every: int = 40            # full review every 40 trading days (~2 months)
    buy_top_pct: float = 0.15         # new buys come from the top 15%
    keep_top_pct: float = 0.35        # at review, keep anything still in the top 35%
    emergency_stop: float = 0.15      # exit if down 15% from entry
    trailing_stop: float = 0.22       # ...or 22% below its peak
    pressure_veto: float = 0.10       # never buy the 10% with the heaviest closing pressure
    bear_gross: float = 0.60          # money in stocks during a confirmed bear market
    normal_gross: float = 0.97
    slippage: float = 0.0025
    max_volume_share: float = 0.05
    ic_horizon: int = 20
    ic_lookback: int = 250
    ic_halflife: int = 60
    prior_mix: float = 0.5            # 50% 8-year research prior, 50% learned from the last year
    priors: dict = field(default_factory=lambda: {k: v[3] for k, v in SIGNALS.items()})


# ------------------------------------------------------------------ fees (SEBON)
def commission(amount: float) -> float:
    if amount <= 50_000:
        r = 0.0036
    elif amount <= 500_000:
        r = 0.0033
    elif amount <= 2_000_000:
        r = 0.0031
    elif amount <= 10_000_000:
        r = 0.0027
    else:
        r = 0.0024
    return max(10.0, amount * r)


SEBON_FEE = 0.00015
DP_CHARGE = 25.0
CGT_SHORT = 0.075
CGT_LONG = 0.05


# ----------------------------------------------------------------- market data
class Market:
    def __init__(self):
        M = research.load()
        self.M = M
        self.dates: list[str] = M["dates"]
        self.didx = {d: i for i, d in enumerate(self.dates)}
        self.close, self.high, self.low = M["close"], M["high"], M["low"]
        self.vwap, self.vol, self.turn, self.prev = M["vwap"], M["volume"], M["turnover"], M["prev_close"]
        self.traded, self.ret, self.ret_raw, self.logp = M["traded"], M["ret"], M["ret_raw"], M["lp"]
        self.nepse = M["nepse"]
        self.symbols = list(self.close.columns)
        self.sector_of = M["sector"]
        self.uni_mask = research.universe_mask(M)
        F = research.factors(M)
        self._signals = {k: F[v[2]] for k, v in SIGNALS.items()}
        self.vol60 = self.ret.rolling(60).std()
        # benchmark: the "average liquid stock" (equal-weight universe, rebalanced daily)
        ew = self.ret_raw.where(self.uni_mask.shift(1, fill_value=False)).mean(axis=1).fillna(0)
        self.ew_index = (1 + ew).cumprod()
        self._z = {}
        self._fwd = {}

    def universe(self, i: int) -> list[str]:
        if i < 120:
            return []
        row = self.uni_mask.iloc[i]
        return list(row[row].index)

    def zscores(self, i: int, uni: list[str] | None = None) -> pd.DataFrame:
        if i not in self._z:
            uni = uni if uni is not None else self.universe(i)
            out = {}
            for k, m in self._signals.items():
                x = m.iloc[i].reindex(uni).astype(float)
                x = x.fillna(x.median())
                sd = x.std()
                out[k] = ((x - x.mean()) / sd).clip(-3, 3) if sd and sd > 0 else x * 0
            self._z[i] = pd.DataFrame(out).fillna(0)
        return self._z[i]

    def forward(self, h: int) -> pd.DataFrame:
        """Return over the h days after the entry day (only ever read for *past* dates)."""
        if h not in self._fwd:
            self._fwd[h] = self.logp.shift(-(1 + h)) - self.logp.shift(-1)
        return self._fwd[h]


# ------------------------------------------------------------- learning weights
class SignalLearner:
    """Walk-forward information-coefficient (IC) tracker.

    IC = rank correlation between a signal today and the stock returns of the
    following 20 days. It is only recorded once those 20 days are in the past.
    Final weight = 50% research prior + 50% what the last ~year of data says.
    """

    def __init__(self, mkt: Market, cfg: Config):
        self.m, self.cfg = mkt, cfg
        self.ic: dict[int, dict] = {}

    def _ic_at(self, j: int):
        if j in self.ic:
            return self.ic[j]
        uni = self.m.universe(j)
        res = None
        if len(uni) >= 30:
            z = self.m.zscores(j, uni)
            f = self.m.forward(self.cfg.ic_horizon).iloc[j].reindex(uni)
            if f.notna().sum() >= 30:
                fr = f.rank()
                res = {k: (float(z[k].rank().corr(fr)) if z[k].std() > 0 else 0.0) for k in SIG_KEYS}
        self.ic[j] = res
        return res

    def weights(self, i: int) -> tuple[dict, dict]:
        h, L = self.cfg.ic_horizon + 1, self.cfg.ic_lookback
        hist = [self._ic_at(j) for j in range(max(120, i - h - L), i - h + 1, 2)]
        hist = [x for x in hist if x]
        prior = self.cfg.priors
        if len(hist) < 30:
            return dict(prior), {k: 0.0 for k in SIG_KEYS}
        df = pd.DataFrame(hist).fillna(0)
        w_ew = 0.5 ** (np.arange(len(df))[::-1] * 2 / self.cfg.ic_halflife)
        mean = df.mul(w_ew, axis=0).sum() / w_ew.sum()
        learned = mean / (mean.abs().sum() + 1e-9)
        mix = self.cfg.prior_mix
        w = {k: mix * prior[k] + (1 - mix) * float(learned[k]) for k in SIG_KEYS}
        tot = sum(abs(v) for v in w.values()) or 1
        return {k: v / tot for k, v in w.items()}, {k: float(mean[k]) for k in SIG_KEYS}


# ------------------------------------------------------------------ the trader
class Engine:
    def __init__(self, mkt: Market, cfg: Config | None = None, state: dict | None = None,
                 log_days: int | None = None):
        self.m = mkt
        self.cfg = cfg or Config()
        self.learner = SignalLearner(mkt, self.cfg)
        self.log_days = log_days
        self._alpha_cache = {}
        self.s = state or {
            "cash": self.cfg.start_capital, "start_capital": self.cfg.start_capital,
            "inception": None, "last_date": None, "positions": {}, "pending": [],
            "trades": [], "equity": [], "fees": {"commission": 0.0, "sebon": 0.0,
                                                  "dp": 0.0, "cgt": 0.0, "slippage": 0.0},
            "days": [], "brain": None, "model": "v2",
        }

    # -------------------------------------------------------------- scoring
    def raw_alpha(self, i):
        if i not in self._alpha_cache:
            uni = self.m.universe(i)
            if len(uni) < 30:
                self._alpha_cache[i] = None
            else:
                w, ic = self.learner.weights(i)
                z = self.m.zscores(i, uni)
                a = (z * pd.Series(w)).sum(axis=1)
                self._alpha_cache[i] = ((a - a.mean()) / (a.std() or 1), z, w, ic)
        return self._alpha_cache[i]

    # -------------------------------------------------------------- helpers
    def _log(self, day, kind, msg, **extra):
        day["log"].append({"k": kind, "m": msg, **extra})

    def _px(self, frame, sym, i):
        v = frame.at[self.m.dates[i], sym] if sym in frame.columns else np.nan
        return None if pd.isna(v) else float(v)

    def tdays_between(self, d1: str, d2: str) -> int:
        return self.m.didx[d2] - self.m.didx[d1]

    def equity(self, i=None):
        return self.s["cash"] + sum(p["qty"] * p["last"] for p in self.s["positions"].values())

    # -------------------------------------------------------------- one day
    def run_day(self, date: str):
        m, s = self.m, self.s
        i = m.didx[date]
        if s["inception"] is None:
            s["inception"] = date
            s["ew_base"] = float(m.ew_index.iloc[i])
        day = {"date": date, "log": []}
        nep = m.nepse.iloc[i]
        nep_prev = m.nepse.iloc[i - 1] if i > 0 else nep
        self._log(day, "data", f"Market closed. Received end-of-day prices for {int(m.traded.iloc[i].sum())} stocks.")
        if not pd.isna(nep):
            ch = (nep / nep_prev - 1) * 100 if nep_prev else 0
            self._log(day, "data", f"NEPSE index closed at {nep:,.2f} ({ch:+.2f}%).")

        self._corporate_actions(day, i)
        self._execute_pending(day, i)
        self._mark(day, i)
        self._decide(day, i)

        eq = self.equity()
        invested = eq - s["cash"]
        s["equity"].append({"date": date, "equity": round(eq, 2), "cash": round(s["cash"], 2),
                            "invested": round(invested, 2),
                            "nepse": None if pd.isna(nep) else round(float(nep), 2),
                            "ew": round(float(m.ew_index.iloc[i]) / s.get("ew_base", 1) * s["start_capital"], 2),
                            "positions": len(s["positions"])})
        self._log(day, "summary", f"End of day: portfolio worth Rs {eq:,.0f} "
                  f"(cash Rs {s['cash']:,.0f}, invested Rs {invested:,.0f}, "
                  f"{len(s['positions'])} holdings).")
        s["days"].append(day)
        if self.log_days:
            s["days"] = s["days"][-self.log_days:]
        s["last_date"] = date

    # bonus / right share price adjustment for held positions
    def _corporate_actions(self, day, i):
        for sym, p in self.s["positions"].items():
            prev = self._px(self.m.prev, sym, i)
            if prev is None or not p.get("last"):
                continue
            factor = prev / p["last"]
            if factor < 0.95:  # exchange adjusted the base price -> bonus/right
                new_qty = int(round(p["qty"] / factor))
                self._log(day, "warn", f"{sym}: corporate action detected (base price adjusted "
                          f"{p['last']:.1f} → {prev:.1f}). Holding adjusted {p['qty']} → {new_qty} shares "
                          f"(bonus shares credited).", sym=sym)
                p["qty"], p["entry_price"] = new_qty, p["entry_price"] * factor
                p["peak"] *= factor
                p["last"] = prev

    def _execute_pending(self, day, i):
        m, cfg, s = self.m, self.cfg, self.s
        orders, s["pending"] = s["pending"], []
        if orders:
            self._log(day, "exec", f"Market opened: sending {len(orders)} order(s) queued at the last close to the broker.")
        for o in sorted(orders, key=lambda o: o["side"] != "SELL"):  # sells first, frees cash
            sym = o["sym"]
            close, hi, lo = self._px(m.close, sym, i), self._px(m.high, sym, i), self._px(m.low, sym, i)
            vwap, vol = self._px(m.vwap, sym, i), self._px(m.vol, sym, i)
            if close is None or not vol:
                self._log(day, "warn", f"{o['side']} {sym}: stock did not trade today. Order expired.", sym=sym)
                self._fail(o, day, "not traded")
                continue
            r = self.m.ret.at[m.dates[i], sym]
            if o["side"] == "BUY":
                if r >= 0.095 and close >= hi * 0.999:
                    self._log(day, "warn", f"BUY {sym}: price locked at upper circuit (+10%), no sellers. "
                              "Order not filled.", sym=sym)
                    self._fail(o, day, "upper circuit")
                    continue
                price = min(hi, vwap * (1 + cfg.slippage))
                qty = min(o["qty"], int(vol * cfg.max_volume_share))
                while qty >= 10:
                    amt = qty * price
                    if amt + commission(amt) + amt * SEBON_FEE <= s["cash"]:
                        break
                    qty -= max(1, qty // 20)
                if qty < 10:
                    self._log(day, "warn", f"BUY {sym}: not enough cash/liquidity for the minimum 10 shares.", sym=sym)
                    self._fail(o, day, "insufficient cash/liquidity")
                    continue
                amt = qty * price
                com, seb = commission(amt), amt * SEBON_FEE
                s["cash"] -= amt + com + seb
                s["fees"]["commission"] += com
                s["fees"]["sebon"] += seb
                s["fees"]["slippage"] += qty * (price - vwap)
                s["positions"][sym] = {
                    "qty": qty, "cost": amt + com + seb, "entry_price": price,
                    "entry_date": m.dates[i], "peak": price, "last": close,
                    "sector": m.sector_of.get(sym), "reason": o["reason"], "signals": o["signals"],
                    "alpha": o["alpha"],
                }
                self._trade(day, o, i, qty, price, amt, com, seb, 0, 0, None)
                self._log(day, "buy", f"BOUGHT {qty} × {sym} @ Rs {price:,.2f} = Rs {amt:,.0f} "
                          f"(+ Rs {com + seb:,.0f} broker & SEBON fees). Shares arrive in demat in T+2.", sym=sym)
            else:
                p = s["positions"].get(sym)
                if not p:
                    continue
                if r <= -0.095 and close <= lo * 1.001:
                    self._log(day, "warn", f"SELL {sym}: price locked at lower circuit (−10%), no buyers. "
                              "Will retry next session.", sym=sym)
                    self._fail(o, day, "lower circuit")
                    continue
                price = max(lo, vwap * (1 - cfg.slippage))
                qty = min(p["qty"], max(10, int(vol * cfg.max_volume_share)))
                amt = qty * price
                com, seb = commission(amt), amt * SEBON_FEE
                basis = p["cost"] * qty / p["qty"]
                gain = amt - com - seb - DP_CHARGE - basis
                held_cal = (pd.Timestamp(m.dates[i]) - pd.Timestamp(p["entry_date"])).days
                cgt = max(0.0, gain) * (CGT_LONG if held_cal >= 365 else CGT_SHORT)
                s["cash"] += amt - com - seb - DP_CHARGE - cgt
                f = s["fees"]
                f["commission"] += com; f["sebon"] += seb; f["dp"] += DP_CHARGE; f["cgt"] += cgt
                f["slippage"] += qty * (vwap - price)
                net = gain - cgt
                self._trade(day, o, i, qty, price, amt, com, seb, DP_CHARGE, cgt, net,
                            entry=p["entry_price"], entry_date=p["entry_date"])
                emo = "profit" if net >= 0 else "loss"
                self._log(day, "sell", f"SOLD {qty} × {sym} @ Rs {price:,.2f} = Rs {amt:,.0f}. "
                          f"Net {emo} after all fees & tax: Rs {net:,.0f} ({net / basis * 100:+.1f}%).", sym=sym)
                if qty >= p["qty"]:
                    del s["positions"][sym]
                else:
                    p["cost"] -= basis
                    p["qty"] -= qty
                    s["pending"].append(dict(o, qty=p["qty"]))  # sell the rest next session

    def _fail(self, o, day, why):
        self.s["trades"].append({"date": day["date"], "side": o["side"], "sym": o["sym"],
                                 "status": "REJECTED", "why": why, "reason": o["reason"]})
        if o["side"] == "SELL" and o["sym"] in self.s["positions"]:
            self.s["pending"].append(o)  # keep trying to get out

    def _trade(self, day, o, i, qty, price, amt, com, seb, dp, cgt, net, **kw):
        self.s["trades"].append({
            "date": day["date"], "side": o["side"], "sym": o["sym"], "status": "FILLED",
            "qty": qty, "price": round(price, 2), "amount": round(amt, 2),
            "fees": round(com + seb + dp, 2), "cgt": round(cgt, 2),
            "pnl": None if net is None else round(net, 2), "reason": o["reason"], **kw})

    def _mark(self, day, i):
        m = self.m
        for sym, p in self.s["positions"].items():
            c = self._px(m.close, sym, i)
            if c is not None:
                p["last"] = c
                p["peak"] = max(p["peak"], c)
            p["days_held"] = self.tdays_between(p["entry_date"], m.dates[i])
            p["settled"] = p["days_held"] >= 2

    # -------------------------------------------------------------- decision
    def regime(self, i):
        """Only a *confirmed* bear market reduces exposure. Simple trend-timing
        (index vs 20/50-day average) failed the hold-out test, so it is not used."""
        n = self.m.nepse
        if i < 260 or pd.isna(n.iloc[i]):
            return "Normal", self.cfg.normal_gross
        sma200 = n.iloc[i - 199:i + 1].mean()
        sma200_prev = n.iloc[i - 259:i - 59].mean()
        if n.iloc[i] < sma200 and sma200 < sma200_prev:
            return "Bear market", self.cfg.bear_gross
        return "Normal", self.cfg.normal_gross

    def vetoes(self, i, uni):
        """Hard 'never buy' rules from the research."""
        m, d = self.m, self.m.dates[i]
        r = m.ret_raw
        upper_today = r.loc[d].reindex(uni) >= 0.095
        lower_5d = (r.iloc[i - 4:i + 1][uni] <= -0.095).any()
        press = m._signals["pressure"].iloc[i].reindex(uni)
        heavy = press.rank(ascending=False, pct=True) <= self.cfg.pressure_veto
        out = {}
        for sym in uni:
            why = []
            if upper_today.get(sym, False):
                why.append("hit the upper circuit today (chasing doesn't pay)")
            if lower_5d.get(sym, False):
                why.append("hit the lower circuit in the last 5 days (weakness tends to continue)")
            if heavy.get(sym, False):
                why.append("heavy closing pressure (late-day push)")
            if why:
                out[sym] = why
        return out

    def _decide(self, day, i):
        m, cfg, s = self.m, self.cfg, self.s
        uni = m.universe(i)
        if len(uni) < 30:
            self._log(day, "think", "Not enough price history yet to trade safely. Waiting.")
            return
        alpha, z, weights, ic = self.raw_alpha(i)
        rank_pct = alpha.rank(ascending=False, pct=True)
        regime, gross = self.regime(i)
        veto = self.vetoes(i, uni)
        self._log(day, "think", f"Scanned {len(uni)} liquid stocks (skipped funds, bonds, debentures and the "
                  f"least-traded 40%). Computed {len(SIG_KEYS)} signals for each.")
        top_sig = sorted(weights.items(), key=lambda kv: -abs(kv[1]))[:3]
        self._log(day, "learn", f"Signal trust (50% from 8 years of NEPSE research, 50% from the last year): "
                  "strongest → " + ", ".join(f"{SIGNALS[k][0]} ({v * 100:+.0f}%)" for k, v in top_sig) + ".")
        nv = {k: sum(1 for w in veto.values() if any(k in x for x in w)) for k in ("upper", "lower", "pressure")}
        self._log(day, "risk", f"Never-buy filters: {nv['upper']} at upper circuit today, {nv['lower']} hit the "
                  f"lower circuit recently, {nv['pressure']} with heavy closing pressure.")
        self._log(day, "risk", f"Market regime: {regime}. Maximum money in stocks: {gross * 100:.0f}%.")

        since = self.tdays_between(s["inception"], m.dates[i])
        review = since % cfg.review_every == 0
        nxt = 0 if review else cfg.review_every - since % cfg.review_every
        if review:
            self._log(day, "think", f"📅 Portfolio review day (every {cfg.review_every} trading days ≈ 2 months). "
                      "Re-ranking everything.")
        else:
            self._log(day, "think", f"Not a review day (next review in {nxt} trading days). Only checking "
                      "safety exits and filling empty slots. Low turnover = low fees.")

        eq = self.equity()
        sells = set()
        r_today = m.ret_raw.iloc[i]
        for sym, p in s["positions"].items():
            if any(o["sym"] == sym and o["side"] == "SELL" for o in s["pending"]):
                sells.add(sym)
                continue
            why = None
            ret = p["last"] / p["entry_price"] - 1
            if p["last"] <= p["entry_price"] * (1 - cfg.emergency_stop):
                why = f"emergency stop ({ret * 100:.1f}% from entry)"
            elif p["last"] <= p["peak"] * (1 - cfg.trailing_stop):
                why = f"trailing stop: fell {(1 - p['last'] / p['peak']) * 100:.1f}% from its peak"
            elif r_today.get(sym, 0) <= -0.095:
                why = "hit the lower circuit today; on NEPSE such stocks tend to keep falling"
            elif review and sym not in rank_pct:
                why = "dropped out of the tradable universe (low liquidity)"
            elif review and rank_pct[sym] > cfg.keep_top_pct:
                why = (f"signal faded at review (now top {rank_pct[sym] * 100:.0f}%, "
                       f"needs top {cfg.keep_top_pct * 100:.0f}%)")
            if why:
                if not p["settled"]:
                    self._log(day, "lock", f"{sym}: wants to sell ({why}) but shares are not settled yet "
                              f"(T+2 rule, day {p['days_held']}/2). Holding.", sym=sym)
                    continue
                sells.add(sym)
                s["pending"].append({"side": "SELL", "sym": sym, "qty": p["qty"], "reason": why,
                                     "alpha": float(alpha.get(sym, 0)), "signals": {}})
                self._log(day, "plan", f"Plan SELL {sym}: {why}.", sym=sym)
            else:
                rk = f"ranked top {rank_pct[sym] * 100:.0f}%" if sym in rank_pct else "outside today's universe"
                self._log(day, "hold", f"Keep {sym}: {ret * 100:+.1f}% so far, {rk}.", sym=sym)

        # ---- buys (review days, or any day there are empty slots)
        held = [k for k in s["positions"] if k not in sells]
        slots = cfg.max_positions - len(held)
        pos_val = lambda k: s["positions"][k]["qty"] * s["positions"][k]["last"]
        invested = sum(pos_val(k) for k in held)
        budget = min(s["cash"] + sum(pos_val(k) for k in sells) * 0.98, gross * eq - invested)
        sect_w = {}
        for k in held:
            sec = s["positions"][k]["sector"]
            sect_w[sec] = sect_w.get(sec, 0) + pos_val(k) / eq
        planned, vetoed_top = 0, []
        if slots > 0 and budget >= cfg.min_order:
            target = gross / cfg.max_positions
            vol = m.vol60.iloc[i].reindex(uni)
            med_vol = float(vol.median())
            for sym in alpha.sort_values(ascending=False).index:
                if slots <= 0 or budget < cfg.min_order or rank_pct[sym] > cfg.buy_top_pct:
                    break
                if sym in s["positions"]:
                    continue
                if sym in veto:
                    vetoed_top.append(sym)
                    self._log(day, "risk", f"Skip {sym} (ranked top {rank_pct[sym] * 100:.0f}%): {veto[sym][0]}.", sym=sym)
                    continue
                sec = m.sector_of.get(sym)
                room = cfg.max_sector_weight - sect_w.get(sec, 0)
                if room < 0.03:
                    self._log(day, "risk", f"Skip {sym}: {sec} sector already at its {cfg.max_sector_weight * 100:.0f}% limit.", sym=sym)
                    continue
                w = min(cfg.max_weight, room, target * min(1.3, max(0.75, med_vol / max(float(vol.get(sym, med_vol)), 1e-4))))
                amt = min(w * eq, budget)
                px = float(m.close.at[m.dates[i], sym])
                qty = int(amt / (px * 1.02))
                if qty < 10 or amt < cfg.min_order:
                    continue
                contrib = (z.loc[sym] * pd.Series(weights)).sort_values(ascending=False)
                top = [SIGNALS[k][0] + ("" if weights[k] > 0 else " (low)") for k, v in contrib.items() if v > 0][:3]
                reason = f"Ranked #{int(alpha.rank(ascending=False)[sym])} of {len(uni)}; driven by " + ", ".join(top)
                s["pending"].append({"side": "BUY", "sym": sym, "qty": qty, "reason": reason,
                                     "alpha": round(float(alpha[sym]), 3),
                                     "signals": {k: round(float(z.at[sym, k]), 2) for k in SIG_KEYS}})
                self._log(day, "plan", f"Plan BUY ~{qty} × {sym} (≈Rs {qty * px:,.0f}, {qty * px / eq * 100:.0f}% of portfolio). "
                          f"Reason: {reason}.", sym=sym)
                sect_w[sec] = sect_w.get(sec, 0) + w
                budget -= qty * px * 1.01
                slots -= 1
                planned += 1
            if slots > 0 and planned == 0 and not review:
                self._log(day, "think", "Empty slots, but no stock passed every filter today. Waiting.")
        if planned == 0 and not sells:
            self._log(day, "think", "No trades today. Doing nothing is often the best decision: every round-trip "
                      "costs ~0.85% plus 7.5% tax on profit.")
        if s["pending"]:
            self._log(day, "exec", f"{len(s['pending'])} order(s) queued for the next trading session.")

        top_n = alpha.sort_values(ascending=False).head(15).index
        s["brain"] = {
            "date": m.dates[i], "universe": len(uni), "regime": regime, "gross": gross, "next_review": nxt,
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "priors": {k: round(v, 4) for k, v in cfg.priors.items()},
            "ic": {k: round(v, 4) for k, v in ic.items()},
            "vetoes": len(veto),
            "top": [{"sym": k, "sector": m.sector_of.get(k), "alpha": round(float(alpha[k]), 2),
                     "close": float(m.close.at[m.dates[i], k]), "veto": veto.get(k, [None])[0],
                     "z": {sk: round(float(z.at[k, sk]), 2) for sk in SIG_KEYS}} for k in top_n],
        }
        s.setdefault("weight_history", []).append({"date": m.dates[i], **{k: round(v, 4) for k, v in weights.items()}})


# ------------------------------------------------------------------ metrics
def _seg(eq: pd.DataFrame, start_capital: float):
    e = eq["equity"].astype(float)
    r = e.pct_change().dropna()
    nep = eq["nepse"].astype(float).ffill()
    ew = eq["ew"].astype(float) if "ew" in eq else None
    n = len(e)
    yrs = n / 240
    return {
        "from": eq["date"].iloc[0], "to": eq["date"].iloc[-1],
        "return_pct": round((e.iloc[-1] / e.iloc[0] - 1) * 100, 2),
        "nepse_return_pct": round((nep.iloc[-1] / nep.iloc[0] - 1) * 100, 2) if nep.notna().any() else None,
        "ew_return_pct": round((ew.iloc[-1] / ew.iloc[0] - 1) * 100, 2) if ew is not None else None,
        "cagr_pct": round(((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs >= 0.9 else None,
        "nepse_cagr_pct": round(((nep.iloc[-1] / nep.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs >= 0.9 else None,
        "max_drawdown_pct": round(float((e / e.cummax() - 1).min()) * 100, 2),
        "nepse_drawdown_pct": round(float((nep / nep.cummax() - 1).min()) * 100, 2),
        "sharpe": round(float(r.mean() / r.std() * math.sqrt(240)), 2) if len(r) > 10 and r.std() > 0 else None,
    }


def metrics(state: dict, split: str | None = None) -> dict:
    eq = pd.DataFrame(state["equity"])
    if eq.empty:
        return {}
    start = state["start_capital"]
    out = _seg(eq, start)
    out["return_pct"] = round((eq["equity"].iloc[-1] / start - 1) * 100, 2)
    closed = [t for t in state["trades"] if t.get("status") == "FILLED" and t["side"] == "SELL"]
    wins = [t for t in closed if t["pnl"] > 0]
    out.update({
        "equity": round(float(eq["equity"].iloc[-1]), 2),
        "trades_closed": len(closed), "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "best_trade": max(closed, key=lambda t: t["pnl"]) if closed else None,
        "worst_trade": min(closed, key=lambda t: t["pnl"]) if closed else None,
        "realized_pnl": round(sum(t["pnl"] for t in closed), 2), "trading_days": len(eq),
    })
    if split and eq["date"].iloc[0] < split < eq["date"].iloc[-1]:
        a, b = eq[eq["date"] <= split], eq[eq["date"] >= eq[eq["date"] <= split]["date"].iloc[-1]]
        out["design"], out["holdout"] = _seg(a, start), _seg(b, start)
    years = []
    for y, g in eq.groupby(eq["date"].str[:4]):
        prev = eq[eq["date"] < g["date"].iloc[0]].tail(1)
        g2 = pd.concat([prev, g]) if len(prev) else g
        sg = _seg(g2, start)
        years.append({"year": y, "bot": sg["return_pct"], "nepse": sg["nepse_return_pct"],
                      "ew": sg["ew_return_pct"], "dd": sg["max_drawdown_pct"]})
    out["years"] = years
    return out
