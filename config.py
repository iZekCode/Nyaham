"""Central configuration: thresholds, MA periods, paths, schedule.

All tunable parameters live here so Phase 4 (backtest tuning) has a single
place to write final values into. Anything read from the environment is
optional and falls back to the defaults below.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "cache.sqlite"))

# --------------------------------------------------------------------------- #
# Secrets / runtime (only needed for the bot, not the screener core)
# --------------------------------------------------------------------------- #
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --------------------------------------------------------------------------- #
# Indicators — the MA stack (source of truth, rule set §2)
# --------------------------------------------------------------------------- #
MA_PERIODS: tuple[int, ...] = (5, 10, 20, 50, 100, 200)

# Fixed color per MA, used identically in the chart and (conceptually) the text.
MA_COLORS: dict[int, str] = {
    5: "#2196F3",    # blue
    10: "#00BCD4",   # cyan
    20: "#4CAF50",   # green
    50: "#FF9800",   # orange
    100: "#9C27B0",  # purple
    200: "#F44336",  # red
}

# --------------------------------------------------------------------------- #
# Screener thresholds (rule set §2 — initial defaults, tuned in Phase 4)
# --------------------------------------------------------------------------- #
NEAR_MA_THRESHOLD = 0.02   # 2%  — "near an MA" (rule 2)
FAR_MA_THRESHOLD = 0.05    # 5%  — "far from MA" / overextended (rule 1)
SUPPORT_LOOKBACK = 5       # trading days held above MA50 for a break to be "fresh"
MIN_BARS = 250             # minimum history for a valid MA200

# v2 exit framework (backtest/FINDINGS.md): the exit is a CONDITION, not a
# price target — exit when a daily close prints below this MA. There is no
# take-profit; the nearest resistance is shown as information only.
EXIT_MA_PERIOD = 50

# Data-quality / freshness
STALE_BAR_MAX_DAYS = 7     # last bar older than this ⇒ suspended/illiquid
FLAT_STREAK_BARS = 5       # consecutive zero-volume/flat bars ⇒ suspension flag

# --------------------------------------------------------------------------- #
# Confidence scoring weights (§4.5 — must sum to 1.0)
# --------------------------------------------------------------------------- #
SCORE_WEIGHTS: dict[str, float] = {
    "ma_count": 0.40,     # rule 3 — MAs above, scaled 0..6
    "proximity": 0.25,    # rules 1–2 — closeness to support MA
    "volume_pressure": 0.20,  # buy-side pressure proxy
    "rvol": 0.15,         # relative volume participation
}

# --------------------------------------------------------------------------- #
# Data fetching (§4.2)
# --------------------------------------------------------------------------- #
FETCH_PERIOD = "2y"        # history window for MAs + backtest
FETCH_MAX_RETRIES = 3
FETCH_BACKOFF_BASE = 1.5   # seconds; exponential: base * 2**attempt
BATCH_REQUEST_DELAY = 0.6  # seconds between tickers during a batch scan

# --------------------------------------------------------------------------- #
# Charting (§4.6)
# --------------------------------------------------------------------------- #
CHART_THEME = os.getenv("CHART_THEME", "dark")  # "dark" | "light"
CHART_LOOKBACK_BARS = 120   # ~6 months of daily candles shown
CHART_WIDTH_PX = 1280
CHART_HEIGHT_PX = 720
CHART_DPI = 100

# --------------------------------------------------------------------------- #
# Scheduled scan (§6) — Asia/Jakarta
# --------------------------------------------------------------------------- #
TIMEZONE = "Asia/Jakarta"
MARKET_CLOSE_HOUR = 16      # IDX regular session closes 16:00 WIB
MARKET_CLOSE_MINUTE = 0
SCAN_HOUR = 16              # daily scan runs after close, with a data buffer
SCAN_MINUTE = 30

# OHLCV bar cache (§6) — reuse stored bars so /ma on a scanned ticker is instant.
OHLCV_CACHE_ENABLED = True

# IDX public holidays — manually updated yearly (YYYY-MM-DD). Scan skips these.
#
# ⚠️ IMPORTANT: this is a BEST-EFFORT list of fixed-date national holidays only.
# It is INCOMPLETE. Movable religious holidays (Idul Fitri, Idul Adha, Nyepi,
# Waisak, Ascension, Islamic New Year, Maulid) and government "cuti bersama"
# (collective-leave) days shift yearly and MUST be filled in from the official
# IDX trading-holiday calendar ("Kalender Libur Bursa") before relying on the
# scheduled scan:  https://www.idx.co.id  →  About IDX  →  Trading Holiday.
# A missing holiday only means one wasted scan (harmless); the bigger risk is
# NOT here since we never mark a real trading day as a holiday.
IDX_HOLIDAYS_2026: tuple[str, ...] = (
    "2026-01-01",  # New Year's Day
    "2026-05-01",  # Labour Day
    "2026-06-01",  # Pancasila Day
    "2026-08-17",  # Independence Day
    "2026-12-25",  # Christmas Day
    # TODO: add movable religious holidays + cuti bersama from the IDX calendar.
)

# Active holiday set consumed by market_calendar (extend per-year above).
IDX_HOLIDAYS: tuple[str, ...] = IDX_HOLIDAYS_2026

# --------------------------------------------------------------------------- #
# Backtest cost model (§7.1)
# --------------------------------------------------------------------------- #
FEE_BUY = 0.0015    # 0.15%
FEE_SELL = 0.0025   # 0.25% incl. sales tax
