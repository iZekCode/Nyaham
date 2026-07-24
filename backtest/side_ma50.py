"""Side experiment: MA50-only strategy.

Rules (as requested):
  - Support is **MA50 only** (the other MAs are ignored).
  - **Entry**: while flat, when the close is at/above MA50 and within
    ``near_threshold`` of it (a pullback-to-MA50 buy) → fill at the next open.
  - **Exit / stop**: when a **daily close** prints below MA50 → sell at that
    close. No fixed take-profit, so winners ride until MA50 breaks.

No look-ahead: the entry/exit decision on day *t* uses day *t*'s close and
MA50[t]; entries fill at the *t+1* open, close-stops fill at the *t* close
(market-on-close). Costs use the same fee model as the main engine.

Run:  python -m backtest.side_ma50 --limit 8 --period 2y
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from backtest.engine import Trade, _net_return
from backtest.metrics import (
    buy_hold_return,
    compute_metrics,
    equal_weight_universe_return,
    equity_curve,
)
from config import BASE_DIR, LOG_LEVEL
from universe import UNIVERSE

logger = logging.getLogger(__name__)
OUTPUT_DIR = BASE_DIR / "backtest" / "output"
MA = 50
MIN_BARS = MA + 10  # need a valid MA50 plus a little runway


def backtest_ticker_ma50(
    ticker: str, df: pd.DataFrame, near_threshold: float
) -> list[Trade]:
    if df is None or len(df) < MIN_BARS + 2:
        return []
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    n = len(df)
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    ma = df["Close"].rolling(MA, min_periods=MA).mean().to_numpy(dtype=float)
    dates = [str(d.date()) for d in df.index]

    trades: list[Trade] = []
    position = None
    i = MA
    while i < n - 1:
        if pd.isna(ma[i]):
            i += 1
            continue

        if position is None:
            # Entry: close at/above MA50 and within near_threshold of it.
            dist = (closes[i] - ma[i]) / ma[i]
            if 0.0 <= dist <= near_threshold:
                entry_i = i + 1
                position = {"entry_i": entry_i, "entry_price": opens[entry_i]}
                i = entry_i
                continue
            i += 1
            continue

        # Holding: exit when the close prints below MA50.
        if not pd.isna(ma[i]) and closes[i] < ma[i]:
            gross, net = _net_return(position["entry_price"], closes[i])
            trades.append(
                Trade(
                    ticker=ticker,
                    entry_date=dates[position["entry_i"]],
                    exit_date=dates[i],
                    entry_price=round(position["entry_price"], 4),
                    exit_price=round(closes[i], 4),
                    holding_days=i - position["entry_i"],
                    exit_reason="close_below_ma50",
                    gross_return=round(gross, 6),
                    net_return=round(net, 6),
                )
            )
            position = None
        i += 1

    if position is not None:  # mark open position to final close
        last = n - 1
        gross, net = _net_return(position["entry_price"], closes[last])
        trades.append(
            Trade(ticker, dates[position["entry_i"]], dates[last],
                  round(position["entry_price"], 4), round(closes[last], 4),
                  last - position["entry_i"], "eod", round(gross, 6), round(net, 6))
        )
    return trades


def backtest_universe_ma50(data, near_threshold: float) -> list[Trade]:
    out: list[Trade] = []
    for t, df in data.items():
        out.extend(backtest_ticker_ma50(t, df, near_threshold))
    return out


def export_equity(trades, name: str) -> None:
    curve = equity_curve(trades)
    if curve.empty:
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(curve.index, curve.values * 100, color="#42A5F5", lw=1.5)
    ax.axhline(0, color="grey", lw=0.8, ls="--")
    ax.set_title(f"MA50-only strategy — cumulative net PnL ({name})")
    ax.set_ylabel("Cumulative return (%)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / f"ma50_equity_{name}.png", dpi=100)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="MA50-only side backtest")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument(
        "--near", type=float, nargs="+",
        default=[0.01, 0.02, 0.03, 0.05, 1.0],
        help="near-MA50 entry thresholds to sweep (1.0 ≈ 'any close above MA50')",
    )
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    # Reuse the main runner's network loader.
    from backtest.__main__ import load_data, load_index

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers ({args.period})…")
    data = load_data(tickers, args.period)
    if not data:
        print("No data.")
        return

    start = min((df.index.min() for df in data.values()), default=None)
    start_str = str(start.date()) if start is not None else None
    ew = equal_weight_universe_return(data, start_str)
    idx = load_index("^JKSE", args.period)
    jkse = buy_hold_return(idx, start_str) if idx is not None else None

    print("\n" + "=" * 74)
    print(f"MA50-ONLY STRATEGY — {len(data)} tickers, period={args.period}")
    print("Entry: pullback to within N% above MA50 · Exit: daily close < MA50")
    print("=" * 74)
    print(f"{'near':>6} {'trades':>7} {'win%':>6} {'exp%':>7} {'PF':>6} "
          f"{'totPnL%':>8} {'maxDD%':>7} {'holdD':>6}")
    print("-" * 74)
    best_name = None
    for near in args.near:
        trades = backtest_universe_ma50(data, near)
        m = compute_metrics(trades)
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        label = "any" if near >= 1.0 else f"{near*100:.0f}%"
        print(f"{label:>6} {m.trades:>7} {m.win_rate*100:>5.1f} "
              f"{m.avg_return*100:>6.2f} {pf:>6} {m.total_pnl*100:>7.1f} "
              f"{m.max_drawdown*100:>6.1f} {m.avg_holding_days:>5.1f}")
        name = f"{int(near*100)}pct" if near < 1.0 else "any"
        export_equity(trades, name)
        best_name = best_name or name

    print("-" * 74)
    print("BENCHMARKS (buy & hold, same window):")
    print(f"  Equal-weight universe: {ew*100:+.1f}%")
    if jkse is not None:
        print(f"  IHSG (^JKSE):          {jkse*100:+.1f}%")
    print("⚠️ Survivorship bias: current index constituents only.")
    print(f"Equity PNGs → {OUTPUT_DIR}/ma50_equity_*.png")


if __name__ == "__main__":
    main()
