"""Trading-calendar logic (§6)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import market_calendar as mc

WIB = ZoneInfo("Asia/Jakarta")


def test_weekend_is_not_trading_day():
    assert mc.is_trading_day(date(2026, 7, 25)) is False  # Saturday
    assert mc.is_trading_day(date(2026, 7, 26)) is False  # Sunday


def test_weekday_is_trading_day():
    assert mc.is_trading_day(date(2026, 7, 24)) is True   # Friday


def test_holiday_is_not_trading_day():
    assert mc.is_trading_day(date(2026, 8, 17)) is False  # Independence Day


def test_last_completed_before_close_is_prev_day():
    # Friday 10:00 WIB — today's session hasn't closed yet.
    now = datetime(2026, 7, 24, 10, 0, tzinfo=WIB)
    assert mc.last_completed_trading_day(now) == date(2026, 7, 23)  # Thursday


def test_last_completed_after_close_is_today():
    # Friday 16:30 WIB — today's session has closed.
    now = datetime(2026, 7, 24, 16, 30, tzinfo=WIB)
    assert mc.last_completed_trading_day(now) == date(2026, 7, 24)


def test_last_completed_on_weekend_is_friday():
    now = datetime(2026, 7, 26, 12, 0, tzinfo=WIB)  # Sunday
    assert mc.last_completed_trading_day(now) == date(2026, 7, 24)  # Friday


def test_last_completed_skips_holiday():
    # Tuesday Aug 18 morning → last completed is Fri Aug 14 (Mon Aug 17 holiday).
    now = datetime(2026, 8, 18, 9, 0, tzinfo=WIB)
    assert mc.last_completed_trading_day(now) == date(2026, 8, 14)
