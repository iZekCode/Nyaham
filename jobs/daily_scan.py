"""Full-universe scan routine.

One synchronous ``run_scan`` function screens every universe ticker, persists
each ``ScreenResult`` to SQLite, and returns a summary. It is called two ways:
  - ``/scan`` — admin manual trigger (Phase 2)
  - the scheduled 16:30 WIB job (Phase 3)

It is deliberately blocking; async callers wrap it in ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from config import BATCH_REQUEST_DELAY, IDX_HOLIDAYS_2026, TIMEZONE
from data import cache
from screener.result import DataQuality, Signal
from screener.screen import screen_ticker
from universe import UNIVERSE

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    scan_date: str
    total: int = 0
    ok: int = 0
    failed: int = 0
    by_signal: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_text(self) -> str:
        sig = "  ".join(f"{k}:{v}" for k, v in sorted(self.by_signal.items()))
        line = (
            f"✅ Scan {self.scan_date}: {self.ok}/{self.total} OK "
            f"in {self.elapsed_s:.0f}s\n{sig or '(no signals)'}"
        )
        if self.failures:
            shown = ", ".join(self.failures[:8])
            more = "" if len(self.failures) <= 8 else f" +{len(self.failures) - 8} more"
            line += f"\n⚠️ Failed: {shown}{more}"
        return line


def is_trading_day(d: Optional[date] = None) -> bool:
    """Weekday and not an IDX public holiday."""
    d = d or datetime.now(ZoneInfo(TIMEZONE)).date()
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d.isoformat() not in set(IDX_HOLIDAYS_2026)


def run_scan(
    tickers: Optional[list[str]] = None,
    delay: float = BATCH_REQUEST_DELAY,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ScanSummary:
    """Screen every ticker, persist results, return a summary.

    ``progress(done, total)`` is called after each ticker if supplied.
    """
    tickers = tickers or UNIVERSE
    start = time.monotonic()
    results = []
    summary = ScanSummary(scan_date="", total=len(tickers))

    for i, ticker in enumerate(tickers, 1):
        try:
            res = screen_ticker(ticker)
            if res.quality is DataQuality.NO_DATA:
                summary.failed += 1
                summary.failures.append(ticker)
            else:
                summary.ok += 1
                if res.scan_date and not summary.scan_date:
                    summary.scan_date = res.scan_date
                results.append(res)
                summary.by_signal[res.signal.value] = (
                    summary.by_signal.get(res.signal.value, 0) + 1
                )
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not abort
            logger.exception("Scan error for %s: %s", ticker, exc)
            summary.failed += 1
            summary.failures.append(ticker)

        if progress:
            progress(i, len(tickers))
        if delay and i < len(tickers):
            time.sleep(delay)

    if not summary.scan_date:
        summary.scan_date = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()

    cache.init_db()
    cache.save_results(results)
    summary.elapsed_s = time.monotonic() - start
    logger.info("Scan complete: %s", summary.as_text().replace("\n", " | "))
    return summary
