"""Walk-forward backtest engine (§7.1).

Reuses ``rules.evaluate`` unchanged: the signal on day *t* is produced by
evaluating the DataFrame sliced through day *t*, so there is no look-ahead and
the code being tested is the code that runs live.

Trade model (v2 exit framework — see backtest/FINDINGS.md)
-----------
- **Entry**: a BUY signal at the close of day *t* fills at the **open of t+1**.
- **Exit**: when day *t*'s close is below the exit MA (MA50) — i.e. the
  evaluated result's exit-MA status is "below", or the signal is SELL — the
  position exits at that close. No take-profit, no intraday stop: the exit is
  a condition, not a price target.
- One open position per ticker at a time.
- Costs: ``FEE_BUY`` on entry, ``FEE_SELL`` on exit (config).

Known limitation (documented, accepted for v1): the universe uses *current*
index constituents ⇒ survivorship bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from config import FEE_BUY, FEE_SELL
from screener import indicators as ind
from screener import rules
from screener.params import DEFAULT_PARAMS, Params
from screener.result import DataQuality, Signal


@dataclass
class Trade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    holding_days: int
    exit_reason: str          # "ma_exit" | "eod" (legacy: stop/tp/sell_signal)
    gross_return: float       # (exit/entry) - 1, before costs
    net_return: float         # after round-trip fees


def _net_return(entry: float, exit_: float) -> tuple[float, float]:
    """Return (gross, net) fractional returns including round-trip fees."""
    gross = exit_ / entry - 1.0
    # Effective buy cost raises the basis; sell cost lowers the proceeds.
    net = (exit_ * (1 - FEE_SELL)) / (entry * (1 + FEE_BUY)) - 1.0
    return gross, net


def backtest_ticker(
    ticker: str,
    df: pd.DataFrame,
    params: Optional[Params] = None,
) -> list[Trade]:
    """Simulate the strategy on one ticker's full OHLCV history."""
    params = params or DEFAULT_PARAMS
    if df is None or len(df) < params.min_bars + 2:
        return []

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    n = len(df)
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    dates = [str(d.date()) for d in df.index]

    # Precompute MAs once for the whole series (rolling means are the hot path).
    # Each bar's evaluate then slices these cheaply instead of recomputing.
    mas_full = ind.moving_averages(df, params.ma_periods)

    def _eval(i: int):
        sl = df.iloc[: i + 1]
        cache = {p: s.iloc[: i + 1] for p, s in mas_full.items()}
        return rules.evaluate(
            ticker, sl, quality=DataQuality.OK, params=params, ma_cache=cache
        )

    def _exit_ma_broken(res) -> bool:
        """True when the close is below the exit MA (v2 exit condition)."""
        exit_ma = next(
            (m for m in res.ma if m.period == params.exit_ma_period), None
        )
        return exit_ma is not None and not exit_ma.above

    trades: list[Trade] = []
    position: Optional[dict] = None

    # Start once MA200 is valid; leave at least one bar ahead for entry fills.
    start = params.min_bars
    i = start
    while i < n - 1:
        if position is None:
            res = _eval(i)
            if res.signal is Signal.BUY and res.is_tradeable:
                entry_i = i + 1
                position = {
                    "entry_i": entry_i,
                    "entry_price": opens[entry_i],
                }
                i = entry_i  # start checking exits from the entry bar
                continue
            i += 1
            continue

        # --- holding: v2 exit — daily close below the exit MA ------------ #
        entry_price = position["entry_price"]
        exit_price: Optional[float] = None
        reason = ""

        res = _eval(i)
        if res.signal is Signal.SELL or _exit_ma_broken(res):
            exit_price, reason = closes[i], "ma_exit"

        if exit_price is not None:
            gross, net = _net_return(entry_price, exit_price)
            trades.append(
                Trade(
                    ticker=ticker,
                    entry_date=dates[position["entry_i"]],
                    exit_date=dates[i],
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    holding_days=i - position["entry_i"],
                    exit_reason=reason,
                    gross_return=round(gross, 6),
                    net_return=round(net, 6),
                )
            )
            position = None
        i += 1

    # Close any position still open at the end of data (mark-to-close).
    if position is not None:
        last = n - 1
        gross, net = _net_return(position["entry_price"], closes[last])
        trades.append(
            Trade(
                ticker=ticker,
                entry_date=dates[position["entry_i"]],
                exit_date=dates[last],
                entry_price=round(position["entry_price"], 4),
                exit_price=round(closes[last], 4),
                holding_days=last - position["entry_i"],
                exit_reason="eod",
                gross_return=round(gross, 6),
                net_return=round(net, 6),
            )
        )

    return trades


def backtest_universe(
    data: dict[str, pd.DataFrame],
    params: Optional[Params] = None,
) -> list[Trade]:
    """Run ``backtest_ticker`` across a preloaded {ticker: df} map.

    Preloading (rather than fetching inside the loop) means the tuner can run
    many parameter sets over the same in-memory data.
    """
    params = params or DEFAULT_PARAMS
    all_trades: list[Trade] = []
    for ticker, df in data.items():
        all_trades.extend(backtest_ticker(ticker, df, params))
    return all_trades
