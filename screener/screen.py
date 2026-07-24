"""High-level orchestration: fetch → evaluate → score → ``ScreenResult``.

This is the one function the bot and backtester call for a single ticker.
Keeping fetch/evaluate/score wired together here means live and backtest paths
run identical logic.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from data.fetcher import get_ohlcv, get_ohlcv_cached
from screener import rules, scoring
from screener.params import Params
from screener.result import ScreenResult


def screen_dataframe(
    ticker: str,
    df: pd.DataFrame,
    quality,
    scan_date: Optional[str] = None,
    params: Optional[Params] = None,
) -> ScreenResult:
    """Evaluate + score an already-fetched DataFrame (used by the backtester)."""
    res = rules.evaluate(ticker, df, quality=quality, scan_date=scan_date, params=params)
    scoring.compute_score(res, params)
    return res


def screen_ticker(ticker: str, use_cache: bool = True) -> ScreenResult:
    """Fetch data (cache-aware by default) and produce a scored ``ScreenResult``."""
    fetch = get_ohlcv_cached if use_cache else get_ohlcv
    df, quality = fetch(ticker)
    return screen_dataframe(ticker, df if df is not None else pd.DataFrame(), quality)
