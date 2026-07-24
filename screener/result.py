"""``ScreenResult`` — the single source of truth passed between the screener,
the bot formatter, the chart renderer, and the backtester.

Everything downstream (Telegram messages, charts, backtest trades) reads from
this dataclass so there is exactly one representation of "what the screener
concluded about a ticker on a given day".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Signal(str, Enum):
    """Final per-stock classification (rules §2)."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"      # a.k.a. WAIT
    AVOID = "AVOID"


class DataQuality(str, Enum):
    """Data-integrity verdict from the fetcher/validator."""

    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # < MIN_BARS rows
    STALE = "STALE"                          # last bar too old (suspended?)
    SUSPENDED = "SUSPENDED"                   # flat price / zero volume streak
    NO_DATA = "NO_DATA"                       # fetch returned nothing


@dataclass
class MAStatus:
    """Price relationship to a single moving average."""

    period: int
    value: float
    above: bool
    distance_pct: float  # signed: (price - ma) / ma, e.g. +0.031 = 3.1% above


@dataclass
class TrendTier:
    """One of the three trend tiers shown in the reference bot."""

    label: str          # "Short", "Medium", "Long"
    periods: tuple[int, ...]
    bullish: bool


@dataclass
class ScreenResult:
    """Complete screening outcome for one ticker on one scan date."""

    ticker: str
    scan_date: str                  # ISO date of the last bar analyzed
    quality: DataQuality = DataQuality.OK

    # Price snapshot
    price: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0

    # MA analysis
    ma: list[MAStatus] = field(default_factory=list)
    ma_above_count: int = 0
    nearest_support: Optional[MAStatus] = None    # highest MA below price
    nearest_resistance: Optional[MAStatus] = None  # lowest MA above price

    # Trend tiers
    trends: list[TrendTier] = field(default_factory=list)

    # Volume
    rvol: float = 0.0               # today vol / 20-day avg
    buy_pressure_pct: float = 0.0   # close position in day range, 0..100
    sell_pressure_pct: float = 0.0  # 100 - buy_pressure_pct

    # Trade levels (already rounded to valid IDX tick size)
    buy_at: Optional[float] = None
    sell_at: Optional[float] = None   # take-profit / resistance
    stop_loss: Optional[float] = None

    # Verdict
    signal: Signal = Signal.HOLD
    verdict: str = ""               # e.g. "🟢 FULL BULLISH"
    reasons: list[str] = field(default_factory=list)
    regime_gated: bool = False      # conservative mode suppressed a fresh BUY

    # Confidence
    score: float = 0.0              # 0..100

    @property
    def is_tradeable(self) -> bool:
        """Data is clean enough to trust the numbers."""
        return self.quality == DataQuality.OK

    @property
    def ma_summary(self) -> str:
        """e.g. ``5/6`` MAs above."""
        return f"{self.ma_above_count}/{len(self.ma)}"
