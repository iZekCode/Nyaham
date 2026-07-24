"""Backtest runner (§7):  ``python -m backtest [tickers...] [--tune]``.

Examples
--------
    python -m backtest --limit 20            # quick run on 20 tickers, 3y
    python -m backtest BBCA TLKM ANTM        # specific tickers
    python -m backtest --tune --limit 30     # grid-search parameter tuning
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from backtest.engine import backtest_universe
from backtest.metrics import (
    buy_hold_return,
    compute_metrics,
    equal_weight_universe_return,
    equity_curve,
)
from backtest.tuner import format_report, grid_search, split_date_for
from config import BASE_DIR, LOG_LEVEL
from data.fetcher import get_ohlcv
from universe import UNIVERSE

logger = logging.getLogger(__name__)
OUTPUT_DIR = BASE_DIR / "backtest" / "output"


def load_data(tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV once per ticker (reused across all parameter sets)."""
    data: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers, 1):
        df, _ = get_ohlcv(t, period=period)
        if df is not None and not df.empty:
            data[t] = df
        logger.info("Loaded %d/%d  %s (%s bars)", i, len(tickers), t,
                    0 if df is None else len(df))
    return data


def load_index(symbol: str, period: str):
    """Fetch a raw index series (e.g. ^JKSE) directly from yfinance."""
    import yfinance as yf

    df = yf.download(symbol, period=period, interval="1d",
                     auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def export_equity(trades, path: Path) -> None:
    curve = equity_curve(trades)
    if curve.empty:
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    curve.to_csv(path.with_suffix(".csv"), header=["cum_net_return"])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(curve.index, curve.values * 100, color="#26A69A", lw=1.5)
    ax.axhline(0, color="grey", lw=0.8, ls="--")
    ax.set_title("Backtest equity curve (cumulative net PnL, 1 unit/trade)")
    ax.set_ylabel("Cumulative return (%)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path.with_suffix(".png"), dpi=100)
    plt.close(fig)


def run_report(data: dict[str, pd.DataFrame], period: str) -> None:
    trades = backtest_universe(data)
    metrics = compute_metrics(trades)

    # Common start for a fair benchmark comparison.
    start = min((df.index.min() for df in data.values()), default=None)
    start_str = str(start.date()) if start is not None else None

    ew = equal_weight_universe_return(data, start_str)
    idx_df = load_index("^JKSE", period)
    jkse = buy_hold_return(idx_df, start_str) if idx_df is not None else None

    print("\n" + "=" * 60)
    print(f"BACKTEST REPORT — {len(data)} tickers, period={period}")
    print("=" * 60)
    print(metrics.as_text())
    print(f"Exit reasons:  {metrics.exit_reasons}")
    print("-" * 60)
    print("BENCHMARKS (buy & hold over same window):")
    print(f"  Strategy total PnL:      {metrics.total_pnl * 100:+.1f}%  "
          f"(1 unit/trade, non-compounded)")
    print(f"  Equal-weight universe:   {ew * 100:+.1f}%")
    if jkse is not None:
        print(f"  IHSG (^JKSE):            {jkse * 100:+.1f}%")
    print("-" * 60)
    print("⚠️ Survivorship bias: universe uses CURRENT index constituents (§7.1).")

    out = OUTPUT_DIR / "equity"
    export_equity(trades, out)
    if trades:
        print(f"Equity curve → {out.with_suffix('.csv')} / {out.with_suffix('.png')}")


def run_tune(data: dict[str, pd.DataFrame]) -> None:
    split = split_date_for(data)
    results, best = grid_search(data, split_date=split)
    if not best:
        print("No results (insufficient data).")
        return
    print("\n" + format_report(results, best, split))


def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG strategy backtester")
    parser.add_argument("tickers", nargs="*", help="tickers (default: full universe)")
    parser.add_argument("--period", default="3y", help="history window (default 3y)")
    parser.add_argument("--limit", type=int, default=0,
                        help="use only the first N universe tickers")
    parser.add_argument("--tune", action="store_true", help="run grid-search tuning")
    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    tickers = args.tickers or UNIVERSE
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"Loading {len(tickers)} tickers ({args.period})… this hits the network.")
    data = load_data(tickers, args.period)
    if not data:
        print("No data loaded — check connectivity / tickers.")
        return

    if args.tune:
        run_tune(data)
    else:
        run_report(data, args.period)


if __name__ == "__main__":
    main()
