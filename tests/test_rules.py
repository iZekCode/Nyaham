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


def _cross_frame():
    """Series whose LAST bar is a fresh close-above-MA50 cross (found via
    pandas, so the construction can't drift from the rule's own math)."""
    import pandas as pd

    n = 340
    closes = []
    for i in range(n):
        if i < 270:
            closes.append(1000.0 + i)          # uptrend
        elif i < 300:
            closes.append(1269.0 - 8 * (i - 269))  # decline below MA50
        else:
            closes.append(1029.0 + 12 * (i - 299))  # recovery rally
    s = pd.Series(closes)
    ma50 = s.rolling(50, min_periods=50).mean()
    cross = None
    for k in range(300, n):
        if closes[k] > ma50[k] and closes[k - 1] <= ma50[k - 1]:
            cross = k
            break
    assert cross is not None, "test construction failed to produce a cross"
    return _frame(closes[: cross + 1]), _frame(closes)


def test_fresh_cross_is_buy():
    df_cross, _ = _cross_frame()
    res = rules.evaluate("CRS", df_cross, quality=DataQuality.OK)
    assert res.signal is Signal.BUY
    assert any("breakout" in r.lower() for r in res.reasons)
    assert res.buy_at is not None


def test_in_trend_is_hold_not_buy():
    # Steady uptrend from the start: always above MA50, never a fresh cross.
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    assert res.ma_above_count == 6
    assert res.signal is Signal.HOLD
    assert any("In trend" in r for r in res.reasons)
    assert res.buy_at is None  # no fresh entry while in trend


def test_avoid_below_all_mas():
    # Steady downtrend: price below every MA → AVOID (rule 5).
    closes = [2000 - i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("DOWN", df, quality=DataQuality.OK)
    assert res.ma_above_count == 0
    assert res.signal is Signal.AVOID


def test_spike_cross_is_buy():
    # Flat at the MA then a single-bar close above it IS a fresh cross under
    # cross_pure (yesterday ≤ MA50, today >) — classified BUY.
    closes = [1000.0] * (N - 1) + [1100.0]
    df = _frame(closes)
    res = rules.evaluate("SPIKE", df, quality=DataQuality.OK)
    assert res.signal is Signal.BUY


def test_conservative_gate_suppresses_buy_when_risk_off():
    # A fresh cross that is BUY in normal mode becomes HOLD when the market is
    # risk-off (regime_ok=False).
    df_cross, _ = _cross_frame()
    normal = rules.evaluate("CRS", df_cross, quality=DataQuality.OK)
    assert normal.signal is Signal.BUY

    gated = rules.evaluate("CRS", df_cross, quality=DataQuality.OK, regime_ok=False)
    assert gated.signal is Signal.HOLD
    assert any("Conservative gate" in r for r in gated.reasons)


def test_conservative_gate_allows_buy_when_risk_on():
    df_cross, _ = _cross_frame()
    ok = rules.evaluate("CRS", df_cross, quality=DataQuality.OK, regime_ok=True)
    assert ok.signal is Signal.BUY


def test_regime_gate_does_not_touch_sell():
    # SELL must fire regardless of regime — exits are never gated.
    closes = [1000.0 + i for i in range(N - 1)] + [1220.0]
    df = _frame(closes)
    sell = rules.evaluate("BRK", df, quality=DataQuality.OK, regime_ok=False)
    assert sell.signal is Signal.SELL


def test_sell_on_ma50_breakdown():
    # Uptrend held above MA50, then a daily close below MA50 → SELL (v2 rule 4).
    closes = [1000.0 + i for i in range(N - 1)] + [1220.0]  # deep close below MA50
    df = _frame(closes)
    res = rules.evaluate("BRK", df, quality=DataQuality.OK)
    assert res.signal is Signal.SELL
    assert any("below MA50" in r for r in res.reasons)


def test_dip_below_ma5_is_not_sell():
    # A shallow dip below MA5 only (still above MA50) must NOT be a SELL under
    # the v2 framework (the old rule 4 would have fired here).
    closes = [1000.0 + i for i in range(N - 1)] + [1000.0 + (N - 2) - 8]
    df = _frame(closes)
    res = rules.evaluate("DIP", df, quality=DataQuality.OK)
    assert res.signal is not Signal.SELL


def test_below_ma50_not_a_buy_candidate():
    # Stale break: price sits below MA50 for a while — HOLD with a structure
    # note, never BUY, even if short-term bounces.
    closes = [1000.0 + i for i in range(N - 30)] + [1100.0] * 30
    df = _frame(closes)
    res = rules.evaluate("STALE", df, quality=DataQuality.OK)
    ma50 = next(m for m in res.ma if m.period == 50)
    if not ma50.above:  # construct guard: only meaningful if below MA50
        assert res.signal in (Signal.HOLD, Signal.AVOID)


def test_stop_loss_is_ma50_level():
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    ma50 = next(m for m in res.ma if m.period == 50)
    from screener.indicators import round_to_tick

    assert res.stop_loss == round_to_tick(ma50.value, "down")


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
    # In-trend HOLD: resistance + exit level set and tick-valid; no entry.
    closes = [1000 + i * 0.5 for i in range(N)]
    df = _frame(closes)
    res = rules.evaluate("UPTR", df, quality=DataQuality.OK)
    assert res.buy_at is None
    for lvl in (res.sell_at, res.stop_loss):
        assert lvl is not None
        assert lvl % 5 == 0  # price band 500–2000 → tick 5

    # Fresh BUY: entry level also set and tick-valid.
    df_cross, _ = _cross_frame()
    res2 = rules.evaluate("CRS", df_cross, quality=DataQuality.OK)
    assert res2.buy_at is not None
    assert res2.buy_at % 5 == 0
