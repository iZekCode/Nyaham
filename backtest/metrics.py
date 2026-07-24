"""Backtest metrics + equity curve (§7.2).

Trade-level statistics (win rate, profit factor, expectancy, holding period) are
sequencing-independent. The equity curve is built **additively** — each trade
risks one unit of capital and realized PnL accrues on the exit date — which
sidesteps the unrealistic "compound every overlapping trade sequentially"
assumption while still giving an honest drawdown figure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

import pandas as pd

from backtest.engine import Trade


@dataclass
class Metrics:
    trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0        # mean net return per trade (= expectancy)
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0     # gross wins / gross losses
    total_pnl: float = 0.0         # sum of net returns (units of capital)
    max_drawdown: float = 0.0      # on the additive equity curve (units)
    avg_holding_days: float = 0.0
    exit_reasons: Optional[dict[str, int]] = None

    def as_dict(self) -> dict:
        return asdict(self)

    def as_text(self) -> str:
        pf = "∞" if self.profit_factor == float("inf") else f"{self.profit_factor:.2f}"
        return (
            f"Trades:        {self.trades}\n"
            f"Win rate:      {self.win_rate * 100:.1f}%\n"
            f"Avg return:    {self.avg_return * 100:+.2f}%  (expectancy)\n"
            f"Avg win/loss:  {self.avg_win * 100:+.2f}% / {self.avg_loss * 100:+.2f}%\n"
            f"Profit factor: {pf}\n"
            f"Total PnL:     {self.total_pnl * 100:+.1f}%  (1 unit/trade)\n"
            f"Max drawdown:  {self.max_drawdown * 100:.1f}%\n"
            f"Avg holding:   {self.avg_holding_days:.1f} days"
        )


def equity_curve(trades: Sequence[Trade]) -> pd.Series:
    """Cumulative realized net PnL indexed by exit date (additive, 1 unit/trade)."""
    if not trades:
        return pd.Series(dtype=float)
    # Build from parallel lists (NOT a dict — a dict would collapse trades that
    # share an exit date, dropping all but one). Then sum same-date exits and
    # accumulate.
    idx = [pd.Timestamp(t.exit_date) for t in trades]
    vals = [t.net_return for t in trades]
    s = pd.Series(vals, index=idx)
    s = s.groupby(level=0).sum().sort_index().cumsum()
    return s


def _max_drawdown(curve: pd.Series) -> float:
    if curve.empty:
        return 0.0
    running_max = curve.cummax()
    drawdown = curve - running_max
    return float(drawdown.min())  # ≤ 0


def compute_metrics(trades: Sequence[Trade]) -> Metrics:
    if not trades:
        return Metrics(exit_reasons={})

    rets = [t.net_return for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    curve = equity_curve(trades)

    return Metrics(
        trades=len(trades),
        win_rate=len(wins) / len(trades),
        avg_return=sum(rets) / len(rets),
        avg_win=(gross_win / len(wins)) if wins else 0.0,
        avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
        profit_factor=pf,
        total_pnl=sum(rets),
        max_drawdown=_max_drawdown(curve),
        avg_holding_days=sum(t.holding_days for t in trades) / len(trades),
        exit_reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# Benchmarks (§7.2): buy-and-hold comparisons over the same window.
# --------------------------------------------------------------------------- #
def buy_hold_return(df: pd.DataFrame, start: Optional[str] = None) -> float:
    """Total buy-and-hold return of a single series over [start, end]."""
    if df is None or df.empty:
        return 0.0
    close = df["Close"].dropna()
    if start:
        close = close[close.index >= pd.Timestamp(start)]
    if len(close) < 2:
        return 0.0
    return float(close.iloc[-1] / close.iloc[0] - 1.0)


def equal_weight_universe_return(
    data: dict[str, pd.DataFrame], start: Optional[str] = None
) -> float:
    """Average buy-and-hold return across the universe (equal weight)."""
    rets = [buy_hold_return(df, start) for df in data.values() if df is not None]
    rets = [r for r in rets if r != 0.0]
    return sum(rets) / len(rets) if rets else 0.0
