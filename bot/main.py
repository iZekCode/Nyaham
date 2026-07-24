"""PTB entry point (§5.1): builds the Application, registers handlers, a global
error handler that alerts the admin, and the daily scan job.

Run with:  ``python -m bot.main``  (needs BOT_TOKEN in the environment / .env).
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import traceback
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import (
    ADMIN_CHAT_ID,
    BOT_TOKEN,
    LOG_LEVEL,
    SCAN_HOUR,
    SCAN_MINUTE,
    TIMEZONE,
)
from bot import handlers
from data import cache

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and alert the admin instead of crashing (§5.1)."""
    logger.error("Handler error", exc_info=context.error)
    if not ADMIN_CHAT_ID:
        return
    tb = "".join(
        traceback.format_exception(None, context.error, context.error.__traceback__)
    )[-1500:]
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"⚠️ <b>Bot error</b>\n<pre>{html.escape(tb)}</pre>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # noqa: BLE001 — never let the error handler throw
        logger.exception("Failed to notify admin of error")


async def _daily_scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled 16:30 WIB scan; skips weekends/holidays (§6)."""
    import asyncio

    from jobs.daily_scan import is_trading_day, run_scan

    if not is_trading_day():
        logger.info("Not a trading day — skipping scan.")
        return
    summary = await asyncio.to_thread(run_scan)
    if ADMIN_CHAT_ID:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=summary.as_text())


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CommandHandler("ma", handlers.ma))
    app.add_handler(CommandHandler("top5", handlers.top5))
    app.add_handler(CommandHandler("scan", handlers.scan))
    app.add_handler(CallbackQueryHandler(handlers.chart_callback, pattern=r"^chart:"))
    app.add_error_handler(_error_handler)

    # Daily scan (§6). JobQueue schedule; weekend/holiday guard is inside the job.
    if app.job_queue is not None:
        app.job_queue.run_daily(
            _daily_scan_job,
            time=dt.time(hour=SCAN_HOUR, minute=SCAN_MINUTE, tzinfo=ZoneInfo(TIMEZONE)),
            name="daily_scan",
        )
    return app


def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and add your token."
        )
    cache.init_db()
    app = build_application()
    logger.info("Bot starting (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
