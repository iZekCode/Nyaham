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

CREATE TABLE IF NOT EXISTS ohlcv (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS scan_meta (
    scan_date     TEXT PRIMARY KEY,
    regime_ok     INTEGER,        -- 1 risk-on, 0 risk-off, NULL unknown
    regime_index  TEXT,
    regime_ma     INTEGER,
    regime_note   TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
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


# --------------------------------------------------------------------------- #
# Scan metadata — the market regime state captured at scan time, so
# conservative /top5 can be served from the same scan (conservative mode).
# --------------------------------------------------------------------------- #
def save_scan_meta(
    scan_date: str,
    regime_ok: Optional[bool],
    regime_index: Optional[str] = None,
    regime_ma: Optional[int] = None,
    regime_note: Optional[str] = None,
) -> None:
    ok = None if regime_ok is None else (1 if regime_ok else 0)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO scan_meta
                (scan_date, regime_ok, regime_index, regime_ma, regime_note)
            VALUES (?,?,?,?,?)
            ON CONFLICT(scan_date) DO UPDATE SET
                regime_ok=excluded.regime_ok, regime_index=excluded.regime_index,
                regime_ma=excluded.regime_ma, regime_note=excluded.regime_note
            """,
            (scan_date, ok, regime_index, regime_ma, regime_note),
        )


def get_scan_meta(scan_date: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Regime metadata for a scan date (defaults to the latest scan)."""
    date = scan_date or latest_scan_date()
    if not date:
        return None
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM scan_meta WHERE scan_date = ?", (date,)
        ).fetchone()


# --------------------------------------------------------------------------- #
# OHLCV bar cache (§6)
# --------------------------------------------------------------------------- #
_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def save_ohlcv(ticker: str, df) -> None:
    """Upsert a ticker's daily bars. Index must be datetime-like."""
    import pandas as pd  # local import keeps cache import-light

    if df is None or df.empty:
        return
    ticker = ticker.upper()
    rows = []
    for idx, row in df.iterrows():
        d = idx.date().isoformat() if isinstance(idx, pd.Timestamp) else str(idx)[:10]
        rows.append(
            (
                ticker,
                d,
                _num(row.get("Open")),
                _num(row.get("High")),
                _num(row.get("Low")),
                _num(row.get("Close")),
                _num(row.get("Volume")),
            )
        )
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv "
            "(ticker, date, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def load_ohlcv(ticker: str):
    """Return a cached OHLCV DataFrame (DatetimeIndex) or None if absent."""
    import pandas as pd

    with _connect() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM ohlcv "
            "WHERE ticker = ? ORDER BY date ASC",
            (ticker.upper(),),
        ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(
        [dict(r) for r in rows]
    ).rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df.index = pd.to_datetime(df.pop("date"))
    return df[_OHLCV_COLS]


def ohlcv_last_date(ticker: str):
    """Most recent cached bar date (datetime.date) for a ticker, or None."""
    from datetime import date as _date

    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM ohlcv WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
    if not row or not row["d"]:
        return None
    return _date.fromisoformat(row["d"])


def _num(v):
    return None if v is None else float(v)


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
