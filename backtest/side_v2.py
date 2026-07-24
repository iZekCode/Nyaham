"""Side experiment: v2 exit spec from FINDINGS.md.

Entry is the UNCHANGED live rules BUY (near support + short-term bullish),
optionally + the Medium/Long trend filter. Exit is ONLY a daily close below a
slow MA (sweep MA10 / MA20 / MA50) — no take-profit, no rule-4 SELL, no tight
stop. This is the "let winners ride, exit on structural break" formula the
MA50 experiment validated, now paired with the screener's own entry.

An entry is skipped if the close is not above the exit MA (it would exit on the
first check anyway).

Run:  python -m backtest.side_v2 --limit 200 --period 2y
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

import pandas as pd

from backtest.engine import Trade, _net_return
from backtest.metrics import (
    buy_hold_return,
    compute_metrics,
    equal_weight_universe_return,
)
from backtest.side_refinements import precompute_evals
from config import LOG_LEVEL
from screener.params import DEFAULT_PARAMS
from screener.result import Signal
from universe import UNIVERSE

logger = logging.getLogger(__name__)

EXIT_MAS = (10, 20, 50)


def run_variant(
    ticker: str,
    df: pd.DataFrame,
    evals: dict,
    exit_ma_period: int,
    trend_filter: bool,
) -> list[Trade]:
    params = DEFAULT_PARAMS
    n = len(df)
    if n < params.min_bars + 2:
        return []
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    exit_ma = (
        df["Close"].rolling(exit_ma_period, min_periods=exit_ma_period)
        .mean().to_numpy(dtype=float)
    )
    dates = [str(d.date()) for d in df.index]

    trades: list[Trade] = []
    position: Optional[dict] = None

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal position
        gross, net = _net_return(position["entry_price"], price)
        trades.append(Trade(
            ticker=ticker,
            entry_date=dates[position["entry_i"]],
            exit_date=dates[i],
            entry_price=round(position["entry_price"], 4),
            exit_price=round(price, 4),
            holding_days=i - position["entry_i"],
            exit_reason=reason,
            gross_return=round(gross, 6),
            net_return=round(net, 6),
        ))
        position = None

    i = params.min_bars
    while i < n - 1:
        if position is None:
            res = evals[i]
            ok = res.signal is Signal.BUY and res.is_tradeable
            if ok and trend_filter:
                ok = res.trends[1].bullish and res.trends[2].bullish
            # Must already be above the exit MA, else it exits immediately.
            if ok and not pd.isna(exit_ma[i]) and closes[i] > exit_ma[i]:
                entry_i = i + 1
                position = {"entry_i": entry_i, "entry_price": opens[entry_i]}
                i = entry_i
                continue
            i += 1
            continue

        # Sole exit: daily close below the exit MA.
        if not pd.isna(exit_ma[i]) and closes[i] < exit_ma[i]:
            close_position(i, closes[i], f"close_below_ma{exit_ma_period}")
        i += 1

    if position is not None:
        close_position(n - 1, closes[n - 1], "eod")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="v2 exit-spec side backtest")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data, load_index

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers ({args.period})…")
    data = load_data(tickers, args.period)
    if not data:
        print("No data.")
        return

    print("Precomputing per-bar evaluations…")
    prepared: dict[str, tuple[pd.DataFrame, dict]] = {}
    for t, df in data.items():
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) >= DEFAULT_PARAMS.min_bars + 2:
            prepared[t] = (df, precompute_evals(t, df))

    start = min((df.index.min() for df, _ in prepared.values()), default=None)
    start_str = str(start.date()) if start is not None else None
    ew = equal_weight_universe_return({t: d for t, (d, _) in prepared.items()}, start_str)
    idx = load_index("^JKSE", args.period)
    jkse = buy_hold_return(idx, start_str) if idx is not None else None

    print("\n" + "=" * 80)
    print(f"V2 EXIT SPEC — {len(prepared)} tickers, period={args.period}")
    print("Entry: live rules BUY · Exit: ONLY daily close < exit MA (no TP/stop)")
    print("=" * 80)
    print(f"{'variant':<22} {'trades':>7} {'win%':>6} {'exp%':>7} {'PF':>6} "
          f"{'totPnL%':>8} {'maxDD%':>8} {'holdD':>6}")
    print("-" * 80)
    for exit_p in EXIT_MAS:
        for tf in (False, True):
            trades: list[Trade] = []
            for t, (df, evals) in prepared.items():
                trades.extend(run_variant(t, df, evals, exit_p, tf))
            m = compute_metrics(trades)
            pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
            name = f"exit_ma{exit_p}" + ("+trend" if tf else "")
            print(f"{name:<22} {m.trades:>7} {m.win_rate*100:>5.1f} "
                  f"{m.avg_return*100:>6.2f} {pf:>6} {m.total_pnl*100:>7.1f} "
                  f"{m.max_drawdown*100:>7.1f} {m.avg_holding_days:>5.1f}")

    print("-" * 80)
    print("BENCHMARKS (buy & hold, same window):")
    print(f"  Equal-weight universe: {ew*100:+.1f}%")
    if jkse is not None:
        print(f"  IHSG (^JKSE):          {jkse*100:+.1f}%")
    print("⚠️ Additive PnL (1 unit/trade); survivorship bias applies.")


if __name__ == "__main__":
    main()
