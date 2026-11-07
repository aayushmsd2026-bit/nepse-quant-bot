"""Sagarmatha Signal Engine — a paper-trading quant model for NEPSE.

Inspired by the publicly known *principles* of Renaissance Technologies'
Medallion fund (their real models are secret):

  1. Many weak, independent statistical signals instead of one big "idea".
  2. Let the data decide how much each signal is trusted (walk-forward
     information-coefficient weighting — nothing is peeked from the future).
  3. Trade small, diversified, risk-balanced positions; respect costs.
  4. Strict, emotionless risk management.

Adapted to NEPSE reality: long-only (no short selling), T+2 settlement
(no intraday), ±10% circuit limits, thin liquidity, broker commission,
SEBON fee, DP charge and 7.5% capital-gains tax.

Everything here is a SIMULATION with virtual money.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EXCLUDED_SECTORS = {"Corporate Debentures", "Government Bonds", "Mutual Fund",
                    "Preference Share", "Promoter Share"}

# ----------------------------------------------------------------- the signals
SIGNALS = {
    "reversal": ("Short-term bounce",
                 "Stocks that fell more than their sector over the last 5 days tend to bounce back."),
    "momentum": ("3-month momentum",
                 "Stocks that rose steadily over ~3 months (ignoring the last week) tend to keep going."),
    "meanrev": ("Rubber band",
                "How far the price is stretched below its 20-day average, measured in volatility units."),
    "pressure": ("Closing pressure",
                 "Closing above the day's average price (VWAP) means buyers were in control late in the day."),
    "volume": ("Volume confirmation",
               "Unusual volume in the direction of the move signals that big players are active."),
    "calm": ("Low volatility",
             "Calm, steady stocks often deliver better risk-adjusted returns than wild ones."),
    "quality": ("Trend quality",
                "Return divided by volatility over 20 days: a smooth uptrend beats a jumpy one."),
    "sector": ("Sector rotation",
               "NEPSE moves in sector waves; favour stocks whose whole sector is gaining strength."),
}
SIG_KEYS = list(SIGNALS)


@dataclass
class Config:
    start_capital: float = 500_000.0
    max_positions: int = 10
    max_weight: float = 0.15          # per stock
    max_sector_weight: float = 0.35
    min_order: float = 10_000.0
    rebalance_every: int = 20         # full portfolio review every 20 trading days (~1 month)
    buy_top_pct: float = 0.10         # new buys must rank in the top 10%
    sell_rank_pct: float = 0.40       # at review, sell anything no longer in the top 40%
    emergency_stop: float = 0.15      # between reviews: exit if down 15% from entry
    trailing_stop: float = 0.20       # ...or 20% below its peak
    alpha_smooth: int = 1             # 1 = use today's score (smoothing tested: it hurt)
    slippage: float = 0.0025
    max_volume_share: float = 0.05    # never be more than 5% of a day's volume
    min_turnover: float = 5_000_000   # Rs 50 lakh median daily turnover
    ic_horizon: int = 20
    ic_lookback: int = 120
    prior_mix: float = 0.2            # 20% prior belief, 80% learned from data


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
        df = pd.read_csv(DATA / "prices.csv")
        df = df.drop_duplicates(["date", "symbol"], keep="last")
        self.sectors: dict = json.loads((DATA / "sectors.json").read_text())
        self.dates: list[str] = sorted(df["date"].unique())
        self.didx = {d: i for i, d in enumerate(self.dates)}
        p = lambda c: df.pivot(index="date", columns="symbol", values=c).reindex(self.dates)
        self.close, self.high, self.low = p("close"), p("high"), p("low")
        self.vwap, self.vol, self.turn = p("vwap"), p("volume"), p("turnover")
        self.prev = p("prev_close")
        self.traded = self.close.notna() & (self.vol > 0)
        ret = (self.close / self.prev - 1).where(self.traded)
        # prev_close is exchange-adjusted for bonus/rights, so this is an adjusted return
        self.ret = ret.clip(-0.11, 0.11).fillna(0.0)
        self.logp = np.log1p(self.ret).cumsum()
        idx = pd.read_csv(DATA / "nepse_index.csv").set_index("date")["close"]
        self.nepse = idx.reindex(self.dates).ffill()
        self.symbols = list(self.close.columns)
        self.sector_of = pd.Series({s: self.sectors.get(s, "Unknown") for s in self.symbols})
        self._signals = self._compute_signals()
        self._fwd = {}

    # ---- vectorised signal matrices (dates x symbols), each z-scored per date
    def _compute_signals(self):
        lp, ret = self.logp, self.ret
        r5 = lp - lp.shift(5)
        r20 = lp - lp.shift(20)
        r60s = lp.shift(5) - lp.shift(65)
        vol20 = ret.rolling(20).std()
        ma20 = self.close.ffill().rolling(20).mean()
        v5 = self.vol.fillna(0).rolling(5).mean()
        v60 = self.vol.fillna(0).rolling(60).mean()
        press = ((self.close - self.vwap) / self.vwap).where(self.traded).rolling(5, min_periods=3).mean()
        groups = self.sector_of

        def sector_mean(m):
            return m.T.groupby(groups).transform("mean").T

        raw = {
            "reversal": -(r5 - sector_mean(r5)),
            "momentum": r60s,
            "meanrev": -((self.close.ffill() - ma20) / ma20) / vol20.replace(0, np.nan),
            "pressure": press,
            "volume": np.log((v5 + 1) / (v60 + 1)) * np.sign(r5),
            "calm": -vol20,
            "quality": r20 / (vol20 * math.sqrt(20)).replace(0, np.nan),
            "sector": sector_mean(r20),
        }
        self.vol20 = vol20
        return raw

    def forward(self, h: int) -> pd.DataFrame:
        """Return over the h days after the entry day (used only for *past* dates)."""
        if h not in self._fwd:
            self._fwd[h] = self.logp.shift(-(1 + h)) - self.logp.shift(-1)
        return self._fwd[h]

    def universe(self, i: int) -> list[str]:
        if i < 70:
            return []
        tr = self.traded.iloc[i - 59:i + 1].sum()
        med_turn = self.turn.iloc[i - 19:i + 1].median()
        ok = (tr >= 50) & (med_turn >= Config.min_turnover) & self.traded.iloc[i]
        sect_ok = ~self.sector_of.isin(EXCLUDED_SECTORS | {"Unknown"})
        return list(ok[ok & sect_ok].index)

    def zscores(self, i: int, uni: list[str]) -> pd.DataFrame:
        out = {}
        for k, m in self._signals.items():
            x = m.iloc[i].reindex(uni).astype(float)
            x = x.fillna(x.median())
            sd = x.std()
            z = (x - x.mean()) / sd if sd and sd > 0 else x * 0
            out[k] = z.clip(-3, 3)
        return pd.DataFrame(out)


# ------------------------------------------------------------- learning weights
class SignalLearner:
    """Walk-forward information-coefficient (IC) tracker.

    IC = rank correlation between a signal today and the stock returns of the
    following 20 days (the holding horizon). It is only recorded once those 5 days are in the past.
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
                res = {k: float(z[k].rank().corr(fr)) for k in SIG_KEYS}
        self.ic[j] = res
        return res

    def weights(self, i: int) -> tuple[dict, dict]:
        h, L = self.cfg.ic_horizon + 1, self.cfg.ic_lookback
        hist = [self._ic_at(j) for j in range(max(70, i - h - L), i - h + 1)]
        hist = [x for x in hist if x]
        prior = {k: 1 / len(SIG_KEYS) for k in SIG_KEYS}
        if len(hist) < 20:
            return prior, {k: 0.0 for k in SIG_KEYS}
        df = pd.DataFrame(hist)
        w_ew = 0.5 ** (np.arange(len(df))[::-1] / 40)  # half-life 40 days
        mean = (df.mul(w_ew, axis=0).sum() / w_ew.sum())
        ir = mean / (df.std() + 1e-6)
        learned = ir / (ir.abs().sum() + 1e-9)
        mix = self.cfg.prior_mix
        w = {k: mix * prior[k] + (1 - mix) * float(learned[k]) for k in SIG_KEYS}
        tot = sum(abs(v) for v in w.values())
        w = {k: v / tot for k, v in w.items()}
        return w, {k: float(mean[k]) for k in SIG_KEYS}


# ------------------------------------------------------------------ the trader
class Engine:
    def __init__(self, mkt: Market, cfg: Config | None = None, state: dict | None = None,
                 log_days: int | None = None):
        self.m = mkt
        self.cfg = cfg or Config()
        self.learner = SignalLearner(mkt, self.cfg)
        self.log_days = log_days
        self.s = state or {
            "cash": self.cfg.start_capital, "start_capital": self.cfg.start_capital,
            "inception": None, "last_date": None, "positions": {}, "pending": [],
            "trades": [], "equity": [], "fees": {"commission": 0.0, "sebon": 0.0,
                                                  "dp": 0.0, "cgt": 0.0, "slippage": 0.0},
            "days": [], "brain": None,
        }

    # -------------------------------------------------------------- scoring
    def raw_alpha(self, i):
        cache = self.__dict__.setdefault("_alpha_cache", {})
        if i not in cache:
            uni = self.m.universe(i)
            if len(uni) < 30:
                cache[i] = None
            else:
                w, ic = self.learner.weights(i)
                z = self.m.zscores(i, uni)
                a = (z * pd.Series(w)).sum(axis=1)
                cache[i] = ((a - a.mean()) / (a.std() or 1), z, w, ic)
        return cache[i]

    def smooth_alpha(self, i):
        cur = self.raw_alpha(i)
        frames = [cur[0]]
        for j in range(i - 1, max(-1, i - self.cfg.alpha_smooth), -1):
            r = self.raw_alpha(j)
            if r is not None:
                frames.append(r[0].reindex(cur[0].index).fillna(0))
        a = pd.concat(frames, axis=1).mean(axis=1)
        return (a - a.mean()) / (a.std() or 1)

    # -------------------------------------------------------------- helpers
    def _log(self, day, kind, msg, **extra):
        day["log"].append({"k": kind, "m": msg, **extra})

    def _px(self, frame, sym, i):
        v = frame.at[self.m.dates[i], sym] if sym in frame.columns else np.nan
        return None if pd.isna(v) else float(v)

    def tdays_between(self, d1: str, d2: str) -> int:
        return self.m.didx[d2] - self.m.didx[d1]

    def equity(self, i):
        val = self.s["cash"]
        for sym, p in self.s["positions"].items():
            val += p["qty"] * p["last"]
        return val

    # -------------------------------------------------------------- one day
    def run_day(self, date: str):
        m, cfg, s = self.m, self.cfg, self.s
        i = m.didx[date]
        if s["inception"] is None:
            s["inception"] = date
        day = {"date": date, "log": []}
        n_traded = int(m.traded.iloc[i].sum())
        nep = m.nepse.iloc[i]
        nep_prev = m.nepse.iloc[i - 1] if i > 0 else nep
        self._log(day, "data", f"Market closed. Received end-of-day prices for {n_traded} stocks.")
        if not pd.isna(nep):
            ch = (nep / nep_prev - 1) * 100 if nep_prev else 0
            self._log(day, "data", f"NEPSE index closed at {nep:,.2f} ({ch:+.2f}%).")

        self._corporate_actions(day, i)
        self._execute_pending(day, i)
        self._mark(day, i)
        self._decide(day, i)

        eq = self.equity(i)
        invested = eq - s["cash"]
        s["equity"].append({"date": date, "equity": round(eq, 2), "cash": round(s["cash"], 2),
                            "invested": round(invested, 2),
                            "nepse": None if pd.isna(nep) else round(float(nep), 2),
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
            self._log(day, "exec", f"Market opened: sending {len(orders)} order(s) queued yesterday to the broker.")
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
                    self._log(day, "warn", f"BUY {sym}: price locked at upper circuit (+10%) — no sellers. "
                              "Order not filled.", sym=sym)
                    self._fail(o, day, "upper circuit")
                    continue
                price = min(hi, vwap * (1 + cfg.slippage))
                qty = min(o["qty"], int(vol * cfg.max_volume_share))
                while qty >= 10:
                    amt = qty * price
                    cost = amt + commission(amt) + amt * SEBON_FEE
                    if cost <= s["cash"]:
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
                    self._log(day, "warn", f"SELL {sym}: price locked at lower circuit (−10%) — no buyers. "
                              "Will retry tomorrow.", sym=sym)
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

    def _fail(self, o, day, why):
        self.s["trades"].append({"date": day["date"], "side": o["side"], "sym": o["sym"],
                                 "status": "REJECTED", "why": why, "reason": o["reason"]})

    def _trade(self, day, o, i, qty, price, amt, com, seb, dp, cgt, net, **kw):
        self.s["trades"].append({
            "date": day["date"], "side": o["side"], "sym": o["sym"], "status": "FILLED",
            "qty": qty, "price": round(price, 2), "amount": round(amt, 2),
            "fees": round(com + seb + dp, 2), "cgt": round(cgt, 2),
            "pnl": None if net is None else round(net, 2), "reason": o["reason"],
            **{k: v for k, v in kw.items()}})

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
        n = self.m.nepse
        if i < 50 or pd.isna(n.iloc[i]):
            return "Neutral", 0.75
        sma20, sma50 = n.iloc[i - 19:i + 1].mean(), n.iloc[i - 49:i + 1].mean()
        px = n.iloc[i]
        if px > sma50 and sma20 > sma50:
            return "Bullish", 0.95
        if px < sma50 and sma20 < sma50:
            return "Bearish", 0.45
        return "Neutral", 0.75

    def _decide(self, day, i):
        m, cfg, s = self.m, self.cfg, self.s
        uni = m.universe(i)
        if len(uni) < 30:
            self._log(day, "think", "Not enough price history yet to trade safely. Waiting.")
            return
        _, z, weights, ic = self.raw_alpha(i)
        alpha = self.smooth_alpha(i)
        rank_pct = alpha.rank(ascending=False, pct=True)
        regime, gross = self.regime(i)
        self._log(day, "think", f"Scanned {len(uni)} liquid stocks (filtered out funds, bonds, debentures "
                  f"and anything trading under Rs 50 lakh/day). Computed {len(SIG_KEYS)} signals for each.")
        top_sig = sorted(weights.items(), key=lambda kv: -abs(kv[1]))[:3]
        self._log(day, "learn", f"Signal trust re-learned from the last 120 days (how well each predicted the next {self.cfg.ic_horizon} days): most trusted → " +
                  ", ".join(f"{SIGNALS[k][0]} ({v * 100:+.0f}%)" for k, v in top_sig) + ".")
        self._log(day, "risk", f"Market mood: {regime}. Maximum money allowed in stocks: {gross * 100:.0f}%.")

        eq = self.equity(i)
        since = self.tdays_between(s["inception"], m.dates[i])
        review = since % cfg.rebalance_every == 0
        nxt = cfg.rebalance_every - since % cfg.rebalance_every
        if review:
            self._log(day, "think", f"📅 Portfolio review day (every {cfg.rebalance_every} trading days). "
                      "Re-ranking everything and rebalancing.")
        else:
            self._log(day, "think", f"Not a review day — next full review in {nxt} trading day(s). "
                      "Only checking emergency stops today (fewer trades = lower fees).")
        sells = set()
        for sym, p in s["positions"].items():
            why = None
            ret = p["last"] / p["entry_price"] - 1
            if p["last"] <= p["entry_price"] * (1 - cfg.emergency_stop):
                why = f"emergency stop ({ret * 100:.1f}% from entry)"
            elif p["last"] <= p["peak"] * (1 - cfg.trailing_stop):
                why = f"trailing stop: fell {(1 - p['last'] / p['peak']) * 100:.1f}% from its peak"
            elif review and sym not in rank_pct:
                why = "dropped out of the tradable universe (low liquidity)"
            elif review and rank_pct[sym] > cfg.sell_rank_pct:
                why = f"signal faded (now ranked top {rank_pct[sym] * 100:.0f}%, needs top {cfg.sell_rank_pct * 100:.0f}%)"
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
                rk = f"ranked top {rank_pct[sym] * 100:.0f}%" if sym in rank_pct else "outside the universe today"
                self._log(day, "hold", f"Keep {sym}: {ret * 100:+.1f}% so far, {rk}.", sym=sym)

        # ---- buys
        held = [k for k in s["positions"] if k not in sells]
        slots = cfg.max_positions - len(held)
        invested = sum(s["positions"][k]["qty"] * s["positions"][k]["last"] for k in held)
        budget = min(s["cash"] + sum(s["positions"][k]["qty"] * s["positions"][k]["last"] for k in sells) * 0.98,
                     gross * eq - invested)
        sect_w = {}
        for k in held:
            sec = s["positions"][k]["sector"]
            sect_w[sec] = sect_w.get(sec, 0) + s["positions"][k]["qty"] * s["positions"][k]["last"] / eq
        cands = [sym for sym in alpha.sort_values(ascending=False).index
                 if rank_pct[sym] <= cfg.buy_top_pct and sym not in s["positions"]] if review else []
        vol = m.vol20.iloc[i].reindex(uni)
        med_vol = float(vol.median())
        planned = 0
        for sym in cands:
            if slots <= 0 or budget < cfg.min_order:
                break
            sec = m.sector_of.get(sym)
            base = gross / cfg.max_positions
            w = min(cfg.max_weight, base * min(1.6, max(0.6, med_vol / max(float(vol[sym]), 1e-4))))
            room = cfg.max_sector_weight - sect_w.get(sec, 0)
            if room < 0.04:
                self._log(day, "risk", f"Skip {sym}: {sec} sector already at its {cfg.max_sector_weight * 100:.0f}% limit.", sym=sym)
                continue
            w = min(w, room)
            amt = min(w * eq, budget)
            px = float(m.close.at[m.dates[i], sym])
            qty = int(amt / (px * 1.02))
            if qty < 10 or amt < cfg.min_order:
                continue
            contrib = (z.loc[sym] * pd.Series(weights)).sort_values(ascending=False)
            top = [SIGNALS[k][0] for k, v in contrib.items() if v > 0][:3]
            reason = f"Ranked #{int(alpha.rank(ascending=False)[sym])} of {len(uni)}; driven by " + ", ".join(top)
            s["pending"].append({"side": "BUY", "sym": sym, "qty": qty, "reason": reason,
                                 "alpha": round(float(alpha[sym]), 3),
                                 "signals": {k: round(float(z.at[sym, k]), 2) for k in SIG_KEYS}})
            self._log(day, "plan", f"Plan BUY ~{qty} × {sym} (≈Rs {qty * px:,.0f}, {w * 100:.0f}% of portfolio). "
                      f"Reason: {reason}.", sym=sym)
            sect_w[sec] = sect_w.get(sec, 0) + w
            budget -= qty * px * 1.01
            slots -= 1
            planned += 1
        if review and planned and slots > 0:
            self._log(day, "think", f"Only {planned} stock(s) passed every filter today; keeping the rest "
                      "in cash rather than forcing weak trades.")
        if planned == 0 and not sells:
            self._log(day, "think", "No trades today. Doing nothing is often the best decision: every round-trip "
                      "costs ~0.8% plus 7.5% tax on profit.")
        if s["pending"]:
            self._log(day, "exec", f"{len(s['pending'])} order(s) queued for the next trading session.")

        # ---- snapshot of the brain for the UI
        top_n = alpha.sort_values(ascending=False).head(15).index
        s["brain"] = {
            "date": m.dates[i], "universe": len(uni), "regime": regime, "gross": gross, "next_review": 0 if review else nxt,
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "ic": {k: round(v, 4) for k, v in ic.items()},
            "top": [{"sym": k, "sector": m.sector_of.get(k), "alpha": round(float(alpha[k]), 2),
                     "close": float(m.close.at[m.dates[i], k]),
                     "z": {sk: round(float(z.at[k, sk]), 2) for sk in SIG_KEYS}} for k in top_n],
            "bottom": [{"sym": k, "alpha": round(float(alpha[k]), 2)}
                       for k in alpha.sort_values().head(5).index],
        }
        s.setdefault("weight_history", []).append({"date": m.dates[i], **{k: round(v, 4) for k, v in weights.items()}})


# ------------------------------------------------------------------ metrics
def metrics(state: dict) -> dict:
    eq = pd.DataFrame(state["equity"])
    if eq.empty:
        return {}
    e = eq["equity"].astype(float)
    r = e.pct_change().dropna()
    start = state["start_capital"]
    nep = eq["nepse"].astype(float).ffill()
    closed = [t for t in state["trades"] if t.get("status") == "FILLED" and t["side"] == "SELL"]
    wins = [t for t in closed if t["pnl"] > 0]
    dd = (e / e.cummax() - 1).min()
    n = len(e)
    ann = (e.iloc[-1] / start) ** (240 / max(n, 1)) - 1 if n > 20 else None
    return {
        "equity": round(float(e.iloc[-1]), 2),
        "return_pct": round((e.iloc[-1] / start - 1) * 100, 2),
        "nepse_return_pct": round((nep.iloc[-1] / nep.iloc[0] - 1) * 100, 2) if nep.notna().any() else None,
        "max_drawdown_pct": round(float(dd) * 100, 2),
        "sharpe": round(float(r.mean() / r.std() * math.sqrt(240)), 2) if len(r) > 10 and r.std() > 0 else None,
        "annualized_pct": None if ann is None else round(ann * 100, 2),
        "trades_closed": len(closed), "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "best_trade": max(closed, key=lambda t: t["pnl"]) if closed else None,
        "worst_trade": min(closed, key=lambda t: t["pnl"]) if closed else None,
        "realized_pnl": round(sum(t["pnl"] for t in closed), 2),
        "trading_days": n,
    }
