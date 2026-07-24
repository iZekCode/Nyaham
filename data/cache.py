"""SQLite persistence for scan results (§4/§6).

Two responsibilities:
  - ``scan_results``: every ``ScreenResult`` from a full-universe scan, keyed by
    (ticker, scan_date). ``/top5`` reads the latest completed scan from here;
    ``/scan`` (admin) and the Phase-3 daily job write to it.

The OHLCV bar cache (§6) is added in Phase 3; this module owns the schema so
that addition is a table, not a rewrite.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from typing import Iterable, Iterator, Optional

from config import DB_PATH
from screener.result import ScreenResult, Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_results (
    ticker      TEXT NOT NULL,
    scan_date   TEXT NOT NULL,
    signal      TEXT NOT NULL,
    quality     TEXT NOT NULL DEFAULT 'OK',
    score       REAL NOT NULL,
    price       REAL,
    change_pct  REAL,
    buy_at      INTEGER,
    sell_at     INTEGER,
    stop_loss   INTEGER,
    verdict     TEXT,
    reason      TEXT,
    payload     TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, scan_date)
);
CREATE INDEX IF NOT EXISTS idx_scan_date_signal
    ON scan_results (scan_date, signal, score DESC);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables/indexes if they don't exist."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _payload(res: ScreenResult) -> str:
    """Serialize the full result to JSON (enums → their values)."""
    return json.dumps(asdict(res), default=lambda o: getattr(o, "value", str(o)))


def save_result(res: ScreenResult) -> None:
    """Upsert a single result."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO scan_results
                (ticker, scan_date, signal, quality, score, price, change_pct,
                 buy_at, sell_at, stop_loss, verdict, reason, payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker, scan_date) DO UPDATE SET
                signal=excluded.signal, quality=excluded.quality,
                score=excluded.score,
                price=excluded.price, change_pct=excluded.change_pct,
                buy_at=excluded.buy_at, sell_at=excluded.sell_at,
                stop_loss=excluded.stop_loss, verdict=excluded.verdict,
                reason=excluded.reason, payload=excluded.payload
            """,
            (
                res.ticker,
                res.scan_date,
                res.signal.value,
                res.quality.value,
                res.score,
                res.price,
                res.change_pct,
                res.buy_at,
                res.sell_at,
                res.stop_loss,
                res.verdict,
                res.reasons[0] if res.reasons else "",
                _payload(res),
            ),
        )


def save_results(results: Iterable[ScreenResult]) -> int:
    """Persist many results; returns the count written."""
    n = 0
    for res in results:
        if res.scan_date:  # never store a result with no date key
            save_result(res)
            n += 1
    return n


def latest_scan_date() -> Optional[str]:
    """Most recent scan_date present, or None if the table is empty."""
    with _connect() as conn:
        row = conn.execute("SELECT MAX(scan_date) AS d FROM scan_results").fetchone()
    return row["d"] if row and row["d"] else None


def get_top_buys(limit: int = 5, scan_date: Optional[str] = None) -> list[sqlite3.Row]:
    """Top BUY-signal rows for a scan date (defaults to the latest scan)."""
    date = scan_date or latest_scan_date()
    if not date:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scan_results
            WHERE scan_date = ? AND signal = ? AND quality = 'OK'
            ORDER BY score DESC
            LIMIT ?
            """,
            (date, Signal.BUY.value, limit),
        ).fetchall()
    return list(rows)


def scan_summary(scan_date: Optional[str] = None) -> dict[str, int]:
    """Count of rows per signal for a scan date (for the admin summary)."""
    date = scan_date or latest_scan_date()
    if not date:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT signal, COUNT(*) AS n FROM scan_results "
            "WHERE scan_date = ? GROUP BY signal",
            (date,),
        ).fetchall()
    return {r["signal"]: r["n"] for r in rows}
