"""Rules engine (§4.4): turn a validated OHLCV DataFrame into a ``ScreenResult``.

Implements the five trading rules (§2):

  1. Avoid stocks far from their MA (overextended).
  2. Enter stocks near an MA support.
  3. Above all MAs is best (full bullish stack).
  4. Sell stocks that fail to hold above an MA they recently held.
  5. Avoid stocks below all MAs.

All thresholds come from a ``Params`` instance (config defaults when omitted),
so the backtest tuner can vary them without touching this logic.
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


def _detect_breakdown(
    df: pd.DataFrame,
    params: Params,
    ma_cache: Optional[dict] = None,
) -> Optional[int]:
    """Rule 4: did today's close break below an MA it had held for >= LOOKBACK days?

    Returns the MA period that was broken (the tightest/highest such MA), or None.
    """
    close = df["Close"]
    lookback = params.support_lookback
    if len(close) < lookback + 2:
        return None

    today = float(close.iloc[-1])
    mas = ind.moving_averages(df, params.ma_periods, ma_cache)
    broken: list[int] = []

    for period, series in mas.items():
        if series.isna().iloc[-1]:
            continue
        ma_today = float(series.iloc[-1])
        if today >= ma_today:
            continue  # still above this MA — no breakdown
        # Were the LOOKBACK bars *before* today all above this MA?
        window_close = close.iloc[-(lookback + 1):-1]
        window_ma = series.iloc[-(lookback + 1):-1]
        if window_ma.isna().any():
            continue
        held = (window_close.values >= window_ma.values).all()
        if held:
            broken.append(period)

    if not broken:
        return None
    return max(broken)  # highest broken MA (tightest support that just failed)


def _round_levels(
    support: Optional[MAStatus],
    resistance: Optional[MAStatus],
    ma_list: list[MAStatus],
    price: float,
    params: Params,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Compute tick-rounded Buy / Sell(TP) / Stop-loss levels."""
    buy = ind.round_to_tick(support.value, "down") if support else None

    if resistance is not None:
        sell = ind.round_to_tick(resistance.value, "up")
    else:
        # Above the whole stack — use a fixed % target.
        sell = ind.round_to_tick(price * (1 + params.far_ma_threshold), "up")

    stop = None
    if support is not None:
        lower = [m for m in ma_list if m.value < support.value]
        if lower:
            next_support = max(lower, key=lambda m: m.value)
            stop = ind.round_to_tick(next_support.value, "down")
        else:
            stop = ind.round_to_tick(
                support.value * (1 - params.near_ma_threshold), "down"
            )
    return buy, sell, stop


def _verdict(signal: Signal, above_count: int, total: int) -> str:
    if signal is Signal.AVOID:
        return "🔴 AVOID"
    if signal is Signal.SELL:
        return "🔴 SELL — support broke"
    if above_count == total:
        return "🟢 FULL BULLISH"
    if signal is Signal.BUY:
        return "🟢 BUY — near support"
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

    res.buy_at, res.sell_at, res.stop_loss = _round_levels(
        support, resistance, ma_list, price, params
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

    broken = _detect_breakdown(df, params, ma_cache)
    if broken is not None:
        signal = Signal.SELL
        res.reasons.append(
            f"Closed below MA{broken} after holding it "
            f"≥{params.support_lookback} days."
        )
    elif res.ma_above_count == 0:
        signal = Signal.AVOID
        res.reasons.append("Price is below all moving averages.")
    else:
        if support is None:
            signal = Signal.AVOID
            res.reasons.append("No MA support beneath price.")
        else:
            dist = abs(support.distance_pct)
            short_bull = res.trends[0].bullish
            if dist > params.far_ma_threshold:
                signal = Signal.AVOID
                res.reasons.append(
                    f"Overextended: {dist*100:.1f}% above MA{support.period} "
                    f"(> {params.far_ma_threshold*100:.0f}%)."
                )
            elif dist <= params.near_ma_threshold and short_bull:
                signal = Signal.BUY
                res.reasons.append(
                    f"Near MA{support.period} support "
                    f"({dist*100:.1f}% away) with bullish short-term stack."
                )
            else:
                signal = Signal.HOLD
                res.reasons.append(
                    f"{dist*100:.1f}% above MA{support.period}; wait for a "
                    f"pullback to support or a cleaner setup."
                )

    if res.ma_above_count == total and total > 0:
        res.reasons.append("Full bullish stack — price above all MAs.")

    res.signal = signal
    res.verdict = _verdict(signal, res.ma_above_count, total)
    return res
