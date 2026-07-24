# Nyaham — IHSG MA Screener Telegram Bot

Screens IDX (Indonesia Stock Exchange) stocks by moving-average alignment and
serves the results over Telegram. See [plan.md](plan.md) for the original design
and [backtest/FINDINGS.md](backtest/FINDINGS.md) for the strategy research.

**Commands**

- **`/ma <ticker> [conservative]`** — single-stock MA analysis (6-MA stack,
  trend, trade plan) + a candlestick chart with all MA lines.
- **`/market`** — is the IHSG index risk-on or risk-off? (index vs its MA50) + chart.
- **`/top5 [conservative]`** — the best fresh MA50 breakouts from the latest scan.
- **`/clear`** — delete the bot's recent messages from the chat.
- **`/scan`** — admin-only: run a full-universe scan now.
- **`/start`, `/help`** — intro and command help.

## Status — all phases complete

| Phase | Scope | State |
|---|---|---|
| **1** | Screener core (universe, fetcher, indicators, rules, scoring, chart) + tests | ✅ Done |
| **2** | Telegram bot (PTB): commands + formatter | ✅ Done |
| **3** | Scheduled daily scan + SQLite scan/OHLCV cache + trading calendar | ✅ Done |
| **4** | Backtest engine + metrics + tuner + extensive strategy research | ✅ Done — [FINDINGS.md](backtest/FINDINGS.md) |
| **5** | Deployment + ops (Docker, systemd, logging, backups, runbook) | ✅ Done — [DEPLOY.md](DEPLOY.md) |

## Quick start

Requires Python 3.11+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q          # 71 tests
```

The **screener core / CLI / backtest** need no secrets. The **bot** needs a
`.env` (`cp .env.example .env`, then set `BOT_TOKEN` from
[@BotFather](https://t.me/BotFather) and `ADMIN_CHAT_ID`).

## Usage

**Screen from the terminal** (no bot needed):

```bash
python -m screener BBCA                 # one ticker
python -m screener BBCA TLKM ANTM       # several
python -m screener BBCA --conservative  # gate on the market regime
python universe.py                      # list the scan universe
```

**Run the bot locally** (long-polling):

```bash
python -m bot.main
```

`/top5` never scans live — it serves the most recent completed scan; run
`/scan` (admin) once to populate it. A daily scan is scheduled for **16:30 WIB**
(Asia/Jakarta), skipping weekends and holidays.

## Deploying (24/7)

Docker (recommended) — auto-restarts on crash/reboot:

```bash
cp .env.example .env    # BOT_TOKEN + ADMIN_CHAT_ID
docker compose -f deploy/docker-compose.yml up -d --build
```

Runs as container **`nyaham-bot`** (compose project `nyaham`). A systemd unit,
hosting options, token rotation, logging, backups, and the maintenance runbook
(universe/holiday updates) are in [DEPLOY.md](DEPLOY.md).

> ⚠️ **One instance per bot token.** Two pollers → Telegram `409 Conflict`.
> Don't run the local process and a container (or two servers) on the same token.

## The strategy — `cross_pure`

The live strategy, chosen after the Phase-4 research (below):

- **BUY** — a *fresh breakout*: today's daily close crossed **above MA50**
  (yesterday's was at/below it). Entries are events — a name that crossed weeks
  ago is "in trend", not a new BUY.
- **SELL** — a fresh daily close **below MA50** after holding above it. The exit
  is a *condition*, not a price target — **there is no take-profit** (nearest
  resistance is shown as information only).
- **AVOID** — below all six MAs (clear downtrend) or data-quality flags.
- **HOLD** — everything else, with context (*in trend* vs *below MA50, waiting
  for the trigger*).

The 6-MA stack (5/10/20/50/100/200), trend tiers, and volume are displayed as
context. Key parameters (`EXIT_MA_PERIOD` 50, `REGIME_MA_PERIOD` 50,
`SUPPORT_LOOKBACK` 5, `MIN_BARS` 250) live in `config.py`.

**Modes** — `/ma` and `/top5` accept an optional `conservative` (or `c`):

- **normal** (default) — every fresh breakout.
- **conservative** — only signals BUY when the market is risk-on (IHSG `^JKSE`
  above its own MA50); holds cash otherwise. In backtests the MA50 regime gate
  was a Pareto improvement (higher return *and* lower drawdown) on the
  least-biased data. Exits are never gated.

## Strategy research (Phase 4)

```bash
python -m backtest --limit 20 --period 3y      # report
python -m backtest --tune --limit 20           # grid-search tuning
```

The engine reuses `rules.evaluate` unchanged (no look-ahead: the day-*t* signal
is evaluated on the slice through *t*; entry fills at the *t+1* open).

The research arc — documented in full, honestly, in
[backtest/FINDINGS.md](backtest/FINDINGS.md) — ran from *"the plan's original
MA-support strategy loses money and fails every out-of-sample test"* to
`cross_pure`, the one configuration with a consistent multi-decade profile
(positive expectancy in **19 of 26 years**, ~16,000 trades, controlled losses in
crashes). Along the way it **rejected take-profits and partial scale-outs in
four forms** (they cap the fat-tail winners), found **diversification** (100
small positions) cuts drawdown far better than tight stops, and validated the
**MA50 regime gate** as the one refinement that improves risk-adjusted return.

> ⚠️ **Honest caveats (read before trusting it):** absolute returns are inflated
> by **survivorship bias** (current index constituents over history), and the
> edge is **not formally out-of-sample validated** for entry — the winning
> config emerged from repeated work on overlapping windows. It's a **~19%
> win-rate trend-follower** with deep drawdowns. Treat the bot as an
> analysis/screening tool, **not** a proven money-maker.

## Architecture

The screener core is a standalone package reused verbatim by the bot **and** the
backtester — the code screened live is the code backtested.

```
config.py            thresholds, MA periods, regime gate, paths (single source of truth)
universe.py          LQ45 ∪ IDX80 ∪ Kompas100, merged & deduplicated (~112 tickers)
market_calendar.py   IDX trading-day + last-completed-bar helpers (WIB)
data/
  fetcher.py         yfinance wrapper: retry/backoff, validation, OHLCV cache, index fetch
  cache.py           SQLite: scan results + scan_meta (regime) + OHLCV bars
screener/
  indicators.py      MAs, distances, RVOL, buy-pressure, IDX tick-size rounding
  rules.py           cross_pure signal → BUY / SELL / HOLD / AVOID (+ regime gate)
  scoring.py         0–100 confidence score
  params.py          tunable Params bundle (lets the tuner vary thresholds)
  regime.py          ^JKSE risk-on/off (conservative mode + /market)
  chart.py           candlestick + 6-MA overlay → PNG bytes (headless)
  result.py          ScreenResult dataclass (shared by bot + backtest)
  screen.py          fetch → evaluate → score orchestration (+ screen_index)
  __main__.py        CLI (python -m screener)
jobs/daily_scan.py   full-universe scan → persist + capture regime
bot/
  formatter.py       ScreenResult → Telegram HTML (/ma, /market, /top5)
  handlers.py        command handlers + inline chart button
  main.py            PTB Application, error handler, start/stop heartbeats, daily job
backtest/
  engine.py metrics.py tuner.py __main__.py   core walk-forward + tuning
  side_*.py          research experiments (ma50, v2/v3, breakout, portfolio, scale-out …)
  FINDINGS.md        the full research write-up
deploy/              Dockerfile, docker-compose.yml, nyaham-bot.service, backup.sh, server-setup.sh
tests/               MA math, rule/signal scenarios, formatter, cache
```

## Notes & caveats

- Data is from yfinance (`.JK`, daily, `auto_adjust=True`), delayed ~15 min —
  fine for daily-MA signals, **not** for intraday trading.
- Universe lists carry an effective date and must be refreshed after each IDX
  rebalancing (~Feb & Aug); see the procedure in `universe.py`.
- **Not financial advice** — informational tooling only, with disclaimers on
  every message.
