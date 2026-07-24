"""Out-of-sample validation of the frozen v2 configuration.

The v2 winner (live BUY entry + Med/Long trend filter + exit ONLY on a daily
close below MA50) was found by iterating on the 2-year window ending
2026-07-24 — that window is contaminated (in-sample by selection). This script
fetches ~5 years, runs the FROZEN config once, and splits trades by entry date:

  - OOS  : entries BEFORE 2024-07-24 (data no experiment ever saw)
  - IS   : entries ON/AFTER 2024-07-24 (should ≈ reproduce the v2 result)

Nothing is tuned here. One config, one run, judged on the unseen years.

Run:  python -m backtest.side_v2_oos --limit 200
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.side_refinements import precompute_evals
from backtest.side_v2 import run_variant
from config import LOG_LEVEL
from screener.params import DEFAULT_PARAMS
from universe import UNIVERSE

logger = logging.getLogger(__name__)

SPLIT_DATE = "2024-07-24"   # start of the contaminated 2y experiment window
EXIT_MA = 50
TREND_FILTER = True
PERIOD = "5y"


def _window_bh(df: pd.DataFrame, start: str, end: str) -> float:
    """Buy-and-hold return of Close between two dates (inclusive slice)."""
    c = df["Close"].dropna()
    c = c[(c.index >= pd.Timestamp(start)) & (c.index <= pd.Timestamp(end))]
    if len(c) < 2:
        return 0.0
    return float(c.iloc[-1] / c.iloc[0] - 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="v2 out-of-sample validation")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data, load_index

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers ({PERIOD})…")
    data = load_data(tickers, PERIOD)
    if not data:
        print("No data.")
        return

    print("Precomputing per-bar evaluations (5y — this takes a while)…")
    all_trades = []
    kept: dict[str, pd.DataFrame] = {}
    for k, (t, df) in enumerate(data.items(), 1):
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < DEFAULT_PARAMS.min_bars + 2:
            continue
        evals = precompute_evals(t, df)
        all_trades.extend(run_variant(t, df, evals, EXIT_MA, TREND_FILTER))
        kept[t] = df
        if k % 20 == 0:
            print(f"  …{k}/{len(data)} tickers simulated")

    oos = [t for t in all_trades if t.entry_date < SPLIT_DATE]
    ins = [t for t in all_trades if t.entry_date >= SPLIT_DATE]
    m_oos, m_ins = compute_metrics(oos), compute_metrics(ins)

    # Benchmarks per window (entries in OOS start after ~250-bar MA warmup).
    oos_start = min((t.entry_date for t in oos), default=SPLIT_DATE)
    oos_end = max((t.exit_date for t in oos), default=SPLIT_DATE)
    ins_end = max((t.exit_date for t in ins), default=SPLIT_DATE)
    ew_oos = [
        _window_bh(df, oos_start, oos_end) for df in kept.values()
    ]
    ew_ins = [
        _window_bh(df, SPLIT_DATE, ins_end) for df in kept.values()
    ]
    ew_oos_avg = sum(ew_oos) / len(ew_oos) if ew_oos else 0.0
    ew_ins_avg = sum(ew_ins) / len(ew_ins) if ew_ins else 0.0

    idx = load_index("^JKSE", PERIOD)
    jkse_oos = _window_bh(idx, oos_start, oos_end) if idx is not None else None
    jkse_ins = _window_bh(idx, SPLIT_DATE, ins_end) if idx is not None else None

    print("\n" + "=" * 78)
    print(f"V2 OUT-OF-SAMPLE VALIDATION — {len(kept)} tickers, {PERIOD}")
    print(f"Frozen config: live BUY + Med/Long trend filter + exit close<MA{EXIT_MA}")
    print(f"Split: entries before {SPLIT_DATE} = OOS (unseen) · after = IS (seen)")
    print("=" * 78)
    for label, m, ew, jk, window in (
        ("OUT-OF-SAMPLE", m_oos, ew_oos_avg, jkse_oos, f"{oos_start} → {oos_end}"),
        ("IN-SAMPLE",     m_ins, ew_ins_avg, jkse_ins, f"{SPLIT_DATE} → {ins_end}"),
    ):
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        print(f"\n{label}  ({window})")
        print(f"  Trades: {m.trades}   Win: {m.win_rate*100:.1f}%   "
              f"Exp: {m.avg_return*100:+.2f}%/trade   PF: {pf}")
        print(f"  Total PnL: {m.total_pnl*100:+.1f}%   MaxDD: {m.max_drawdown*100:.1f}%"
              f"   Hold: {m.avg_holding_days:.1f}d")
        jk_s = f"{jk*100:+.1f}%" if jk is not None else "n/a"
        print(f"  Benchmarks: equal-weight B&H {ew*100:+.1f}% · IHSG {jk_s}")
    print("\n⚠️ Additive PnL (1 unit/trade); survivorship bias applies.")


if __name__ == "__main__":
    main()
