"""Telegram command handlers (§5.2–5.4).

Blocking work (yfinance fetches, matplotlib rendering, full scans) runs in a
worker thread via ``asyncio.to_thread`` so the async event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_CHAT_ID
from bot import formatter as fmt
from data import cache
from data.fetcher import get_ohlcv_cached
from screener.chart import render_chart
from screener.result import DataQuality
from screener.screen import screen_dataframe
from universe import normalize

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024


# --------------------------------------------------------------------------- #
# Blocking helpers (run off the event loop)
# --------------------------------------------------------------------------- #
def _screen_with_df(ticker: str):
    """Fetch + screen, returning (result, df) so the chart can reuse the df."""
    df, quality = get_ohlcv_cached(ticker)
    import pandas as pd

    res = screen_dataframe(ticker, df if df is not None else pd.DataFrame(), quality)
    return res, df


def _render(res, df) -> bytes:
    return render_chart(res, df)


# --------------------------------------------------------------------------- #
# /start, /help
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>IHSG MA Screener</b>\n\n"
        "I analyze IDX stocks by moving-average alignment.\n\n"
        "• <b>/ma &lt;ticker&gt;</b> — full analysis + chart (e.g. <code>/ma BBCA</code>)\n"
        "• <b>/top5</b> — today's best BUY setups\n"
        "• <b>/help</b> — how it works\n\n"
        f"<i>{fmt.DISCLAIMER}</i>",
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>Commands</b>\n"
        "• <b>/ma &lt;ticker&gt;</b> — MA stack, trend, trade plan + chart\n"
        "• <b>/top5</b> — highest-confidence fresh breakouts from the last scan\n\n"
        "<b>The strategy (cross_pure)</b>\n"
        "🟢 <b>BUY</b> — a daily close crosses <i>above</i> MA50 (fresh breakout)\n"
        "🔴 <b>SELL</b> — a daily close prints <i>below</i> MA50 (structure broke)\n"
        "🚫 No profit target — winners ride until MA50 gives way\n"
        "⚪ Everything else is HOLD/WAIT context, not a signal\n\n"
        "The 6-MA stack (5/10/20/50/100/200), trend tiers, and volume are "
        "shown as context.\n\n"
        f"<i>{fmt.DISCLAIMER}</i>",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------- #
# /ma <ticker>
# --------------------------------------------------------------------------- #
async def ma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/ma &lt;ticker&gt;</code>  e.g. <code>/ma BBCA</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ticker = normalize(context.args[0])
    status = await update.message.reply_text(
        f"⏳ Fetching {ticker}…", parse_mode=ParseMode.HTML
    )

    try:
        res, df = await asyncio.to_thread(_screen_with_df, ticker)
    except Exception:  # noqa: BLE001
        logger.exception("/ma failed for %s", ticker)
        await status.edit_text("😵 Something went wrong fetching that ticker. Try again.")
        return

    if res.quality is DataQuality.NO_DATA:
        await status.edit_text(
            fmt.format_ma(res), parse_mode=ParseMode.HTML
        )
        return

    text = fmt.format_ma(res)
    try:
        png = await asyncio.to_thread(_render, res, df)
    except Exception:  # noqa: BLE001 — chart is a nice-to-have; text still ships
        logger.exception("Chart render failed for %s", ticker)
        png = None

    await status.delete()
    if png is None:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    # Send photo; if the full analysis fits a caption use it, else caption-lite
    # + a follow-up text message (§4.6 fallback).
    caption = text if len(text) <= CAPTION_LIMIT else fmt.format_ma_caption(res)
    await update.message.reply_photo(
        photo=png, caption=caption, parse_mode=ParseMode.HTML
    )
    if len(text) > CAPTION_LIMIT:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /top5
# --------------------------------------------------------------------------- #
async def top5(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scan_date = await asyncio.to_thread(cache.latest_scan_date)
    if not scan_date:
        await update.message.reply_text(
            "📭 No scan has run yet. An admin can trigger one with /scan, "
            "or wait for the daily scan.",
        )
        return

    rows = await asyncio.to_thread(cache.get_top_buys, 5, scan_date)
    text = fmt.format_top5(rows, scan_date)
    keyboard = (
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"📈 {r['ticker']}", callback_data=f"chart:{r['ticker']}")]
                for r in rows
            ]
        )
        if rows
        else None
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline '📈 Chart' button on a /top5 entry → render that ticker on demand."""
    query = update.callback_query
    await query.answer("Rendering chart…")
    ticker = normalize(query.data.split(":", 1)[1])
    try:
        res, df = await asyncio.to_thread(_screen_with_df, ticker)
        if res.quality is DataQuality.NO_DATA or df is None:
            await query.message.reply_text(f"No data for {ticker}.")
            return
        png = await asyncio.to_thread(_render, res, df)
    except Exception:  # noqa: BLE001
        logger.exception("Chart callback failed for %s", ticker)
        await query.message.reply_text(f"Couldn't render {ticker} right now.")
        return
    await query.message.reply_photo(
        photo=png, caption=fmt.format_ma_caption(res), parse_mode=ParseMode.HTML
    )


# --------------------------------------------------------------------------- #
# /scan (admin only)
# --------------------------------------------------------------------------- #
async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_CHAT_ID and update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    status = await update.message.reply_text("🔍 Starting full-universe scan…")
    from jobs.daily_scan import run_scan

    summary = await asyncio.to_thread(run_scan)
    await status.edit_text(summary.as_text())
