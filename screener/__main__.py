"""CLI entry point (§4.7):  ``python -m screener ARCI``  → terminal printout.

Fetches live data for the ticker(s), runs the full screener, and prints a
plain-text summary you can eyeball against TradingView.
"""

from __future__ import annotations

import argparse
import logging

from config import LOG_LEVEL
from screener.result import DataQuality, ScreenResult
from screener.screen import screen_ticker


def _fmt_price(v) -> str:
    return "—" if v is None else f"{int(v):,}".replace(",", ".")


def render(res: ScreenResult) -> str:
    lines: list[str] = []
    lines.append(f"===== {res.ticker}  ({res.scan_date}) =====")
    if res.quality is not DataQuality.OK:
        lines.append(f"[data quality: {res.quality.value}]")
    if res.quality is DataQuality.NO_DATA:
        return "\n".join(lines)

    lines.append(
        f"Price: {_fmt_price(res.price)}  ({res.change_pct:+.2f}%)   "
        f"MAs above: {res.ma_summary}"
    )
    lines.append("")
    for m in res.ma:
        mark = "✅" if m.above else "❌"
        lines.append(
            f"  {mark} MA{m.period:<3}  {_fmt_price(m.value):>10}   "
            f"{m.distance_pct*100:+6.2f}%"
        )
    lines.append("")
    trend = "  ".join(
        f"{t.label}:{'🟢' if t.bullish else '⚪'}" for t in res.trends
    )
    lines.append(f"Trend   {trend}")
    lines.append(f"Verdict {res.verdict}   (signal={res.signal.value}, score={res.score})")
    lines.append("")
    lines.append(
        f"Buy @ {_fmt_price(res.buy_at)}   "
        f"TP @ {_fmt_price(res.sell_at)}   "
        f"SL @ {_fmt_price(res.stop_loss)}"
    )
    lines.append(
        f"Volume  buy {res.buy_pressure_pct:.0f}% / sell {res.sell_pressure_pct:.0f}%   "
        f"RVOL {res.rvol:.2f}x"
    )
    if res.reasons:
        lines.append("")
        lines.append("Why:")
        for r in res.reasons:
            lines.append(f"  - {r}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="IHSG MA screener — CLI")
    parser.add_argument("tickers", nargs="+", help="IDX board codes, e.g. BBCA ARCI")
    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    for i, t in enumerate(args.tickers):
        res = screen_ticker(t)
        print(render(res))
        if i < len(args.tickers) - 1:
            print()


if __name__ == "__main__":
    main()
