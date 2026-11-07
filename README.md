# 🏔️ NEPSE Quant Lab: a transparent paper-trading bot for Nepal

A fully automatic, **open** experiment. A quant bot starts with **Rs 5,00,000 of virtual money** and trades every NEPSE trading day using **real end-of-day market data**. Every decision, trade, fee and rupee of tax is logged and shown on a public dashboard.

> ⚠️ Simulation only: no real money, no broker connection, not investment advice. The model is *inspired by* the publicly known principles of Renaissance Technologies. It is not affiliated with them, and their real models are secret.

## How it runs

```
GitHub Actions (after 3 PM NPT, Sun–Fri)
  └─ bot/fetch.py   → downloads today's prices for all stocks + NEPSE index (ShareSansar)
  └─ bot/engine.py  → executes yesterday's queued orders, marks to market, thinks, plans
  └─ bot/run.py     → saves state, rebuilds the historical simulation, writes docs/data/*.json
  └─ git commit     → GitHub Pages serves docs/ as the live dashboard
```

## The model: "Sagarmatha Signal Engine"

These are the Renaissance-style principles, adapted to NEPSE:

| Principle | What the bot does |
|---|---|
| Many weak signals, not one big idea | 8 signals: short-term bounce, 3-month momentum, rubber band (stretch vs 20-day average), closing pressure (close vs VWAP), volume confirmation, low volatility, trend quality, sector rotation |
| Let the data decide | Every day it measures each signal's **information coefficient** (rank correlation with the next 20 days' returns) over the last 120 days. It uses only the past, walk-forward. Weights = 20% prior + 80% learned. Signals can turn negative if they work in reverse. |
| Costs matter | Reviews the portfolio only every 20 trading days. Holdings are kept unless they fall out of the top 40%. |
| Risk first | Max 10 stocks, 15% per stock, 35% per sector. Market-mood filter on the NEPSE trend (95% / 75% / 45% max invested). Emergency exits at −15% from entry or −20% from peak. |

### NEPSE realism built into the simulator
- **T+2 settlement:** shares can't be sold until 2 trading days after purchase. No intraday trading.
- Orders are decided after the close and **filled next session at VWAP ± 0.25% slippage**.
- **±10% circuit:** buys fail at upper-circuit lock; sells fail at lower-circuit lock.
- Max 5% of a stock's daily volume. Minimum 10 shares.
- **Broker commission** (SEBON tiers 0.36% → 0.24%), **SEBON fee** 0.015%, **DP charge** Rs 25 per sell, **CGT** 7.5% (<1 year) / 5%.
- Bonus/right-share price adjustments are detected and holdings adjusted.
- Excludes mutual funds, bonds, debentures, preference/promoter shares and illiquid stocks (< Rs 50 lakh per day).

## Honest results so far

The **historical simulation** (Sep 2025 → Nov 2026, which includes the Sep 2025 shutdown and crash) is **negative: about −7.5% vs NEPSE −3.9%**.

Research notes from building it:
- Before costs, the learned ranking *did* beat the average stock (top-10 ≈ +8% vs universe ≈ −8%).
- NEPSE's ~0.8% round-trip cost plus 7.5% CGT ate most of that edge when trading every 10 days. Moving to a 20-day cycle cut costs by more than half.
- In this data, **low volatility** was the strongest signal. **Closing pressure works in reverse**: closing above VWAP predicted *lower* returns, probably end-of-day pushes that unwind.
- Parameters were deliberately *not* tuned further to make the backtest look good. The live run is the real test.

## Run it yourself

```bash
pip install -r requirements.txt
python -m bot.fetch backfill 2025-04-01   # one-time history download
python -m bot.run                         # daily: fetch + trade + publish
python -m http.server -d docs 8000        # view dashboard at localhost:8000
```

Delete `data/live_state.json` to restart the live portfolio from Rs 5,00,000.

## Data source
End-of-day prices from public ShareSansar pages (`today-share-price`, `index-history-data`). No real-time data and no order placement. NEPSE offers no public trading API to retail investors.
