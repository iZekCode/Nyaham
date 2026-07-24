"""Pure numeric indicators computed from a daily OHLCV DataFrame.

No signal logic here — this module only turns price/volume into numbers
(MAs, distances, volume metrics, tick-size rounding). Rules §4.4 consume these.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from config import MA_PERIODS


# --------------------------------------------------------------------------- #
# IDX tick size (fraksi harga) — Regulation II-A price bands.
# --------------------------------------------------------------------------- #
# (upper_bound_exclusive, tick). The last band uses infinity.
_TICK_BANDS: tuple[tuple[float, int], ...] = (
    (200, 1),
    (500, 2),
    (2000, 5),
    (5000, 10),
    (float("inf"), 25),
)


def tick_size(price: float) -> int:
    """Return the valid IDX tick size (Rp) for a given price band."""
    for upper, tick in _TICK_BANDS:
        if price < upper:
            return tick
    return 25  # unreachable; keeps type-checkers happy


def round_to_tick(price: float, direction: str = "nearest") -> int:
    """Round a price to a tradeable IDX tick.

    direction: "nearest" (default), "down" (floor — good for buy/support),
    or "up" (ceil — good for resistance/TP).
    """
    if price <= 0:
        return 0
    tick = tick_size(price)
    q = price / tick
    if direction == "down":
        import math

        n = math.floor(q)
    elif direction == "up":
        import math

        n = math.ceil(q)
    else:
        n = round(q)
    return int(n * tick)


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #
def moving_averages(
    df: pd.DataFrame, periods: tuple[int, ...] = MA_PERIODS
) -> dict[int, pd.Series]:
    """Rolling-mean MA of Close for each period.

    Series with fewer than ``period`` valid points are NaN at the head, which
    is the correct pandas behavior (``min_periods=period``).
    """
    close = df["Close"]
    return {p: close.rolling(window=p, min_periods=p).mean() for p in periods}


def latest_ma_values(
    df: pd.DataFrame, periods: tuple[int, ...] = MA_PERIODS
) -> dict[int, Optional[float]]:
    """Most recent MA value per period (None if not enough history)."""
    mas = moving_averages(df, periods)
    out: dict[int, Optional[float]] = {}
    for p, series in mas.items():
        val = series.iloc[-1] if len(series) else float("nan")
        out[p] = None if pd.isna(val) else float(val)
    return out


def distance_pct(price: float, ma_value: float) -> float:
    """Signed relative distance: (price - ma) / ma."""
    if ma_value == 0:
        return 0.0
    return (price - ma_value) / ma_value


# --------------------------------------------------------------------------- #
# Volume metrics (§4.3)
# --------------------------------------------------------------------------- #
def relative_volume(df: pd.DataFrame, window: int = 20) -> float:
    """RVOL = latest volume / average volume over the prior ``window`` bars."""
    vol = df["Volume"]
    if len(vol) < window + 1:
        return 0.0
    avg = vol.iloc[-(window + 1):-1].mean()
    if avg <= 0:
        return 0.0
    return float(vol.iloc[-1] / avg)


def buy_pressure(df: pd.DataFrame) -> float:
    """Close position within the day's range, as a 0..100 percentage.

    Proxy for intraday buy/sell balance (yfinance has no tick data):
    close near the high ⇒ buyers won the day.
    """
    row = df.iloc[-1]
    high, low, close = float(row["High"]), float(row["Low"]), float(row["Close"])
    rng = high - low
    if rng <= 0:
        return 50.0  # doji / flat bar — neutral
    return round((close - low) / rng * 100, 1)


def change_pct(df: pd.DataFrame) -> float:
    """Latest close vs previous close, as a percentage."""
    if len(df) < 2:
        return 0.0
    prev = float(df["Close"].iloc[-2])
    last = float(df["Close"].iloc[-1])
    if prev == 0:
        return 0.0
    return round((last - prev) / prev * 100, 2)
