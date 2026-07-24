"""Side experiment v3: portfolio-level sim combining the five improvements.

Base signal (frozen from v2): live ``rules.evaluate`` BUY + Med/Long trend
filter, entered at next open. Exit is structural (below), no TP.

The five improvements under test:
  1. Regime filter    — entries only when risk-on: ^JKSE > its MA200, or
                        breadth (>50% of universe above MA50). Axis: none /
                        jkse_ma200 / breadth50.
  2. Whipsaw exit     — axis: ma50_confirm2 (two consecutive closes < MA50) /
                        ma50_buffer1 (close < MA50×0.99) / ma100 (plain).
  3. Trade less       — 5-day re-entry cooldown per ticker (always on) +
                        whatever the slower exits add.
  4. Selection        — axis: rank BUY candidates by 126-day momentum vs
                        first-come, filling limited slots.
  5. Portfolio realism— max 10 concurrent positions, equal allocation of
                        current equity per slot, compounding, fees per trade.

ANTI-SNOOPING PROTOCOL
----------------------
  dev      entries < 2022-01-01   → grid search happens HERE ONLY (--dev)
  val      2022-01-01 … 2024-07-23 → one look, after freezing (--final)
  confirm  ≥ 2024-07-24            → one look, same run      (--final)

--dev prints ONLY dev-window results for all combos. --final runs one frozen
combo and prints all three windows. Fetch+precompute is cached to disk so the
two phases share identical data.

Run:
  python -m backtest.side_v3 --dev
  python -m backtest.side_v3 --final ma50_confirm2 jkse_ma200 momentum
"""

from __future__ import annotations

import argparse
import logging
import pickle
from math import isnan
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import Trade, _net_return
from config import FEE_BUY, FEE_SELL, LOG_LEVEL
from screener import indicators as ind
from screener import rules
from screener.params import DEFAULT_PARAMS
from screener.result import DataQuality, Signal
from universe import UNIVERSE

logger = logging.getLogger(__name__)

DEV_END = "2022-01-01"
VAL_END = "2024-07-24"
PERIOD = "10y"

MAX_POS = 10
COOLDOWN = 5           # ticker bars after an exit before re-entry
MOM_LOOKBACK = 126     # ~6 months

EXITS = ("ma50_confirm2", "ma50_buffer1", "ma100")
REGIMES = ("none", "jkse_ma200", "breadth50")
RANKINGS = ("momentum", "none")

CACHE_DEFAULT = Path(
    "/private/tmp/claude-501/-Users-filbertnaldowijaya-Documents-GitHub-Nyaham/"
    "9daa5609-45c9-4033-80a6-37d17ccc0c85/scratchpad/v3_cache.pkl"
)


# --------------------------------------------------------------------------- #
# Data preparation (expensive; cached)
# --------------------------------------------------------------------------- #
def prep_ticker(ticker: str, df: pd.DataFrame):
    """Arrays + per-bar BUY flags from the live rules (trend filter baked in)."""
    params = DEFAULT_PARAMS
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    n = len(df)
    if n < params.min_bars + 2:
        return None

    closes = df["Close"].to_numpy(dtype=float)
    opens = df["Open"].to_numpy(dtype=float)
    ma50 = df["Close"].rolling(50, min_periods=50).mean().to_numpy(dtype=float)
    ma100 = df["Close"].rolling(100, min_periods=100).mean().to_numpy(dtype=float)
    mom = np.full(n, np.nan)
    mom[MOM_LOOKBACK:] = closes[MOM_LOOKBACK:] / closes[:-MOM_LOOKBACK] - 1.0

    mas_full = ind.moving_averages(df, params.ma_periods)
    buy_ok = np.zeros(n, dtype=bool)
    for i in range(params.min_bars, n):
        cache = {p: s.iloc[: i + 1] for p, s in mas_full.items()}
        res = rules.evaluate(
            ticker, df.iloc[: i + 1], quality=DataQuality.OK,
            params=params, ma_cache=cache,
        )
        buy_ok[i] = (
            res.signal is Signal.BUY
            and res.is_tradeable
            and res.trends[1].bullish
            and res.trends[2].bullish
        )

    dates = list(df.index)
    return {
        "n": n,
        "dates": dates,
        "idx": {d: i for i, d in enumerate(dates)},
        "open": opens,
        "close": closes,
        "ma50": ma50,
        "ma100": ma100,
        "mom": mom,
        "buy_ok": buy_ok,
    }


def build_cache(tickers: list[str], cache_path: Path) -> dict:
    from backtest.__main__ import load_data, load_index

    print(f"Loading {len(tickers)} tickers ({PERIOD})…")
    data = load_data(tickers, PERIOD)
    print("Precomputing per-bar BUY flags (10y × universe — slow)…")
    prepped: dict[str, dict] = {}
    for k, (t, df) in enumerate(data.items(), 1):
        p = prep_ticker(t, df)
        if p is not None:
            prepped[t] = p
        if k % 10 == 0:
            print(f"  …{k}/{len(data)}")

    jkse = load_index("^JKSE", PERIOD)
    payload = {"prepped": prepped, "jkse": jkse}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"Cached → {cache_path}")
    return payload


def load_or_build(tickers: list[str], cache_path: Path) -> dict:
    if cache_path.exists():
        print(f"Loading cache {cache_path}…")
        with open(cache_path, "rb") as f:
            return pickle.load(f)
    return build_cache(tickers, cache_path)


# --------------------------------------------------------------------------- #
# Regime series
# --------------------------------------------------------------------------- #
def build_regimes(prepped: dict, jkse: pd.DataFrame, calendar: list) -> dict:
    out: dict[str, dict] = {"none": {}}

    jk_ok: dict = {}
    if jkse is not None and not jkse.empty:
        c = jkse["Close"].dropna()
        ma200 = c.rolling(200, min_periods=200).mean()
        flags = (c > ma200)
        # Align to the trade calendar (ffill across holiday mismatches).
        flags = flags.reindex(pd.DatetimeIndex(calendar)).ffill().fillna(False)
        jk_ok = {d: bool(v) for d, v in flags.items()}
    out["jkse_ma200"] = jk_ok

    br_ok: dict = {}
    above: dict = {}
    total: dict = {}
    for p in prepped.values():
        cl, ma = p["close"], p["ma50"]
        for d, i in p["idx"].items():
            if isnan(ma[i]):
                continue
            total[d] = total.get(d, 0) + 1
            if cl[i] > ma[i]:
                above[d] = above.get(d, 0) + 1
    for d in calendar:
        n = total.get(d, 0)
        br_ok[d] = n >= 10 and (above.get(d, 0) / n) > 0.5
    out["breadth50"] = br_ok
    return out


# --------------------------------------------------------------------------- #
# Portfolio simulation
# --------------------------------------------------------------------------- #
def simulate(prepped: dict, calendar: list, regime: dict,
             exit_mode: str, ranking: str) -> tuple[list[Trade], pd.Series]:
    positions: dict[str, dict] = {}
    cooldown_until: dict[str, int] = {}
    last_close: dict[str, float] = {}
    cash = 1.0
    trades: list[Trade] = []
    eq_dates, eq_vals = [], []

    def exit_hit(p: dict, i: int) -> bool:
        c = p["close"][i]
        if exit_mode == "ma50_confirm2":
            m = p["ma50"]
            return (i >= 1 and not isnan(m[i]) and not isnan(m[i - 1])
                    and c < m[i] and p["close"][i - 1] < m[i - 1])
        if exit_mode == "ma50_buffer1":
            m = p["ma50"]
            return not isnan(m[i]) and c < m[i] * 0.99
        m = p["ma100"]
        return not isnan(m[i]) and c < m[i]

    entry_ma_key = "ma100" if exit_mode == "ma100" else "ma50"

    for d in calendar:
        # ---- exits ----------------------------------------------------- #
        for t in list(positions):
            p = prepped[t]
            i = p["idx"].get(d)
            if i is None:
                continue
            pos = positions[t]
            if exit_hit(p, i) or i == p["n"] - 1:
                price = p["close"][i]
                cash += pos["shares"] * price * (1 - FEE_SELL)
                gross, net = _net_return(pos["entry_price"], price)
                trades.append(Trade(
                    ticker=t,
                    entry_date=str(pos["entry_date"].date()),
                    exit_date=str(d.date()),
                    entry_price=round(pos["entry_price"], 4),
                    exit_price=round(price, 4),
                    holding_days=i - pos["entry_i"],
                    exit_reason="ma_exit" if i < p["n"] - 1 else "eod",
                    gross_return=round(gross, 6),
                    net_return=round(net, 6),
                ))
                cooldown_until[t] = i + COOLDOWN
                del positions[t]

        # ---- update marks ---------------------------------------------- #
        for t, p in prepped.items():
            i = p["idx"].get(d)
            if i is not None:
                last_close[t] = p["close"][i]
        mark = cash + sum(pos["shares"] * last_close[t]
                          for t, pos in positions.items())

        # ---- entries (decision at close d, fill next open) -------------- #
        if regime.get(d, True) if regime else True:
            slots = MAX_POS - len(positions)
            if slots > 0:
                cands = []
                for t, p in prepped.items():
                    if t in positions:
                        continue
                    i = p["idx"].get(d)
                    if i is None or i + 1 >= p["n"]:
                        continue
                    if i < cooldown_until.get(t, -1):
                        continue
                    if not p["buy_ok"][i]:
                        continue
                    m = p[entry_ma_key]
                    if isnan(m[i]) or p["close"][i] <= m[i]:
                        continue
                    mv = p["mom"][i]
                    cands.append((t, i, float("-inf") if isnan(mv) else mv))
                if ranking == "momentum":
                    cands.sort(key=lambda x: -x[2])
                for t, i, _ in cands[:slots]:
                    price = prepped[t]["open"][i + 1]
                    if isnan(price) or price <= 0:
                        continue
                    alloc = min(mark / MAX_POS, cash)
                    if alloc < mark * 0.02:
                        break
                    shares = alloc / (price * (1 + FEE_BUY))
                    cash -= shares * price * (1 + FEE_BUY)
                    positions[t] = {
                        "shares": shares,
                        "entry_price": price,
                        "entry_i": i + 1,
                        "entry_date": prepped[t]["dates"][i + 1],
                    }

        eq_dates.append(d)
        eq_vals.append(cash + sum(pos["shares"] * last_close[t]
                                  for t, pos in positions.items()))

    return trades, pd.Series(eq_vals, index=pd.DatetimeIndex(eq_dates))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def window_stats(trades: list[Trade], equity: pd.Series,
                 start: str, end: str) -> dict:
    eq = equity[(equity.index >= start) & (equity.index < end)]
    if len(eq) < 2:
        return {}
    ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (1.0 + ret) ** (1.0 / years) - 1.0
    dd = float((eq / eq.cummax() - 1.0).min())
    tw = [t for t in trades if start <= t.entry_date < end]
    wins = [t for t in tw if t.net_return > 0]
    return {
        "ret": ret, "cagr": cagr, "dd": dd, "trades": len(tw),
        "win": len(wins) / len(tw) if tw else 0.0,
        "exp": sum(t.net_return for t in tw) / len(tw) if tw else 0.0,
    }


def bench_window(prepped: dict, jkse, start: str, end: str) -> tuple[float, float]:
    rets = []
    for p in prepped.values():
        s = pd.Series(p["close"], index=pd.DatetimeIndex(p["dates"]))
        s = s[(s.index >= start) & (s.index < end)]
        if len(s) >= 2:
            rets.append(float(s.iloc[-1] / s.iloc[0] - 1.0))
    ew = sum(rets) / len(rets) if rets else 0.0
    jk = 0.0
    if jkse is not None and not jkse.empty:
        c = jkse["Close"].dropna()
        c = c[(c.index >= start) & (c.index < end)]
        if len(c) >= 2:
            jk = float(c.iloc[-1] / c.iloc[0] - 1.0)
    return ew, jk


def main() -> None:
    parser = argparse.ArgumentParser(description="v3 portfolio side backtest")
    parser.add_argument("--dev", action="store_true",
                        help="grid over all combos, DEV window results only")
    parser.add_argument("--final", nargs=3, metavar=("EXIT", "REGIME", "RANK"),
                        help="one frozen combo, all three windows")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--cache", type=Path, default=CACHE_DEFAULT)
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    payload = load_or_build(UNIVERSE[: args.limit], args.cache)
    prepped, jkse = payload["prepped"], payload["jkse"]
    all_dates = sorted({d for p in prepped.values() for d in p["dates"]})
    regimes = build_regimes(prepped, jkse, all_dates)
    data_start = str(all_dates[0].date()) if all_dates else "?"
    data_end = str(all_dates[-1].date()) if all_dates else "?"

    if args.dev:
        print("\n" + "=" * 88)
        print(f"V3 DEV GRID — {len(prepped)} tickers, data {data_start}→{data_end}"
              f", DEV window < {DEV_END} ONLY")
        print(f"Portfolio: max {MAX_POS} positions, cooldown {COOLDOWN}d, "
              f"compounding, fees {FEE_BUY+FEE_SELL:.2%} r/t")
        print("=" * 88)
        ew, jk = bench_window(prepped, jkse, "1900-01-01", DEV_END)
        print(f"{'exit':<14} {'regime':<11} {'rank':<9} {'trades':>6} {'win%':>6} "
              f"{'exp%':>6} {'ret%':>8} {'CAGR%':>7} {'maxDD%':>7}")
        print("-" * 88)
        for ex in EXITS:
            for rg in REGIMES:
                for rk in RANKINGS:
                    trades, eq = simulate(prepped, all_dates, regimes[rg], ex, rk)
                    s = window_stats(trades, eq, "1900-01-01", DEV_END)
                    if not s:
                        continue
                    print(f"{ex:<14} {rg:<11} {rk:<9} {s['trades']:>6} "
                          f"{s['win']*100:>5.1f} {s['exp']*100:>5.2f} "
                          f"{s['ret']*100:>7.1f} {s['cagr']*100:>6.1f} "
                          f"{s['dd']*100:>6.1f}")
        print("-" * 88)
        print(f"DEV benchmarks: equal-weight B&H {ew*100:+.1f}% · IHSG {jk*100:+.1f}%")
        print("Pick ONE combo, then run --final. Do not re-grid after seeing val/confirm.")
        return

    if args.final:
        ex, rg, rk = args.final
        trades, eq = simulate(prepped, all_dates, regimes[rg], ex, rk)
        print("\n" + "=" * 78)
        print(f"V3 FINAL — frozen: exit={ex} regime={rg} rank={rk}")
        print(f"{len(prepped)} tickers, data {data_start}→{data_end}")
        print("=" * 78)
        for label, a, b in (
            ("DEV      (seen, grid-searched)", "1900-01-01", DEV_END),
            ("VALIDATE (unseen — one look)", DEV_END, VAL_END),
            ("CONFIRM  (contaminated 2y)", VAL_END, "2100-01-01"),
        ):
            s = window_stats(trades, eq, a, b)
            if not s:
                print(f"\n{label}: no data")
                continue
            ew, jk = bench_window(prepped, jkse, a, b)
            print(f"\n{label}")
            print(f"  Portfolio return: {s['ret']*100:+.1f}%  "
                  f"(CAGR {s['cagr']*100:+.1f}%)   MaxDD: {s['dd']*100:.1f}%")
            print(f"  Trades: {s['trades']}   Win: {s['win']*100:.1f}%   "
                  f"Exp: {s['exp']*100:+.2f}%/trade")
            print(f"  Benchmarks: equal-weight B&H {ew*100:+.1f}% · IHSG {jk*100:+.1f}%")
        print("\n⚠️ Survivorship bias applies (current constituents, 10y back).")
        return

    parser.error("choose --dev or --final EXIT REGIME RANK")


if __name__ == "__main__":
    main()
