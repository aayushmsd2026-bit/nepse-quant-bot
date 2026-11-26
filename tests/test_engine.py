"""Safety tests for the simulator. Run with:  python -m pytest -q

They check the things that would silently make results wrong: fee maths, the
accounting identity, NEPSE trading rules, data integrity and, most importantly,
that the bot can never see the future.
"""
import csv
import math
from pathlib import Path

import pandas as pd
import pytest

from bot import engine as E

ROOT = Path(__file__).resolve().parent.parent
SLICE = ("2023-01-01", "2024-03-31")


@pytest.fixture(scope="module")
def market():
    return E.Market()


@pytest.fixture(scope="module")
def run(market):
    eng = E.Engine(market, E.Config())
    days = [d for d in market.dates if SLICE[0] <= d <= SLICE[1]]
    snaps = []
    for d in days:
        eng.run_day(d)
        snaps.append((d, len(eng.s["positions"]), eng.equity()))
    return eng, days, snaps


# ---------------------------------------------------------------- fee maths
def test_commission_tiers():
    assert E.commission(1_000) == 10.0                      # minimum Rs 10
    assert E.commission(50_000) == pytest.approx(180.0)     # 0.36%
    assert E.commission(100_000) == pytest.approx(330.0)    # 0.33%
    assert E.commission(1_000_000) == pytest.approx(3100.0)  # 0.31%
    assert E.commission(5_000_000) == pytest.approx(13500.0)  # 0.27%
    assert E.commission(20_000_000) == pytest.approx(48000.0)  # 0.24%


def test_slippage_model():
    cfg = E.Config()
    small = E.slippage_rate(10_000, 10_000_000, 0.02, cfg)
    big = E.slippage_rate(400_000, 10_000_000, 0.02, cfg)
    thin = E.slippage_rate(400_000, 1_000_000, 0.02, cfg)
    assert cfg.half_spread < small < big < thin              # bigger / thinner costs more
    assert E.slippage_rate(1e9, 1.0, 0.05, cfg) == cfg.max_slippage
    assert E.slippage_rate(10_000, 10_000_000, float("nan"), cfg) > cfg.half_spread


# ---------------------------------------------------------------- accounting
def test_accounting_identity(run):
    """equity = start capital + every realised profit/loss + unrealised gains.
    If any fee, tax or share count leaked, this breaks."""
    eng, _, _ = run
    s = eng.s
    realised = sum(t["pnl"] for t in s["trades"] if t.get("status") == "FILLED" and t["side"] == "SELL")
    unrealised = sum(p["qty"] * p["last"] - p["cost"] for p in s["positions"].values())
    assert eng.equity() == pytest.approx(s["start_capital"] + realised + unrealised, abs=5.0)
    assert s["cash"] >= -1e-6
    assert all(v >= 0 for v in s["fees"].values())


def test_cgt_only_on_profit(run):
    eng, _, _ = run
    for t in eng.s["trades"]:
        if t.get("status") == "FILLED" and t["side"] == "SELL":
            assert t["cgt"] >= 0
            if t["pnl"] < 0:
                assert t["cgt"] == 0


# ---------------------------------------------------------------- NEPSE rules
def test_t_plus_2_settlement(run, market):
    eng, _, _ = run
    for t in eng.s["trades"]:
        if t.get("status") == "FILLED" and t["side"] == "SELL":
            held = market.didx[t["date"]] - market.didx[t["entry_date"]]
            assert held >= 3, f"{t['sym']} sold {held} trading days after purchase"


def test_fills_respect_circuits_and_volume(run, market):
    eng, _, _ = run
    cfg = eng.cfg
    for t in eng.s["trades"]:
        if t.get("status") != "FILLED" or "slip" not in t:
            continue
        d, sym = t["date"], t["sym"]
        hi, lo, vol = market.high.at[d, sym], market.low.at[d, sym], market.vol.at[d, sym]
        assert lo - 0.01 <= t["price"] <= hi + 0.01           # never outside the day's range
        r, close = market.ret.at[d, sym], market.close.at[d, sym]
        if t["side"] == "BUY":
            assert not (r >= 0.095 and close >= hi * 0.999), "bought a stock locked at upper circuit"
            assert t["qty"] <= math.floor(vol * cfg.max_volume_share) + 1e-9
            assert t["qty"] >= 10
        else:
            assert not (r <= -0.095 and close <= lo * 1.001), "sold a stock locked at lower circuit"


def test_position_limits(run):
    eng, _, snaps = run
    assert max(n for _, n, _ in snaps) <= eng.cfg.max_positions
    assert all(e > 0 for _, _, e in snaps)


def test_never_buys_vetoed_or_excluded(run, market):
    eng, _, _ = run
    bad = E.research.EXCLUDED
    for t in eng.s["trades"]:
        if t.get("status") == "FILLED" and t["side"] == "BUY":
            assert market.sector_of[t["sym"]] not in bad


# ---------------------------------------------------------------- no lookahead
def test_no_lookahead(market):
    """Decisions up to day D must be identical whether or not data after D exists."""
    days = [d for d in market.dates if "2023-06-01" <= d <= "2023-12-31"]
    full = E.Engine(market, E.Config())
    for d in days:
        full.run_day(d)
    blind_market = E.Market(end=days[-1])                    # the future is deleted
    blind = E.Engine(blind_market, E.Config())
    for d in days:
        blind.run_day(d)
    assert blind_market.dates[-1] == days[-1]
    assert [e["equity"] for e in full.s["equity"]] == [e["equity"] for e in blind.s["equity"]]
    key = lambda s: [(t["date"], t["side"], t["sym"], t.get("qty"), t.get("price")) for t in s["trades"]]
    assert key(full.s) == key(blind.s)
    assert [(o["side"], o["sym"], o["qty"]) for o in full.s["pending"]] == \
           [(o["side"], o["sym"], o["qty"]) for o in blind.s["pending"]]


# ---------------------------------------------------------------- data integrity
def test_price_files_are_clean():
    seen, last_key, glitches = set(), None, 0
    for f in sorted((ROOT / "data" / "prices").glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                key = (r["date"], r["symbol"])
                assert key not in seen, f"duplicate row {key}"
                seen.add(key)
                assert r["date"][:4] == f.stem
                assert last_key is None or key > last_key, f"{f.name} not sorted at {key}"
                last_key = key
                c, h, l = float(r["close"]), float(r["high"]), float(r["low"])
                assert c > 0 and h > 0 and l > 0
                # Known source glitch: when a price crosses 100 or 1,000 intraday the site
                # compares numbers as text ("99" > "100"). Rare; repaired by the loader.
                glitches += h < l
    assert len(seen) > 400_000
    assert glitches < 200


def test_loader_repairs_price_ranges(market):
    ok = market.traded
    assert bool((market.high.where(ok) >= market.low.where(ok)).where(ok, True).all().all())
    assert bool((market.high.where(ok) >= market.close.where(ok) - 1e-9).where(ok, True).all().all())
    assert bool((market.low.where(ok) <= market.close.where(ok) + 1e-9).where(ok, True).all().all())


def test_index_file_is_clean():
    idx = pd.read_csv(ROOT / "data" / "nepse_index.csv")
    assert idx["date"].is_monotonic_increasing and idx["date"].is_unique
    assert (idx["close"] > 500).all() and (idx["close"] < 20_000).all()
    assert idx["close"].pct_change().abs().max() < 0.12       # index cannot jump more than circuits allow


# ---------------------------------------------------------------- XIRR
def test_xirr_matches_known_values():
    # Rs 100 grows to Rs 110 in exactly one year -> 10% a year
    assert E.xirr([("2023-01-01", -100), ("2024-01-01", 110)]) == pytest.approx(0.10, abs=1e-4)
    # doubling in two years -> sqrt(2) - 1
    assert E.xirr([("2022-01-01", -100), ("2024-01-01", 200)]) == pytest.approx(2 ** (365 / 730) - 1, abs=1e-4)
    # a loss
    assert E.xirr([("2023-01-01", -100), ("2024-01-01", 80)]) == pytest.approx(-0.20, abs=1e-4)
    # two deposits (Excel XIRR reference: -1000 on 2023-01-01, -1000 on 2023-07-01, +2200 on 2024-01-01)
    r = E.xirr([("2023-01-01", -1000), ("2023-07-01", -1000), ("2024-01-01", 2200)])
    npv = -1000 - 1000 / (1 + r) ** (181 / 365) + 2200 / (1 + r) ** (365 / 365)
    assert abs(npv) < 1e-4 and 0.12 < r < 0.14
    assert E.xirr([("2023-01-01", 100), ("2024-01-01", 110)]) is None     # no money put in


def test_xirr_in_metrics(run):
    eng, _, _ = run
    m = E.metrics(eng.s)
    assert m["days"] >= E.XIRR_MIN_DAYS and m["xirr_pct"] is not None
    years = m["days"] / 365
    assert m["xirr_pct"] == pytest.approx(((1 + m["return_pct"] / 100) ** (1 / years) - 1) * 100, abs=0.05)
    assert m["nepse_xirr_pct"] is not None


# ---------------------------------------------------------------- short-term study
def test_shortterm_costs_and_verdict():
    import pandas as pd
    from bot import shortterm as ST
    g = pd.Series([0.0, 0.10, -0.10])
    n = ST.net(g)
    assert n[0] == pytest.approx(-ST.COST)                                   # flat trade loses the costs
    assert n[1] == pytest.approx(((1.10 * (1 - ST.COST)) - 1) * (1 - ST.CGT))  # tax only on profit
    assert n[2] == pytest.approx(0.90 * (1 - ST.COST) - 1)
    good = {"design": {"h5": {"n": 100, "net": 1, "excess": 1, "t": 3}}, "holdout": {"h5": {"n": 50, "net": 1, "excess": 1, "t": 1}}}
    bad = {"design": {"h5": {"n": 100, "net": -1, "excess": -1, "t": -3}}, "holdout": {"h5": {"n": 50, "net": -1, "excess": -1, "t": -3}}}
    assert ST.verdict(good) == "edge" and ST.verdict(bad) == "avoid"
    assert ST.verdict({"design": {"h5": {"n": 5}}, "holdout": {"h5": {"n": 5}}}) == "too few"


def test_floorsheet_summary():
    from bot.floorsheet import summarise
    rows = [{"buyer": "1", "seller": "2", "quantity": "100", "amount": "10000", "date_": "2026-01-01"},
            {"buyer": "1", "seller": "3", "quantity": "50", "amount": "5000", "date_": "2026-01-01"},
            {"buyer": "4", "seller": "1", "quantity": "50", "amount": "5000", "date_": "2026-01-01"}]
    s = summarise("ABC", rows)
    assert s["qty"] == 200 and s["buyers"] == 2 and s["top_buyer"] == "1"
    assert s["top_buyer_pct"] == 75.0 and s["top_net_broker"] == "1" and s["top_net_pct"] == 50.0
