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
        f"replies {coin.get('reply_count')} · live {bool(coin.get('is_currently_live'))}",
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


async def boot_message(http: httpx.AsyncClient) -> None:
    text = (
        "<b>Pump.fun runner scanner online</b>\n"
        "Calls only: tight news map, or first mint of a ticker that is being copied.\n"
        "No daily cap. Late $80k+ first looks are skipped.\n"
        f"Leaderboard every {config.LEADERBOARD_SEC // 3600}h"
    )
    ok = await send(http, text)
    if ok:
        log.info("Boot message sent to %s", config.TELEGRAM_CHAT_ID)
    else:
        log.error("Boot message failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
