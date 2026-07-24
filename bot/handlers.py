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
from screener.regime import get_regime_state
from screener.result import DataQuality
from screener.screen import screen_dataframe
from universe import normalize

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
_CONSERVATIVE_WORDS = {"c", "cons", "conservative", "safe"}


def _is_conservative(args: list[str]) -> bool:
    """True if any argument selects conservative mode."""
    return any(a.lower() in _CONSERVATIVE_WORDS for a in args)


# --------------------------------------------------------------------------- #
# Blocking helpers (run off the event loop)
# --------------------------------------------------------------------------- #
def _screen_with_df(ticker: str, regime_ok=None):
    """Fetch + screen, returning (result, df) so the chart can reuse the df."""
    df, quality = get_ohlcv_cached(ticker)
    import pandas as pd

    res = screen_dataframe(
        ticker, df if df is not None else pd.DataFrame(), quality,
        regime_ok=regime_ok,
    )
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
        "• <b>/ma &lt;ticker&gt; [conservative]</b> — MA stack, trend, plan + chart\n"
        "• <b>/top5 [conservative]</b> — best fresh breakouts from the last scan\n\n"
        "<b>The strategy (cross_pure)</b>\n"
        "🟢 <b>BUY</b> — a daily close crosses <i>above</i> MA50 (fresh breakout)\n"
        "🔴 <b>SELL</b> — a daily close prints <i>below</i> MA50 (structure broke)\n"
        "🚫 No profit target — winners ride until MA50 gives way\n"
        "⚪ Everything else is HOLD/WAIT context, not a signal\n\n"
        "<b>Modes</b>\n"
        "• <b>normal</b> (default) — every fresh breakout\n"
        "• <b>conservative</b> — only when the market (IHSG) is risk-on, i.e. "
        "above its own MA50. In backtests this kept most of the return while "
        "cutting drawdown sharply. Add the word <code>conservative</code> "
        "(or <code>c</code>) — e.g. <code>/ma BBCA c</code>, <code>/top5 c</code>.\n\n"
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

    conservative = _is_conservative(context.args)
    non_mode = [a for a in context.args if a.lower() not in _CONSERVATIVE_WORDS]
    if not non_mode:
        await update.message.reply_text(
            "Usage: <code>/ma &lt;ticker&gt; [conservative]</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    ticker = normalize(non_mode[0])
    status = await update.message.reply_text(
        f"⏳ Fetching {ticker}{' (conservative)' if conservative else ''}…",
        parse_mode=ParseMode.HTML,
    )

    # Conservative mode: fetch the market regime and gate BUYs accordingly.
    banner = ""
    regime_ok = None
    if conservative:
        regime = await asyncio.to_thread(get_regime_state)
        if regime is not None:
            regime_ok = regime.ok
            icon = "🟢" if regime.ok else "🔴"
            banner = f"🛡 <b>Conservative</b> · {icon} {regime.summary}\n\n"
        else:
            banner = "🛡 <b>Conservative</b> · ⚠️ regime unavailable — showing normal\n\n"

    try:
        res, df = await asyncio.to_thread(_screen_with_df, ticker, regime_ok)
    except Exception:  # noqa: BLE001
        logger.exception("/ma failed for %s", ticker)
        await status.edit_text("😵 Something went wrong fetching that ticker. Try again.")
        return

    if res.quality is DataQuality.NO_DATA:
        await status.edit_text(
            fmt.format_ma(res), parse_mode=ParseMode.HTML
        )
        return

    text = banner + fmt.format_ma(res)
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

    conservative = _is_conservative(context.args)
    banner = ""
    if conservative:
        meta = await asyncio.to_thread(cache.get_scan_meta, scan_date)
        note = meta["regime_note"] if meta and meta["regime_note"] else "regime unknown"
        risk_off = meta is not None and meta["regime_ok"] == 0
        icon = "🔴" if risk_off else "🟢"
        banner = f"🛡 <b>Conservative</b> · {icon} {note}\n\n"
        if risk_off:
            # Conservative mode sits in cash when the market is risk-off.
            await update.message.reply_text(
                banner
                + "💤 Market is <b>risk-off</b> — conservative mode recommends "
                "<b>holding cash</b>. No new breakouts until the index turns "
                "risk-on.\n\n<i>" + fmt.DISCLAIMER + "</i>",
                parse_mode=ParseMode.HTML,
            )
            return

    rows = await asyncio.to_thread(cache.get_top_buys, 5, scan_date)
    text = banner + fmt.format_top5(rows, scan_date)
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
