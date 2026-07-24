"""IDX trading-calendar helpers (Asia/Jakarta).

Two questions the rest of the system asks:
  - ``is_trading_day(d)``          — should the daily scan run today?
  - ``last_completed_trading_day`` — what is the newest daily bar that should
                                     exist right now? (drives OHLCV cache freshness)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import IDX_HOLIDAYS, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_HOLIDAYS = set(IDX_HOLIDAYS)


def now_wib() -> datetime:
    return datetime.now(_TZ)


def is_trading_day(d: Optional[date] = None) -> bool:
    """Weekday and not an IDX public holiday."""
    d = d or now_wib().date()
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d.isoformat() not in _HOLIDAYS


def _session_closed(now: datetime) -> bool:
    """Has today's 16:00 WIB close already passed?"""
    close_minutes = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    return now.hour * 60 + now.minute >= close_minutes


def last_completed_trading_day(now: Optional[datetime] = None) -> date:
    """Date of the most recent trading session whose close has passed.

    Before today's 16:00 WIB close (or on a weekend/holiday), this is the
    previous trading day — so the OHLCV cache isn't considered stale just
    because today's bar doesn't exist yet.
    """
    now = now or now_wib()
    d = now.date()
    # Today only counts if it's a trading day AND its session has closed.
    if not (is_trading_day(d) and _session_closed(now)):
        d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
