"""yfinance wrapper with retry/backoff and data-quality validation (§4.2).

Every fetch returns ``(DataFrame, DataQuality)`` so callers never have to guess
whether the numbers are trustworthy. All symbols are Asia/Jakarta daily bars.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

from config import (
    FETCH_BACKOFF_BASE,
    FETCH_MAX_RETRIES,
    FETCH_PERIOD,
    FLAT_STREAK_BARS,
    MIN_BARS,
    STALE_BAR_MAX_DAYS,
)
from screener.result import DataQuality
from universe import to_yf

logger = logging.getLogger(__name__)

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _download(symbol: str, period: str) -> Optional[pd.DataFrame]:
    """Single yfinance download attempt. Returns None on failure/empty."""
    import yfinance as yf

    df = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=True,   # split/dividend adjusted — critical for MAs (§4.2)
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return None
    # yfinance may return a MultiIndex column frame for single tickers.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    keep = [c for c in _OHLCV_COLS if c in df.columns]
    df = df[keep].dropna(how="all")
    return df if not df.empty else None


def _validate(df: pd.DataFrame) -> DataQuality:
    """Classify data quality per §4.2."""
    if df is None or df.empty:
        return DataQuality.NO_DATA

    # Freshness: last bar must be recent enough.
    last_date = df.index[-1]
    if isinstance(last_date, pd.Timestamp):
        age_days = (pd.Timestamp.now(tz=last_date.tz) - last_date).days
        if age_days > STALE_BAR_MAX_DAYS:
            return DataQuality.STALE

    # Suspension: a streak of zero-volume or flat-price bars at the tail.
    tail = df.tail(FLAT_STREAK_BARS)
    if len(tail) == FLAT_STREAK_BARS:
        zero_vol = (tail["Volume"].fillna(0) <= 0).all()
        flat_price = tail["Close"].nunique() == 1
        if zero_vol or flat_price:
            return DataQuality.SUSPENDED

    # History depth for a valid MA200.
    if len(df) < MIN_BARS:
        return DataQuality.INSUFFICIENT_DATA

    return DataQuality.OK


def get_ohlcv(
    ticker: str, period: str = FETCH_PERIOD
) -> tuple[Optional[pd.DataFrame], DataQuality]:
    """Fetch daily OHLCV with retry/backoff and validation.

    Returns ``(df, quality)``. ``df`` may still be returned alongside a
    non-OK quality (e.g. INSUFFICIENT_DATA) so callers can show partial info;
    it is None only when nothing could be fetched.
    """
    symbol = to_yf(ticker)
    last_err: Optional[Exception] = None

    for attempt in range(FETCH_MAX_RETRIES):
        try:
            df = _download(symbol, period)
            if df is not None:
                quality = _validate(df)
                return df, quality
            logger.warning("Empty data for %s (attempt %d)", symbol, attempt + 1)
        except Exception as exc:  # noqa: BLE001 — network layer, retry everything
            last_err = exc
            logger.warning("Fetch error %s (attempt %d): %s", symbol, attempt + 1, exc)

        if attempt < FETCH_MAX_RETRIES - 1:
            time.sleep(FETCH_BACKOFF_BASE * (2 ** attempt))

    if last_err:
        logger.error("Giving up on %s after %d attempts", symbol, FETCH_MAX_RETRIES)
    return None, DataQuality.NO_DATA


def get_ohlcv_cached(
    ticker: str, period: str = FETCH_PERIOD
) -> tuple[Optional[pd.DataFrame], DataQuality]:
    """Cache-aware fetch (§6): serve stored bars when they're up to date.

    The cache is "fresh" when its newest bar is at least the last completed
    trading day (see :mod:`market_calendar`). On a miss we fetch live and
    persist the bars so subsequent ``/ma`` calls are instant.
    """
    from config import OHLCV_CACHE_ENABLED

    if not OHLCV_CACHE_ENABLED:
        return get_ohlcv(ticker, period)

    from data import cache
    from market_calendar import last_completed_trading_day

    try:
        cache.init_db()
        last = cache.ohlcv_last_date(ticker)
        if last is not None and last >= last_completed_trading_day():
            df = cache.load_ohlcv(ticker)
            if df is not None and not df.empty:
                return df, _validate(df)
    except Exception as exc:  # noqa: BLE001 — cache must never break a fetch
        logger.warning("OHLCV cache read failed for %s: %s", ticker, exc)

    df, quality = get_ohlcv(ticker, period)
    if df is not None and not df.empty:
        try:
            from data import cache as _cache

            _cache.save_ohlcv(ticker, df)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OHLCV cache write failed for %s: %s", ticker, exc)
    return df, quality
