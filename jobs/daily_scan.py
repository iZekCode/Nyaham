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
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from config import BATCH_REQUEST_DELAY, TIMEZONE
from data import cache
from market_calendar import is_trading_day  # re-exported for callers/tests
from screener.result import DataQuality
from screener.screen import screen_ticker
from universe import UNIVERSE

logger = logging.getLogger(__name__)

__all__ = ["ScanSummary", "is_trading_day", "run_scan"]


@dataclass
class ScanSummary:
    scan_date: str
    total: int = 0
    ok: int = 0
    failed: int = 0
    by_signal: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    regime_note: str = ""

    def as_text(self) -> str:
        sig = "  ".join(f"{k}:{v}" for k, v in sorted(self.by_signal.items()))
        line = (
            f"✅ Scan {self.scan_date}: {self.ok}/{self.total} OK "
            f"in {self.elapsed_s:.0f}s\n{sig or '(no signals)'}"
        )
        if self.regime_note:
            line += f"\n🛡 Regime: {self.regime_note}"
        if self.failures:
            shown = ", ".join(self.failures[:8])
            more = "" if len(self.failures) <= 8 else f" +{len(self.failures) - 8} more"
            line += f"\n⚠️ Failed: {shown}{more}"
        return line


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

    # Capture the market regime once so conservative /top5 is served from the
    # same scan (conservative mode gates BUYs when the index is risk-off).
    try:
        from screener.regime import get_regime_state

        regime = get_regime_state()
        if regime is not None:
            summary.regime_note = regime.summary
            cache.save_scan_meta(
                summary.scan_date, regime.ok, regime.symbol,
                regime.ma_period, regime.summary,
            )
    except Exception as exc:  # noqa: BLE001 — regime is optional metadata
        logger.warning("Regime capture failed: %s", exc)

    summary.elapsed_s = time.monotonic() - start
    logger.info("Scan complete: %s", summary.as_text().replace("\n", " | "))
    return summary
