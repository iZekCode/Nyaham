"""Side experiment: partial scale-out vs pure trailing exit on cross_pure.

Entry = MA50 cross-above (fill next open). The remainder always trails on the
same rule (exit at the daily close that prints below MA50). Variants differ
only in whether a FRACTION is sold earlier at a fixed profit target:

  pure          all trailed on MA50 (the current live strategy)
  t15_third     sell 1/3 at +15%, trail 2/3
  t25_third     sell 1/3 at +25%, trail 2/3
  t20_half      sell 1/2 at +20%, trail 1/2
  t15+30_two    sell 1/3 at +15% and 1/3 at +30%, trail 1/3

A target is a resting limit → fills intraday when High ≥ target (no look-ahead);
the trailed remainder still exits at the MA50-break close. Fees per leg.

Because the MA50 exit timing is identical across variants, **MFE (peak during
the hold) is the same for every variant** — so we can read how much of that
peak each one captures. Reports per-trade stats + a "giveback" view, split
by the unseen (<2021-07) / seen eras.

Run:  python -m backtest.side_scaleout --limit 200
"""

from __future__ import annotations

import argparse
import logging
from math import isnan
from statistics import pstdev

import pandas as pd

from config import FEE_BUY, FEE_SELL, LOG_LEVEL
from universe import UNIVERSE

logger = logging.getLogger(__name__)

SEEN_START = "2021-07-01"

# variant name -> list of (fraction_of_position, target_gain)
VARIANTS: dict[str, list[tuple[float, float]]] = {
    "pure": [],
    "t15_third": [(1 / 3, 0.15)],
    "t25_third": [(1 / 3, 0.25)],
    "t20_half": [(1 / 2, 0.20)],
    "t15+30_two": [(1 / 3, 0.15), (1 / 3, 0.30)],
}


def _trades_for_ticker(df: pd.DataFrame):
    """Yield per-entry raw data: (entry_date, entry_price, exit_price, mfe,
    tranche_fills) where tranche_fills maps target_gain -> fill_price.

    We record, for each entry, the peak (MFE) and the exit; target fills are
    derived per-variant later from the recorded high path summary."""
    df = df.dropna(subset=["Open", "High", "Close"])
    n = len(df)
    if n < 60:
        return []
    opens = df["Open"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    ma50 = df["Close"].rolling(50, min_periods=50).mean().to_numpy(dtype=float)
    dates = [str(d.date()) for d in df.index]

    out = []
    i = 50
    while i < n - 1:
        if (not isnan(ma50[i]) and not isnan(ma50[i - 1])
                and closes[i - 1] <= ma50[i - 1] and closes[i] > ma50[i]):
            entry_i = i + 1
            entry = opens[entry_i]
            if isnan(entry) or entry <= 0:
                i += 1
                continue
            # Walk the hold: record the ordered peak-high path and the exit.
            j = entry_i
            peak = entry
            # For target fills we need the FIRST bar each target is reached, but
            # targets are relative to entry, so a running max of high suffices:
            # a target g is hit the first time running_max_high >= entry*(1+g).
            running_high = entry
            exit_price = closes[n - 1]
            exit_j = n - 1
            while j < n:
                running_high = max(running_high, highs[j])
                peak = max(peak, highs[j])
                if not isnan(ma50[j]) and closes[j] < ma50[j]:
                    exit_price = closes[j]
                    exit_j = j
                    break
                j += 1
            mfe = running_high / entry - 1.0
            out.append({
                "entry_date": dates[entry_i],
                "entry": entry,
                "exit": exit_price,
                "mfe": mfe,      # peak gain reached during the hold
            })
            i = exit_j + 1
        else:
            i += 1
    return out


def _blended_net(rec: dict, tranches: list[tuple[float, float]]) -> float:
    """Net return for one entry under a scale-out schedule.

    A tranche (frac, g) fills at entry*(1+g) IF the hold's MFE reached g
    (running-high based, no look-ahead — the peak really occurred). The
    remainder exits at the trailed MA50-break price. Fees applied per leg.
    """
    entry = rec["entry"]
    cost = entry * (1 + FEE_BUY)              # per 1 unit of position
    proceeds = 0.0
    remaining = 1.0
    for frac, g in tranches:
        if rec["mfe"] >= g:                    # target was reached during hold
            target_price = entry * (1 + g)
            proceeds += frac * target_price * (1 - FEE_SELL)
            remaining -= frac
    proceeds += remaining * rec["exit"] * (1 - FEE_SELL)
    return proceeds / cost - 1.0


def _stats(rets: list[float], mfes: list[float]) -> dict:
    if not rets:
        return {}
    wins = [r for r in rets if r > 0]
    gl = abs(sum(r for r in rets if r <= 0))
    pf = (sum(wins) / gl) if gl > 0 else float("inf")
    return {
        "n": len(rets),
        "win": len(wins) / len(rets),
        "exp": sum(rets) / len(rets),
        "pf": pf,
        "tot": sum(rets),
        "std": pstdev(rets) if len(rets) > 1 else 0.0,
        "mfe": sum(mfes) / len(mfes),
        "capture": (sum(rets) / len(rets)) / (sum(mfes) / len(mfes))
        if sum(mfes) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="scale-out vs pure trailing")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--period", default="max")
    args = parser.parse_args()
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    from backtest.__main__ import load_data

    tickers = args.tickers or UNIVERSE[: args.limit]
    print(f"Loading {len(tickers)} tickers ({args.period})…")
    data = load_data(tickers, args.period)

    recs = []
    for df in data.values():
        recs.extend(_trades_for_ticker(df))
    if not recs:
        print("No trades.")
        return

    def report(label: str, subset: list[dict]) -> None:
        print(f"\n{label}  ({len(subset)} entries)")
        if not subset:
            print("  (no entries in window)")
            return
        print(f"{'variant':<12} {'win%':>6} {'exp%':>7} {'PF':>6} {'std%':>7} "
              f"{'totPnL%':>9} {'capt.MFE':>9}")
        print("-" * 60)
        for name, tr in VARIANTS.items():
            rets = [_blended_net(r, tr) for r in subset]
            mfes = [r["mfe"] for r in subset]
            s = _stats(rets, mfes)
            pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
            print(f"{name:<12} {s['win']*100:>5.1f} {s['exp']*100:>6.2f} "
                  f"{pf:>6} {s['std']*100:>6.1f} {s['tot']*100:>8.0f} "
                  f"{s['capture']*100:>8.1f}")

    print("\n" + "=" * 72)
    print(f"SCALE-OUT vs PURE TRAILING — {len(data)} tickers, {args.period}, "
          f"{len(recs)} entries")
    print("Avg MFE (peak gain per trade) is identical across variants — "
          "capt.MFE = exp ÷ avg MFE")
    print("=" * 72)
    report("UNSEEN (<2021-07)", [r for r in recs if r["entry_date"] < SEEN_START])
    report("SEEN (2021-07→)", [r for r in recs if r["entry_date"] >= SEEN_START])
    report("FULL SPAN", recs)
    print("\n⚠️ Additive per-trade (1 unit/entry); survivorship bias; "
          "windows reused this session.")


if __name__ == "__main__":
    main()
