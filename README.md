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
| **2** | Telegram bot (PTB): `/ma`, `/top5`, `/help`, `/start`, `/scan` + formatter | ✅ Done |
| **3** | Scheduled daily scan + SQLite scan cache + OHLCV bar cache + trading calendar | ✅ Done |
| **4** | Backtest engine + metrics + grid-search tuner + extensive strategy research | ✅ Done — see [FINDINGS.md](backtest/FINDINGS.md) |
| **5** | Deployment + ops (Docker, systemd, logging, backups, runbook) | ✅ Done — see [DEPLOY.md](DEPLOY.md) |

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

## Running the bot (Phase 2)

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. `cp .env.example .env` and set `BOT_TOKEN` and `ADMIN_CHAT_ID`.
3. Start it (long-polling):

```bash
python -m bot.main
```

Commands: `/start`, `/help`, `/ma <ticker> [conservative]`, `/top5
[conservative]`, and `/scan` (admin only — runs a full-universe scan and
populates the cache that `/top5` reads). `/top5` never scans live; it serves
the most recent completed scan. A daily scan job is registered for 16:30 WIB
(Asia/Jakarta), skipping weekends/holidays.

## Deploying (Phase 5)

For 24/7 hosting, use Docker (recommended) or systemd — both auto-restart:

```bash
cp .env.example .env    # set BOT_TOKEN + ADMIN_CHAT_ID
docker compose -f deploy/docker-compose.yml up -d --build
```

Full hosting guide, secrets/token rotation, logging, backups, and the
maintenance runbook (universe/holiday updates) are in [DEPLOY.md](DEPLOY.md).

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
market_calendar.py   IDX trading-day + last-completed-bar helpers (WIB)
data/cache.py        SQLite: scan results + OHLCV bar cache (instant repeat /ma)
jobs/daily_scan.py   full-universe scan → persist (used by /scan + daily job)
bot/
  formatter.py       ScreenResult → Telegram HTML message
  handlers.py        /ma, /top5, /help, /start, /scan, chart button
  main.py            PTB Application, error handler, daily-job registration
tests/               MA math + rule-trigger + formatter scenarios
```

### The strategy (cross_pure — replaced the original 5-rule logic)

- **BUY** — a *fresh breakout*: today's daily close crossed **above MA50**
  (yesterday's close was at/below it). Entries are events — a stock that
  crossed weeks ago is "in trend", not a new BUY.
- **SELL** — a fresh daily close **below MA50** after holding above it.
  The exit is a *condition*, not a price target; there is **no take-profit**
  (nearest resistance is shown as information only).
- **AVOID** — below all six MAs (clear downtrend) or data-quality flags.
- **HOLD** — everything else, with context: *in trend* (stay in if entered)
  or *below MA50* (the entry trigger is a daily close back above it).

The 6-MA stack, trend tiers, and volume metrics remain as displayed context.

**Modes** — both `/ma` and `/top5` take an optional `conservative` (or `c`):

- **normal** (default) — every fresh breakout.
- **conservative** — only signals BUY when the market itself is risk-on
  (IHSG `^JKSE` above its own MA50). In backtests this kept ~most of the return
  while cutting max drawdown sharply (a Pareto improvement on the least-biased
  data). When the market is risk-off, conservative `/top5` recommends holding
  cash. Exits are never gated.

cross_pure emerged from the Phase 4 investigation as the only configuration
with a consistent multi-decade profile (positive expectancy in 19 of 26 years
on ~16,000 trades, controlled losses in crashes) — full story and the honest
caveats (survivorship bias unresolved; not formally validated) in
[backtest/FINDINGS.md](backtest/FINDINGS.md).

Key parameters (`EXIT_MA_PERIOD` 50, `REGIME_MA_PERIOD` 50, `SUPPORT_LOOKBACK`
5 days, `MIN_BARS` 250) live in `config.py`.

## Backtest (Phase 4)

```bash
python -m backtest --limit 20 --period 3y      # report on 20 tickers
python -m backtest --tune --limit 20           # grid-search parameter tuning
```

The engine reuses `rules.evaluate` unchanged (no look-ahead: signal on day *t*
is evaluated on the slice through *t*, entry fills at the *t+1* open). Metrics,
benchmarks (buy-and-hold IHSG + equal-weight universe), and an equity-curve
CSV/PNG are written to `backtest/output/`.

> ⚠️ **Honest finding:** with the plan's initial default thresholds — and across
> the **entire** tuning grid — the strategy **loses money and underperforms
> buy-and-hold**. Average holding is ~1 day: trades stop out almost immediately
> because the MA-based stop sits just below the entry (the entry condition is
> "near support", so the next MA down is very close, especially in clustered-MA
> zones). No parameter set in the grid rescues it. Deliberately, **no losing
> parameters were written into `config.py`.** The strategy logic needs
> refinement (wider/ATR stops, cluster-as-zone stops, a trend filter, longer
> holds) before it's worth deploying signals. **Full write-up:**
> [backtest/FINDINGS.md](backtest/FINDINGS.md).

## Notes & caveats

- Data is from yfinance (`.JK`, daily, `auto_adjust=True`), delayed ~15 min —
  fine for daily-MA signals, **not** for intraday trading.
- Universe lists carry an effective date and must be refreshed after each IDX
  rebalancing (~Feb & Aug); see the update procedure in `universe.py`.
- **Not financial advice** — informational tooling only.
