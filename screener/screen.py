"""High-level orchestration: fetch → evaluate → score → ``ScreenResult``.

This is the one function the bot and backtester call for a single ticker.
Keeping fetch/evaluate/score wired together here means live and backtest paths
run identical logic.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from data.fetcher import get_index_ohlcv, get_ohlcv, get_ohlcv_cached
from screener import rules, scoring
from screener.params import Params
from screener.result import DataQuality, ScreenResult


def screen_dataframe(
    ticker: str,
    df: pd.DataFrame,
    quality,
    scan_date: Optional[str] = None,
    params: Optional[Params] = None,
    regime_ok: Optional[bool] = None,
) -> ScreenResult:
    """Evaluate + score an already-fetched DataFrame (used by the backtester).

    ``regime_ok`` selects conservative mode (see ``rules.evaluate``).
    """
    res = rules.evaluate(
        ticker, df, quality=quality, scan_date=scan_date, params=params,
        regime_ok=regime_ok,
    )
    scoring.compute_score(res, params)
    return res


def screen_ticker(
    ticker: str, use_cache: bool = True, regime_ok: Optional[bool] = None
) -> ScreenResult:
    """Fetch data (cache-aware by default) and produce a scored ``ScreenResult``.

    ``regime_ok`` selects conservative mode; None = normal.
    """
    fetch = get_ohlcv_cached if use_cache else get_ohlcv
    df, quality = fetch(ticker)
    return screen_dataframe(
        ticker, df if df is not None else pd.DataFrame(), quality,
        regime_ok=regime_ok,
    )


def screen_index(symbol: str, label: str):
    """Screen a raw index (e.g. ``^JKSE``) with cross_pure. Returns (res, df).

    The index is analyzed exactly like a stock (MA stack, MA50 cross state), so
    a fresh cross of the index above/below its MA50 IS the risk-on/off flip that
    conservative mode gates on. df is returned for charting.
    """
    df, quality = get_index_ohlcv(symbol)
    if df is None:
        empty = ScreenResult(ticker=label, scan_date="", quality=DataQuality.NO_DATA)
        return empty, None
    res = rules.evaluate(label, df, quality=quality)
    scoring.compute_score(res)
    return res, df
