from __future__ import annotations

import html
import logging

import httpx

from . import config
from .engine import Verdict

log = logging.getLogger("runner")


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=False)


async def send(http: httpx.AsyncClient, text: str, preview: bool = False) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": not preview,
    }
    try:
        r = await http.post(url, json=payload, timeout=20.0)
        if r.status_code != 200:
            log.error("Telegram send failed %s %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as exc:
        log.error("Telegram send error: %s", exc)
        return False


def format_signal(v: Verdict, expansion: str) -> str:
    coin = v.coin
    st = v.structure
    story = v.story
    usd = coin.get("usd_market_cap") or 0
    ath = coin.get("ath_market_cap") or usd
    socials = []
    if coin.get("twitter"):
        socials.append("X")
    if coin.get("telegram"):
        socials.append("TG")
    if coin.get("website"):
        socials.append("web")

    lines = [
        "<b>PUMP.FUN RUNNER CANDIDATE</b>",
        "",
        f"<b>${_esc(coin.get('symbol'))}</b>  {_esc(coin.get('name'))}",
        f"<a href=\"{_esc(coin.get('url'))}\">{_esc(coin.get('mint'))}</a>",
        "",
        "<b>WHY THIS EXISTS</b>",
        _esc(story.title if story else "n/a"),
    ]
    if story and story.url:
        lines.append(f"<a href=\"{_esc(story.url)}\">source</a> · match {v.match_score}")
    lines += [
        "",
        "<b>WHY THIS TOKEN</b>",
        "first mint that maps to the story in our window",
        f"curve {coin.get('curve_pct')}% · MC ${_esc(f'{usd:,.0f}')} · ATH ${_esc(f'{ath:,.0f}')}",
        f"path {v.path or '—'} · replies {coin.get('reply_count')} · live {bool(coin.get('is_currently_live'))}"
        + (f" ({coin.get('num_participants')} in room)" if coin.get("num_participants") else ""),
        "",
        "<b>STRUCTURE</b>",
    ]
    if st:
        lines += [
            f"dev other tokens: {st.creator_tokens}",
            f"holders: {st.total_holders}",
            f"top holder ex-LP: {st.top_holder_pct}%",
            f"top10 ex-LP: {st.top10_pct}%",
            f"insider: {st.insider_pct}%",
            f"rugcheck score: {st.rug_score}",
        ]
        if socials:
            lines.append("listed socials: " + ", ".join(socials))
    lines += [
        "",
        "<b>EXPANSION</b>",
        _esc(expansion),
        "",
        "<b>INVALIDATE IF</b>",
        "dev or clustered wallets start selling",
        "holder count stalls while MC pumps",
        "a cleaner original appears",
        "the story stops spreading outside crypto",
        "",
        "<i>Not a trade call. Attention + structure snapshot only. Most names still die.</i>",
    ]
    return "\n".join(lines)


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.1f}k"
    return f"${value:,.0f}"


def _fmt_x(mult: float) -> str:
    if mult >= 100:
        return f"{mult:.0f}x"
    if mult >= 10:
        return f"{mult:.1f}x"
    return f"{mult:.2f}x"


def format_leaderboard(rows: list[dict], scanned: int, headlines: int) -> str:
    lines = [
        "<b>ALL-TIME LEADERBOARD</b>",
        "Best ATH multiple from our calls",
        "",
    ]
    if not rows:
        lines += [
            "No calls yet.",
            "I only post when every runner gate passes — no daily cap.",
        ]
    else:
        ranked = []
        for row in rows:
            entry = float(row.get("entry_mc") or 0)
            ath = float(row.get("ath_mc") or row.get("last_mc") or 0)
            last = float(row.get("last_mc") or 0)
            if entry <= 0:
                continue
            ranked.append({**row, "mult": ath / entry, "now_mult": last / entry if last else 0})
        ranked.sort(key=lambda r: r["mult"], reverse=True)
        for i, row in enumerate(ranked[: config.LEADERBOARD_SIZE], start=1):
            symbol = _esc(row.get("symbol") or "?")
            url = row.get("url") or ""
            name = f"${symbol}"
            if url:
                name = f"<a href=\"{_esc(url)}\">${symbol}</a>"
            lines.append(
                f"{i}. {name}  <b>{_esc(_fmt_x(row['mult']))}</b>  "
                f"ATH {_esc(_fmt_usd(row['ath_mc'] or 0))}  "
                f"now {_esc(_fmt_usd(row['last_mc'] or 0))}"
            )
        lines += [
            "",
            f"{len(rows)} call{'s' if len(rows) != 1 else ''} tracked · top {min(config.LEADERBOARD_SIZE, len(ranked))} by ATH x",
        ]
    lines += [
        "",
        f"<i>headlines {headlines} · new mints seen {scanned}</i>",
    ]
    return "\n".join(lines)


def _fmt_sol(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.3f} SOL"


def format_paper_fill(fill, snap: dict) -> str:
    pos = fill.pos
    url = pos.get("url") or ""
    name = f"${_esc(fill.symbol)}"
    if url:
        name = f"<a href=\"{_esc(url)}\">${_esc(fill.symbol)}</a>"
    pnl = snap["equity"] - snap["start"]
    if fill.side == "buy":
        lines = [
            "<b>PAPER BUY</b>  (not real SOL)",
            "",
            f"{name}  {_esc(pos.get('name') or '')}",
            f"size <b>{abs(fill.sol):.3f} SOL</b> · entry MC ${_esc(f'{fill.mc:,.0f}')}",
            f"path {_esc(pos.get('path') or '—')}",
            "",
            "<b>PLAN</b>",
            f"stop −{int((1 - config.PAPER_STOP_FRAC) * 100)}% · flatten if dead {config.PAPER_TIME_DEAD_SEC // 60}m",
            f"sell {int(config.PAPER_TP1_SELL * 100)}% at {config.PAPER_TP1_MULT:.1f}x",
            f"sell {int(config.PAPER_TP2_SELL * 100)}% at {config.PAPER_TP2_MULT:.1f}x",
            f"moonbag {int((1 - config.PAPER_TP1_SELL - config.PAPER_TP2_SELL) * 100)}% · clip half at {config.PAPER_TP3_MULT:.0f}x · trail −{int(config.PAPER_TRAIL_GIVEBACK * 100)}% off ATH",
        ]
    else:
        left = float(pos.get("remaining_frac") or 0)
        status = pos.get("status") or "open"
        lines = [
            "<b>PAPER SELL</b>  (not real SOL)",
            "",
            f"{name}  {_esc(fill.reason)}",
            f"{int(fill.frac * 100)}% of original @ <b>{fill.multiple:.2f}x</b> · {_esc(_fmt_sol(fill.sol))}",
            f"left {left * 100:.0f}% · {status}",
        ]
    lines += [
        "",
        f"wallet  cash {snap['cash']:.3f}  open {snap['unreal']:.3f}  equity <b>{snap['equity']:.3f} SOL</b>",
        f"vs start {snap['start']:.2f}  {_esc(_fmt_sol(pnl))}",
        f"{len(snap['open'])} open · next size {snap['size']:.3f} SOL",
    ]
    return "\n".join(lines)


def format_paper_book(snap: dict) -> str:
    pnl = snap["equity"] - snap["start"]
    lines = [
        "<b>PAPER BOOK</b>  (not real SOL)",
        f"equity <b>{snap['equity']:.3f} SOL</b>  {_esc(_fmt_sol(pnl))} from {snap['start']:.2f}",
        f"cash {snap['cash']:.3f} · in positions {snap['unreal']:.3f}",
        f"next buy {snap['size']:.3f} SOL · max {config.PAPER_MAX_OPEN} open",
        "",
    ]
    if not snap["open"]:
        lines.append("No open paper positions.")
    else:
        for p in snap["open"]:
            entry = float(p.get("entry_mc") or 0)
            last = float(p.get("last_mc") or 0)
            mult = (last / entry) if entry else 0
            url = p.get("url") or ""
            tag = f"${_esc(p.get('symbol') or '?')}"
            if url:
                tag = f"<a href=\"{_esc(url)}\">{tag}</a>"
            lines.append(
                f"• {tag}  {mult:.2f}x  left {float(p.get('remaining_frac') or 0)*100:.0f}%  "
                f"{_esc(p.get('status') or '')}  mark {mark_sol(p):.3f}"
            )
    lines.append(f"\n<i>{snap['closed_n']} closed paper trades</i>")
    return "\n".join(lines)


def mark_sol(pos: dict) -> float:
    entry = float(pos.get("entry_mc") or 0)
    last = float(pos.get("last_mc") or 0)
    qty = float(pos.get("remaining_qty_sol") or 0)
    if entry <= 0:
        return 0.0
    return qty * (last / entry)


async def boot_message(http: httpx.AsyncClient) -> None:
    text = (
        "<b>Pump.fun runner scanner online</b>\n"
        "Calls: rare culture hit or a real live crowd. Few names. 3/day max.\n"
        "USWS-class painted books stay banned. Not every BOOST coin.\n"
        f"Leaderboard every {config.LEADERBOARD_SEC // 3600}h · paper balance every {config.PAPER_REPORT_SEC // 3600}h"
    )
    if config.PAPER_ENABLED:
        text += (
            f"\n\n<b>Paper book reset: {config.PAPER_START_SOL:.2f} SOL</b> — no real fills.\n"
            f"Size {config.PAPER_SIZE_FRAC * 100:.1f}% of equity "
            f"({config.PAPER_SIZE_MIN:.2f}–{config.PAPER_SIZE_MAX:.2f}), "
            f"max {config.PAPER_MAX_OPEN} open."
        )
    ok = await send(http, text)
    if ok:
        log.info("Boot message sent to %s", config.TELEGRAM_CHAT_ID)
    else:
        log.error("Boot message failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
