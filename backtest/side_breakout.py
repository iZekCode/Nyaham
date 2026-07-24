"""Side experiment: MA50 breakout entry (user-proposed spec).

Proposed strategy:
  - ENTRY : daily close crosses ABOVE MA50 (yesterday ≤, today >) → next open
  - STOP  : daily close below MA50 → exit at that close
  - TP    : nearest MA above price at entry (intraday limit fill), OR a daily
            close below MA10 (trailing momentum exit) — whichever first

Variants isolate each exit component:
  cross_pure   entry + MA50 close-stop only (no TP, no MA10)
  +tp_nearest  … plus TP at the nearest MA above entry
  +ma10_trail  … plus exit on daily close < MA10
  full_spec    all three exits (the proposal as stated)

Re-entry needs a fresh cross (price must close back below MA50 first), which
naturally throttles churn. Fees per the main engine. Pure price/MA logic — no
rules.evaluate needed.

⚠️ Exploratory: both report windows have been used repeatedly this session;
results are directional, not validated.

Run:  python -m backtest.side_breakout --limit 200
"""

from __future__ import annotations

import argparse
import logging
from math import isnan

import numpy as np
import pandas as pd

from backtest.engine import Trade, _net_return
from backtest.metrics import compute_metrics
from config import LOG_LEVEL, MA_PERIODS
from universe import UNIVERSE

logger = logging.getLogger(__name__)

PERIOD = "5y"
SPLIT = "2024-07-24"   # window boundary used throughout FINDINGS.md

VARIANTS = ("cross_pure", "+tp_nearest", "+ma10_trail", "full_spec")


def backtest_ticker(ticker: str, df: pd.DataFrame, variant: str) -> list[Trade]:
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    n = len(df)
    if n < 60:
        return []
    opens = df["Open"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    mas = {
        p: df["Close"].rolling(p, min_periods=p).mean().to_numpy(dtype=float)
        for p in MA_PERIODS
    }
    ma50, ma10 = mas[50], mas[10]
    dates = [str(d.date()) for d in df.index]

    use_tp = variant in ("+tp_nearest", "full_spec")
    use_ma10 = variant in ("+ma10_trail", "full_spec")

    trades: list[Trade] = []
    position = None

    def close_pos(i: int, price: float, reason: str) -> None:
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

    i = 50
    while i < n - 1:
        if position is None:
            # Cross: yesterday closed at/below MA50, today closed above.
            if (not isnan(ma50[i]) and not isnan(ma50[i - 1])
                    and closes[i - 1] <= ma50[i - 1] and closes[i] > ma50[i]):
                tp = None
                if use_tp:
                    above = [mas[p][i] for p in MA_PERIODS
                             if not isnan(mas[p][i]) and mas[p][i] > closes[i]]
                    tp = min(above) if above else None
                entry_i = i + 1
                position = {
                    "entry_i": entry_i,
                    "entry_price": opens[entry_i],
                    "tp": tp,
                }
                i = entry_i
                continue
            i += 1
            continue

        # Exits: TP is a resting limit order → fills intraday, checked first;
        # the close-based exits are only known at end of day.
        tp = position["tp"]
        if use_tp and tp and highs[i] >= tp:
            close_pos(i, float(tp), "tp")
        elif not isnan(ma50[i]) and closes[i] < ma50[i]:
            close_pos(i, closes[i], "ma50_stop")
        elif use_ma10 and not isnan(ma10[i]) and closes[i] < ma10[i]:
            close_pos(i, closes[i], "ma10_trail")
        i += 1

    if position is not None:
        close_pos(n - 1, closes[n - 1], "eod")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="MA50-breakout side backtest")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--period", default=PERIOD)
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data, load_index
    from backtest.metrics import buy_hold_return, equal_weight_universe_return

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers ({args.period})…")
    data = load_data(tickers, args.period)
    if not data:
        print("No data.")
        return

    print("\n" + "=" * 96)
    print(f"MA50 BREAKOUT ENTRY — {len(data)} tickers, {args.period}")
    print("Entry: close crosses above MA50 · windows split at "
          f"{SPLIT} (early ≈ less contaminated)")
    print("=" * 96)
    hdr = (f"{'variant':<13} {'win':>16} | {'trades':>6} {'win%':>6} {'exp%':>7} "
           f"{'PF':>6} {'totPnL%':>8} {'holdD':>6}  exits")
    print(hdr)
    print("-" * 96)
    for variant in VARIANTS:
        all_trades: list[Trade] = []
        for t, df in data.items():
            all_trades.extend(backtest_ticker(t, df, variant))
        for wname, lo, hi in (("early(22-24)", "1900-01-01", SPLIT),
                              ("late(24-26)", SPLIT, "2100-01-01")):
            tw = [t for t in all_trades if lo <= t.entry_date < hi]
            m = compute_metrics(tw)
            pf = ("inf" if m.profit_factor == float("inf")
                  else f"{m.profit_factor:.2f}")
            ex = " ".join(f"{k}:{v}" for k, v in
                          sorted((m.exit_reasons or {}).items()))
            print(f"{variant:<13} {wname:>16} | {m.trades:>6} "
                  f"{m.win_rate*100:>5.1f} {m.avg_return*100:>6.2f} {pf:>6} "
                  f"{m.total_pnl*100:>7.1f} {m.avg_holding_days:>5.1f}  {ex}")
        print("-" * 96)

    for wname, lo, hi in (("early(22-24)", None, SPLIT),
                          ("late(24-26)", SPLIT, None)):
        # Benchmark: equal-weight window B&H via close series clipping.
        rets = []
        for df in data.values():
            c = df["Close"].dropna()
            if lo:
                c = c[c.index >= lo]
            if hi:
                c = c[c.index < hi]
            if len(c) >= 2:
                rets.append(float(c.iloc[-1] / c.iloc[0] - 1.0))
        ew = sum(rets) / len(rets) if rets else 0.0
        print(f"B&H equal-weight {wname}: {ew*100:+.1f}%")
    print("⚠️ Additive PnL (1 unit/trade); survivorship bias; "
          "windows previously used — directional only.")


if __name__ == "__main__":
    main()
