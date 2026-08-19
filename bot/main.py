from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

from . import config, health, paper
from .attention import Attention
from .engine import Verdict, confirm_expansion, evaluate_new
from .httputil import client
from .pump import (
    active_coins,
    age_seconds,
    fetch_coin,
    graduated_coins,
    latest_coins,
    live_coins,
)
from .state import State
from .telegram import (
    boot_message,
    format_leaderboard,
    format_paper_book,
    format_paper_fill,
    format_signal,
    send,
)

# Railway paints stderr red. Keep INFO on stdout so the dashboard stays white.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("runner")


@dataclass
class Pending:
    coin: dict
    first_look: dict
    first_look_at: float
    ready_at: float
    story: object
    match_score: int
    structure: object
    path: str


async def run() -> None:
    config.require_telegram()
    state = State()
    if config.PAPER_ENABLED:
        wallet = state.ensure_paper_wallet(config.PAPER_START_SOL)
        if (wallet.get("book_id") or "") != config.PAPER_BOOK_ID:
            log.info(
                "Resetting paper book %s → %s at %.3f SOL",
                wallet.get("book_id"),
                config.PAPER_BOOK_ID,
                config.PAPER_START_SOL,
            )
            state.reset_paper_book(
                config.PAPER_START_SOL,
                config.PAPER_BOOK_ID,
                reason=f"reset to {config.PAPER_BOOK_ID}",
            )
        health.STATUS["paper_equity"] = round(paper.snapshot(state)["equity"], 4)
    attention = Attention()
    pending: dict[str, Pending] = {}
    skip_logged: set[str] = set()
    last_attention = 0.0
    last_leaderboard = time.time()
    last_paper_report = time.time()
    last_feed_log = 0.0

    health.STATUS["ok"] = True
    health.start(config.PORT)

    async with client() as http:
        await boot_message(http, signals_today=state.signals_today())
        try:
            await attention.refresh(http)
            last_attention = time.time()
            health.STATUS["attention"] = len(attention.stories)
        except Exception as exc:
            log.warning("Initial attention refresh failed: %s", exc)

        log.info(
            "Scanner loop started. poll=%ss wait=%ss no daily cap · board every %sh",
            config.PUMP_POLL_SEC,
            config.EXPANSION_WAIT_SEC,
            config.LEADERBOARD_SEC // 3600,
        )

        while True:
            loop_start = time.time()
            try:
                if time.time() - last_attention >= config.ATTENTION_POLL_SEC:
                    await attention.refresh(http)
                    last_attention = time.time()
                    health.STATUS["attention"] = len(attention.stories)

                fresh = await latest_coins(http, limit=40)
                streaming = await live_coins(http, limit=20)
                trading = await active_coins(http, limit=80)
                graduates = await graduated_coins(http, limit=25)
                health.STATUS["feeds"] = {
                    "latest": len(fresh),
                    "live": len(streaming),
                    "last_trade": len(trading),
                    "graduated": len(graduates),
                }
                health.STATUS["quota"] = state.signals_today()
                health.STATUS["watches"] = len(pending)
                if time.time() - last_feed_log >= 60:
                    log.info(
                        "feeds latest=%s live=%s last_trade=%s graduated=%s "
                        "watches=%s quota=%s/%s",
                        len(fresh),
                        len(streaming),
                        len(trading),
                        len(graduates),
                        len(pending),
                        state.signals_today(),
                        config.MAX_SIGNALS_PER_DAY,
                    )
                    last_feed_log = time.time()
                seen_this_loop: set[str] = set()
                for coin in fresh + streaming + trading + graduates:
                    mint = coin.get("mint")
                    if not mint or mint in seen_this_loop:
                        continue
                    seen_this_loop.add(mint)
                    is_new = state.mark_seen(coin)
                    if is_new:
                        health.STATUS["seen"] = health.STATUS.get("seen", 0) + 1
                    if state.already_posted(mint) or mint in pending:
                        continue
                    # Birth-shot is not enough: homepage runners often print 1–6h later.
                    if not is_new and not coin.get("is_currently_live"):
                        age = age_seconds(coin, time.time())
                        usd = float(coin.get("usd_market_cap") or 0)
                        if age > config.MAX_ACTIVE_AGE_SEC or usd > config.MAX_FIRST_LOOK_MC:
                            continue

                    verdict = await evaluate_new(http, attention, state, coin)
                    usd = float(coin.get("usd_market_cap") or 0)
                    age = age_seconds(coin, time.time())
                    notable = usd >= config.MIN_TAPE_MC and age <= config.MAX_ACTIVE_AGE_SEC
                    if verdict.failed_gate == "first-mover":
                        log.info("Skip %s first-mover: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "farm":
                        if is_new or notable:
                            _log_skip_once(
                                skip_logged,
                                mint,
                                coin.get("symbol"),
                                "farm",
                                verdict.fail_reason,
                            )
                        continue
                    if verdict.failed_gate == "quota":
                        _log_skip_once(
                            skip_logged,
                            "quota",
                            coin.get("symbol"),
                            "quota",
                            verdict.fail_reason,
                        )
                        continue
                    if verdict.failed_gate in {"attention", "age", "late", "dumped"}:
                        if notable:
                            _log_skip_once(
                                skip_logged,
                                mint,
                                coin.get("symbol"),
                                verdict.failed_gate,
                                verdict.fail_reason,
                            )
                        continue
                    if verdict.failed_gate == "structure":
                        log.info("Skip %s structure: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "wait-expansion":
                        _queue_watch(pending, coin, verdict)

                due = [m for m, p in pending.items() if time.time() >= p.ready_at]
                for mint in due:
                    item = pending[mint]
                    status, why, fresh_coin = await confirm_expansion(
                        http, item.coin, item.first_look
                    )
                    if status == "wait":
                        held = time.time() - item.first_look_at
                        if held >= config.EXPANSION_HOLD_SEC:
                            pending.pop(mint, None)
                            log.info(
                                "Drop %s expansion: no +20%% in %.0fm (%s)",
                                item.coin.get("symbol"),
                                held / 60,
                                why,
                            )
                            continue
                        item.coin = fresh_coin
                        item.ready_at = time.time() + config.EXPANSION_RECHECK_SEC
                        continue
                    pending.pop(mint, None)
                    if status != "post":
                        log.info("Drop %s expansion: %s", item.coin.get("symbol"), why)
                        continue
                    v = Verdict(
                        post=True,
                        mint=mint,
                        coin=fresh_coin,
                        story=item.story,
                        match_score=item.match_score,
                        structure=item.structure,
                        path=item.path or "",
                    )
                    text = format_signal(v, why)
                    sent = await send(http, text, preview=True)
                    if sent:
                        story_title = getattr(item.story, "title", "") or ""
                        state.mark_posted(fresh_coin, story_title)
                        health.STATUS["posted"] = len(state.list_posted())
                        log.info("POSTED %s — %s", fresh_coin.get("symbol"), why)
                        if config.PAPER_ENABLED:
                            fill = paper.try_open(state, fresh_coin, item.path or "")
                            if fill:
                                snap = paper.snapshot(state)
                                health.STATUS["paper_equity"] = round(snap["equity"], 4)
                                await send(http, format_paper_fill(fill, snap), preview=True)
                    else:
                        log.error("Failed to post %s", fresh_coin.get("symbol"))

                if config.PAPER_ENABLED:
                    await _manage_paper(http, state)
                    if time.time() - last_paper_report >= config.PAPER_REPORT_SEC:
                        await _send_paper_report(http, state)
                        last_paper_report = time.time()

                if time.time() - last_leaderboard >= config.LEADERBOARD_SEC:
                    await _send_leaderboard(http, state, attention, health.STATUS.get("seen", 0))
                    last_leaderboard = time.time()

                health.STATUS["last_error"] = ""
            except Exception as exc:
                health.STATUS["last_error"] = str(exc)
                log.exception("Loop error: %s", exc)

            elapsed = time.time() - loop_start
            await asyncio.sleep(max(1.0, config.PUMP_POLL_SEC - elapsed))


def _log_skip_once(
    seen: set[str], mint: str, symbol: object, gate: str, reason: str
) -> None:
    key = f"{gate}:{mint}"
    if key in seen:
        return
    seen.add(key)
    log.info("Skip %s %s: %s", symbol, gate, reason)


def _queue_watch(pending: dict[str, Pending], coin: dict, verdict: Verdict) -> None:
    if coin["mint"] in pending:
        return
    story = verdict.story
    now = time.time()
    pending[coin["mint"]] = Pending(
        coin=coin,
        first_look=dict(coin),
        first_look_at=now,
        ready_at=now + config.EXPANSION_WAIT_SEC,
        story=story,
        match_score=verdict.match_score,
        structure=verdict.structure,
        path=verdict.path or "",
    )
    log.info(
        "Watching %s ($%s) path=%s first=$%s — %s",
        coin.get("symbol"),
        f"{float(coin.get('usd_market_cap') or 0):,.0f}",
        verdict.path or "tape",
        f"{float(coin.get('usd_market_cap') or 0):,.0f}",
        (story.title[:80] if story else ""),
    )


async def _send_leaderboard(http, state: State, attention, scanned: int) -> None:
    rows = state.list_posted()
    for row in rows:
        mint = row.get("mint")
        if not mint:
            continue
        coin = await fetch_coin(http, mint)
        if not coin:
            continue
        last = float(coin.get("usd_market_cap") or 0)
        ath = float(coin.get("ath_market_cap") or last)
        prev_ath = float(row.get("ath_mc") or 0)
        state.update_quotes(mint, last, max(ath, prev_ath, last))
    rows = state.list_posted()
    text = format_leaderboard(rows, scanned, len(attention.stories))
    ok = await send(http, text)
    if ok:
        log.info("Leaderboard sent (%s tracked)", len(rows))
    else:
        log.error("Leaderboard send failed")
    if config.PAPER_ENABLED:
        snap = paper.snapshot(state)
        health.STATUS["paper_equity"] = round(snap["equity"], 4)
        book = format_paper_book(snap)
        sent_book = await send(http, book)
        if sent_book:
            log.info("Paper book sent (equity %.3f)", snap["equity"])


async def _refresh_paper_marks(http, state: State) -> None:
    for pos in state.open_paper_positions():
        mint = pos.get("mint")
        if not mint:
            continue
        coin = await fetch_coin(http, mint)
        if not coin:
            continue
        last = float(coin.get("usd_market_cap") or 0)
        if last <= 0:
            continue
        ath = max(float(pos.get("ath_mc") or 0), float(coin.get("ath_market_cap") or 0), last)
        pos["last_mc"] = last
        pos["ath_mc"] = ath
        state.upsert_paper_position(pos)


async def _send_paper_report(http, state: State) -> None:
    await _refresh_paper_marks(http, state)
    snap = paper.snapshot(state)
    health.STATUS["paper_equity"] = round(snap["equity"], 4)
    ok = await send(http, format_paper_book(snap))
    if ok:
        log.info("Paper balance report sent (equity %.3f)", snap["equity"])
    else:
        log.error("Paper balance report failed")


async def _manage_paper(http, state: State) -> None:
    for pos in state.open_paper_positions():
        mint = pos.get("mint")
        if not mint:
            continue
        coin = await fetch_coin(http, mint)
        if not coin:
            continue
        fills = paper.on_tick(state, pos, coin)
        if not fills:
            continue
        snap = paper.snapshot(state)
        health.STATUS["paper_equity"] = round(snap["equity"], 4)
        for fill in fills:
            await send(http, format_paper_fill(fill, snap), preview=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
