# Deployment & Operations (Phase 5)

How to run the bot 24/7, keep it healthy, and maintain it. See
[plan.md §8](plan.md) for the design rationale.

The bot is a single always-on polling process plus a JobQueue that scans the
universe daily at **16:30 WIB**. It needs: an always-on host, `BOT_TOKEN` +
`ADMIN_CHAT_ID`, and persistent storage for `data/cache.sqlite`.

> ⚠️ **Exactly one instance may run per bot token.** Two pollers → Telegram
> returns `409 Conflict` and neither works. Don't scale replicas; stop the old
> instance before starting a new one.

---

## 1. Hosting

| Option | Cost | Notes |
|---|---|---|
| **Oracle Cloud Always Free (ARM Ampere)** ⭐ | Rp0 | Best free fit — a real always-on VM (up to 4 OCPU / 24 GB), full root. All deps have ARM wheels. Sign-up can be finicky; retry with a different card/region. |
| GCP e2-micro Always Free | Rp0 | Works; 1 GB RAM is tight but enough. US region only (latency irrelevant for daily signals). |
| Home box / Raspberry Pi | Rp0 | Fine for personal use; depends on your uptime. |
| Cheap VPS (Hetzner / IDCloudHost / DO) | ~Rp50–80k/mo | Zero-hassle fallback. IDCloudHost is WIB-local. |
| Render / Railway / Fly free tiers | — | ⚠️ Poor fit — they spin down idle web services, which kills polling and the scheduled scan. Not recommended. |

Any Linux box with Docker **or** Python 3.11 works. Two deploy paths follow.

---

## 2. Deploy — Docker (recommended)

Reproducible, auto-restarts, isolates dependencies. Works on ARM and x86.

```bash
git clone <repo> ihsg-skem-bot && cd ihsg-skem-bot
cp .env.example .env          # then edit: BOT_TOKEN, ADMIN_CHAT_ID
docker compose -f deploy/docker-compose.yml up -d --build
```

- `data/` and `logs/` are mounted from the host, so the SQLite cache and logs
  survive rebuilds.
- `restart: unless-stopped` brings it back after crashes and reboots.

```bash
docker compose -f deploy/docker-compose.yml logs -f     # follow logs
docker compose -f deploy/docker-compose.yml restart      # restart
docker compose -f deploy/docker-compose.yml down         # stop
```

## 2b. Deploy — systemd (no Docker)

```bash
git clone <repo> ihsg-skem-bot && cd ihsg-skem-bot
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # edit secrets; also set LOG_FILE=logs/bot.log

# Edit User/paths in the unit to match your box, then:
sudo cp deploy/ihsg-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ihsg-bot
journalctl -u ihsg-bot -f      # follow logs
```

`Restart=always` handles crashes; `enable` starts it on boot.

---

## 3. Secrets & token security

- Secrets live **only** in `.env` on the server. `.env` is gitignored and
  `.dockerignore`d — it is never committed or baked into the image.
- **If the token leaks**: message [@BotFather](https://t.me/BotFather) →
  `/revoke` → pick the bot → it issues a new token. Put the new token in `.env`
  and restart. The old token dies immediately.
- Never paste bot logs publicly unredacted — PTB logs the token in some request
  URLs at DEBUG level (kept at INFO/WARNING here to avoid that).

---

## 4. Logging & monitoring

- **Logs**: console always; set `LOG_FILE` for a rotating file
  (`LOG_MAX_BYTES` × `LOG_BACKUP_COUNT`, defaults 5 MB × 5). In Docker it's
  `logs/bot.log` on the host.
- **Startup heartbeat**: on boot the bot DMs the admin "🤖 Bot started…".
- **Daily heartbeat**: the 16:30 WIB scan DMs the admin a summary
  (`118/121 OK`, signal counts, regime). **If that message doesn't arrive on a
  trading day, something is wrong** — check logs.
- **Errors**: unhandled handler errors are logged *and* DM'd to the admin
  instead of crashing the bot.

Quick manual health check: send `/start` — a reply means it's polling.

---

## 5. Backups

`data/cache.sqlite` holds scan history + the OHLCV cache. It's rebuildable
(re-run `/scan`) but worth backing up. [deploy/backup.sh](deploy/backup.sh)
takes a consistent online snapshot and prunes old copies.

```bash
# Weekly, Sundays 02:00 — crontab -e:
0 2 * * 0  /home/ubuntu/ihsg-skem-bot/deploy/backup.sh >> /home/ubuntu/ihsg-skem-bot/logs/backup.log 2>&1
```

Backups land in `backups/` (gitignored). Restore = stop the bot, copy a
`cache-*.sqlite.gz` back to `data/cache.sqlite` (gunzipped), start.

---

## 6. Maintenance runbook

| Task | When | How |
|---|---|---|
| **Universe refresh** | After each IDX rebalancing (~Feb & Aug) | Update the lists in [universe.py](universe.py) from the official IDX index constituents; bump `EFFECTIVE_DATE`; `python universe.py` to sanity-check the size (~100–120). See the procedure at the bottom of that file. |
| **Holiday calendar** | Yearly (Dec/Jan) | Fill in `IDX_HOLIDAYS_20XX` in [config.py](config.py) from the IDX "Kalender Libur Bursa" — **including movable religious holidays + cuti bersama** (the current list is fixed-date only and incomplete). A missing holiday just wastes one scan; it never marks a real trading day as closed. |
| **Dependency updates** | Occasionally | `pip install -U -r requirements.txt` in a branch, run `pytest`, redeploy. yfinance breaks most often — it's isolated in `data/fetcher.py`. |
| **Redeploy after code changes** | As needed | Docker: `up -d --build`. systemd: `git pull && systemctl restart ihsg-bot`. Always stop the old instance first. |

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `409 Conflict` in logs | Two instances on one token. Kill the extra (`pgrep -f bot.main`), keep one. |
| No startup/scan heartbeat | Bot down, no network, or `ADMIN_CHAT_ID` wrong. Check logs / `systemctl status`. |
| `/top5` empty | Correct when there are no fresh MA50 breakouts that day (or, in conservative mode, when IHSG is risk-off). Not a bug. |
| Many `NO_DATA` / yfinance 404s | Delisted ticker, or yfinance rate-limiting/outage. Throttling + retries are built in; a few failures per scan are normal (the summary reports `OK/total`). |
| Chart render errors | Headless matplotlib (Agg) needs no display; if a single ticker fails the text still ships. |
| Bot won't start: "BOT_TOKEN is not set" | `.env` missing or not loaded. Ensure it's in the repo root (Docker reads `../.env` via compose). |

---

## 8. Strategy note

The live strategy is **cross_pure** (MA50 cross-in / close-below-MA50-out, no
take-profit), with an optional **conservative** mode (IHSG-above-MA50 regime
gate). Its research history, risk profile, and the honest survivorship caveat
are in [backtest/FINDINGS.md](backtest/FINDINGS.md). The bot is an analysis and
signalling tool with disclaimers on every message — **not** financial advice.
