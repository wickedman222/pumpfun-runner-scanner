from __future__ import annotations

import html
import logging

import httpx

from . import config
from .engine import Verdict

log = logging.getLogger("runner")


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=False)


def _why_token(path: str) -> str:
    if path == "live":
        return "live crowd with a real book"
    if path == "wallet":
        return "wallets that sat in recent held runners are already in this book"
    return "spotted on-curve, then bought only after it held and expanded — not the graduation fill"


async def send(http: httpx.AsyncClient, text: str, preview: bool = False) -> bool:
    del preview  # always off — keep the URL, never the embed
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
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
        _esc(_why_token(v.path)),
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
        "the book dies after entry",
        "",
        "<i>Not a trade call. Tape trigger + structure only. Most still die.</i>",
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


def _short_wallet(w: str) -> str:
    w = w or ""
    if len(w) > 10:
        return f"{w[:4]}…{w[-4:]}"
    return w


def format_early_boot(snap: dict | None = None, watches: list | None = None) -> str:
    if snap is None and not watches:
        return "\n".join(
            [
                "<b>wallet gather</b>",
                "paper off. mining launch buyers on pump.fun top-runners (the website list).",
                "a later dump of that bag counts as PnL.",
                "",
                f"filters: site runners first · ATH ≥ ${_esc(f'{config.EARLY_MIN_ATH:,.0f}')} · first {config.EARLY_MAX_RANK} unique curve buyers · skip farms",
                "<i>list dumps here as it fills. no paper until the list is real.</i>",
            ]
        )
    eq = float((snap or {}).get("equity") or config.PAPER_START_SOL)
    lines = [
        "<b>early-copy live</b>",
        "gather keeps mining launch buyers. paper copies the current top list (new buys only).",
        f"equity <b>{eq:.3f} SOL</b> · {config.PAPER_SIZE_FIXED:.1f} SOL/trade · need {config.COPY_MIN_ALPHAS} wallets · max {config.PAPER_MAX_OPEN} open",
        "",
        "<b>copying now</b>",
    ]
    if not watches:
        lines.append("waiting on ranked wallets.")
    else:
        for a in watches:
            lines.append(f"• {_esc(a.name)}  {_esc(a.why)}")
            lines.append(f"  <code>{_esc(a.address)}</code>")
    lines.append(
        "\n<i>watchlist refreshes as gather finds better wallets. no historical copy.</i>"
    )
    return "\n".join(lines)


def format_early_board(board: dict) -> str:
    lines = [
        "<b>early-buyer list</b>",
        f"{int(board.get('wallets') or 0)} wallets · {int(board.get('mints') or 0)} runners · "
        f"{int(board.get('hits') or 0)} hits · {int(board.get('sold') or 0)} sold into the run",
        "",
    ]
    top = board.get("top") or []
    if not top:
        lines.append("still mining genesis buyers. nothing ranked yet.")
        return "\n".join(lines)
    for i, row in enumerate(top[:15], start=1):
        w = row.get("wallet") or ""
        link = (
            f"<a href=\"https://solscan.io/account/{_esc(w)}\">{_esc(_short_wallet(w))}</a>"
        )
        pnl = float(row.get("pnl") or 0)
        lines.append(
            f"{i}. {link}  <b>{int(row.get('n') or 0)} runs</b> · "
            f"{int(row.get('sold_n') or 0)} sold · avg rank {float(row.get('avg_rank') or 0):.0f} · "
            f"{_esc(_fmt_sol(pnl))}"
        )
        bits = []
        for run in row.get("runs") or []:
            sym = (run.get("symbol") or "?").strip() or "?"
            tag = "sold" if int(run.get("sold") or 0) else "held"
            ath = float(run.get("ath_mc") or 0)
            bits.append(
                f"${_esc(sym)} r{int(run.get('buy_rank') or 0)} {tag} ATH {_esc(_fmt_usd(ath))}"
            )
        if bits:
            lines.append("   " + " · ".join(bits[:4]))
    lines.append(
        "\n<i>rank 1 = first unique curve buy. sold = they dumped that runner. paper copies the top of this list live.</i>"
    )
    return "\n".join(lines)


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
            "flatten when the alpha sells",
            f"clip {int(config.PAPER_TP1_SELL * 100)}% at {config.PAPER_TP1_MULT:.1f}x / {int(config.PAPER_TP2_SELL * 100)}% at {config.PAPER_TP2_MULT:.1f}x if it rips first",
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


def _wallet_x(ath: float) -> str:
    if ath <= 0:
        return "—"
    x = ath / 10_000.0
    if x >= 100:
        return f"{x:.0f}x"
    if x >= 10:
        return f"{x:.1f}x"
    return f"{x:.2f}x"


def format_wallet_follow(rep: dict) -> str:
    lines = [
        "<b>WALLET FOLLOW</b>",
        f"{rep.get('wallets') or 0} wallets · {rep.get('mints') or 0} runner coins",
        "x is ATH vs a $10k book",
        "",
    ]
    top = rep.get("top") or []
    if not top:
        lines.append("Still collecting. No wallets scored yet.")
        return "\n".join(lines)
    for i, row in enumerate(top, start=1):
        w = row.get("wallet") or ""
        short = f"{w[:4]}…{w[-4:]}" if len(w) > 10 else w
        link = f"<a href=\"https://solscan.io/account/{_esc(w)}\">{_esc(short)}</a>"
        lines.append(f"{i}. {link}  <b>{int(row.get('n') or 0)} runners</b>")
        bits = []
        for run in row.get("runs") or []:
            sym = (run.get("symbol") or "?").strip() or "?"
            ath = float(run.get("ath_mc") or 0)
            bits.append(f"${_esc(sym)} {_esc(_wallet_x(ath))}")
        if bits:
            lines.append("   " + " · ".join(bits))
    coins = rep.get("coins") or []
    if coins:
        lines += ["", "<b>Harvested runners</b>"]
        for c in coins[:6]:
            sym = (c.get("symbol") or "?").strip() or "?"
            ath = float(c.get("ath_mc") or 0)
            n = int(c.get("wallets") or 0)
            lines.append(f"${_esc(sym)}  {_esc(_wallet_x(ath))}  {n} wallets")
    lines.append(
        "\n<i>Snipers = early curve buyers on real runs. Buy when 2 show up, or 1 plus a live room / held expansion.</i>"
    )
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


def format_copy_boot(snap: dict | None, alphas: list) -> str:
    eq = float((snap or {}).get("equity") or 2.0)
    lines = [
        "<b>copy paper</b>",
        f"equity <b>{eq:.3f} SOL</b> · start {config.PAPER_START_SOL:.3f} · {config.PAPER_SIZE_FIXED:.1f} SOL/trade · copy their exit",
        "",
        "<b>watchlist</b>",
    ]
    for a in alphas:
        tag = "COPY" if a.copy else "OBS"
        wr = f"{a.wr*100:.0f}% WR" if a.wr else "n/a WR"
        lines.append(f"• {tag} {_esc(a.name)}  {wr}")
        lines.append(f"  <code>{_esc(a.address)}</code>")
    lines.append("\n<i>No real SOL. First run only marks wallet cursors — no historical copy.</i>")
    return "\n".join(lines)


def format_copy_hit(hit, snap: dict) -> str:
    coin = hit.coin
    usd = float(coin.get("usd_market_cap") or 0)
    lines = [
        "<b>COPY BUY</b>  (paper)",
        f"${_esc(coin.get('symbol'))}  {_esc(coin.get('name') or '')}",
        f"<a href=\"{_esc(coin.get('url') or '')}\">{_esc(coin.get('mint') or '')}</a>",
        "",
        _esc(hit.thesis),
        f"size <b>{hit.size_sol:.3f} SOL</b> ({hit.frac*100:.1f}% eq) · MC ${_esc(f'{usd:,.0f}')}",
        f"invalidate: {_esc(hit.invalidation)}",
        "",
        f"equity <b>{snap['equity']:.3f} SOL</b>  cash {snap['cash']:.3f}  open {len(snap['open'])}",
    ]
    return "\n".join(lines)


def format_copy_exit(ex, snap: dict) -> str:
    coin = ex.coin
    usd = float(coin.get("usd_market_cap") or 0)
    lines = [
        "<b>COPY EXIT</b>  (paper)",
        f"${_esc(coin.get('symbol'))}  {_esc(ex.reason)}",
        f"MC ${_esc(f'{usd:,.0f}')} · {_esc(ex.alpha.name)} sold",
        "",
        f"equity <b>{snap['equity']:.3f} SOL</b>  cash {snap['cash']:.3f}  open {len(snap['open'])}",
    ]
    return "\n".join(lines)


def format_copy_session(snap: dict, alphas: list, hours: int = 6) -> str:
    pnl = snap["equity"] - snap["start"]
    lines = [
        f"<b>{hours}h copy book</b>",
        f"equity <b>{snap['equity']:.3f} SOL</b>  {_esc(_fmt_sol(pnl))} from {snap['start']:.2f}",
        f"cash {snap['cash']:.3f} · open {len(snap['open'])} · closed {snap['closed_n']}",
        "",
    ]
    if snap["open"]:
        lines.append("<b>open</b>")
        for p in snap["open"]:
            entry = float(p.get("entry_mc") or 0)
            last = float(p.get("last_mc") or 0)
            mult = (last / entry) if entry else 0
            lines.append(
                f"• ${_esc(p.get('symbol') or '?')}  {mult:.2f}x  "
                f"{float(p.get('remaining_frac') or 0)*100:.0f}%  {_esc(p.get('path') or '')}"
            )
    else:
        lines.append("no open paper")
    lines += ["", "<b>book</b>"]
    for a in alphas:
        tag = "C" if a.copy else "O"
        lines.append(f"{tag} {_esc(a.name)}")
    return "\n".join(lines)


def format_gather(rep: dict, hours: int = 6) -> str:
    """One dump of what we actually saw. No boot/candidate/paper fluff."""

    def _x(num: float, den: float) -> str:
        if den <= 0 or num <= 0:
            return "—"
        return _fmt_x(num / den)

    def _name(row: dict) -> str:
        symbol = _esc(row.get("symbol") or "?")
        url = row.get("url") or ""
        if url:
            return f"<a href=\"{_esc(url)}\">${symbol}</a>"
        mint = row.get("mint") or ""
        if mint:
            return f"<a href=\"https://pump.fun/coin/{_esc(mint)}\">${symbol}</a>"
        return f"${symbol}"

    spots = rep.get("spots") or []
    armed = rep.get("armed") or []
    lines = [
        f"<b>{hours}h gather</b>",
        f"spots {len(spots)} · armed {len(armed)} · skipped {rep.get('skip_n') or 0} · farm {rep.get('farm_n') or 0}",
        "",
    ]
    if spots:
        lines.append("<b>spots</b>  arm → now (ATH)")
        for row in spots[:15]:
            entry = float(row.get("entry_mc") or 0)
            last = float(row.get("last_mc") or 0)
            ath = float(row.get("ath_mc") or last)
            story = (row.get("story") or "").strip()
            bit = f"  {_esc(story)}" if story else ""
            lines.append(
                f"• {_name(row)}  {_esc(_fmt_usd(entry))} → {_esc(_fmt_usd(last))}  "
                f"<b>{_esc(_x(ath, entry))}</b> ATH{_esc(bit)}"
            )
    else:
        lines.append("no expansion spots this window")
    live_armed = [r for r in armed if float(r.get("last_mc") or 0) > 0]
    if live_armed:
        lines += ["", "<b>armed, no expansion yet</b>"]
        for row in live_armed[:12]:
            arm = float(row.get("armed_mc") or 0)
            last = float(row.get("last_mc") or 0)
            ath = float(row.get("ath_mc") or last)
            lines.append(
                f"• {_name(row)}  {_esc(_fmt_usd(arm))} → {_esc(_fmt_usd(last))}  "
                f"{_esc(_x(last, arm))} now / {_esc(_x(ath, arm))} ATH"
            )
    return "\n".join(lines)
