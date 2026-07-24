"""cross_pure on maximum history: MA50 cross-above entry, close-below exit.

Reuses ``side_breakout.backtest_ticker`` (variant="cross_pure") over
``period="max"``. Everything from ~2021-07 onward has been handled repeatedly
this session; entries BEFORE 2021-07-01 are genuinely unseen. Reports:

  - aggregate stats for the unseen era vs the seen era
  - a per-entry-year breakdown (regime behavior: 2008, 2013, 2015, 2018, 2020…)
  - per-era benchmarks (equal-weight B&H of the same universe, and IHSG)

⚠️ Survivorship bias grows with lookback (current constituents only) — the
equal-weight benchmark shares the bias, so read strategy-vs-benchmark, not
absolute returns.

Run:  python -m backtest.side_cross_history --limit 200
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.side_breakout import backtest_ticker
from config import LOG_LEVEL
from universe import UNIVERSE

logger = logging.getLogger(__name__)

SEEN_START = "2021-07-01"   # session experiments covered ~2021-07 → 2026-07


def _window_bh(close: pd.Series, start: str | None, end: str | None) -> float:
    c = close.dropna()
    if start:
        c = c[c.index >= start]
    if end:
        c = c[c.index < end]
    if len(c) < 2:
        return 0.0
    return float(c.iloc[-1] / c.iloc[0] - 1.0)


def _print_stats(label: str, trades: list) -> None:
    m = compute_metrics(trades)
    if m.trades == 0:
        print(f"{label:<14} (no trades)")
        return
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print(f"{label:<14} {m.trades:>6} {m.win_rate*100:>5.1f} "
          f"{m.avg_return*100:>6.2f} {pf:>6} {m.total_pnl*100:>8.1f} "
          f"{m.avg_holding_days:>5.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="cross_pure on max history")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data, load_index

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers (max history)…")
    data = load_data(tickers, "max")
    if not data:
        print("No data.")
        return

    all_trades = []
    for t, df in data.items():
        all_trades.extend(backtest_ticker(t, df, "cross_pure"))

    earliest = min((df.index.min() for df in data.values()), default=None)
    print("\n" + "=" * 86)
    print(f"CROSS_PURE — MAX HISTORY — {len(data)} tickers, "
          f"earliest bar {earliest.date() if earliest is not None else '?'}")
    print("Entry: close crosses above MA50 · Exit: daily close < MA50 · no TP")
    print(f"UNSEEN era: entries before {SEEN_START} · SEEN era: after")
    print("=" * 86)
    hdr = (f"{'window':<14} {'trades':>6} {'win%':>6} {'exp%':>7} "
           f"{'PF':>6} {'totPnL%':>9} {'holdD':>6}")
    print(hdr)
    print("-" * 66)

    unseen = [t for t in all_trades if t.entry_date < SEEN_START]
    seen = [t for t in all_trades if t.entry_date >= SEEN_START]
    _print_stats("UNSEEN (<21)", unseen)
    _print_stats("SEEN (21-26)", seen)
    print("-" * 66)

    years = sorted({t.entry_date[:4] for t in all_trades})
    for y in years:
        yt = [t for t in all_trades if t.entry_date.startswith(y)]
        _print_stats(y, yt)

    print("-" * 86)
    idx = load_index("^JKSE", "max")
    for label, lo, hi in (("UNSEEN era", None, SEEN_START),
                          ("SEEN era", SEEN_START, None)):
        rets = [_window_bh(df["Close"], lo, hi) for df in data.values()]
        rets = [r for r in rets if r != 0.0]
        ew = sum(rets) / len(rets) if rets else 0.0
        jk = _window_bh(idx["Close"], lo, hi) if idx is not None else 0.0
        print(f"B&H {label}: equal-weight {ew*100:+.1f}% · IHSG {jk*100:+.1f}%")
    print("⚠️ Additive PnL; survivorship bias grows with lookback — compare "
          "strategy vs benchmark within an era, not across eras.")


if __name__ == "__main__":
    main()
