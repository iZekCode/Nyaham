# Project Plan: IHSG Stock Screening Telegram Bot

## 1. Overview

A Telegram bot that screens IDX (Indonesia Stock Exchange) stocks based on Moving Average (MA) alignment, with two main features:

1. **`/ma <ticker>`** — on-demand analysis of a single stock (detailed output modeled after the reference screenshot: MA stack, trend summary, entry/exit levels, volume, verdict) **+ a candlestick chart image with all 6 MA lines drawn in distinct colors**
2. **`/top5`** — automatic recommendation of the 5 best stocks, ranked by a confidence score

**Decided stack:**

| Component | Choice | Rationale |
|---|---|---|
| Market data | yfinance (`.JK` suffix, daily OHLCV) | Free, deep history for backtesting; 15-min delay is irrelevant for daily-MA signals |
| Bot framework | python-telegram-bot (PTB) v21+ (async) | Most mature, best docs |
| Scan universe | LQ45 + IDX80 + Kompas100 (merged, deduplicated → ~100–120 tickers) | Liquid names, avoids illiquid/manipulated small caps |
| Indicators | pandas rolling mean (no external TA library) | Simple, transparent, fewer dependencies |
| Charting | mplfinance (matplotlib) | Native candlestick + overlay lines, renders PNG server-side for Telegram |
| Storage/cache | SQLite | Zero-ops, single file, sufficient scale |
| Backtesting | Custom pandas engine | Strategy is simple; full transparency over logic |
| Runtime | Python 3.11+ | |

## 2. Trading Rules (Source of Truth)

All screener logic derives from these 5 rules:

1. **Avoid stocks far from their MA** — price too extended above the nearest MA = overextended, skip
2. **Enter stocks near an MA** — price within a threshold distance of an MA support = entry candidate
3. **Above all MAs is best** — full bullish stack (price above all 6 MAs) = highest score
4. **Sell stocks that fail to hold above an MA** — a close below an MA that recently acted as support = SELL signal
5. **Avoid stocks below all MAs** — 0/6 MAs = blacklisted, never recommended

**Key parameters (initial defaults; tuned in Phase 5 backtest):**

| Parameter | Default | Meaning |
|---|---|---|
| `MA_PERIODS` | 5, 10, 20, 50, 100, 200 (daily) | The MA stack |
| `NEAR_MA_THRESHOLD` | 2% | "Near an MA" (rule 2) |
| `FAR_MA_THRESHOLD` | 5% | "Far from MA" / overextended (rule 1) |
| `SUPPORT_LOOKBACK` | 5 trading days | How long price must have held above an MA for a breakdown to count as "failed to hold" (rule 4) |
| `MIN_BARS` | 250 | Minimum history for a valid MA200 |

## 3. Repository Structure

```
ihsg-skem-bot/
├── .env.example           # BOT_TOKEN, ADMIN_CHAT_ID (never commit real .env)
├── config.py              # thresholds, MA periods, scan schedule, paths
├── universe.py            # ticker lists: LQ45, IDX80, Kompas100 + merge/dedup
├── data/
│   ├── fetcher.py         # yfinance wrapper with retry/backoff + validation
│   └── cache.py           # SQLite: OHLCV cache + daily scan results
├── screener/
│   ├── indicators.py      # MAs, distance-to-MA, volume metrics, RVOL
│   ├── rules.py           # the 5 rules → signal (BUY / SELL / HOLD / AVOID)
│   ├── scoring.py         # confidence score 0–100
│   ├── chart.py           # candlestick + MA overlay chart → PNG bytes
│   └── result.py          # ScreenResult dataclass (single source for bot + backtest)
├── bot/
│   ├── main.py            # PTB entry point, error handler, job registration
│   ├── handlers.py        # /ma, /top5, /help, /scan (admin), /subscribe (later)
│   └── formatter.py       # ScreenResult → Telegram message (emoji, sections)
├── jobs/
│   └── daily_scan.py      # scheduled full-universe scan after market close
├── backtest/
│   ├── engine.py          # historical entry/exit simulation
│   ├── metrics.py         # win rate, PF, drawdown, etc.
│   └── tuner.py           # grid search over thresholds & score weights
├── tests/
│   ├── test_indicators.py # MA math vs known values
│   ├── test_rules.py      # signal logic on synthetic price series
│   └── test_formatter.py  # message rendering
├── requirements.txt
├── README.md
└── plan.md
```

## 4. Phase 1 — Screener Core (no Telegram yet)

Goal: a standalone, terminal-testable module that is reused verbatim by both the bot and the backtester.

### 4.1 Universe (`universe.py`)

- Hardcode the LQ45, IDX80, and Kompas100 constituent lists (sourced from the official IDX index announcements); merge and deduplicate
- Store the effective date of each list; IDX rebalances roughly every 6 months (major eval Feb & Aug) — document the manual update procedure
- Helper: `normalize(ticker)` → uppercase, strip `.JK`, re-append for yfinance calls

### 4.2 Data fetcher (`data/fetcher.py`)

- `get_ohlcv(ticker, period="2y")` → daily DataFrame (Open, High, Low, Close, Volume)
- Use `auto_adjust=True` so splits/dividends are adjusted — critical for correct MAs and for backtest integrity
- Robustness:
  - Retry with exponential backoff on transient failures
  - Throttle between requests during batch scans (yfinance rate limits)
  - Validation: reject if empty, if fewer than `MIN_BARS` rows (MA200 invalid → mark ticker `INSUFFICIENT_DATA`), or if the last bar is older than N days (suspended/illiquid stock)
  - Detect zero-volume / flat-price streaks (suspension indicator) and flag instead of scoring
- Timezone: treat all data as Asia/Jakarta (WIB); "today's bar" only final after 16:00 WIB close

### 4.3 Indicators (`screener/indicators.py`)

- MA5/10/20/50/100/200 as rolling means of Close
- For each MA: above/below flag + signed distance in %
- Nearest support MA (highest MA below price) and nearest resistance MA (lowest MA above price)
- Volume metrics (approximated from daily candles, since yfinance has no tick data):
  - Buy/sell pressure proxy: position of close within the day's high–low range
  - RVOL = today's volume ÷ 20-day average volume
- Price change % vs previous close (for the header line)

### 4.4 Rules engine (`screener/rules.py`)

Per-stock evaluation, output fields:

- `ma_above_count` (0–6)
- Trend flags: short (5·10·20), medium (20·50), long (50·100·200) — each Bullish / Not yet, matching the reference bot's three-tier summary
- One-line verdict, e.g. 🟢 "FULL BULLISH", 🟡 "BULLISH short-term only", 🔴 "AVOID"
- Trade levels:
  - **Buy at**: nearest MA support (or breakout above current price if price sits on top of the stack)
  - **Sell/TP at**: nearest MA resistance (if above all MAs: use recent swing high or a fixed % target — configurable)
  - **Stop loss**: next MA support below the entry
  - Round all levels to the valid IDX tick size (fraksi harga: Rp1 / Rp2 / Rp5 / Rp10 / Rp25 depending on price band) so levels are actually tradeable
- Signal classification:
  - `AVOID` — below all 6 MAs (rule 5), or distance to nearest support MA > `FAR_MA_THRESHOLD` (rule 1), or data-quality flags (suspension, insufficient history)
  - `BUY` — within `NEAR_MA_THRESHOLD` of a support MA **and** at least short-term trend bullish (rule 2)
  - `SELL` — close crossed below an MA that price had held above for ≥ `SUPPORT_LOOKBACK` days (rule 4)
  - `HOLD/WAIT` — everything else
- Edge cases to handle explicitly: price exactly on an MA; multiple MAs clustered together (use the tightest as support, the cluster as a zone); newly listed stocks (< 250 bars → partial analysis with a disclaimer, never in `/top5`)

### 4.5 Confidence scoring (`screener/scoring.py`)

Score 0–100. Initial weights (all tunable in Phase 5):

| Factor | Weight | Notes |
|---|---|---|
| MA count above (0–6, scaled) | 40% | Rule 3 |
| Proximity to support MA (closer = higher, 0 beyond FAR) | 25% | Rules 1–2 |
| Buy-side volume pressure | 20% | Confirms the move |
| RVOL ≥ 1x (capped bonus) | 15% | Participation |

Hard overrides: `AVOID` → score 0; `SELL` → excluded from recommendations regardless of score.

### 4.6 Chart generation (`screener/chart.py`)

Produces the price chart image sent alongside the `/ma` text analysis.

- **Library**: mplfinance (built on matplotlib) — native candlestick rendering plus `addplot` overlays. Alternative considered: plotly + kaleido (prettier, but heavier dependency and slower render on a small VPS); mplfinance wins for v1.
- **Chart content**:
  - Daily candlesticks, last ~6 months (~120 bars) so MA200 context is visible without cramming — window length configurable
  - All 6 MA lines overlaid, each with a fixed distinct color used consistently everywhere:
    | MA | Color |
    |---|---|
    | MA5 | blue |
    | MA10 | cyan |
    | MA20 | green |
    | MA50 | orange |
    | MA100 | purple |
    | MA200 | red |
  - Legend mapping color → MA period
  - Volume subplot below the price panel
  - Horizontal dashed lines for the computed **Buy / Sell (TP) / Stop-loss** levels with labels, so the chart and the text analysis tell the same story
  - Title: ticker, last price, % change, scan date
- **Technical requirements**:
  - `matplotlib.use("Agg")` — headless backend, no display server needed on the VPS
  - Render to an in-memory `BytesIO` PNG (no temp files), sized ~1280×720 @ 100 dpi — sharp on mobile, small payload
  - Dark theme to match Telegram's default dark mode (configurable light/dark)
  - Render time target < 1 s per chart
- **Delivery in the bot**: `/ma` sends the chart via `send_photo` with the text analysis as the caption (Telegram caption limit is 1024 chars — if the full analysis exceeds it, send photo first, full text as a follow-up message)
- **`/top5` interaction**: each of the 5 entries gets an inline button ("📈 Chart") that triggers the chart for that ticker on demand — avoids blasting 5 images at once

### 4.7 CLI + validation

- `python -m screener ARCI` → full printout in terminal
- Manually cross-check MA values and levels for 3–5 tickers against TradingView charts before proceeding
- Unit tests: MA math on synthetic data, each rule triggering on constructed scenarios

**Exit criteria:** screener runs for any universe ticker, numbers verified, tests green.

## 5. Phase 2 — Telegram Bot (PTB)

### 5.1 Setup & hygiene

- Create bot via @BotFather; store token in `.env` (never in code / git)
- PTB v21+ async `Application`; global error handler that logs and notifies the admin chat instead of crashing
- Polling mode first (simplest); webhook is a deploy-time option later

### 5.2 `/ma <ticker>`

- Input handling: case-insensitive, with/without `.JK`, unknown ticker → friendly error, missing argument → usage hint
- Send "⏳ Fetching data..." immediately (yfinance takes 2–5 s), then edit the message with the result
- Output format mirrors the reference screenshot:
  - Header: 📊 MA STACK: TICKER, 💰 price (+x.x%)
  - Price vs each MA with ✅/❌ and the "above N/6" counter
  - ⚡ Short / 📊 Medium / ⛰ Long trend lines with 🟢/⚪
  - 🎯 verdict line
  - 💵 ENTRY & EXIT: Buy at / Sell at / Stop loss (tick-size-valid prices)
  - 📦 Volume: buy % / sell %, RVOL, health comment
  - 🗣 "Bottom line": 2–3 sentence actionable summary
- **Chart image**: candlestick + 6 colored MA lines + level lines (see §4.6), sent as a photo with the analysis as caption (fallback: photo + separate text message if over the 1024-char caption limit)
- Message under Telegram's 4096-char limit; MarkdownV2 or HTML parse mode with proper escaping

### 5.3 `/top5`

- Reads the latest completed scan from SQLite — never scans live (instant response)
- Filter to `BUY` signals only, sort by confidence desc, take 5; if fewer than 5 BUYs exist, show however many qualify and say so honestly
- Per stock: rank, ticker, price, confidence, buy/TP/SL levels, one-line reason
- Inline "📈 Chart" button per entry → renders that ticker's chart on demand (callback query handler)
- Footer: scan timestamp + "data is delayed & informational only" disclaimer

### 5.4 Auxiliary commands

- `/help` — command list + brief rule explanation
- `/start` — welcome + disclaimer (not financial advice)
- `/scan` — admin-only manual scan trigger (guarded by `ADMIN_CHAT_ID`)

**Exit criteria:** both core commands work end-to-end in a real chat; malformed input never crashes the bot.

## 6. Phase 3 — Scheduled Scan + Cache

- Daily job via PTB `JobQueue` at **16:30 WIB** (market closes 16:00; buffer for data availability)
- Skip weekends; maintain a small IDX holiday list in `config.py` (manually updated yearly) — on holidays, skip and keep the previous scan
- Batch scan ~120 tickers with inter-request delay → estimated 3–8 minutes total
- Persist every `ScreenResult` to SQLite keyed by (ticker, scan_date); keep history (useful later for signal-change alerts and for auditing)
- Partial-failure policy: individual ticker failures are logged and skipped; scan completes with a summary (e.g. "118/121 OK") sent to admin
- OHLCV caching: store fetched bars so `/ma` on an already-scanned ticker is instant; refresh only if stale
- Optional (post-MVP): `/subscribe` → auto-push the daily top 5 to subscribers after each scan

**Exit criteria:** `/top5` responds instantly with same-day data every trading day without manual intervention.

## 7. Phase 4 — Backtest

### 7.1 Engine (`backtest/engine.py`)

- Reuses `screener/` modules unchanged — the code being backtested is the code running live
- Walk-forward over 2–3 years of daily bars per universe ticker
- Trade simulation:
  - **Entry**: BUY signal on day *t* → enter at day *t+1* open (no look-ahead bias)
  - **Exit**: SELL signal (rule 4), stop-loss touch, or TP/resistance touch — whichever first; intraday touches checked against High/Low
  - One open position per ticker; configurable max concurrent positions for portfolio-level runs
- Cost model: total fees ~0.3% round trip (typical Indonesian broker buy 0.15% + sell 0.25% incl. sales tax — configurable); no slippage initially, add a slippage % option later
- Known limitation to document: universe uses **current** index constituents → survivorship bias; acceptable for v1, note it in the report

### 7.2 Metrics (`backtest/metrics.py`)

- Per-strategy: win rate, average return/trade, profit factor, expectancy, max drawdown, trade count, average holding period
- Equity curve export (CSV + chart)
- Benchmarks: buy-and-hold IHSG (^JKSE) and buy-and-hold the equal-weighted universe over the same period

### 7.3 Tuning (`backtest/tuner.py`)

- Grid search: `NEAR_MA_THRESHOLD` ∈ {1, 1.5, 2, 2.5, 3}%, `FAR_MA_THRESHOLD` ∈ {4–7}%, `SUPPORT_LOOKBACK` ∈ {3, 5, 10}, scoring weights (coarse grid)
- Guard against overfitting: split data into in-sample (older) / out-of-sample (recent) periods; pick parameters on in-sample, validate out-of-sample
- Deliverable: short report + final parameters written into `config.py`

**Exit criteria:** backtest report exists, strategy beats (or is honestly compared against) buy-and-hold, final parameters locked in.

## 8. Phase 5 — Deployment & Operations

### 8.1 Hosting options (free-first)

| Option | Cost | Fit for this bot | Caveats |
|---|---|---|---|
| **Oracle Cloud Always Free (ARM VM)** ⭐ | Rp0, permanent | **Best free option** — a real always-on VM (up to 4 ARM OCPU / 24 GB RAM), full root access, runs polling bot + JobQueue + matplotlib with room to spare | Sign-up sometimes rejected (retry with different card/region); idle instances can be reclaimed — a bot polling 24/7 usually counts as active; ARM arch (all our deps have ARM wheels, non-issue) |
| **Google Cloud e2-micro Always Free** | Rp0, permanent | Works — small x86 VM, always free in specific US regions | 1 GB RAM is tight but enough; US region = higher latency to Telegram/Yahoo (irrelevant for a daily-signal bot); requires credit card |
| **Home hardware (old laptop / Raspberry Pi)** | Rp0 | Perfectly fine for a personal bot | Depends on your electricity/internet uptime; no SLA but you own it |
| Render free tier | Rp0 | ⚠️ Poor fit — free web services spin down after 15 min idle, which kills the polling loop and the 16:30 scheduled scan | Only viable with webhook mode + external cron pinger; fragile, not recommended |
| Railway | ~Rp0 → paid | ⚠️ No longer really free — small monthly credit only; an always-on bot burns through it | Fine for a short demo, not for 24/7 |
| Fly.io | Paid | No free tier for new users anymore | — |
| **Cheap VPS (fallback)**: IDCloudHost / Hetzner / DigitalOcean | ~Rp50–80k/mo | Zero-surprise always-on box, WIB-friendly (IDCloudHost is local) | Costs money |

**Recommendation**: try **Oracle Cloud Always Free** first — it's the only genuinely free option that matches this bot's requirements (always-on process, scheduled jobs, chart rendering). If the sign-up fights you, fall back to GCP e2-micro, or a local VPS if you'd rather pay a little for zero hassle.

### 8.2 Operations
- **Process management**: `systemd` service (or Docker) with auto-restart on failure
- **Secrets**: `.env` on server only; token revocation procedure documented
- **Logging**: rotating file logs (INFO in prod); errors additionally pushed to the admin chat
- **Monitoring**: daily scan-completion message to admin doubles as a heartbeat; if it doesn't arrive, something's wrong
- **Backups**: weekly SQLite file copy (cron)
- **Maintenance runbook**: updating universe lists after IDX rebalancing (~every 6 months), updating the holiday calendar (yearly), dependency updates

## 9. Work Order & Effort Split

| # | Phase | Effort | Depends on |
|---|---|---|---|
| 1 | Screener core + validation + tests | ~40% | — |
| 2 | Telegram bot + formatter | ~20% | 1 |
| 3 | Scheduled scan + cache | ~10% | 1, 2 |
| 4 | Backtest + tuning | ~25% | 1 |
| 5 | Deploy + ops | ~5% | 2, 3 |

Phases 3 and 4 are independent of each other and can be swapped or parallelized.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| yfinance rate limiting / breakage | Throttling, retries, OHLCV cache; upgrade path to paid API (GoAPI / Sectors) is isolated inside `fetcher.py` |
| Bad data (splits, gaps, suspensions) | `auto_adjust=True`, validation layer, suspension detection, flags instead of silent wrong numbers |
| Overfitting in tuning | In-sample / out-of-sample split |
| Survivorship bias in backtest | Documented limitation for v1 |
| Index rebalancing drift | Dated universe lists + documented update procedure |
| Bot token leak | `.env` only, gitignored, revocation runbook |
| Users treating output as financial advice | Disclaimer in `/start`, `/help`, and `/top5` footer |

## 11. Open Decisions (deferred, with owners)

- Final threshold values and scoring weights → decided by Phase 4 backtest
- TP logic when price is above all MAs (swing high vs fixed %) → test both in backtest
- `/subscribe` daily push → post-MVP
- Webhook vs polling in production → decide at deploy time
- Paid realtime data upgrade → only if intraday signals are ever needed
