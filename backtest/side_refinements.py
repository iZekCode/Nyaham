"""Side experiment: test each FINDINGS.md refinement in isolation vs baseline.

Variants (entry logic is IDENTICAL to live `rules.evaluate` everywhere —
BUY = near support + short-term bullish; rule-4 SELL exits stay active):

  baseline      current engine behavior: stop = next MA below entry support,
                triggered intraday (Low <= stop)
  close_stop    refinement #3 — same stop level, but only a daily CLOSE below
                the stop exits (no intraday noise stop-outs)
  atr_stop      refinement #1a — stop = entry − 1.5×ATR(14), intraday
  cluster_stop  refinement #1b — stop 1% below the whole MA cluster (all MAs
                within 3% under the support MA), intraday
  trend_filter  refinement #2 — baseline exits, but entries also require the
                Medium AND Long tiers bullish
  cooldown      refinement #4 — baseline, but after a stop-out the same ticker
                can't re-enter for 5 trading days
  combo         atr stop + close trigger + trend filter + cooldown

Take-profit (nearest MA resistance) is kept identical everywhere so only the
refinement under test changes. Fees per the main engine.

Run:  python -m backtest.side_refinements --limit 200 --period 2y
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from backtest.engine import Trade, _net_return
from backtest.metrics import (
    buy_hold_return,
    compute_metrics,
    equal_weight_universe_return,
)
from config import LOG_LEVEL
from screener import indicators as ind
from screener import rules
from screener.params import DEFAULT_PARAMS
from screener.result import DataQuality, Signal
from universe import UNIVERSE

logger = logging.getLogger(__name__)

ATR_PERIOD = 14
ATR_MULT = 1.5
CLUSTER_PCT = 0.03    # MAs within 3% below support form the "cluster"
CLUSTER_BUF = 0.01    # stop sits 1% below the cluster low
COOLDOWN_DAYS = 5


@dataclass
class Variant:
    name: str
    stop_mode: str = "next_ma"     # next_ma | atr | cluster
    close_trigger: bool = False    # stop fires on close, not intraday low
    trend_filter: bool = False     # require Medium+Long tiers bullish at entry
    cooldown: int = 0              # trading days blocked after a stop-out


VARIANTS: tuple[Variant, ...] = (
    Variant("baseline"),
    Variant("close_stop", close_trigger=True),
    Variant("atr_stop", stop_mode="atr"),
    Variant("cluster_stop", stop_mode="cluster"),
    Variant("trend_filter", trend_filter=True),
    Variant("cooldown", cooldown=COOLDOWN_DAYS),
    Variant("combo", stop_mode="atr", close_trigger=True,
            trend_filter=True, cooldown=COOLDOWN_DAYS),
)


def _atr(df: pd.DataFrame) -> np.ndarray:
    """ATR(14): rolling mean of true range."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean().to_numpy(dtype=float)


def precompute_evals(ticker: str, df: pd.DataFrame) -> dict:
    """Evaluate the live rules once per bar; shared by every variant."""
    params = DEFAULT_PARAMS
    mas_full = ind.moving_averages(df, params.ma_periods)
    evals: dict[int, object] = {}
    for i in range(params.min_bars, len(df)):
        cache = {p: s.iloc[: i + 1] for p, s in mas_full.items()}
        evals[i] = rules.evaluate(
            ticker, df.iloc[: i + 1], quality=DataQuality.OK,
            params=params, ma_cache=cache,
        )
    return evals


def _stop_level(res, entry_price: float, atr_now: float, mode: str) -> Optional[float]:
    if mode == "atr":
        if np.isnan(atr_now):
            return res.stop_loss
        return ind.round_to_tick(entry_price - ATR_MULT * atr_now, "down")
    if mode == "cluster":
        sup = res.nearest_support
        if sup is None:
            return res.stop_loss
        cluster = [m.value for m in res.ma
                   if sup.value * (1 - CLUSTER_PCT) <= m.value <= sup.value]
        lo = min(cluster) if cluster else sup.value
        return ind.round_to_tick(lo * (1 - CLUSTER_BUF), "down")
    return res.stop_loss  # next_ma (baseline)


def run_variant(ticker: str, df: pd.DataFrame, evals: dict, v: Variant) -> list[Trade]:
    params = DEFAULT_PARAMS
    n = len(df)
    if n < params.min_bars + 2:
        return []
    opens = df["Open"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    atr = _atr(df)
    dates = [str(d.date()) for d in df.index]

    trades: list[Trade] = []
    position: Optional[dict] = None
    blocked_until = -1

    def close_position(i: int, exit_price: float, reason: str) -> None:
        nonlocal position, blocked_until
        gross, net = _net_return(position["entry_price"], exit_price)
        trades.append(Trade(
            ticker=ticker,
            entry_date=dates[position["entry_i"]],
            exit_date=dates[i],
            entry_price=round(position["entry_price"], 4),
            exit_price=round(exit_price, 4),
            holding_days=i - position["entry_i"],
            exit_reason=reason,
            gross_return=round(gross, 6),
            net_return=round(net, 6),
        ))
        if reason == "stop" and v.cooldown:
            blocked_until = i + v.cooldown
        position = None

    i = params.min_bars
    while i < n - 1:
        if position is None:
            if i >= blocked_until:
                res = evals[i]
                ok = res.signal is Signal.BUY and res.is_tradeable
                if ok and v.trend_filter:
                    ok = res.trends[1].bullish and res.trends[2].bullish
                if ok:
                    entry_i = i + 1
                    entry_price = opens[entry_i]
                    position = {
                        "entry_i": entry_i,
                        "entry_price": entry_price,
                        "stop": _stop_level(res, entry_price, atr[i], v.stop_mode),
                        "tp": res.sell_at,
                    }
                    i = entry_i
                    continue
            i += 1
            continue

        stop, tp = position["stop"], position["tp"]
        if v.close_trigger:
            # TP is a resting limit order — it fills intraday before the close
            # is known; the close-based stop is checked at end of day.
            if tp and highs[i] >= tp:
                close_position(i, float(tp), "tp")
            elif stop and closes[i] < stop:
                close_position(i, closes[i], "stop")
            elif evals[i].signal is Signal.SELL:
                close_position(i, closes[i], "sell_signal")
        else:
            # Intraday: stop wins ties (conservative), as in the main engine.
            if stop and lows[i] <= stop:
                close_position(i, float(stop), "stop")
            elif tp and highs[i] >= tp:
                close_position(i, float(tp), "tp")
            elif evals[i].signal is Signal.SELL:
                close_position(i, closes[i], "sell_signal")
        i += 1

    if position is not None:
        close_position(n - 1, closes[n - 1], "eod")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Refinement side backtests")
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

    # Clean frames + one shared per-bar evaluation pass per ticker.
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

    print("\n" + "=" * 86)
    print(f"REFINEMENT SIDE BACKTESTS — {len(prepared)} tickers, period={args.period}")
    print("Entry: live rules BUY (near support + short-term bullish) unless noted")
    print("=" * 86)
    print(f"{'variant':<14} {'trades':>7} {'win%':>6} {'exp%':>7} {'PF':>6} "
          f"{'totPnL%':>8} {'maxDD%':>8} {'holdD':>6}  exits")
    print("-" * 86)
    for v in VARIANTS:
        trades: list[Trade] = []
        for t, (df, evals) in prepared.items():
            trades.extend(run_variant(t, df, evals, v))
        m = compute_metrics(trades)
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        ex = " ".join(f"{k}:{n}" for k, n in sorted((m.exit_reasons or {}).items()))
        print(f"{v.name:<14} {m.trades:>7} {m.win_rate*100:>5.1f} "
              f"{m.avg_return*100:>6.2f} {pf:>6} {m.total_pnl*100:>7.1f} "
              f"{m.max_drawdown*100:>7.1f} {m.avg_holding_days:>5.1f}  {ex}")

    print("-" * 86)
    print("BENCHMARKS (buy & hold, same window):")
    print(f"  Equal-weight universe: {ew*100:+.1f}%")
    if jkse is not None:
        print(f"  IHSG (^JKSE):          {jkse*100:+.1f}%")
    print("⚠️ Additive PnL (1 unit/trade); survivorship bias applies.")


if __name__ == "__main__":
    main()
