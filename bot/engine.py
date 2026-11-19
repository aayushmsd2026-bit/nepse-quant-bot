"""Sagarmatha Signal Engine v3: a paper-trading quant model built for NEPSE.

Inspired by the publicly known *principles* of Renaissance Technologies (their
real models are secret): many weak statistical signals, weights learned from
data walk-forward, diversified positions, obsessive attention to trading costs.

What 8 years of NEPSE data say (bot/research.py; design period 2019-01..2024-06,
everything after is a hold-out):

  * NEPSE punishes crowd attention. The most heavily traded stocks, stocks whose
    turnover just surged, stocks pushed up into the close, circuit-chasers and
    one-day "lottery" spikes all underperform afterwards. Quiet, ignored, steady
    stocks outperform. A small account can own those; big funds cannot.
  * After a bonus/right-share price adjustment, and after a lower-circuit day,
    stocks keep underperforming. After an upper-circuit day there is no follow-through.
  * Momentum, sector rotation, calendar effects and index trend-timing have no
    reliable edge here.
  * Fixed stop-losses destroyed value: stopped-out stocks did not keep falling,
    so the stop only locked in the loss and paid another round-trip.
  * Costs decide everything (~0.9% round-trip + 7.5% CGT), so the portfolio is
    reviewed only every 60 trading days and holdings are kept while they stay
    in the better half.

Simulation realism: long-only, T+2 settlement, +/-10% circuits, VWAP fills with a
square-root market-impact cost model, 5% volume cap, SEBON commission tiers,
SEBON fee, DP charge, capital-gains tax, bonus/right adjustments, suspensions.
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
MODEL_VERSION = "v3"

# ------------------------------------------------------------------ the signals
# key: (display name, plain-English description, research factor, prior weight)
# Priors come from the 2019-2024 design-period research (sign and rough size).
# A negative weight means "high score counts AGAINST the stock".
SIGNALS = {
    "crowd": ("Crowded trading",
              "How heavily the stock is traded (60-day turnover). On NEPSE the most-traded stocks "
              "underperform and quiet ones outperform: the strongest pattern in the data.", "size", -0.32),
    "calm": ("Low volatility",
             "Calm, steady stocks (60-day volatility). Helps avoid the wild names; lags in euphoric bull runs.",
             "calm60", 0.16),
    "surge": ("Turnover surge",
              "Is the stock suddenly trading far more than its own 1-year normal? "
              "A surge of attention tends to be followed by weaker returns.", "surge", -0.12),
    "pressure": ("Closing pressure",
                 "Closing above the day's average price (VWAP). Late-day pushes tend to unwind.",
                 "pressure", -0.12),
    "nolottery": ("No lottery spikes",
                  "Stocks WITHOUT a recent huge one-day jump. Spiky 'lottery' stocks tend to disappoint later.",
                  "maxret", 0.10),
    "high52": ("Near 52-week high",
               "Stocks close to their 1-year high tend to keep doing slightly better.", "high52", 0.10),
    "chasing": ("Circuit chasing",
                "How often the stock hit the +10% upper circuit in the last 20 days. "
                "Frequent circuit-hitters tend to fall back.", "upcirc", -0.08),
}
SIG_KEYS = list(SIGNALS)


@dataclass
class Config:
    start_capital: float = 500_000.0
    max_positions: int = 16
    max_weight: float = 0.08          # per stock
    max_sector_weight: float = 0.35
    min_order: float = 10_000.0
    review_every: int = 60            # each holding is reviewed every 60 trading days (~3 months)
    stagger: int = 3                  # initial holdings get review dates spread over 3 waves
    trim_above: float | None = None   # at review, trim a winner back to target if above this weight
    buy_top_pct: float = 0.15         # new buys come from the top 15%
    keep_top_pct: float = 0.50        # at review, keep anything still in the better half
    stop_loss: float | None = None    # fixed stop-loss (research: destroys value -> off)
    trailing_stop: float | None = None
    lower_circuit_exit: bool = True   # sell after a lower-circuit day
    pressure_veto: float = 0.10       # never buy the 10% with the heaviest closing pressure
    postadj_veto_days: int = 20       # never buy within 20 days of a bonus/right adjustment
    bear_gross: float = 0.98          # no market timing: tested, it did not cut drawdowns (set <0.98 to re-enable)
    normal_gross: float = 0.98
    half_spread: float = 0.0015       # cost of crossing the spread
    impact_coef: float = 1.0          # square-root market impact: coef * daily vol * sqrt(order / day turnover)
    max_slippage: float = 0.02
    max_volume_share: float = 0.05
    halt_days: int = 60               # no trades for this long -> treated as suspended/merged
    ic_horizon: int = 40
    ic_lookback: int = 250
    ic_halflife: int = 80
    prior_mix: float = 1.0            # 1.0 = fixed research weights (more robust than re-learning; see README)
    priors: dict = field(default_factory=lambda: {k: v[3] for k, v in SIGNALS.items()})


# Account-size presets. Same model and signals; only the portfolio shape changes.
# A Rs 1 lakh account cannot hold 16 stocks: each slice (~Rs 6,000) is below a sensible
# minimum order, and fixed fees (Rs 10 commission floor, Rs 25 DP charge) would bite.
# 10 stocks of ~Rs 10,000 tested best for that size (design period, 4 start dates).
PORTFOLIOS = {
    "500k": {"name": "Rs 5 lakh", "cfg": {}},
    "100k": {"name": "Rs 1 lakh", "cfg": {"start_capital": 100_000.0, "max_positions": 10,
                                          "max_weight": 0.105, "min_order": 7_000.0}},
}


def config_for(pid: str) -> "Config":
    return Config(**PORTFOLIOS[pid]["cfg"])


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


def slippage_rate(order_value: float, day_turnover: float, daily_vol: float, cfg: Config) -> float:
    """Half the bid-ask spread plus square-root market impact (the standard
    institutional cost model). Bigger orders in thinner, jumpier stocks cost more."""
    part = order_value / max(day_turnover, 1.0)
    vol = daily_vol if daily_vol and not math.isnan(daily_vol) else 0.02
    return min(cfg.max_slippage, cfg.half_spread + cfg.impact_coef * vol * math.sqrt(part))


# ----------------------------------------------------------------- market data
class Market:
    def __init__(self, end: str | None = None):
        M = research.load(end)
        self.M = M
        self.dates: list[str] = M["dates"]
        self.didx = {d: i for i, d in enumerate(self.dates)}
        self.close, self.high, self.low = M["close"], M["high"], M["low"]
        self.vwap, self.vol, self.turn, self.prev = M["vwap"], M["volume"], M["turnover"], M["prev_close"]
        self.traded, self.ret, self.ret_raw, self.logp = M["traded"], M["ret"], M["ret_raw"], M["lp"]
        self.nepse = M["nepse"]
        self.symbols = list(self.close.columns)
        self.sector_of = M["sector"]
        self.uni_mask = research.universe_mask(M)          # may BUY
        self.hold_mask = research.hold_mask(M)             # may KEEP
        self.adj_event = research.adjustment_events(M)
        F = research.factors(M)
        self._signals = {k: F[v[2]] for k, v in SIGNALS.items()}
        self.vol20 = self.ret.rolling(20).std()
        # benchmark: the "average liquid stock" (equal-weight universe, rebalanced daily, no costs)
        ew = self.ret_raw.where(self.uni_mask.shift(1, fill_value=False)).mean(axis=1).fillna(0)
        self.ew_index = (1 + ew).cumprod()
        self._z = {}
        self._fwd = {}

    def universe(self, i: int) -> list[str]:
        if i < 120:
            return []
        row = self.uni_mask.iloc[i]
        return list(row[row].index)

    def zscores(self, i: int, extra: tuple = ()) -> pd.DataFrame:
        """Signals standardised across the buy universe. `extra` symbols (current holdings
        that left the buy universe) are scored on the same scale."""
        key = (i, tuple(sorted(extra)))
        if key not in self._z:
            uni = self.universe(i)
            syms = uni + [s for s in extra if s not in uni]
            out = {}
            for k, m in self._signals.items():
                row = m.iloc[i]
                x = row.reindex(uni).astype(float)
                med, mean, sd = x.median(), x.mean(), x.std()
                v = row.reindex(syms).astype(float).fillna(med)
                out[k] = ((v - mean) / sd).clip(-3, 3) if sd and sd > 0 else v * 0
            self._z[key] = pd.DataFrame(out).fillna(0)
        return self._z[key]

    def forward(self, h: int) -> pd.DataFrame:
        """Return over the h days after the entry day (only ever read for *past* dates)."""
        if h not in self._fwd:
            self._fwd[h] = self.logp.shift(-(1 + h)) - self.logp.shift(-1)
        return self._fwd[h]


# ------------------------------------------------------------- learning weights
class SignalLearner:
    """Walk-forward information-coefficient (IC) tracker.

    IC = rank correlation between a signal today and stock returns over the next
    `ic_horizon` days. It is only recorded once those days are in the past.
    Final weight = prior_mix x research prior + (1 - prior_mix) x recent evidence.
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
            z = self.m.zscores(j)
            f = self.m.forward(self.cfg.ic_horizon).iloc[j].reindex(uni)
            if f.notna().sum() >= 30:
                fr = f.rank()
                res = {k: (float(z[k].rank().corr(fr)) if z[k].std() > 0 else 0.0) for k in SIG_KEYS}
        self.ic[j] = res
        return res

    def health(self, i: int) -> dict | None:
        """Recent average IC of each signal (walk-forward). Positive x prior sign = still working."""
        h, L = self.cfg.ic_horizon + 1, self.cfg.ic_lookback
        hist = [self._ic_at(j) for j in range(max(120, i - h - L), i - h + 1, 3)]
        hist = [x for x in hist if x]
        if len(hist) < 30:
            return None
        df = pd.DataFrame(hist).fillna(0)
        w_ew = 0.5 ** (np.arange(len(df))[::-1] * 3 / self.cfg.ic_halflife)
        mean = df.mul(w_ew, axis=0).sum() / w_ew.sum()
        return {k: float(mean[k]) for k in SIG_KEYS}

    def weights(self, i: int) -> tuple[dict, dict]:
        prior = self.cfg.priors
        ic = self.health(i)
        if ic is None:
            return dict(prior), {k: 0.0 for k in SIG_KEYS}
        mix = self.cfg.prior_mix
        if mix >= 1:
            return dict(prior), ic
        tot = sum(abs(v) for v in ic.values()) + 1e-9
        w = {k: mix * prior[k] + (1 - mix) * ic[k] / tot for k in SIG_KEYS}
        norm = sum(abs(v) for v in w.values()) or 1
        return {k: v / norm for k, v in w.items()}, ic


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
            "days": [], "brain": None, "model": MODEL_VERSION,
        }

    # -------------------------------------------------------------- scoring
    def score(self, i):
        """-> (alpha for universe + holdings, z-scores, weights, recent ICs, rank% among the universe)"""
        uni = self.m.universe(i)
        if len(uni) < 30:
            return None
        w, ic = self.learner.weights(i)
        held = tuple(k for k in self.s["positions"] if k not in uni and k in self.m.close.columns)
        z = self.m.zscores(i, held)
        raw = (z * pd.Series(w)).sum(axis=1)
        mu, sd = raw.reindex(uni).mean(), raw.reindex(uni).std() or 1
        alpha = (raw - mu) / sd
        au = np.sort(alpha.reindex(uni).values)
        # share of the universe scoring strictly higher (0% = best)
        rank_pct = pd.Series(1 - np.searchsorted(au, alpha.values, side="right") / len(au), index=alpha.index)
        return alpha, z, w, ic, rank_pct, uni

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
        live = [o for o in orders if not o.get("quiet")]
        if live:
            self._log(day, "exec", f"Market opened: sending {len(live)} order(s) queued at the last close to the broker.")
        for o in sorted(orders, key=lambda o: o["side"] != "SELL"):  # sells first, frees cash
            sym = o["sym"]
            close, hi, lo = self._px(m.close, sym, i), self._px(m.high, sym, i), self._px(m.low, sym, i)
            vwap, vol = self._px(m.vwap, sym, i), self._px(m.vol, sym, i)
            if close is None or not vol:
                if not o.get("quiet"):
                    self._log(day, "warn", f"{o['side']} {sym}: stock did not trade today. "
                              + ("Order expired." if o["side"] == "BUY" else "Will keep trying."), sym=sym)
                self._fail(o, day, "not traded")
                continue
            r = self.m.ret.at[m.dates[i], sym]
            turn = self._px(m.turn, sym, i) or vol * vwap
            dvol = self._px(m.vol20, sym, i)
            if o["side"] == "BUY":
                if len(s["positions"]) >= cfg.max_positions:
                    self._log(day, "warn", f"BUY {sym}: cancelled, no free slot (an earlier sell has not "
                              "fully completed yet).", sym=sym)
                    self._fail(o, day, "no free slot")
                    continue
                if r >= 0.095 and close >= hi * 0.999:
                    self._log(day, "warn", f"BUY {sym}: price locked at upper circuit (+10%), no sellers. "
                              "Order not filled.", sym=sym)
                    self._fail(o, day, "upper circuit")
                    continue
                qty = min(o["qty"], int(vol * cfg.max_volume_share))
                slip = slippage_rate(qty * vwap, turn, dvol, cfg)
                price = min(hi, vwap * (1 + slip))
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
                    "alpha": o["alpha"], "no_trade": 0,
                    "review_on": self._review_index(i, len(s["positions"])),
                }
                self._trade(day, o, i, qty, price, amt, com, seb, 0, 0, None, slip=round(slip * 100, 3))
                self._log(day, "buy", f"BOUGHT {qty} × {sym} @ Rs {price:,.2f} = Rs {amt:,.0f} "
                          f"(+ Rs {com + seb:,.0f} broker & SEBON fees, {slip * 100:.2f}% slippage). "
                          "Shares arrive in demat in T+2.", sym=sym)
            else:
                p = s["positions"].get(sym)
                if not p:
                    continue
                if r <= -0.095 and close <= lo * 1.001:
                    self._log(day, "warn", f"SELL {sym}: price locked at lower circuit (−10%), no buyers. "
                              "Will retry next session.", sym=sym)
                    self._fail(o, day, "lower circuit")
                    continue
                want = min(p["qty"], o["qty"]) if o.get("trim") else p["qty"]
                qty = min(want, max(10, int(vol * cfg.max_volume_share)))
                slip = slippage_rate(qty * vwap, turn, dvol, cfg)
                price = max(lo, vwap * (1 - slip))
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
                            entry=p["entry_price"], entry_date=p["entry_date"], slip=round(slip * 100, 3))
                emo = "profit" if net >= 0 else "loss"
                self._log(day, "sell", f"SOLD {qty} × {sym} @ Rs {price:,.2f} = Rs {amt:,.0f}. "
                          f"Net {emo} after all fees & tax: Rs {net:,.0f} ({net / basis * 100:+.1f}%).", sym=sym)
                if qty >= p["qty"]:
                    del s["positions"][sym]
                else:
                    p["cost"] -= basis
                    p["qty"] -= qty
                    if not o.get("trim"):
                        s["pending"].append(dict(o, qty=p["qty"]))  # sell the rest next session
                    elif want - qty >= 10:
                        s["pending"].append(dict(o, qty=want - qty))

    def _review_index(self, i, n_held):
        """Trading-day index of a new holding's first review. The very first batch is spread
        over `stagger` waves so the whole portfolio is never re-decided on one (lucky or
        unlucky) day; after that every holding simply follows its own clock."""
        cfg = self.cfg
        first_batch = self.tdays_between(self.s["inception"], self.m.dates[i]) <= 5
        wave = (n_held % cfg.stagger) + 1 if first_batch and cfg.stagger > 1 else cfg.stagger
        return i + round(cfg.review_every * wave / max(cfg.stagger, 1))

    def _fail(self, o, day, why):
        if not o.get("quiet"):
            self.s["trades"].append({"date": day["date"], "side": o["side"], "sym": o["sym"],
                                     "status": "REJECTED", "why": why, "reason": o["reason"]})
        if o["side"] == "SELL" and o["sym"] in self.s["positions"]:
            # keep trying to get out, but don't repeat the same message every day
            self.s["pending"].append(dict(o, quiet=(why == "not traded")))

    def _trade(self, day, o, i, qty, price, amt, com, seb, dp, cgt, net, **kw):
        self.s["trades"].append({
            "date": day["date"], "side": o["side"], "sym": o["sym"], "status": "FILLED",
            "qty": qty, "price": round(price, 2), "amount": round(amt, 2),
            "fees": round(com + seb + dp, 2), "cgt": round(cgt, 2),
            "pnl": None if net is None else round(net, 2), "reason": o["reason"], **kw})

    def _mark(self, day, i):
        m, s = self.m, self.s
        for sym in list(s["positions"]):
            p = s["positions"][sym]
            c = self._px(m.close, sym, i)
            if c is not None:
                p["last"] = c
                p["peak"] = max(p["peak"], c)
                p["no_trade"] = 0
            else:
                p["no_trade"] = p.get("no_trade", 0) + 1
            p["days_held"] = self.tdays_between(p["entry_date"], m.dates[i])
            p["settled"] = p["days_held"] >= 2
            if p["no_trade"] >= self.cfg.halt_days:
                # Suspended for a merger/acquisition: in reality the shares convert into the
                # acquirer's shares. Without the swap ratio the neutral assumption is "worth
                # what it last traded at"; release that value so the slot can be reused.
                val = p["qty"] * p["last"]
                net = val - p["cost"]
                s["cash"] += val
                s["trades"].append({"date": day["date"], "side": "SELL", "sym": sym, "status": "FILLED",
                                    "qty": p["qty"], "price": p["last"], "amount": round(val, 2), "fees": 0,
                                    "cgt": 0, "pnl": round(net, 2), "entry": p["entry_price"],
                                    "entry_date": p["entry_date"],
                                    "reason": f"suspended {self.cfg.halt_days} trading days (likely merger): "
                                              "settled at last traded price"})
                s["pending"] = [o for o in s["pending"] if o["sym"] != sym]
                self._log(day, "warn", f"{sym} has not traded for {self.cfg.halt_days} trading days (suspended, "
                          f"probably for a merger). Settled at its last price, Rs {p['last']:,.1f}; in real life "
                          "you would receive the merged company's shares.", sym=sym)
                del s["positions"][sym]

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
        m, cfg, d = self.m, self.cfg, self.m.dates[i]
        r = m.ret_raw
        upper_today = r.loc[d].reindex(uni) >= 0.095
        lower_5d = (r.iloc[i - 4:i + 1][uni] <= -0.095).any()
        press = m._signals["pressure"].iloc[i].reindex(uni)
        heavy = press.rank(ascending=False, pct=True) <= cfg.pressure_veto
        k = cfg.postadj_veto_days
        adj = m.adj_event.iloc[max(0, i - k + 1):i + 1][uni].any() if k else pd.Series(False, index=uni)
        out = {}
        for sym in uni:
            why = []
            if upper_today.get(sym, False):
                why.append("hit the upper circuit today (chasing doesn't pay)")
            if lower_5d.get(sym, False):
                why.append("hit the lower circuit in the last 5 days (weakness tends to continue)")
            if adj.get(sym, False):
                why.append(f"bonus/right price adjustment in the last {k} days (stocks tend to sag after)")
            if heavy.get(sym, False):
                why.append("heavy closing pressure (late-day push)")
            if why:
                out[sym] = why
        return out

    def _decide(self, day, i):
        m, cfg, s = self.m, self.cfg, self.s
        sc = self.score(i)
        if sc is None:
            self._log(day, "think", "Not enough price history yet to trade safely. Waiting.")
            return
        alpha, z, weights, ic, rank_pct, uni = sc
        regime, gross = self.regime(i)
        veto = self.vetoes(i, uni)
        hold_ok = m.hold_mask.iloc[i]
        self._log(day, "think", f"Scanned {len(uni)} liquid stocks (skipped funds, bonds, debentures and the "
                  f"least-traded 40%). Computed {len(SIG_KEYS)} signals for each.")
        top_sig = sorted(weights.items(), key=lambda kv: -abs(kv[1]))[:3]
        src = "from 8 years of NEPSE research" if cfg.prior_mix >= 1 else \
            f"{cfg.prior_mix * 100:.0f}% from 8 years of NEPSE research, {(1 - cfg.prior_mix) * 100:.0f}% from the last year"
        self._log(day, "learn", f"Signal trust ({src}): strongest → "
                  + ", ".join(f"{SIGNALS[k][0]} ({v * 100:+.0f}%)" for k, v in top_sig) + ".")
        nv = {k: sum(1 for w in veto.values() if any(k in x for x in w))
              for k in ("upper", "lower", "adjustment", "pressure")}
        self._log(day, "risk", f"Never-buy filters: {nv['upper']} at upper circuit today, {nv['lower']} hit the "
                  f"lower circuit recently, {nv['adjustment']} just had a bonus/right adjustment, "
                  f"{nv['pressure']} with heavy closing pressure.")
        self._log(day, "risk", f"Market regime: {regime}. Maximum money in stocks: {gross * 100:.0f}%.")

        legacy = [k for k, p in s["positions"].items() if "review_on" not in p]
        if legacy:
            # Holdings bought by an older model version: review them in waves, worst-ranked
            # first, instead of dumping everything at once (cost-aware transition).
            legacy.sort(key=lambda k: -(float(rank_pct[k]) if k in rank_pct.index else 1.0))
            for n, k in enumerate(legacy):
                wave = n % max(cfg.stagger, 1) + 1
                s["positions"][k]["review_on"] = i + round(cfg.review_every * wave / max(cfg.stagger, 1))
            self._log(day, "think", f"Model upgraded to {MODEL_VERSION}. The {len(legacy)} existing holdings stay; "
                      f"they will be re-checked in {max(cfg.stagger, 1)} waves over the next {cfg.review_every} "
                      "trading days (weakest-ranked first) to avoid paying fees to switch all at once.")
        due = [k for k, p in s["positions"].items() if i >= p["review_on"]]
        upcoming = [p["review_on"] - i for p in s["positions"].values() if p["review_on"] > i]
        nxt = min(upcoming) if upcoming else 0
        if due:
            self._log(day, "think", f"📅 Review day for {len(due)} holding(s): {', '.join(due)}. Each holding is "
                      f"re-checked every {cfg.review_every} trading days (≈3 months) after it was bought.")
        elif s["positions"]:
            self._log(day, "think", f"No holding is due for review (next one in {nxt} trading days). Only checking "
                      "safety exits and filling empty slots. Low turnover = low fees.")

        eq = self.equity()
        sells, stuck = set(), set()
        r_today = m.ret_raw.iloc[i]
        for sym, p in s["positions"].items():
            if any(o["sym"] == sym and o["side"] == "SELL" and not o.get("trim") for o in s["pending"]):
                stuck.add(sym)     # an earlier sell has not filled yet: its slot is NOT free
                continue
            why = None
            ret = p["last"] / p["entry_price"] - 1
            rk = float(rank_pct[sym]) if sym in rank_pct.index else None
            if cfg.stop_loss and p["last"] <= p["entry_price"] * (1 - cfg.stop_loss):
                why = f"stop-loss ({ret * 100:.1f}% from entry)"
            elif cfg.trailing_stop and p["last"] <= p["peak"] * (1 - cfg.trailing_stop):
                why = f"trailing stop: fell {(1 - p['last'] / p['peak']) * 100:.1f}% from its peak"
            elif cfg.lower_circuit_exit and r_today.get(sym, 0) <= -0.095:
                why = "hit the lower circuit today; on NEPSE such stocks tend to keep falling"
            elif sym in due and (rk is None or not bool(hold_ok.get(sym, False))):
                why = "became too thinly traded to hold safely"
            elif sym in due and rk > cfg.keep_top_pct:
                why = (f"signal faded at its review (now top {max(rk, 0.01) * 100:.0f}%, "
                       f"needs top {cfg.keep_top_pct * 100:.0f}%)")
            elif sym in due:
                p["review_on"] = i + cfg.review_every
                target_w = min(cfg.max_weight, gross / cfg.max_positions)
                wgt = p["qty"] * p["last"] / eq
                if cfg.trim_above and wgt > cfg.trim_above and p["settled"]:
                    cut = int(p["qty"] * (1 - target_w / wgt))
                    if cut >= 10:
                        s["pending"].append({"side": "SELL", "sym": sym, "qty": cut, "trim": True,
                                             "reason": f"trim: grew to {wgt * 100:.0f}% of the portfolio, "
                                                       f"back to {target_w * 100:.0f}%",
                                             "alpha": float(alpha.get(sym, 0)), "signals": {}})
                        self._log(day, "plan", f"Plan TRIM {sym}: it grew to {wgt * 100:.0f}% of the portfolio. "
                                  f"Selling {cut} shares to bring it back to {target_w * 100:.0f}% "
                                  "(don't let one stock decide everything).", sym=sym)
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
                where = f"ranked top {max(rk, 0.01) * 100:.0f}%" if rk is not None else "not scored today"
                self._log(day, "hold", f"Keep {sym}: {ret * 100:+.1f}% so far, {where}.", sym=sym)

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
        planned = 0
        if slots > 0 and budget >= cfg.min_order:
            target = min(cfg.max_weight, gross / cfg.max_positions)
            order = alpha.reindex(uni).sort_values(ascending=False)
            pos_in_uni = {sym: n + 1 for n, sym in enumerate(order.index)}
            for sym in order.index:
                if slots <= 0 or budget < cfg.min_order or rank_pct[sym] > cfg.buy_top_pct:
                    break
                if sym in s["positions"]:
                    continue
                if sym in veto:
                    self._log(day, "risk", f"Skip {sym} (ranked #{pos_in_uni[sym]}): {veto[sym][0]}.", sym=sym)
                    continue
                sec = m.sector_of.get(sym)
                room = cfg.max_sector_weight - sect_w.get(sec, 0)
                if room < 0.03:
                    self._log(day, "risk", f"Skip {sym}: {sec} sector already at its "
                              f"{cfg.max_sector_weight * 100:.0f}% limit.", sym=sym)
                    continue
                w = min(target, room)
                amt = min(w * eq, budget)
                px = float(m.close.at[m.dates[i], sym])
                qty = int(amt / (px * 1.02))
                if qty < 10 or amt < cfg.min_order:
                    continue
                contrib = (z.loc[sym] * pd.Series(weights)).sort_values(ascending=False)
                top = [SIGNALS[k][0] + ("" if weights[k] > 0 else " (low)") for k, v in contrib.items() if v > 0][:3]
                reason = f"Ranked #{pos_in_uni[sym]} of {len(uni)}; driven by " + ", ".join(top)
                s["pending"].append({"side": "BUY", "sym": sym, "qty": qty, "reason": reason,
                                     "alpha": round(float(alpha[sym]), 3),
                                     "signals": {k: round(float(z.at[sym, k]), 2) for k in SIG_KEYS}})
                self._log(day, "plan", f"Plan BUY ~{qty} × {sym} (≈Rs {qty * px:,.0f}, "
                          f"{qty * px / eq * 100:.0f}% of portfolio). Reason: {reason}.", sym=sym)
                sect_w[sec] = sect_w.get(sec, 0) + w
                budget -= qty * px * 1.01
                slots -= 1
                planned += 1
            if slots > 0 and planned == 0 and not due:
                self._log(day, "think", "Empty slots, but no stock passed every filter today. Waiting.")
        if planned == 0 and not sells and not stuck:
            self._log(day, "think", "No trades today. Doing nothing is often the best decision: every round-trip "
                      "costs ~0.9% plus 7.5% tax on profit.")
        live = [o for o in s["pending"] if not o.get("quiet")]
        if live:
            self._log(day, "exec", f"{len(live)} order(s) queued for the next trading session.")

        top_n = alpha.reindex(uni).sort_values(ascending=False).head(15).index
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
        s["brain"]["health"] = {k: ("ok" if ic[k] * cfg.priors[k] > 0 else "weak") for k in SIG_KEYS} if any(ic.values()) else {}
        s.setdefault("health_history", []).append({"date": m.dates[i], **{k: round(v, 4) for k, v in ic.items()}})
        s["model"] = MODEL_VERSION


# ------------------------------------------------------------------ metrics
def _seg(eq: pd.DataFrame, start_capital: float):
    e = eq["equity"].astype(float)
    r = e.pct_change().dropna()
    nep = eq["nepse"].astype(float).ffill()
    ew = eq["ew"].astype(float) if "ew" in eq else None
    n = len(e)
    yrs = n / 235
    rn = nep.pct_change().reindex(r.index).fillna(0)
    beta = float(np.cov(r, rn)[0, 1] / rn.var()) if len(r) > 60 and rn.var() > 0 else None
    return {
        "from": eq["date"].iloc[0], "to": eq["date"].iloc[-1],
        "return_pct": round((e.iloc[-1] / e.iloc[0] - 1) * 100, 2),
        "nepse_return_pct": round((nep.iloc[-1] / nep.iloc[0] - 1) * 100, 2) if nep.notna().any() else None,
        "ew_return_pct": round((ew.iloc[-1] / ew.iloc[0] - 1) * 100, 2) if ew is not None else None,
        "cagr_pct": round(((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs >= 0.9 else None,
        "nepse_cagr_pct": round(((nep.iloc[-1] / nep.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs >= 0.9 else None,
        "max_drawdown_pct": round(float((e / e.cummax() - 1).min()) * 100, 2),
        "nepse_drawdown_pct": round(float((nep / nep.cummax() - 1).min()) * 100, 2),
        "sharpe": round(float(r.mean() / r.std() * math.sqrt(235)), 2) if len(r) > 10 and r.std() > 0 else None,
        "nepse_sharpe": round(float(rn.mean() / rn.std() * math.sqrt(235)), 2) if len(r) > 10 and rn.std() > 0 else None,
        "vol_pct": round(float(r.std() * math.sqrt(235)) * 100, 1) if len(r) > 10 else None,
        "beta": None if beta is None else round(beta, 2),
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
        "costs_total": round(sum(state["fees"].values()), 2),
    })
    if split and eq["date"].iloc[0] < split < eq["date"].iloc[-1]:
        a = eq[eq["date"] <= split]
        b = eq[eq["date"] >= a["date"].iloc[-1]]
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
