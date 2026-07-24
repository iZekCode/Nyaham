"""Message rendering tests (§4.7 / §5)."""

from __future__ import annotations

from bot import formatter as fmt
from screener.result import (
    DataQuality,
    MAStatus,
    ScreenResult,
    Signal,
    TrendTier,
)


def _result(**over) -> ScreenResult:
    res = ScreenResult(ticker="BBCA", scan_date="2026-07-24")
    res.price = 6250
    res.prev_close = 6275
    res.change_pct = -0.40
    res.ma = [
        MAStatus(5, 6405, False, -0.0242),
        MAStatus(10, 6320, False, -0.0111),
        MAStatus(20, 6155, True, 0.0154),
        MAStatus(50, 5991, True, 0.0431),
        MAStatus(100, 6225, True, 0.0039),
        MAStatus(200, 6918, False, -0.0966),
    ]
    res.ma_above_count = 3
    res.nearest_support = res.ma[4]
    res.nearest_resistance = res.ma[1]
    res.trends = [
        TrendTier("Short", (5, 10, 20), False),
        TrendTier("Medium", (20, 50), True),
        TrendTier("Long", (50, 100, 200), False),
    ]
    res.rvol = 0.53
    res.buy_pressure_pct = 71
    res.sell_pressure_pct = 29
    res.buy_at = 6225
    res.sell_at = 6325
    res.stop_loss = 6150
    res.signal = Signal.HOLD
    res.verdict = "🟡 BULLISH short-term only"
    res.score = 57.3
    res.reasons = ["0.4% above MA100; wait for a pullback."]
    for k, v in over.items():
        setattr(res, k, v)
    return res


def test_rupiah_formatting():
    assert fmt.rupiah(6250) == "6.250"
    assert fmt.rupiah(1234567) == "1.234.567"
    assert fmt.rupiah(None) == "—"
    assert fmt.rupiah(150) == "150"


def test_format_ma_contains_core_sections():
    text = fmt.format_ma(_result())
    assert "MA STACK: BBCA" in text
    assert "6.250" in text
    assert "Above <b>3/6</b>" in text
    assert "ENTRY" in text
    assert "6.225" in text  # buy
    assert "Bottom line" in text
    assert fmt.DISCLAIMER in text


def test_format_ma_no_data():
    res = _result(quality=DataQuality.NO_DATA)
    text = fmt.format_ma(res)
    assert "BBCA" in text
    assert "No price data" in text


def test_caption_within_limit():
    caption = fmt.format_ma_caption(_result())
    assert len(caption) <= 1024
    assert "BBCA" in caption


def test_format_top5_with_rows():
    rows = [
        {
            "ticker": "ANTM",
            "price": 1600,
            "score": 82.0,
            "buy_at": 1580,
            "sell_at": 1700,
            "stop_loss": 1500,
            "reason": "Near MA20 support with bullish stack.",
        },
        {
            "ticker": "TLKM",
            "price": 3200,
            "score": 74.0,
            "buy_at": 3180,
            "sell_at": 3400,
            "stop_loss": 3100,
            "reason": "Near MA50 support.",
        },
    ]
    text = fmt.format_top5(rows, "2026-07-24")
    assert "ANTM" in text and "TLKM" in text
    assert "82/100" in text
    assert "1.580" in text
    assert "2026-07-24" in text


def test_format_top5_empty():
    text = fmt.format_top5([], "2026-07-24")
    assert "No BUY candidates" in text


def test_bottom_line_varies_by_signal():
    buy = fmt.format_ma(_result(signal=Signal.BUY))
    sell = fmt.format_ma(_result(signal=Signal.SELL))
    avoid = fmt.format_ma(_result(signal=Signal.AVOID, ma_above_count=0))
    assert "entry zone" in buy
    assert "lost an MA" in sell
    assert "below every MA" in avoid
