# 🏔️ NEPSE Quant Lab: a transparent paper-trading bot for Nepal

A fully automatic, **open** experiment. A quant bot starts with **Rs 5,00,000 of virtual money** and trades every NEPSE trading day using **real end-of-day market data**. Every decision, trade, fee and rupee of tax is logged and shown on a public dashboard.

> ⚠️ Simulation only: no real money, no broker connection, not investment advice. The model is *inspired by* the publicly known principles of Renaissance Technologies. It is not affiliated with them, and their real models are secret.

## How it runs

```
GitHub Actions (after 3 PM NPT, Sun–Fri)
  └─ bot/fetch.py   → downloads today's prices for all stocks + NEPSE index (ShareSansar)
  └─ bot/engine.py  → executes yesterday's queued orders, marks to market, thinks, plans
  └─ bot/run.py     → saves state, rebuilds the historical simulation, writes docs/data/*.json
  └─ git commit     → docs/ is published to the gh-pages branch = the live dashboard
```

## The model: Sagarmatha Signal Engine v2

v2 was rebuilt from **8 years of NEPSE data (2019–2026, ~1,800 trading days, 475 stocks
including merged/delisted ones)**. Research: `python -m bot.research`.

**Method, to avoid fooling ourselves:** the model was designed on 2019-01 → 2024-06 only.
2024-07 → today is a **hold-out** it never saw. Patterns that didn't survive the hold-out were rejected.

### What the research found about NEPSE
| Pattern | Evidence | In the bot? |
|---|---|---|
| **Closing pressure reverses.** Closing above VWAP → lower returns next week | Negative in 7–8 of 8 years, both periods | ✅ strongest signal (negative) |
| **Boring beats exciting.** Low volatility, no lottery spikes | Strong in 2019, 2022, 2025–26; failed in the 2020–21 mania | ✅ weight adapts |
| **Lower circuit → keeps falling** (~−2.5% next week); **upper circuit → no follow-through** | Both periods | ✅ never-buy rules + exit on lower circuit |
| Near 52-week high, short-term bounce | Small, mostly consistent | ✅ small weight |
| 3/6/12-month momentum, sector rotation | No reliable edge; negative in hold-out | ⛔ zero starting weight |
| Market timing (index vs 20/50/100/200-day average) | Helped 2019–24, failed badly in hold-out (−8% vs +29%) | ⛔ only a confirmed-bear filter |
| Turnover-regime switching | Looked good in design, reversed in hold-out | ⛔ rejected |
| Calendar months / festival effects | Mostly noise with 7–8 years of data | ⛔ not used |
| **Signals spot losers better than winners**; NEPSE costs (~0.85% + 7.5% CGT) punish turnover | Top-decile picks ≈ market before costs | ✅ 40-day review, keep while in top 35% |

### Rules
- 8 signals. Trust = **50% research prior + 50% learned walk-forward** from the last ~250 days (20-day information coefficient).
- **Never buy:** a stock at the upper circuit today, one that hit the lower circuit in the last 5 days, or the 10% with the heaviest closing pressure.
- **Portfolio:** ~10–12 stocks, max 12% each, 35% per sector. Buy from the top 15%; at each **40-trading-day review**, sell anything no longer in the top 35%.
- **Daily safety exits:** −15% from entry, −22% from peak, or a lower-circuit day.
- **Exposure:** 97% normally, 60% only in a confirmed bear market (NEPSE below a falling 200-day average).
- Liquidity: top 60% of stocks by turnover and at least Rs 10 lakh/day; funds, bonds, debentures and promoter shares excluded.

### NEPSE realism built into the simulator
- **T+2 settlement:** no selling until shares settle. No intraday trading.
- Orders are decided after the close and **filled next session at VWAP ± 0.25% slippage**.
- **±10% circuits:** buys fail at upper-circuit lock; sells fail at lower-circuit lock and retry.
- Max 5% of a stock's daily volume. Minimum 10 shares.
- **Broker commission** (SEBON tiers 0.36% → 0.24%), **SEBON fee** 0.015%, **DP charge** Rs 25 per sell, **CGT** 7.5% (<1 year) / 5%.
- Bonus/right-share price adjustments are detected and holdings adjusted. Cash dividends are *not* counted, which is conservative.

## Honest results (same 7 years, 2019-10 → 2026-11, all costs and taxes)

| | Design 2019-10 → 2024-06 | **Hold-out 2024-07 → 2026-11** | Whole period |
|---|---|---|---|
| v1 (generic signals) | −30.2% | −4.7% | |
| **v2 (NEPSE research)** | **+59.5%** | **+17.9%** | **+88.1% (9.9%/yr)** |
| NEPSE index | +77.8% | +30.3% | +131.8% (13.4%/yr) |
| Average liquid stock (no costs) | +192.6% | +17.1% | +242.6% |

**Takeaways:**
- **v2 is far better than v1.**
- It beat NEPSE in 5 of 8 calendar years (2019, 2021, 2022, 2024, 2026).
- In the hold-out it roughly matched the average stock.
- It **still trails the NEPSE index overall**, mainly because it badly missed the 2020 COVID-rebound mania (+29% vs +79%).
- Its worst drop (−55%, 2021–23) was deeper than NEPSE's (−43%).

**The likely next real edges** need data this bot doesn't have yet:
- fundamentals: EPS, P/E, book value, dividends
- floorsheet broker accumulation
- book-closure and AGM dates

## Run it yourself

```bash
pip install -r requirements.txt
python -m bot.fetch backfill 2019-01-01   # one-time history download (~10 min)
python -m bot.research                    # NEPSE pattern research report
python -m bot.run                         # daily: fetch + trade + publish
python -m http.server -d docs 8000        # view dashboard at localhost:8000
```

Delete `data/live_state.json` to restart the live portfolio from Rs 5,00,000.

## Data source
End-of-day prices from public ShareSansar pages (`today-share-price`, `index-history-data`). No real-time data and no order placement. NEPSE offers no public trading API to retail investors.
