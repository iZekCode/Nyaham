"""MA math + tick-size rounding vs known values (§4.7)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener import indicators as ind


def _frame(closes, highs=None, lows=None, vols=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs if highs is not None else closes,
            "Low": lows if lows is not None else closes,
            "Close": closes,
            "Volume": vols if vols is not None else [1000] * n,
        },
        index=idx,
    )


def test_moving_average_known_value():
    closes = list(range(1, 21))  # 1..20
    df = _frame(closes)
    mas = ind.latest_ma_values(df, periods=(5,))
    # mean of 16..20 = 18
    assert mas[5] == pytest.approx(18.0)


def test_ma_none_when_insufficient_history():
    df = _frame(list(range(1, 6)))  # only 5 bars
    mas = ind.latest_ma_values(df, periods=(10,))
    assert mas[10] is None


def test_distance_pct_sign():
    assert ind.distance_pct(110, 100) == pytest.approx(0.10)
    assert ind.distance_pct(90, 100) == pytest.approx(-0.10)


@pytest.mark.parametrize(
    "price,expected",
    [
        (150, 1),     # < 200
        (200, 2),     # band boundary → next tier
        (499, 2),
        (500, 5),
        (1999, 5),
        (2000, 10),
        (4999, 10),
        (5000, 25),
        (12345, 25),
    ],
)
def test_tick_size_bands(price, expected):
    assert ind.tick_size(price) == expected


def test_round_to_tick_directions():
    # price 5030, tick 25 → down 5025, up 5050, nearest 5025
    assert ind.round_to_tick(5030, "down") == 5025
    assert ind.round_to_tick(5030, "up") == 5050
    assert ind.round_to_tick(5030, "nearest") == 5025


def test_buy_pressure_close_at_high_is_100():
    df = _frame([100], highs=[100], lows=[90])
    assert ind.buy_pressure(df) == 100.0


def test_buy_pressure_flat_bar_is_neutral():
    df = _frame([100], highs=[100], lows=[100])
    assert ind.buy_pressure(df) == 50.0


def test_relative_volume():
    vols = [100] * 20 + [200]  # avg of prior 20 = 100, today 200 → 2x
    df = _frame(list(range(1, 22)), vols=vols)
    assert ind.relative_volume(df, window=20) == pytest.approx(2.0)
