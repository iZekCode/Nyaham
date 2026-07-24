"""Parameter tuning via grid search (§7.3).

Guards against overfitting by splitting the timeline: parameters are chosen on
the **in-sample** (older) period and validated on the **out-of-sample** (recent)
period. Trades are partitioned by entry date, so every run keeps full MA history
and there is no boundary discontinuity.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from backtest.engine import Trade, backtest_universe
from backtest.metrics import Metrics, compute_metrics
from screener.params import Params

# Grid from plan §7.3 (coarse; widen/narrow as needed).
DEFAULT_GRID: dict[str, Iterable] = {
    "near_ma_threshold": (0.01, 0.015, 0.02, 0.025, 0.03),
    "far_ma_threshold": (0.04, 0.05, 0.06, 0.07),
    "support_lookback": (3, 5, 10),
}

MIN_TRADES = 20  # ignore parameter sets with too few trades to trust


@dataclass
class TuneResult:
    params: Params
    in_sample: Metrics
    out_sample: Metrics

    @property
    def objective(self) -> float:
        """Rank by in-sample total PnL, but disqualify thin samples."""
        if self.in_sample.trades < MIN_TRADES:
            return float("-inf")
        return self.in_sample.total_pnl


def _split_trades(
    trades: list[Trade], split_date: str
) -> tuple[list[Trade], list[Trade]]:
    """Partition by entry date into (in-sample, out-of-sample)."""
    in_s = [t for t in trades if t.entry_date < split_date]
    out_s = [t for t in trades if t.entry_date >= split_date]
    return in_s, out_s


def split_date_for(
    data: dict[str, pd.DataFrame], in_sample_frac: float = 0.6
) -> str:
    """Pick a split date at ``in_sample_frac`` of the overall date range."""
    all_dates: list[pd.Timestamp] = []
    for df in data.values():
        if df is not None and not df.empty:
            all_dates.append(df.index.min())
            all_dates.append(df.index.max())
    if not all_dates:
        return "2100-01-01"
    lo, hi = min(all_dates), max(all_dates)
    split = lo + (hi - lo) * in_sample_frac
    return str(split.date())


def grid_search(
    data: dict[str, pd.DataFrame],
    grid: Optional[dict[str, Iterable]] = None,
    split_date: Optional[str] = None,
    in_sample_frac: float = 0.6,
) -> tuple[list[TuneResult], TuneResult]:
    """Run every grid combo; return (all_results_sorted, best_by_objective)."""
    grid = grid or DEFAULT_GRID
    split = split_date or split_date_for(data, in_sample_frac)

    keys = list(grid.keys())
    results: list[TuneResult] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        overrides = dict(zip(keys, combo))
        params = Params(**overrides)
        trades = backtest_universe(data, params)
        in_s, out_s = _split_trades(trades, split)
        results.append(
            TuneResult(
                params=params,
                in_sample=compute_metrics(in_s),
                out_sample=compute_metrics(out_s),
            )
        )

    results.sort(key=lambda r: r.objective, reverse=True)
    best = results[0] if results else None
    return results, best


def format_report(
    results: list[TuneResult], best: TuneResult, split_date: str
) -> str:
    lines = [
        "GRID SEARCH — in-sample selection, out-of-sample validation",
        f"Split date: {split_date}  (before = in-sample, on/after = out-of-sample)",
        "",
        f"{'near':>6} {'far':>6} {'lb':>4} | "
        f"{'IS_n':>5} {'IS_win':>7} {'IS_exp':>8} {'IS_pf':>6} | "
        f"{'OOS_n':>5} {'OOS_win':>7} {'OOS_exp':>8}",
        "-" * 78,
    ]
    for r in results:
        p, i, o = r.params, r.in_sample, r.out_sample
        ispf = "inf" if i.profit_factor == float("inf") else f"{i.profit_factor:.2f}"
        lines.append(
            f"{p.near_ma_threshold:>6.3f} {p.far_ma_threshold:>6.3f} "
            f"{p.support_lookback:>4d} | "
            f"{i.trades:>5d} {i.win_rate*100:>6.1f}% {i.avg_return*100:>7.2f}% "
            f"{ispf:>6} | "
            f"{o.trades:>5d} {o.win_rate*100:>6.1f}% {o.avg_return*100:>7.2f}%"
        )

    lines += [
        "",
        "RECOMMENDED (best in-sample objective, check OOS holds up):",
        f"  NEAR_MA_THRESHOLD = {best.params.near_ma_threshold}",
        f"  FAR_MA_THRESHOLD  = {best.params.far_ma_threshold}",
        f"  SUPPORT_LOOKBACK  = {best.params.support_lookback}",
        "",
        "In-sample:",
        best.in_sample.as_text(),
        "",
        "Out-of-sample:",
        best.out_sample.as_text(),
    ]
    return "\n".join(lines)
