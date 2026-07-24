# IHSG MA Screener — Telegram Bot

Screens IDX (Indonesia Stock Exchange) stocks by moving-average alignment and
serves the results over Telegram. See [plan.md](plan.md) for the full design.

- **`/ma <ticker>`** — detailed single-stock MA analysis + a candlestick chart
  with all six MA lines and the computed Buy / TP / Stop-loss levels.
- **`/top5`** — the five highest-confidence BUY candidates from the latest scan.

## Status

| Phase | Scope | State |
|---|---|---|
| **1** | Screener core (universe, fetcher, indicators, rules, scoring, chart) + tests | ✅ Done |
| 2 | Telegram bot (PTB) + message formatter | ⏳ Next |
| 3 | Scheduled daily scan + SQLite cache | — |
| 4 | Backtest + parameter tuning | — |
| 5 | Deployment + ops | — |

## Setup

Requires Python 3.11+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For the bot (Phase 2+), copy `.env.example` to `.env` and fill in `BOT_TOKEN`
and `ADMIN_CHAT_ID`. The screener core (below) needs no secrets.

## Usage (Phase 1)

Screen one or more tickers from the terminal:

```bash
python -m screener BBCA
python -m screener BBCA TLKM ANTM
```

List the scan universe:

```bash
python universe.py
```

Run the tests:

```bash
pytest -q
```

## Architecture

The screener core is a standalone package reused verbatim by both the bot and
the backtester — the code being screened live is the code being backtested.

```
config.py            thresholds, MA periods, weights, paths (single source of truth)
universe.py          LQ45 ∪ IDX80 ∪ Kompas100, merged & deduplicated (112 tickers)
data/fetcher.py      yfinance wrapper: retry/backoff + data-quality validation
screener/
  indicators.py      MAs, distances, RVOL, buy-pressure, IDX tick-size rounding
  rules.py           the 5 trading rules → BUY / SELL / HOLD / AVOID
  scoring.py         0–100 confidence score
  chart.py           candlestick + 6-MA overlay → PNG bytes (headless)
  result.py          ScreenResult dataclass (shared by bot + backtest)
  screen.py          fetch → evaluate → score orchestration
  __main__.py        CLI (python -m screener)
tests/               MA math + rule-trigger scenarios
```

### The 5 rules (source of truth: [plan.md §2](plan.md))

1. Avoid stocks far from their MA (overextended → `AVOID`).
2. Enter stocks near an MA support (+ short-term bullish → `BUY`).
3. Above all six MAs is the strongest state.
4. Sell when a close breaks below an MA it had held ≥5 days (→ `SELL`).
5. Avoid stocks below all MAs (→ `AVOID`).

Key thresholds (`NEAR_MA_THRESHOLD` 2%, `FAR_MA_THRESHOLD` 5%,
`SUPPORT_LOOKBACK` 5 days, `MIN_BARS` 250) live in `config.py` and will be
tuned by the Phase 4 backtest.

## Notes & caveats

- Data is from yfinance (`.JK`, daily, `auto_adjust=True`), delayed ~15 min —
  fine for daily-MA signals, **not** for intraday trading.
- Universe lists carry an effective date and must be refreshed after each IDX
  rebalancing (~Feb & Aug); see the update procedure in `universe.py`.
- **Not financial advice** — informational tooling only.
