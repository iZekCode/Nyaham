"""Market regime gate for conservative mode (backtest/FINDINGS.md).

Conservative mode only allows BUY signals when the market itself is risk-on:
the regime index (``^JKSE``) closing above its own MA (``REGIME_MA_PERIOD``,
default 50). Exits are never gated. This module fetches the index and reports
the current regime; the gate is applied in ``rules.evaluate(..., regime_ok=)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from config import REGIME_INDEX_SYMBOL, REGIME_MA_PERIOD

logger = logging.getLogger(__name__)


@dataclass
class RegimeState:
    ok: bool               # True = risk-on (index above its MA)
    index_price: float
    index_ma: float
    symbol: str = REGIME_INDEX_SYMBOL
    ma_period: int = REGIME_MA_PERIOD

    @property
    def label(self) -> str:
        return "risk-on" if self.ok else "risk-off"

    @property
    def summary(self) -> str:
        rel = "above" if self.ok else "below"
        return (
            f"{self.symbol} {self.index_price:,.0f} {rel} MA{self.ma_period} "
            f"({self.index_ma:,.0f}) — {self.label}"
        )


def get_regime_state(period: str = "1y") -> Optional[RegimeState]:
    """Fetch the regime index and evaluate risk-on/off. None on failure."""
    import yfinance as yf

    try:
        df = yf.download(
            REGIME_INDEX_SYMBOL, period=period, interval="1d",
            auto_adjust=True, progress=False, threads=False,
        )
    except Exception as exc:  # noqa: BLE001 — network layer
        logger.warning("Regime fetch failed: %s", exc)
        return None

    if df is None or df.empty:
        logger.warning("Regime fetch returned no data for %s", REGIME_INDEX_SYMBOL)
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].dropna()
    if len(close) < REGIME_MA_PERIOD:
        logger.warning("Not enough index history for MA%d", REGIME_MA_PERIOD)
        return None

    ma = close.rolling(REGIME_MA_PERIOD, min_periods=REGIME_MA_PERIOD).mean()
    price = float(close.iloc[-1])
    ma_val = float(ma.iloc[-1])
    return RegimeState(ok=price > ma_val, index_price=price, index_ma=ma_val)
