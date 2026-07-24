"""Backtest engine mechanics + metrics (§7)."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import engine, metrics
from backtest.engine import Trade, backtest_ticker
from screener import rules
from screener.params import Params
from screener.result import DataQuality, MAStatus, ScreenResult, Signal


def _ohlc(opens, highs, lows, closes) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(opens), freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": [1000] * len(opens)},
        index=idx,
    )


# --------------------------------------------------------------------------- #
# Engine mechanics — scripted signals via monkeypatched evaluate (v2 exits).
# --------------------------------------------------------------------------- #
def _scripted(buy_at_bar, break_bar=None, sell_bar=None):
    """Fake evaluate: BUY at ``buy_at_bar``; MA50 'above' flips False from
    ``break_bar`` on; SELL signal at ``sell_bar``."""

    def fake(ticker, df, quality=DataQuality.OK, scan_date=None, params=None,
             ma_cache=None):
        i = len(df) - 1
        r = ScreenResult(ticker=ticker, scan_date=str(df.index[-1].date()))
        r.quality = DataQuality.OK
        above = break_bar is None or i < break_bar
        r.ma = [MAStatus(period=50, value=90.0, above=above, distance_pct=0.0)]
        if i == buy_at_bar:
            r.signal = Signal.BUY
        elif sell_bar is not None and i == sell_bar:
            r.signal = Signal.SELL
        else:
            r.signal = Signal.HOLD
        return r

    return fake


P = Params(min_bars=5)


def test_entry_next_open_and_ma_exit(monkeypatch):
    # BUY at bar 5 → enter at bar 6 open; MA50 breaks at bar 8 → exit at that
    # day's close (v2: condition-based, not an intraday price touch).
    monkeypatch.setattr(rules, "evaluate", _scripted(buy_at_bar=5, break_bar=8))
    n = 12
    opens = [100] * n
    highs = [105] * n
    lows = [95] * n
    closes = [100] * n
    closes[8] = 97
    trades = backtest_ticker("X", _ohlc(opens, highs, lows, closes), P)

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "ma_exit"
    assert t.entry_price == 100  # open of bar 6 (t+1 after signal at bar 5)
    assert t.exit_price == 97    # the breaking close, NOT an intraday level
    assert t.holding_days == 2   # entered bar 6, exited bar 8


def test_intraday_dip_does_not_exit(monkeypatch):
    # Lows pierce far below, but the close stays above MA50 → position rides
    # to end of data (the old engine would have stopped out intraday).
    monkeypatch.setattr(rules, "evaluate", _scripted(buy_at_bar=5))
    n = 12
    opens = [100] * n
    highs = [105] * n
    lows = [80] * n       # deep intraday dips every day
    closes = [100] * n
    trades = backtest_ticker("X", _ohlc(opens, highs, lows, closes), P)

    assert len(trades) == 1
    assert trades[0].exit_reason == "eod"


def test_sell_signal_exit_at_close(monkeypatch):
    monkeypatch.setattr(rules, "evaluate", _scripted(buy_at_bar=5, sell_bar=8))
    n = 12
    opens = [100] * n
    highs = [105] * n
    lows = [95] * n
    closes = [100] * n
    closes[8] = 103
    trades = backtest_ticker("X", _ohlc(opens, highs, lows, closes), P)

    assert len(trades) == 1
    assert trades[0].exit_reason == "ma_exit"
    assert trades[0].exit_price == 103


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #
def test_net_return_below_gross():
    gross, net = engine._net_return(100, 110)
    assert gross == pytest.approx(0.10)
    assert net < gross  # fees eat into it
    assert net == pytest.approx(0.0956, abs=1e-3)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _trade(ret, entry="2025-01-01", exit_="2025-01-05"):
    return Trade("X", entry, exit_, 100, 100 * (1 + ret), 3, "tp", ret, ret)


def test_metrics_basic():
    trades = [
        _trade(0.10, exit_="2025-01-02"),
        _trade(-0.05, exit_="2025-01-03"),
        _trade(0.20, exit_="2025-01-04"),
        _trade(-0.10, exit_="2025-01-05"),
    ]
    m = metrics.compute_metrics(trades)
    assert m.trades == 4
    assert m.win_rate == pytest.approx(0.5)
    assert m.profit_factor == pytest.approx(2.0)      # 0.30 / 0.15
    assert m.avg_return == pytest.approx(0.0375)
    assert m.max_drawdown == pytest.approx(-0.10)     # peak 0.25 → 0.15

def test_equity_curve_handles_same_date_exits():
    # Three trades exiting on the SAME date must all count (regression: a dict
    # keyed by date silently dropped all but one).
    trades = [
        _trade(0.10, exit_="2025-01-02"),
        _trade(0.20, exit_="2025-01-02"),
        _trade(-0.05, exit_="2025-01-02"),
    ]
    curve = metrics.equity_curve(trades)
    m = metrics.compute_metrics(trades)
    # Endpoint of the equity curve must equal total PnL.
    assert curve.iloc[-1] == pytest.approx(0.25)
    assert curve.iloc[-1] == pytest.approx(m.total_pnl)


def test_metrics_empty():
    m = metrics.compute_metrics([])
    assert m.trades == 0
    assert m.exit_reasons == {}


# --------------------------------------------------------------------------- #
# Integration with the REAL rules engine (no network).
# --------------------------------------------------------------------------- #
def test_ma_cache_matches_uncached():
    # The backtest's precomputed-MA path must equal the live (recompute) path.
    from screener import indicators as ind

    n = 260
    closes = [1000 + i * 0.5 for i in range(n)]
    df = _ohlc(closes, [c * 1.01 for c in closes], [c * 0.99 for c in closes], closes)
    cache = {p: s.iloc[: n] for p, s in ind.moving_averages(df).items()}

    a = rules.evaluate("X", df)
    b = rules.evaluate("X", df, ma_cache=cache)
    assert a.signal is b.signal
    assert a.ma_above_count == b.ma_above_count
    assert (a.buy_at, a.sell_at, a.stop_loss) == (b.buy_at, b.sell_at, b.stop_loss)


def test_downtrend_produces_no_trades():
    n = 300
    closes = [2000 - i for i in range(n)]
    df = _ohlc(closes, [c + 1 for c in closes], [c - 1 for c in closes], closes)
    assert backtest_ticker("DOWN", df) == []


def test_monotonic_uptrend_never_crosses_so_no_trades():
    # cross_pure entries are events: a series that is above MA50 from the
    # start never produces a fresh cross → no trades.
    n = 300
    closes = [1000 + i * 5 for i in range(n)]
    df = _ohlc(closes, [c * 1.01 for c in closes], [c * 0.99 for c in closes],
               closes)
    assert backtest_ticker("UP", df) == []


def test_dip_and_recover_produces_profitable_cross_trade():
    # Uptrend → decline below MA50 → strong recovery: the recovery close
    # crossing back above MA50 is the entry; the rally rides to end of data.
    n = 380
    closes = []
    for i in range(n):
        if i < 270:
            closes.append(1000.0 + i)
        elif i < 300:
            closes.append(1269.0 - 8 * (i - 269))
        else:
            closes.append(1029.0 + 12 * (i - 299))
    df = _ohlc(closes, [c * 1.01 for c in closes], [c * 0.99 for c in closes],
               closes)
    trades = backtest_ticker("DIPREC", df)
    assert len(trades) >= 1
    assert trades[-1].exit_reason == "eod"       # rally never breaks MA50 again
    assert sum(t.net_return for t in trades) > 0
