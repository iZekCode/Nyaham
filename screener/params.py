"""Tunable screener parameters, bundled so the backtest tuner can vary them.

Live code calls ``rules.evaluate(...)`` with no params and gets the config
defaults, so the exact logic that runs live is the logic being backtested — the
only difference is which ``Params`` instance is threaded through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import (
    FAR_MA_THRESHOLD,
    MA_PERIODS,
    MIN_BARS,
    NEAR_MA_THRESHOLD,
    SCORE_WEIGHTS,
    SUPPORT_LOOKBACK,
)


@dataclass
class Params:
    """The knobs Phase 4 tunes. Defaults come from ``config.py``."""

    ma_periods: tuple[int, ...] = MA_PERIODS
    near_ma_threshold: float = NEAR_MA_THRESHOLD
    far_ma_threshold: float = FAR_MA_THRESHOLD
    support_lookback: int = SUPPORT_LOOKBACK
    min_bars: int = MIN_BARS
    score_weights: dict[str, float] = field(
        default_factory=lambda: dict(SCORE_WEIGHTS)
    )

    def as_row(self) -> dict[str, float]:
        """Flat dict for reports / CSV."""
        return {
            "near_ma_threshold": self.near_ma_threshold,
            "far_ma_threshold": self.far_ma_threshold,
            "support_lookback": self.support_lookback,
        }


DEFAULT_PARAMS = Params()
