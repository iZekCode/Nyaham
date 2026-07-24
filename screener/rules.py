"""Rules engine — cross_pure strategy: OHLCV DataFrame → ``ScreenResult``.

The live strategy (see backtest/FINDINGS.md, "cross_pure on maximum history"):

  ENTRY (BUY)  — a FRESH breakout: today's close crossed ABOVE MA50
                 (yesterday's close was at/below its MA50). Entries are
                 events; a stock that crossed weeks ago is not a new BUY.
  EXIT  (SELL) — a fresh daily close BELOW MA50 after holding above it.
                 The exit is a CONDITION, not a price target: ``stop_loss``
                 carries the current MA50 level, and there is NO take-profit
                 (``sell_at`` is informational resistance only).
  AVOID        — below all six MAs (clear downtrend), or data-quality flags.
  HOLD         — everything else, with context: "in trend" (above MA50 —
                 stay in if entered) or "below MA50" (wait for a fresh cross;
                 ``buy_at`` then carries the trigger level).

The 6-MA stack, trend tiers, and volume metrics remain as displayed context.
All thresholds come from a ``Params`` instance (config defaults when omitted).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from screener import indicators as ind
from screener.params import DEFAULT_PARAMS, Params
from screener.result import (
    DataQuality,
    MAStatus,
    ScreenResult,
    Signal,
    TrendTier,
)

# Trend tiers shown in the reference bot's three-line summary.
_TREND_TIERS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("Short", (5, 10, 20)),
    ("Medium", (20, 50)),
    ("Long", (50, 100, 200)),
)


def _build_ma_status(
    price: float, ma_values: dict[int, Optional[float]], periods: tuple[int, ...]
) -> list[MAStatus]:
    out: list[MAStatus] = []
    for period in periods:
        val = ma_values.get(period)
        if val is None:
            continue
        out.append(
            MAStatus(
                period=period,
                value=val,
                above=price >= val,
                distance_pct=ind.distance_pct(price, val),
            )
        )
    return out


def _trend_tiers(ma_by_period: dict[int, MAStatus]) -> list[TrendTier]:
    """A tier is bullish when price is above every MA in that tier."""
    tiers: list[TrendTier] = []
    for label, periods in _TREND_TIERS:
        present = [ma_by_period[p] for p in periods if p in ma_by_period]
        bullish = bool(present) and all(m.above for m in present)
        tiers.append(TrendTier(label=label, periods=periods, bullish=bullish))
    return tiers


def _nearest_support_resistance(
    ma_list: list[MAStatus],
) -> tuple[Optional[MAStatus], Optional[MAStatus]]:
    """Highest MA below price (support) and lowest MA above price (resistance)."""
    below = [m for m in ma_list if m.above]        # price >= ma ⇒ ma is support
    above = [m for m in ma_list if not m.above]    # price < ma  ⇒ ma is resistance
    support = max(below, key=lambda m: m.value) if below else None
    resistance = min(above, key=lambda m: m.value) if above else None
    return support, resistance


def _fresh_exit_break(
    df: pd.DataFrame,
    params: Params,
    ma_cache: Optional[dict] = None,
) -> bool:
    """v2 rule 4: did today's close break below the exit MA (MA50) after the
    previous ``support_lookback`` closes had all held at/above it?

    Only a *fresh* break is a SELL event; a stock that has been below MA50 for
    a while is simply not a candidate (handled in the signal logic).
    """
    close = df["Close"]
    lookback = params.support_lookback
    if len(close) < lookback + 2:
        return False

    mas = ind.moving_averages(df, params.ma_periods, ma_cache)
    series = mas.get(params.exit_ma_period)
    if series is None or series.isna().iloc[-1]:
        return False
    if float(close.iloc[-1]) >= float(series.iloc[-1]):
        return False  # still above the exit MA

    window_close = close.iloc[-(lookback + 1):-1]
    window_ma = series.iloc[-(lookback + 1):-1]
    if window_ma.isna().any():
        return False
    return bool((window_close.values >= window_ma.values).all())


def _fresh_cross_above(
    df: pd.DataFrame,
    params: Params,
    ma_cache: Optional[dict] = None,
) -> bool:
    """cross_pure entry: today's close crossed above the exit MA (MA50) —
    yesterday's close was at/below its MA50, today's is above."""
    close = df["Close"]
    if len(close) < 2:
        return False
    mas = ind.moving_averages(df, params.ma_periods, ma_cache)
    series = mas.get(params.exit_ma_period)
    if series is None or len(series) < 2:
        return False
    if series.isna().iloc[-1] or series.isna().iloc[-2]:
        return False
    return (
        float(close.iloc[-1]) > float(series.iloc[-1])
        and float(close.iloc[-2]) <= float(series.iloc[-2])
    )


def _round_levels(
    resistance: Optional[MAStatus],
    ma_by_period: dict[int, MAStatus],
    df: pd.DataFrame,
    price: float,
    params: Params,
) -> tuple[Optional[int], Optional[int]]:
    """Tick-rounded (sell_at, stop_loss) under cross_pure.

    sell_at    — INFORMATIONAL resistance: nearest MA above price, else the
                 20-bar swing high (never a take-profit instruction)
    stop_loss  — the current exit-MA (MA50) level: "exit if a daily close is
                 below this"

    ``buy_at`` is set in the signal classification (it depends on state:
    breakout price for a fresh BUY, the MA50 trigger when below, None while
    in-trend).
    """
    if resistance is not None:
        sell = ind.round_to_tick(resistance.value, "up")
    else:
        # Above the whole stack — show the recent swing high as resistance.
        swing = float(df["High"].tail(20).max())
        ref = swing if swing > price else price * (1 + params.far_ma_threshold)
        sell = ind.round_to_tick(ref, "up")

    exit_ma = ma_by_period.get(params.exit_ma_period)
    stop = ind.round_to_tick(exit_ma.value, "down") if exit_ma else None
    return sell, stop


def _verdict(signal: Signal, above_count: int, total: int) -> str:
    if signal is Signal.AVOID:
        return "🔴 AVOID"
    if signal is Signal.SELL:
        return "🔴 SELL — closed below exit MA"
    if signal is Signal.BUY:
        return "🟢 BUY — MA50 breakout"
    if above_count == total:
        return "🟢 FULL BULLISH — in trend"
    if above_count >= total // 2:
        return "🟡 BULLISH short-term only"
    return "⚪ WAIT"


def evaluate(
    ticker: str,
    df: pd.DataFrame,
    quality: DataQuality = DataQuality.OK,
    scan_date: Optional[str] = None,
    params: Optional[Params] = None,
    ma_cache: Optional[dict] = None,
) -> ScreenResult:
    """Evaluate one ticker's DataFrame into a full ``ScreenResult``.

    ``ma_cache`` (optional) is a {period: MA-series} map aligned to ``df``; the
    backtester passes it so MAs aren't recomputed on every bar. Live callers
    omit it and MAs are computed from ``df`` as usual.
    """
    params = params or DEFAULT_PARAMS
    date_str = scan_date or (str(df.index[-1].date()) if len(df) else "")
    res = ScreenResult(ticker=ticker.upper(), scan_date=date_str, quality=quality)

    if quality is DataQuality.NO_DATA or df is None or df.empty:
        res.quality = DataQuality.NO_DATA
        res.signal = Signal.AVOID
        res.verdict = "🔴 AVOID — no data"
        res.reasons.append("No price data available.")
        return res

    price = float(df["Close"].iloc[-1])
    res.price = price
    res.prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else price
    res.change_pct = ind.change_pct(df)

    ma_values = ind.latest_ma_values(df, params.ma_periods, ma_cache)
    ma_list = _build_ma_status(price, ma_values, params.ma_periods)
    res.ma = ma_list
    res.ma_above_count = sum(1 for m in ma_list if m.above)

    ma_by_period = {m.period: m for m in ma_list}
    res.trends = _trend_tiers(ma_by_period)

    support, resistance = _nearest_support_resistance(ma_list)
    res.nearest_support = support
    res.nearest_resistance = resistance

    res.rvol = ind.relative_volume(df)
    res.buy_pressure_pct = ind.buy_pressure(df)
    res.sell_pressure_pct = round(100 - res.buy_pressure_pct, 1)

    res.sell_at, res.stop_loss = _round_levels(
        resistance, ma_by_period, df, price, params
    )

    if len(df) < params.min_bars or ma_values.get(200) is None:
        res.quality = DataQuality.INSUFFICIENT_DATA

    # ------------------------------------------------------------------ #
    # Signal classification (order matters — SELL and AVOID dominate).
    # ------------------------------------------------------------------ #
    total = len(ma_list)
    signal = Signal.HOLD

    # Data-quality gate (§4.4): suspended/stale data is untrustworthy ⇒ AVOID.
    if res.quality in (DataQuality.SUSPENDED, DataQuality.STALE):
        res.signal = Signal.AVOID
        res.reasons.append(
            f"Data-quality flag ({res.quality.value}) — numbers unreliable, "
            f"not tradeable."
        )
        res.verdict = "🔴 AVOID — data flag"
        return res

    exit_p = params.exit_ma_period
    exit_ma = ma_by_period.get(exit_p)

    if _fresh_exit_break(df, params, ma_cache):
        signal = Signal.SELL
        res.reasons.append(
            f"Daily close below MA{exit_p} after holding above it "
            f"≥{params.support_lookback} days — structural exit."
        )
    elif res.ma_above_count == 0:
        signal = Signal.AVOID
        res.reasons.append(
            f"Price is below all moving averages — clear downtrend; wait for "
            f"a daily close back above MA{exit_p}."
        )
    elif _fresh_cross_above(df, params, ma_cache):
        signal = Signal.BUY
        res.buy_at = ind.round_to_tick(price, "nearest")
        res.reasons.append(
            f"Fresh breakout: today's close crossed above MA{exit_p}. "
            f"Trend entry at market; exit on a daily close below "
            f"MA{exit_p}."
        )
    elif exit_ma is not None and not exit_ma.above:
        # Below the exit line, no fresh break — waiting for the trigger.
        signal = Signal.HOLD
        res.buy_at = ind.round_to_tick(exit_ma.value, "up")
        res.reasons.append(
            f"Below MA{exit_p} — no position. Entry trigger: a daily close "
            f"back above MA{exit_p}."
        )
    elif exit_ma is not None:
        # In trend: above MA50 but the cross was not today. Hold if entered;
        # no fresh entry until price resets below and re-crosses.
        signal = Signal.HOLD
        dist = exit_ma.distance_pct * 100
        res.reasons.append(
            f"In trend — {dist:+.1f}% above MA{exit_p}. Hold if entered "
            f"(exit on a daily close below MA{exit_p}); no new entry until "
            f"the next fresh cross."
        )
    else:
        signal = Signal.HOLD
        res.reasons.append(
            f"MA{exit_p} unavailable — insufficient history for the strategy."
        )

    if res.ma_above_count == total and total > 0:
        res.reasons.append("Full bullish stack — price above all MAs.")

    res.signal = signal
    res.verdict = _verdict(signal, res.ma_above_count, total)
    return res
