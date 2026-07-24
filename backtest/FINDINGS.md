# Backtest Findings — Phase 4

**Status:** strategy underperforms; no parameters locked into `config.py`.
**Date:** 2026-07-24 · **Author:** initial backtest pass

---

## TL;DR

With the plan's initial default thresholds **and across the entire 60-combination
tuning grid**, the MA-support strategy **loses money and underperforms
buy-and-hold**. The dominant failure mode is near-instant stop-outs (average
holding ≈ **0.1–0.8 days**). Deliberately, **no losing parameters were written
into `config.py`.**

## Methodology

- **Engine** ([engine.py](engine.py)) reuses `screener.rules.evaluate` unchanged —
  the code backtested is the code that runs live. No look-ahead: the signal on
  day *t* is evaluated on the price slice through *t*; a BUY fills at the **t+1
  open**.
- **Exits** each held day, priority order: stop-loss (intraday Low ≤ stop) →
  take-profit (intraday High ≥ TP) → SELL signal (rule 4) at the close. Stop wins
  ties (conservative).
- **Costs:** 0.15% buy + 0.25% sell (config `FEE_BUY` / `FEE_SELL`).
- **Tuning** ([tuner.py](tuner.py)): grid over `NEAR_MA_THRESHOLD`
  {1.0–3.0%}, `FAR_MA_THRESHOLD` {4–7%}, `SUPPORT_LOOKBACK` {3,5,10}, with an
  in-sample (older 60%) / out-of-sample (recent 40%) split to catch overfitting.

## Headline results

Representative run — 8 liquid tickers, 2-year daily history:

| Metric | Strategy (defaults) |
|---|---|
| Trades | 185 |
| Win rate | 25.4% |
| Avg return / trade (expectancy) | **−0.82%** |
| Avg win / avg loss | +3.42% / −2.27% |
| Profit factor | 0.51 |
| Total PnL (1 unit/trade) | **−152%** |
| Max drawdown | **−163%** |
| Avg holding | **0.8 days** |
| Exit reasons | stop 103 · tp 48 · sell 32 · eod 2 |

**Benchmarks over the same window:** equal-weight universe **+5.7%**,
IHSG (^JKSE) **−14.9%**, strategy **≈ −150%**.

**Tuning:** every one of the 60 grid combinations produced **negative
expectancy in both in-sample and out-of-sample** periods. Best out-of-sample
profit factor observed was ~0.77 (still < 1.0 = losing). No parameter set
rescues the strategy — this is a structural problem, not a tuning problem.

## Root-cause diagnosis

The ~1-day average holding period is the tell. The entry condition is "price is
**near** a support MA", but the stop-loss is placed at the **next MA below** the
entry support. Precisely *because* the entry fires when MAs are clustered near
price, that next MA sits only a fraction below the entry — so ordinary intraday
range stops the trade out on the entry bar or the one after. The reward side is
actually healthy (avg win +3% vs avg loss −1.9%), but a ~25–35% win rate can't
overcome stops that trigger on noise.

The plan anticipated this (§4.4: *"multiple MAs clustered together → use the
tightest as support, the cluster as a zone"*), but v1 implemented a single-MA
stop rather than a cluster-zone stop.

## Recommended refinements (for a future pass)

Not implemented — captured here so the next iteration has a starting point:

1. **Wider, structure-aware stops** — place the stop below the *whole MA
   cluster/zone*, or use an ATR-based buffer (e.g. entry − 1.5×ATR) instead of
   the next single MA.
2. **Trend filter** — only take BUYs when the medium/long tiers are also bullish
   (currently only short-term is required), to avoid buying dips in downtrends.
3. **Longer minimum hold / no same-bar stop** — optionally skip the stop check on
   the entry bar, or require the close (not just the intraday low) to breach the
   stop.
4. **Re-entry throttle** — after a stop-out, don't immediately re-buy the same
   name the next day (the current logic churns).
5. Re-run the grid after each change; only lock parameters into `config.py` once
   a config beats buy-and-hold **out-of-sample**.

## Known limitations

- **Survivorship bias:** the universe uses *current* index constituents, so
  delisted/relegated names are absent (documented, accepted for v1).
- **Equity curve** is additive (1 unit/trade), not a compounding portfolio
  simulator with position limits — fine for comparing strategies, not a P&L
  forecast.
- Small-sample runs (a handful of tickers) are illustrative; a full-universe,
  3-year run is the real test and should be run offline (it's slower).

## How to reproduce

```bash
python -m backtest --limit 8 --period 2y      # the headline run above
python -m backtest --tune --limit 6 --period 2y   # the grid search
```

Outputs (equity CSV/PNG) land in `backtest/output/` (gitignored).

---

## Correction — equity/drawdown bug (fixed 2026-07-24)

A defect was found in `metrics.equity_curve`: it built the curve from a **dict
keyed by exit date**, so trades sharing an exit date were silently dropped,
**understating max drawdown by roughly 4×**. Fixed (build from parallel lists;
regression test `test_equity_curve_handles_same_date_exits`).

`total_pnl`, win rate, profit factor, and expectancy are computed independently
and were **always correct**. Only max-drawdown figures and the equity PNGs were
affected. The 6-MA baseline max drawdown above has been corrected to **−163%**
(was reported −103% under the bug); conclusions are unchanged.

---

## Side experiment — MA50-only strategy

Requested variant (see [side_ma50.py](side_ma50.py)): ignore the 6-MA stack,
use **MA50 as the sole support**, and **exit when a daily _close_ prints below
MA50** (no fixed take-profit, so winners ride until MA50 breaks). Two entry
definitions were swept.

**Full universe — 111 tickers, 2-year daily, ~1,700 trades:**

| Entry rule | Trades | Win% | Exp/trade | PF | Total PnL* | Max DD* | Hold |
|---|---|---|---|---|---|---|---|
| Pullback ≤1% above MA50 | 1,175 | 15.1% | −0.53% | 0.76 | −620% | −931% | 7.0d |
| Pullback ≤2% | 1,544 | 15.0% | −0.58% | 0.77 | −890% | −1393% | 7.7d |
| Pullback ≤3% | 1,666 | 15.8% | −0.42% | 0.84 | −697% | −1574% | 8.5d |
| Pullback ≤5% | 1,728 | 16.5% | −0.03% | 0.99 | −49% | −1727% | 9.5d |
| **Any close above MA50** | **1,761** | **17.5%** | **+0.77%** | **1.25** | **+1361%** | −1450% | 10.7d |

\* Total PnL and Max DD are **additive, 1-unit-per-trade** — the sum of many
independent bets, **not** a portfolio return. The meaningful figure is the
per-trade expectancy.

**Benchmarks (same window):** equal-weight universe **+34.9%**, IHSG (^JKSE)
**−14.5%**.

### What it shows

1. **The close-below-MA50 stop fixes the 6-MA version's core defect.** Holding
   period rises from ~0.8 days to ~7–11 days — the instant-noise-stopout problem
   is gone, because the stop is close-based (structural) rather than an intraday
   MA touch.
2. **The pullback entry is the part that loses.** Requiring the entry to be
   *near* MA50 (within 1–5%) puts price right next to its own stop → PF 0.76–0.99,
   negative expectancy across the board.
3. **The pure MA50 trend filter has a genuine edge.** Dropping the "near"
   requirement — simply be long whenever the close is above MA50, exit on a close
   below — gives **PF 1.25, +0.77%/trade over 1,761 trades**. Large enough to be
   signal, not noise.

### Caveats (do not deploy on this alone)

- It's a **low-win-rate trend-follower** (~17%): long stretches of small losses
  bailed out by a few big winners. The equity curve was deeply underwater
  through mid-2025 before a late-2025→2026 trend rescued it — the profit is
  concentrated, not steady.
- On a comparable per-name basis it **does not clearly beat buy-and-hold**
  (+34.9% equal-weight) in this 2-year up-market — but it is long/flat, so it
  sidesteps drawdowns and vastly beat the −14.5% index. Its edge is regime-
  dependent (shines in choppy/down markets).
- **Single 2-year window + survivorship bias.** Needs out-of-sample and longer,
  multi-regime history before trusting.

### Reproduce

```bash
python -m backtest.side_ma50 --limit 200 --period 2y   # full universe sweep
python -m backtest.side_ma50 BBCA TLKM ANTM ASII       # specific names
```

---

## Side experiment — testing the recommended refinements

Each refinement from the list above was tested **in isolation** against the
baseline, plus all four combined (see [side_refinements.py](side_refinements.py)).
Entry logic is the live `rules.evaluate` BUY everywhere; only the exit/filter
mechanics under test change. Take-profit (nearest MA resistance) kept identical
across variants.

**Full universe — 111 tickers, 2-year daily:**

| Variant | Trades | Win% | Exp/trade | PF | Hold | Stop exits |
|---|---|---|---|---|---|---|
| baseline (next-MA stop, intraday) | 2,663 | 28.5% | −0.76% | 0.53 | 1.0d | 1,441 |
| #3 close-triggered stop | 2,421 | 37.5% | −0.66% | 0.64 | 1.5d | 809 |
| #1a ATR stop (entry − 1.5×ATR14) | 2,392 | 38.6% | −0.69% | 0.64 | 1.9d | 488 |
| #1b cluster-zone stop | 2,424 | 35.6% | −0.72% | 0.59 | 1.6d | 861 |
| #2 trend filter (Med+Long bullish) | 1,308 | 30.0% | −0.75% | 0.58 | 1.2d | 714 |
| #4 cooldown (5d after stop-out) | 2,104 | 29.4% | −0.77% | 0.54 | 1.1d | 1,083 |
| **Combo (all four)** | **1,090** | **41.6%** | **−0.62%** | **0.72** | 2.6d | **100** |

Benchmarks (same window): equal-weight universe +34.4%, IHSG −14.8%.

### Conclusion

**Every refinement improves the baseline; none — not even all four combined —
turns it profitable.** The stop-out problem is genuinely fixed (combo: 1,441 →
100 stop exits, win rate 28% → 42%), but expectancy stays negative because the
exits simply migrate to the other two exit paths:

1. **The take-profit caps winners instantly.** "Sell at nearest MA resistance"
   is typically only 1–3% above entry in a clustered stack (combo: 393 TP exits).
2. **Rule-4 SELL fires on any minor dip** — a close below MA5, the tightest MA
   that price hugs constantly (combo: 585 SELL exits, avg hold still 2.6 days).

The loss side was treated; **the profit side is the binding constraint** — the
strategy structurally cuts winners. This converges with the MA50 experiment
from the opposite direction: the profitable variant there had **no TP** and an
exit hung on a **slow** MA, letting winners ride ~11 days (PF 1.25).

**Implication for v2:** (a) drop or drastically widen the nearest-resistance
TP, and (b) base the SELL rule on a slower MA (e.g. MA20/MA50), not the
tightest one. The refinements #1–#4 remain worth keeping, but they are
secondary to fixing the exit asymmetry.

### Reproduce

```bash
python -m backtest.side_refinements --limit 200 --period 2y   # full universe
python -m backtest.side_refinements --limit 8 --period 2y     # quick sample
```

---

## Side experiment — v2 exit spec (first profitable configuration)

Implements the implication above (see [side_v2.py](side_v2.py)): entry is the
**unchanged live rules BUY** (optionally + Med/Long trend filter); exit is
**only a daily close below a slow MA** — no take-profit, no rule-4 SELL, no
tight stop. Entries are skipped if the close isn't above the exit MA.

**Full universe — 111 tickers, 2-year daily:**

| Variant | Trades | Win% | Exp/trade | PF | Total PnL* | Hold |
|---|---|---|---|---|---|---|
| exit_ma10 | 1,551 | 24.9% | −0.32% | 0.87 | −499% | 4.6d |
| exit_ma10+trend | 835 | 25.6% | −0.37% | 0.86 | −311% | 4.7d |
| exit_ma20 | 1,327 | 20.0% | +0.23% | 1.08 | +309% | 6.5d |
| exit_ma20+trend | 646 | 22.3% | +0.56% | 1.17 | +364% | 8.2d |
| exit_ma50 | 771 | 18.7% | +0.38% | 1.11 | +293% | 11.2d |
| **exit_ma50+trend** | **482** | 19.9% | **+1.24%** | **1.32** | **+598%** | **15.0d** |

\* Additive, 1 unit/trade. Benchmarks (same window): equal-weight +34.5%,
IHSG −14.9%.

### What it shows

1. **First profitable configuration using the screener's own entry.**
   `exit_ma50+trend` — live BUY entry + Med/Long trend filter + exit on a
   daily close below MA50 — reaches **PF 1.32, +1.24%/trade over 482 trades**,
   well clear of the 0.4% round-trip cost.
2. **The entry adds real selectivity.** It beats the entry-less MA50-any
   strategy (PF 1.25, +0.77%) with 73% fewer trades — the entry was never the
   flaw; the old exits were strangling it.
3. **Clean monotonic gradient** — exit MA10 loses → MA20 marginal → MA50 wins,
   and the trend filter improves every profitable tier. Structure, not noise.

### Caveats

- **Data snooping:** this configuration emerged from four sequential
  experiments on the *same 2-year window* — that window is now effectively
  in-sample, so PF 1.32 is optimistic. **Must be validated on unseen data**
  (e.g. a 5y run with the config frozen, judged on the earlier years) before
  any of it is wired into the live bot.
- Survivorship bias, additive PnL, single market regime — all prior caveats
  apply. Win rate ~20% means long losing streaks; drawdown tolerance required.

### Reproduce

```bash
python -m backtest.side_v2 --limit 200 --period 2y
```

---

## Out-of-sample validation — v2 does NOT hold up (final)

The frozen v2 config (live BUY + Med/Long trend filter + exit on close < MA50)
was run over ~5 years ([side_v2_oos.py](side_v2_oos.py)), splitting trades at
**2024-07-24** — the start of the 2-year window every experiment above had
seen. Entries before the split are genuinely unseen data.

**Full universe — 111 tickers, one frozen config, no tuning:**

| Window | Trades | Win% | Exp/trade | PF | Strategy vs equal-weight B&H |
|---|---|---|---|---|---|
| **Out-of-sample** (2022-08 → 2024-12) | 790 | 22.5% | **+0.02%** | **1.01** | +13.5%* vs **+35.2%** |
| In-sample (2024-07 → 2026-07) | 868 | 20.2% | +1.79% | 1.46 | +1550%* vs +33.6% |

\* additive, 1 unit/trade.

### Verdict

**The edge does not validate.** PF 1.01 / +0.02% per trade on 790 unseen
trades is indistinguishable from zero edge; buy-and-hold beat it decisively in
the same window. The in-sample PF (1.32–1.46) was a product of (a) four rounds
of experimentation on the same 2-year window — data snooping — and (b) that
window containing a strong late-2025→2026 trend that trend-following exits
harvest exceptionally well.

**What survives:** the exit redesign genuinely stopped the bleeding — the
original baseline lost −0.76%/trade; the v2 config breaks even on unseen data.
The exit work turned a structural loser into a coin flip minus commissions.
That is real diagnostic progress, but **it is not a deployable edge**.

### Bottom line for the project

- **Do not wire any of these configurations into the live bot** as a
  signal-generating strategy. `config.py` and the live rules remain untouched.
- The bot remains valuable as an *analysis/screening tool* (`/ma`, `/top5`
  present structured MA context, not a proven money-making system) — the
  disclaimers already say exactly this.
- Any future strategy iteration must reserve a validation window **up front**
  (never touched during development) and only claim an edge if it survives
  there. This investigation is the template — and the cautionary tale.

---

## v3 — all five improvements combined, portfolio-level (final chapter)

The five post-mortem improvements (regime filter, whipsaw-resistant exit,
trade-less/hold-longer, momentum selection, portfolio realism) were combined in
a **portfolio simulator** ([side_v3.py](side_v3.py)): max 10 concurrent
positions, equal allocation of compounding equity, fees per trade, 5-day
re-entry cooldown. Entry = unchanged live BUY + Med/Long trend filter.

**Anti-snooping protocol, enforced:** 10 years of data; an 18-combo grid
(3 exits × 3 regimes × 2 rankings) was searched on the **dev window
(< 2022-01-01) only**; one config was frozen, then the validate (2022-01 →
2024-07) and confirm (≥ 2024-07) windows were looked at exactly once.

**Dev-window survivorship bias, measured:** equal-weight B&H of the (current-
constituent) universe returned **+1137%** in dev while IHSG returned +26%.
That gap *is* the bias — today's index members include yesterday's 10–100×
rockets. Dev numbers are therefore only comparable across combos, never
absolute. Comparative dev findings: MA100 exit dominated (fewest trades,
highest win rates), momentum ranking helped nearly everywhere, regime filters
cut drawdown. Frozen pick: **exit=MA100 · regime=JKSE>MA200 · rank=momentum**
(the index-based regime signal was chosen over breadth because breadth is
computed from the biased universe itself).

**Final one-shot result (111 tickers):**

| Window | Portfolio return | MaxDD | Fresh trades | Win% | Exp/trade |
|---|---|---|---|---|---|
| Dev (grid-searched, bias-inflated) | +377% (CAGR 33%) | −25% | 124 | 45.2% | +49.9% |
| **Validate (unseen, one look)** | +149%† | **−54%** | 90 | 31.1% | **−0.74%** |
| Confirm (contaminated) | +85% | −36% | 40 | 20.0% | +24.8% |

† **The +149% validate return is a mirage of attribution.** It is carried by
positions *entered in late 2021 (dev)* — momentum ranking had loaded coal names
just before the 2022 coal supercycle, and their gains landed in the validate
window's equity curve. The signal quality on trades actually *entered* in the
unseen window is the honest metric: **31% win, −0.74%/trade, with a −54%
drawdown**. No edge.

### Final verdict — triple-confirmed

| Test | Unseen-data result |
|---|---|
| v2 OOS (per-ticker) | +0.02%/trade over 790 trades |
| v3 validate (portfolio, all 5 improvements) | −0.74%/trade over 90 trades |
| Every profitable window, explained | regime luck (2025–26 trend), carried positions (2022 coal), or survivorship bias (10y lookback) |

**The five improvements are genuine risk-management upgrades** — 10× fewer
trades, higher dev win rate, demonstrably lower drawdown with the regime
filter — **but they cannot manufacture an entry edge that does not exist.**
The MA-proximity entry has now failed on unseen data in every configuration
tested. Strategy research on this signal family stops here; a future attempt
needs a *different entry hypothesis* (and a bias-free historical universe),
not further exit engineering.

### Reproduce

```bash
python -m backtest.side_v3 --dev                                # grid, dev only
python -m backtest.side_v3 --final ma100 jkse_ma200 momentum    # frozen one-shot
```
