"""Portfolio-level cross_pure simulation — real account numbers.

Signals are exactly cross_pure (close crosses above MA50 → buy next open;
daily close below MA50 → sell at that close; no TP). Portfolio model as in v3:

  - max 10 concurrent positions
  - each new position is allocated 10% of CURRENT equity (compounding),
    capped by available cash
  - fees 0.15% buy / 0.25% sell
  - no ranking: same-day candidates fill free slots in universe order
  - re-entry needs a genuine re-cross (price must close below MA50 first),
    so no cooldown is required

Outputs account-level metrics (total return, CAGR, max drawdown, exposure)
for the full span, the unseen era (< 2021-07-01), the seen era, and per year
vs IHSG.

⚠️ Survivorship bias (current constituents over 25 years) inflates results —
read alongside the additive study in side_cross_history.py.

Run:  python -m backtest.side_cross_portfolio --limit 200
"""

from __future__ import annotations

import argparse
import logging
from math import isnan

import pandas as pd

from backtest.engine import _net_return
from config import FEE_BUY, FEE_SELL, LOG_LEVEL
from universe import UNIVERSE

logger = logging.getLogger(__name__)

MAX_POS = 10  # overridden by --slots
SEEN_START = "2021-07-01"


def prep(df: pd.DataFrame):
    df = df.dropna(subset=["Open", "Close"])
    n = len(df)
    if n < 60:
        return None
    closes = df["Close"].to_numpy(dtype=float)
    opens = df["Open"].to_numpy(dtype=float)
    ma50 = df["Close"].rolling(50, min_periods=50).mean().to_numpy(dtype=float)
    cross = [False] * n
    below = [False] * n
    for i in range(1, n):
        if isnan(ma50[i]) or isnan(ma50[i - 1]):
            continue
        below[i] = closes[i] < ma50[i]
        cross[i] = closes[i] > ma50[i] and closes[i - 1] <= ma50[i - 1]
    dates = list(df.index)
    return {
        "n": n, "dates": dates, "idx": {d: i for i, d in enumerate(dates)},
        "open": opens, "close": closes, "cross": cross, "below": below,
    }


def simulate(prepped: dict) -> tuple[pd.Series, list[dict], pd.Series]:
    calendar = sorted({d for p in prepped.values() for d in p["dates"]})
    positions: dict[str, dict] = {}
    last_close: dict[str, float] = {}
    cash = 1.0
    trades: list[dict] = []
    eq_vals, exp_vals = [], []

    for d in calendar:
        # exits
        for t in list(positions):
            p = prepped[t]
            i = p["idx"].get(d)
            if i is None:
                continue
            if p["below"][i] or i == p["n"] - 1:
                pos = positions.pop(t)
                price = p["close"][i]
                cash += pos["shares"] * price * (1 - FEE_SELL)
                _, net = _net_return(pos["entry_price"], price)
                trades.append({"entry_date": pos["entry_date"], "net": net})

        # marks
        for t, p in prepped.items():
            i = p["idx"].get(d)
            if i is not None:
                last_close[t] = p["close"][i]
        invested = sum(pos["shares"] * last_close[t]
                       for t, pos in positions.items())
        mark = cash + invested

        # entries (decision at close d, fill next open)
        slots = MAX_POS - len(positions)
        if slots > 0:
            for t, p in prepped.items():
                if slots == 0:
                    break
                if t in positions:
                    continue
                i = p["idx"].get(d)
                if i is None or not p["cross"][i] or i + 1 >= p["n"]:
                    continue
                price = p["open"][i + 1]
                if isnan(price) or price <= 0:
                    continue
                slot_size = mark / MAX_POS
                alloc = min(slot_size, cash)
                if alloc < slot_size * 0.5:  # can't fund half a slot → stop
                    break
                shares = alloc / (price * (1 + FEE_BUY))
                cash -= shares * price * (1 + FEE_BUY)
                positions[t] = {
                    "shares": shares, "entry_price": price,
                    "entry_date": str(p["dates"][i + 1].date()),
                }
                slots -= 1

        invested = sum(pos["shares"] * last_close[t]
                       for t, pos in positions.items())
        eq_vals.append(cash + invested)
        exp_vals.append(invested / (cash + invested))

    idx = pd.DatetimeIndex(calendar)
    return pd.Series(eq_vals, index=idx), trades, pd.Series(exp_vals, index=idx)


def window_stats(eq: pd.Series, lo: str | None, hi: str | None) -> dict:
    w = eq
    if lo:
        w = w[w.index >= lo]
    if hi:
        w = w[w.index < hi]
    if len(w) < 2:
        return {}
    ret = float(w.iloc[-1] / w.iloc[0] - 1.0)
    years = max((w.index[-1] - w.index[0]).days / 365.25, 1e-9)
    cagr = (1.0 + ret) ** (1.0 / years) - 1.0
    dd = float((w / w.cummax() - 1.0).min())
    return {"ret": ret, "cagr": cagr, "dd": dd, "years": years}


def main() -> None:
    parser = argparse.ArgumentParser(description="cross_pure portfolio sim")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--slots", type=int, default=10,
                        help="max concurrent positions (each ~1/slots of equity)")
    args = parser.parse_args()

    global MAX_POS
    MAX_POS = args.slots
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data, load_index

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers (max history)…")
    data = load_data(tickers, "max")
    prepped = {}
    for t, df in data.items():
        p = prep(df)
        if p is not None:
            prepped[t] = p
    if not prepped:
        print("No data.")
        return

    eq, trades, exposure = simulate(prepped)
    idxdf = load_index("^JKSE", "max")
    jkse = idxdf["Close"].dropna() if idxdf is not None else None

    wins = sum(1 for t in trades if t["net"] > 0)
    print("\n" + "=" * 78)
    print(f"PORTFOLIO cross_pure — {len(prepped)} tickers · {MAX_POS} slots · "
          f"10% equity/position · fees {FEE_BUY + FEE_SELL:.2%} r/t")
    print(f"Span {eq.index[0].date()} → {eq.index[-1].date()} · "
          f"{len(trades)} trades · win {wins / max(len(trades), 1) * 100:.1f}% · "
          f"avg exposure {exposure.mean() * 100:.0f}%")
    print("=" * 78)
    for label, lo, hi in (("FULL SPAN", None, None),
                          ("UNSEEN (<2021-07)", None, SEEN_START),
                          ("SEEN (2021-07→)", SEEN_START, None)):
        s = window_stats(eq, lo, hi)
        if not s:
            continue
        line = (f"{label:<19} return {s['ret'] * 100:+10.1f}%   "
                f"CAGR {s['cagr'] * 100:+6.1f}%   maxDD {s['dd'] * 100:6.1f}%")
        if jkse is not None:
            j = jkse
            if lo:
                j = j[j.index >= lo]
            if hi:
                j = j[j.index < hi]
            if len(j) >= 2:
                jret = float(j.iloc[-1] / j.iloc[0] - 1.0)
                jcagr = (1.0 + jret) ** (1.0 / s["years"]) - 1.0
                line += f"   | IHSG CAGR {jcagr * 100:+5.1f}%"
        print(line)

    print("-" * 78)
    print(f"{'year':<6} {'portfolio':>10} {'IHSG':>8}")
    yearly = eq.resample("YE").last()
    prev = eq.iloc[0]
    for ts, v in yearly.items():
        y = ts.year
        ret = v / prev - 1.0
        prev = v
        jr = ""
        if jkse is not None:
            jy = jkse[jkse.index.year == y]
            if len(jy) >= 2:
                jr = f"{(jy.iloc[-1] / jy.iloc[0] - 1) * 100:+7.1f}%"
        print(f"{y:<6} {ret * 100:>+9.1f}% {jr:>8}")
    print("-" * 78)
    print("⚠️ Survivorship bias (current constituents, 25y back) inflates this.")


if __name__ == "__main__":
    main()
