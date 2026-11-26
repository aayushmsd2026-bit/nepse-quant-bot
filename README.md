# 🏔️ NEPSE Quant Lab: a transparent paper-trading bot for Nepal

A fully automatic, **open** experiment. A quant bot starts with **Rs 5,00,000 of virtual money** and trades every NEPSE trading day using **real end-of-day market data**. Every decision, trade, fee and rupee of tax is logged and shown on a public dashboard.

> ⚠️ Simulation only: no real money, no broker connection, not investment advice. The model is *inspired by* the publicly known principles of Renaissance Technologies. It is not affiliated with them, and their real models are secret.

## How it runs

```
GitHub Actions (after the 3 PM close, Sun–Fri)
  └─ bot/fetch.py       → downloads the day's prices + NEPSE index; rejects half-day or incomplete data
  └─ bot/engine.py      → fills yesterday's queued orders, marks to market, scores, plans
  └─ bot/run.py         → saves state atomically, rebuilds the historical simulation, writes docs/data/*.json
  └─ tests/             → safety tests; nothing is committed or published if they fail
  └─ git commit         → docs/ is published to the gh-pages branch = the live dashboard
```

## The model: Sagarmatha Signal Engine v3

Built from **8 years of NEPSE data (2019–2026, ~1,800 trading days, 475 stocks including merged and delisted ones)**.

**Method:** every design decision used 2019-01 → 2024-06 only. 2024-07 → today is a **hold-out**, run once after the model was frozen.

### The core finding: NEPSE punishes crowd attention
| Pattern (design period) | In the bot |
|---|---|
| **Heavily traded stocks lag, quiet ones lead.** Strongest pattern (t ≈ −3, same sign in 5 of 6 design years). A small account can own quiet stocks; big funds cannot. | Biggest weight (−32%) |
| **Turnover surges** (trading far above the stock's 1-year norm) are followed by weaker returns | Weight −12% |
| **Late-day pushes unwind:** closing above VWAP predicts lower returns | Weight −12% + never-buy rule |
| **Calm beats wild; lottery-like spikes disappoint; circuit-chasers fall back** | Weights +16%, +10%, −8% |
| Near 52-week high: small positive | Weight +10% |
| **After a lower-circuit day stocks keep falling; after a bonus/right adjustment they sag for ~20 days** | Never-buy rules + exit |
| Momentum, sector rotation, calendar effects, average trade size, beta | Not used: no reliable edge |
| **Stop-losses:** stopped-out stocks did not keep falling. Stops cut return from +238% to +183% and doubled trading | Removed |
| **Market timing / bear-market cash filter:** did not reduce the worst drop | Removed |
| **Re-learning weights daily:** +156% vs +238% with fixed weights, and a deeper worst drop | Weights fixed; health monitor only |

### Two live portfolios
| | Rs 5 lakh | Rs 1 lakh |
|---|---|---|
| Stocks | 16, about Rs 30,000 each | 10, about Rs 10,000 each |
| Why | — | 16 slices of Rs 6,000 fall under the minimum order and fixed fees (Rs 10 commission floor, Rs 25 DP charge) bite; 10 stocks tested best for this size |
| State file | `data/live_state.json` | `data/live_state_100k.json` |

Same model, signals and rules otherwise. Switch between them at the top of the dashboard (or open it with `?pf=100k`).

### Rules
- **Universe:** common stocks in the top 60% by turnover with at least Rs 10 lakh/day.
- **Portfolio:** 16 stocks, about 6% each (max 8%), max 35% per sector, bought from the top 15% of scores. Always ~98% invested.
- **Review:** each holding is re-checked every 60 trading days after purchase and kept while it stays in the better half. Reviews are staggered, so the whole book is never re-decided on one day.
- **Exits between reviews:** only a lower-circuit day. No stop-losses.
- **Never buy:** upper circuit today, lower circuit in the last 5 days, bonus/right adjustment in the last 20 days, or the 10% with the heaviest closing pressure.

### Simulator realism
- T+2 settlement, no intraday. Orders decided after the close, filled next session at VWAP.
- **Market-impact cost model:** half-spread 0.15% + daily volatility × √(order ÷ day's turnover). Thin stocks and big orders cost more (average ≈ 0.45% per side for this strategy).
- ±10% circuits (locked stocks don't fill), max 5% of a day's volume, minimum 10 shares.
- SEBON commission tiers, SEBON fee, DP charge, CGT 7.5% / 5%.
- Bonus/right adjustments credited; suspended stocks settled at the last traded price after 60 no-trade days.
- Cash dividends are *not* counted (conservative).

## Honest results (all costs and taxes)

| | Design 2019-10 → 2024-06 | Hold-out 2024-07 → 2026-11 |
|---|---|---|
| v1 (generic signals) | −30.2% | −4.7% |
| v2 (first NEPSE model) | +59.5% | +17.9% |
| **v3, four start dates** | **+197% to +268%** (avg +238%) | **+32% to +37%** (avg +34.7%) |
| **v3, fresh Rs 5 lakh on 2024-07-01** | — | **+14.4%** |
| NEPSE index | +78% | +30.0% |
| Average liquid stock (no costs) | +194% | +16.9% |

**How to read this:**
- **v3 is a large improvement in the design period** and stays good under every setting change tested (+156% to +256% across 16 variations; +231% with trading costs doubled).
- **The hold-out is mixed.** Portfolios already running since 2019 finished a little ahead of NEPSE. A fresh portfolio started in July 2024 finished **behind NEPSE** and about level with the average stock. The monthly edge over NEPSE in the hold-out is statistically zero (t ≈ 0.1).
- **Why:** in 2025–26 the large, heavily traded banks led and quiet stocks lagged, the opposite of the model's main bet. The dashboard's signal health monitor shows this.
- **Expectation:** roughly the return of the average NEPSE stock; ahead of the index in broad rallies, behind it when a few big names lead. Not a multiple of the index.
- **Capacity:** about Rs 20 lakh. Beyond that, quiet stocks cannot absorb the orders.

Reproduce: `python -m bot.research` (patterns) and `python -m bot.robustness` (start-date, sensitivity, hold-out; ~10 min).

**Likely next real edges** need data this bot doesn't have: fundamentals (EPS, P/E, book value, dividends), floorsheet broker accumulation, and book-closure/AGM dates.

## Quick trades (1–2 weeks): what history says

`python -m bot.shortterm` tests 11 short-term setups as real trades: buy at the next session's VWAP, sell 3/5/10 trading days later (T+2 makes 3 the minimum), ~1.4% round-trip costs + 7.5% CGT, compared with the average stock, design vs hold-out.

- **No setup made money after costs** in either period. Chasing (upper circuits, volume spikes, late pushes, rallies, breakouts) is reliably worse than the market.
- **The one real pattern is pre-announcement:** before a 10%+ bonus is announced, the stock beats the market by ~4.6% over 20 trading days, then gives ~2% back after. Buying on the public announcement (data: `bot/events.py`, ShareSansar dividend calendar, 2018→) did not beat the market.
- **Next data:** `bot/floorsheet.py` now saves a daily broker summary (only today's floorsheet is ever published), so broker-accumulation signals can be tested after 6–12 months. Quarterly earnings dates, intraday depth and timestamped news are not freely available.

The dashboard's **Quick trades** section shows all of this plus a daily radar of stocks that triggered each setup.

## Chart picks (2–3 weeks)

`python -m bot.technical` turns a chart analyst's toolkit into exact rules (20/50/200-day averages, MACD, RSI, ADX/DMI, squeeze breakouts, pullbacks, higher lows, 52-week highs, relative strength, volume). Each week it ranks liquid stocks on an 8-point bullish checklist and shows the top picks with ATR-based stop and target levels.

**Backtest (buy next-day VWAP, hold 15 trading days, after costs):** the weekly top 5 went up 47% of the time in 2019–mid-2024 (random stock: 44%) but only 33% in mid-2024–2026 (random: 37%). None of the 11 chart signals beat random picks reliably. The picks are shown as an experiment, and every week's top 5 is logged in `data/swing_log.json` and judged 15 trading days later, so the dashboard keeps a live, uneditable scoreboard.

## Run it yourself

```bash
pip install -r requirements.txt
python -m bot.fetch backfill 2019-01-01   # one-time history download (~10 min)
python -m bot.research                    # NEPSE pattern research report
python -m bot.robustness                  # robustness report (~10 min)
python -m pytest -q                       # safety tests
python -m bot.run                         # daily: fetch + trade + publish
python -m http.server -d docs 8000        # view dashboard at localhost:8000
```

Delete `data/live_state.json` to restart the live portfolio from Rs 5,00,000.

## Data source
End-of-day prices from public ShareSansar pages (`today-share-price`, `index-history-data`). No real-time data and no order placement. NEPSE offers no public trading API to retail investors.
