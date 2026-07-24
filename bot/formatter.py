"""``ScreenResult`` → Telegram message text (§5.2 / §5.3).

Uses HTML parse mode (simpler, safer escaping than MarkdownV2). All dynamic
text is passed through ``html.escape``. Output mirrors the reference bot's
layout: MA stack, three-tier trend, verdict, entry/exit, volume, bottom line.
"""

from __future__ import annotations

import html
from typing import Optional

from config import EXIT_MA_PERIOD
from screener.result import DataQuality, ScreenResult, Signal

DISCLAIMER = "⚠️ Delayed data · informational only · not financial advice."


def rupiah(v: Optional[float]) -> str:
    """Format an IDX price with dot thousands separators (e.g. 6.250)."""
    if v is None:
        return "—"
    return f"{int(round(v)):,}".replace(",", ".")


def _quality_note(res: ScreenResult) -> Optional[str]:
    notes = {
        DataQuality.NO_DATA: "No price data — ticker unknown, delisted, or the "
        "data source is down.",
        DataQuality.STALE: "⚠️ Last bar is several days old — the stock may be "
        "suspended or illiquid. Numbers may be outdated.",
        DataQuality.SUSPENDED: "⚠️ Flat price / zero volume detected — looks "
        "suspended. Treat with caution.",
        DataQuality.INSUFFICIENT_DATA: "ℹ️ Newly listed / short history — MA200 "
        "unavailable, so this is a partial analysis (excluded from /top5).",
    }
    return notes.get(res.quality)


def _bottom_line(res: ScreenResult) -> str:
    """2–3 sentence actionable summary keyed off the signal."""
    if res.regime_gated:
        return (
            f"{res.ticker} broke out above MA50 — but the market itself is "
            f"risk-off, so conservative mode stays out. Revisit when the index "
            f"turns risk-on (or use normal mode to take it anyway)."
        )
    if res.signal is Signal.BUY:
        return (
            f"{res.ticker} just broke out — today's close crossed above MA50. "
            f"Trend entry at market ({rupiah(res.buy_at)}). No profit target: "
            f"ride it and exit only if a daily close prints below "
            f"{rupiah(res.stop_loss)} (MA50)."
        )
    if res.signal is Signal.SELL:
        return (
            f"{res.ticker} closed below its MA50 after holding above it — the "
            f"structure broke. Under the exit rule this is a sell; don't add "
            f"here."
        )
    if res.signal is Signal.AVOID:
        return (
            f"{res.ticker} is below every MA — a clear downtrend. "
            f"Stay out; the earliest signal to watch is a daily close back "
            f"above MA50."
        )
    # HOLD — two flavors: in trend (above MA50) vs waiting for the trigger.
    ma50 = next((m for m in res.ma if m.period == EXIT_MA_PERIOD), None)
    if ma50 is not None and ma50.above:
        return (
            f"{res.ticker} is in an uptrend above MA50. If you hold it, stay "
            f"in until a daily close below {rupiah(res.stop_loss)}. No new "
            f"entry here — wait for the next fresh breakout."
        )
    return (
        f"{res.ticker} is below MA50 — no position. The entry trigger is a "
        f"daily close back above {rupiah(res.buy_at)}."
    )


def format_ma(res: ScreenResult) -> str:
    """Full /ma analysis text (may exceed a photo caption; handler decides)."""
    t = html.escape(res.ticker)

    if res.quality is DataQuality.NO_DATA:
        return f"❓ <b>{t}</b>\n{_quality_note(res)}"

    sign = "🟢" if res.change_pct >= 0 else "🔴"
    lines: list[str] = []
    lines.append(f"📊 <b>MA STACK: {t}</b>")
    lines.append(f"💰 <b>{rupiah(res.price)}</b>  {sign} {res.change_pct:+.2f}%")

    note = _quality_note(res)
    if note:
        lines.append(html.escape(note))
    lines.append("")

    # Price vs each MA
    for m in res.ma:
        mark = "✅" if m.above else "❌"
        lines.append(
            f"{mark} MA{m.period:<3} {rupiah(m.value):>8}  "
            f"<code>{m.distance_pct * 100:+5.1f}%</code>"
        )
    lines.append(f"\n📈 Above <b>{res.ma_summary}</b> moving averages")
    lines.append("")

    # Three-tier trend
    tier_emoji = {"Short": "⚡", "Medium": "📊", "Long": "⛰"}
    for tr in res.trends:
        dot = "🟢" if tr.bullish else "⚪"
        state = "Bullish" if tr.bullish else "Not yet"
        lines.append(f"{tier_emoji.get(tr.label, '•')} {tr.label:<7} {dot} {state}")
    lines.append("")

    # Verdict
    lines.append(f"🎯 <b>{html.escape(res.verdict)}</b>")
    if res.is_tradeable and res.signal is not Signal.AVOID:
        lines.append(f"🔢 Confidence: <b>{res.score:.0f}/100</b>")
    lines.append("")

    # Trade plan (cross_pure: entries are breakout events; the exit is a
    # condition, not a price target)
    lines.append("💵 <b>TRADE PLAN</b>")
    if res.regime_gated:
        entry_line = "   Entry      : 🛡 gated — breakout, but market risk-off"
    elif res.signal is Signal.BUY:
        entry_line = f"   Entry      : <b>{rupiah(res.buy_at)}</b> (breakout — at market)"
    elif res.buy_at is not None:
        entry_line = f"   Entry trig : daily close &gt; <b>{rupiah(res.buy_at)}</b> (MA50)"
    else:
        entry_line = "   Entry      : — (in trend; wait for the next fresh cross)"
    lines.append(entry_line)
    lines.append(f"   Resistance : {rupiah(res.sell_at)} <i>(info only)</i>")
    lines.append(
        f"   Exit       : daily close &lt; <b>{rupiah(res.stop_loss)}</b> (MA50)"
    )
    lines.append("")

    # Volume
    health = "healthy" if res.rvol >= 1 else "below average"
    lines.append("📦 <b>Volume</b>")
    lines.append(
        f"   Buy {res.buy_pressure_pct:.0f}% / Sell {res.sell_pressure_pct:.0f}%  ·  "
        f"RVOL {res.rvol:.2f}x ({health})"
    )
    lines.append("")

    # Bottom line
    lines.append(f"🗣 <b>Bottom line</b>\n{html.escape(_bottom_line(res))}")
    lines.append("")
    lines.append(f"<i>{html.escape(DISCLAIMER)}</i>")
    return "\n".join(lines)


def format_ma_caption(res: ScreenResult) -> str:
    """Compact version for a photo caption (Telegram limit 1024 chars)."""
    t = html.escape(res.ticker)
    sign = "🟢" if res.change_pct >= 0 else "🔴"
    reason = html.escape(res.reasons[0]) if res.reasons else ""
    return (
        f"📊 <b>{t}</b>  💰 {rupiah(res.price)} {sign} {res.change_pct:+.2f}%\n"
        f"📈 Above {res.ma_summary} MAs  ·  🎯 {html.escape(res.verdict)}\n"
        f"💵 Buy {rupiah(res.buy_at)} · Res {rupiah(res.sell_at)} · "
        f"Exit&lt;{rupiah(res.stop_loss)}\n"
        f"{reason}"
    )


def format_top5(rows: list, scan_date: str) -> str:
    """Render the /top5 list from cached scan rows (sqlite3.Row-like)."""
    if not rows:
        return (
            "🤔 <b>No BUY candidates right now.</b>\n"
            "The latest scan found no stocks near MA support with a bullish "
            "stack. Check back after the next scan."
        )

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["🏆 <b>TOP PICKS</b> — highest-confidence BUY setups\n"]
    for i, r in enumerate(rows):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        lines.append(
            f"{medal} <b>{html.escape(r['ticker'])}</b>  "
            f"{rupiah(r['price'])}  ·  <b>{r['score']:.0f}/100</b>\n"
            f"    💵 Buy {rupiah(r['buy_at'])} · Res {rupiah(r['sell_at'])} · "
            f"Exit&lt;{rupiah(r['stop_loss'])}\n"
            f"    <i>{html.escape(r['reason'] or '')}</i>"
        )
    lines.append(f"\n🕒 Scan: {html.escape(scan_date)}")
    lines.append(f"<i>{html.escape(DISCLAIMER)}</i>")
    return "\n".join(lines)


def format_scan_count(rows: list) -> int:
    return len(rows)
