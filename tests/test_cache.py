"""SQLite cache: scan-result queries + OHLCV round-trip (§6)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data import cache
from screener.result import DataQuality, ScreenResult, Signal


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point the cache at a throwaway SQLite file for each test."""
    monkeypatch.setattr(cache, "DB_PATH", tmp_path / "test.sqlite")
    cache.init_db()
    yield


def _res(ticker, signal, score, quality=DataQuality.OK, scan_date="2026-07-24"):
    r = ScreenResult(ticker=ticker, scan_date=scan_date, quality=quality)
    r.signal = signal
    r.score = score
    r.price = 1000
    r.buy_at, r.sell_at, r.stop_loss = 990, 1100, 950
    r.reasons = ["reason"]
    return r


def test_top_buys_orders_by_score_and_filters_signal():
    cache.save_results(
        [
            _res("AAA", Signal.BUY, 80),
            _res("BBB", Signal.BUY, 90),
            _res("CCC", Signal.HOLD, 95),   # not a BUY → excluded
            _res("DDD", Signal.SELL, 99),   # not a BUY → excluded
        ]
    )
    rows = cache.get_top_buys(5)
    assert [r["ticker"] for r in rows] == ["BBB", "AAA"]


def test_top_buys_excludes_non_ok_quality():
    cache.save_results(
        [
            _res("AAA", Signal.BUY, 80, quality=DataQuality.OK),
            _res("SUSP", Signal.BUY, 99, quality=DataQuality.SUSPENDED),
        ]
    )
    rows = cache.get_top_buys(5)
    assert [r["ticker"] for r in rows] == ["AAA"]


def test_latest_scan_date():
    cache.save_results([_res("AAA", Signal.BUY, 80, scan_date="2026-07-23")])
    cache.save_results([_res("BBB", Signal.BUY, 80, scan_date="2026-07-24")])
    assert cache.latest_scan_date() == "2026-07-24"


def test_upsert_replaces_same_key():
    cache.save_result(_res("AAA", Signal.HOLD, 10))
    cache.save_result(_res("AAA", Signal.BUY, 88))  # same (ticker, date)
    rows = cache.get_top_buys(5)
    assert len(rows) == 1
    assert rows[0]["score"] == 88


def test_ohlcv_round_trip():
    idx = pd.date_range("2026-07-01", periods=5, freq="B")
    df = pd.DataFrame(
        {
            "Open": [100, 101, 102, 103, 104],
            "High": [105, 106, 107, 108, 109],
            "Low": [95, 96, 97, 98, 99],
            "Close": [102, 103, 104, 105, 106],
            "Volume": [1000, 1100, 1200, 1300, 1400],
        },
        index=idx,
    )
    cache.save_ohlcv("AAA", df)

    loaded = cache.load_ohlcv("AAA")
    assert list(loaded.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(loaded) == 5
    assert float(loaded["Close"].iloc[-1]) == 106.0
    assert cache.ohlcv_last_date("AAA") == idx[-1].date()


def test_ohlcv_missing_ticker_is_none():
    assert cache.load_ohlcv("ZZZ") is None
    assert cache.ohlcv_last_date("ZZZ") is None


def test_ohlcv_upsert_no_duplicates():
    idx = pd.date_range("2026-07-01", periods=3, freq="B")
    df = pd.DataFrame(
        {c: [1, 2, 3] for c in ["Open", "High", "Low", "Close", "Volume"]},
        index=idx,
    )
    cache.save_ohlcv("AAA", df)
    cache.save_ohlcv("AAA", df)  # same dates again
    assert len(cache.load_ohlcv("AAA")) == 3


# --------------------------------------------------------------------------- #
# Scan metadata (regime state — conservative mode)
# --------------------------------------------------------------------------- #
def test_scan_meta_roundtrip():
    cache.save_scan_meta("2026-07-24", True, "^JKSE", 50, "^JKSE risk-on")
    row = cache.get_scan_meta("2026-07-24")
    assert row is not None
    assert row["regime_ok"] == 1
    assert row["regime_index"] == "^JKSE"
    assert row["regime_ma"] == 50


def test_scan_meta_risk_off_is_zero():
    cache.save_scan_meta("2026-07-24", False)
    assert cache.get_scan_meta("2026-07-24")["regime_ok"] == 0


def test_scan_meta_missing_is_none():
    assert cache.get_scan_meta("1999-01-01") is None


def test_scan_meta_upsert():
    cache.save_scan_meta("2026-07-24", False)
    cache.save_scan_meta("2026-07-24", True, "^JKSE", 50)
    assert cache.get_scan_meta("2026-07-24")["regime_ok"] == 1
