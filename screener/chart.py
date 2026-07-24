"""Candlestick + 6-MA overlay chart → PNG bytes (§4.6).

Headless (Agg backend), renders to an in-memory buffer so the bot can call
``send_photo`` without touching disk. Colors match ``config.MA_COLORS`` exactly
so the chart and the text analysis tell the same story.
"""

from __future__ import annotations

import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # headless — no display server on the VPS

import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from config import (
    CHART_DPI,
    CHART_HEIGHT_PX,
    CHART_LOOKBACK_BARS,
    CHART_THEME,
    CHART_WIDTH_PX,
    MA_COLORS,
    MA_PERIODS,
)
from screener.indicators import moving_averages
from screener.result import ScreenResult


def _style(theme: str):
    if theme == "light":
        base = "yahoo"
        facecolor = "white"
    else:
        base = "nightclouds"
        facecolor = "#101418"
    mc = mpf.make_marketcolors(up="#26A69A", down="#EF5350", inherit=True)
    return mpf.make_mpf_style(
        base_mpf_style=base,
        marketcolors=mc,
        facecolor=facecolor,
        gridcolor="#2A2E39" if theme == "dark" else "#E0E0E0",
        gridstyle=":",
    )


def render_chart(
    res: ScreenResult,
    df: pd.DataFrame,
    lookback: int = CHART_LOOKBACK_BARS,
    theme: Optional[str] = None,
) -> bytes:
    """Render a PNG for ``res`` using its OHLCV ``df``. Returns raw PNG bytes.

    MAs are computed on the FULL history, then the frame is trimmed to the last
    ``lookback`` bars so the lines are correct at the left edge of the view.
    """
    theme = theme or CHART_THEME
    if df is None or df.empty:
        raise ValueError("Cannot render a chart with no data")

    mas = moving_averages(df)
    plot_df = df.tail(lookback).copy()

    addplots = []
    for period in MA_PERIODS:
        series = mas[period].tail(lookback)
        if series.notna().sum() == 0:
            continue  # not enough history for this MA in the window
        addplots.append(
            mpf.make_addplot(series, color=MA_COLORS[period], width=1.1)
        )

    # Horizontal level lines (Buy / TP / SL) tie the chart to the text.
    hlines_vals, hlines_colors = [], []
    for val, color in (
        (res.buy_at, "#26A69A"),
        (res.sell_at, "#42A5F5"),
        (res.stop_loss, "#EF5350"),
    ):
        if val:
            hlines_vals.append(val)
            hlines_colors.append(color)
    hlines = (
        dict(hlines=hlines_vals, colors=hlines_colors, linestyle="--", linewidths=0.9)
        if hlines_vals
        else None
    )

    figsize = (CHART_WIDTH_PX / CHART_DPI, CHART_HEIGHT_PX / CHART_DPI)
    title = f"\n{res.ticker}  {int(res.price):,}  ({res.change_pct:+.2f}%)  {res.scan_date}"

    kwargs = dict(
        type="candle",
        style=_style(theme),
        addplot=addplots,
        volume=True,
        figsize=figsize,
        title=title,
        returnfig=True,
        tight_layout=True,
        update_width_config=dict(candle_linewidth=0.8, candle_width=0.6),
    )
    if hlines:
        kwargs["hlines"] = hlines

    fig, axes = mpf.plot(plot_df, **kwargs)

    # Legend mapping color → MA period.
    handles = [
        plt.Line2D([0], [0], color=MA_COLORS[p], lw=1.5, label=f"MA{p}")
        for p in MA_PERIODS
        if mas[p].tail(lookback).notna().sum() > 0
    ]
    if handles:
        axes[0].legend(
            handles=handles, loc="upper left", fontsize=7, ncol=3, framealpha=0.3
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
