"""Confidence scoring (§4.5): map a ``ScreenResult`` to a 0..100 score.

Factors (weights in ``Params.score_weights`` / ``config.SCORE_WEIGHTS``):
  - ma_count        MAs above the price, scaled 0..6   (rule 3)
  - proximity       closeness to support MA, 0 beyond FAR (rules 1–2)
  - volume_pressure buy-side pressure proxy
  - rvol            relative-volume participation, capped

Hard overrides: AVOID ⇒ 0; SELL ⇒ 0 (excluded from recommendations).
"""

from __future__ import annotations

from typing import Optional

from screener.params import DEFAULT_PARAMS, Params
from screener.result import ScreenResult, Signal


def _proximity_factor(res: ScreenResult, far: float) -> float:
    """1.0 sitting on support, decaying to 0.0 at ``far`` away."""
    if res.nearest_support is None:
        return 0.0
    dist = abs(res.nearest_support.distance_pct)
    if dist >= far:
        return 0.0
    return 1.0 - (dist / far)


def _rvol_factor(res: ScreenResult) -> float:
    """0 below 1x, ramping to full credit at 2x and capped there."""
    if res.rvol <= 1.0:
        return 0.0
    return min((res.rvol - 1.0), 1.0)


def compute_score(res: ScreenResult, params: Optional[Params] = None) -> float:
    """Return a 0..100 confidence score and also stash it on the result."""
    params = params or DEFAULT_PARAMS
    if not res.is_tradeable or res.signal in (Signal.AVOID, Signal.SELL):
        res.score = 0.0
        return 0.0

    total_mas = len(res.ma) or 1
    weights = params.score_weights
    factors = {
        "ma_count": res.ma_above_count / total_mas,
        "proximity": _proximity_factor(res, params.far_ma_threshold),
        "volume_pressure": res.buy_pressure_pct / 100.0,
        "rvol": _rvol_factor(res),
    }

    score = sum(weights[k] * factors[k] for k in weights) * 100.0
    score = round(max(0.0, min(100.0, score)), 1)
    res.score = score
    return score
