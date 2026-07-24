"""Confidence scoring (§4.5): map a ``ScreenResult`` to a 0..100 score.

Factors (weights in ``config.SCORE_WEIGHTS``):
  - ma_count        40%  MAs above the price, scaled 0..6   (rule 3)
  - proximity       25%  closeness to support MA, 0 beyond FAR (rules 1–2)
  - volume_pressure 20%  buy-side pressure proxy
  - rvol            15%  relative-volume participation, capped

Hard overrides: AVOID ⇒ 0; SELL ⇒ 0 (excluded from recommendations).
"""

from __future__ import annotations

from config import FAR_MA_THRESHOLD, SCORE_WEIGHTS
from screener.result import ScreenResult, Signal


def _proximity_factor(res: ScreenResult) -> float:
    """1.0 sitting on support, decaying to 0.0 at FAR_MA_THRESHOLD away."""
    if res.nearest_support is None:
        return 0.0
    dist = abs(res.nearest_support.distance_pct)
    if dist >= FAR_MA_THRESHOLD:
        return 0.0
    return 1.0 - (dist / FAR_MA_THRESHOLD)


def _rvol_factor(res: ScreenResult) -> float:
    """0 below 1x, ramping to full credit at 2x and capped there."""
    if res.rvol <= 1.0:
        return 0.0
    return min((res.rvol - 1.0), 1.0)  # 1x→0, 2x+→1


def compute_score(res: ScreenResult) -> float:
    """Return a 0..100 confidence score and also stash it on the result."""
    if not res.is_tradeable or res.signal in (Signal.AVOID, Signal.SELL):
        res.score = 0.0
        return 0.0

    total_mas = len(res.ma) or 1
    factors = {
        "ma_count": res.ma_above_count / total_mas,
        "proximity": _proximity_factor(res),
        "volume_pressure": res.buy_pressure_pct / 100.0,
        "rvol": _rvol_factor(res),
    }

    score = sum(SCORE_WEIGHTS[k] * factors[k] for k in SCORE_WEIGHTS) * 100.0
    score = round(max(0.0, min(100.0, score)), 1)
    res.score = score
    return score
