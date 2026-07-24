"""Signal logic on constructed price series (§4.7).

Each scenario builds a synthetic 260-bar series so all six MAs (incl. MA200)
are defined, then asserts the resulting signal.
"""

from __future__ import annotations

import pandas as pd

from screener import rules, scoring
from screener.result import DataQuality, Signal

N = 260


def _frame(closes, highs=None, lows=None, vols=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs if highs is not None else [c * 1.01 for c in closes],
            "Low": lows if lows is not None else [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": vols if vols is not None else [1000] * n,
        },
        index=idx,
    )


def test_buy_uptrend_near_support():
    # Gentle uptrend: price sits just above a rising MA5 → BUY.
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    assert res.ma_above_count == 6
    assert res.signal is Signal.BUY
    assert res.trends[0].bullish is True


def test_avoid_below_all_mas():
    # Steady downtrend: price below every MA → AVOID (rule 5).
    closes = [2000 - i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("DOWN", df, quality=DataQuality.OK)
    assert res.ma_above_count == 0
    assert res.signal is Signal.AVOID


def test_avoid_overextended():
    # Flat then a single-bar spike far above MA5 → overextended AVOID (rule 1).
    closes = [1000.0] * (N - 1) + [1100.0]
    df = _frame(closes)
    res = rules.evaluate("SPIKE", df, quality=DataQuality.OK)
    assert res.signal is Signal.AVOID
    assert any("Overextended" in r for r in res.reasons)


def test_sell_on_ma_breakdown():
    # Uptrend held above MA5 for days, then a close dips below it → SELL (rule 4).
    closes = [1000.0 + i for i in range(N - 1)] + [1000.0 + (N - 2) - 8]
    df = _frame(closes)
    res = rules.evaluate("BRK", df, quality=DataQuality.OK)
    assert res.signal is Signal.SELL
    assert any("Closed below MA" in r for r in res.reasons)


def test_suspended_data_forces_avoid():
    # A bullish-looking stack must still be AVOID when data is flagged.
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("SUSP", df, quality=DataQuality.SUSPENDED)
    assert res.signal is Signal.AVOID
    assert any("Data-quality flag" in r for r in res.reasons)


def test_full_bullish_verdict():
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    assert "BULLISH" in res.verdict


def test_scoring_avoid_is_zero():
    closes = [2000 - i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("DOWN", df, quality=DataQuality.OK)
    assert scoring.compute_score(res) == 0.0


def test_scoring_buy_is_positive():
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes, highs=[c * 1.001 for c in [1000 + i * 0.5 for i in range(N)]])
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    score = scoring.compute_score(res)
    assert score > 0
    assert score <= 100


def test_levels_are_tick_valid():
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    # Price band 500–2000 → tick 5; all levels must be multiples of 5.
    for lvl in (res.buy_at, res.sell_at, res.stop_loss):
        assert lvl is not None
        assert lvl % 5 == 0
