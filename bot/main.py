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
from .pump import active_coins, age_seconds, fetch_coin, latest_coins, live_coins
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
    ready_at: float
    story_title: str
    match_score: int
    verdict_key: str


async def run() -> None:
    config.require_telegram()
    state = State()
    if config.PAPER_ENABLED:
        state.ensure_paper_wallet(config.PAPER_START_SOL)
        health.STATUS["paper_equity"] = round(paper.snapshot(state)["equity"], 4)
    attention = Attention()
    pending: dict[str, Pending] = {}
    last_attention = 0.0
    last_leaderboard = time.time()

    health.STATUS["ok"] = True
    health.start(config.PORT)

    async with client() as http:
        await boot_message(http)
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
                trading = await active_coins(http, limit=30)
                seen_this_loop: set[str] = set()
                for coin in fresh + streaming + trading:
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
                    if verdict.failed_gate == "first-mover":
                        log.info("Skip %s first-mover: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "farm":
                        if is_new:
                            log.info("Skip %s farm: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate in {"attention", "generic", "age", "quota", "late", "dumped"}:
                        continue
                    if verdict.failed_gate == "structure":
                        log.info("Skip %s structure: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "wait-expansion":
                        _queue_watch(pending, coin, verdict)

                due = [m for m, p in pending.items() if time.time() >= p.ready_at]
                for mint in due:
                    item = pending.pop(mint)
                    ok, why, fresh = await confirm_expansion(http, item.coin, item.coin)
                    if not ok:
                        log.info("Drop %s expansion: %s", item.coin.get("symbol"), why)
                        continue
                    v = Verdict(
                        post=True,
                        mint=mint,
                        coin=fresh,
                        story=item.coin.get("_story"),
                        match_score=item.match_score,
                        structure=item.coin.get("_structure"),
                        path=item.coin.get("_path") or "",
                    )
                    text = format_signal(v, why)
                    sent = await send(http, text, preview=True)
                    if sent:
                        story_title = ""
                        if item.coin.get("_story") is not None:
                            story_title = getattr(item.coin["_story"], "title", "") or ""
                        state.mark_posted(fresh, story_title)
                        health.STATUS["posted"] = len(state.list_posted())
                        log.info("POSTED %s — %s", fresh.get("symbol"), why)
                        if config.PAPER_ENABLED:
                            fill = paper.try_open(state, fresh, item.coin.get("_path") or "")
                            if fill:
                                snap = paper.snapshot(state)
                                health.STATUS["paper_equity"] = round(snap["equity"], 4)
                                await send(http, format_paper_fill(fill, snap), preview=True)
                    else:
                        log.error("Failed to post %s", fresh.get("symbol"))

                if config.PAPER_ENABLED:
                    await _manage_paper(http, state)

                if time.time() - last_leaderboard >= config.LEADERBOARD_SEC:
                    await _send_leaderboard(http, state, attention, health.STATUS.get("seen", 0))
                    last_leaderboard = time.time()

                health.STATUS["last_error"] = ""
            except Exception as exc:
                health.STATUS["last_error"] = str(exc)
                log.exception("Loop error: %s", exc)

            elapsed = time.time() - loop_start
            await asyncio.sleep(max(1.0, config.PUMP_POLL_SEC - elapsed))


def _queue_watch(pending: dict[str, Pending], coin: dict, verdict: Verdict) -> None:
    if coin["mint"] in pending:
        return
    story = verdict.story
    pending[coin["mint"]] = Pending(
        coin=coin,
        ready_at=time.time() + config.EXPANSION_WAIT_SEC,
        story_title=(story.title if story else ""),
        match_score=verdict.match_score,
        verdict_key=coin["mint"],
    )
    coin["_story"] = story
    coin["_match"] = verdict.match_score
    coin["_structure"] = verdict.structure
    coin["_path"] = verdict.path
    log.info(
        "Watching %s ($%s) path=%s match=%s — %s",
        coin.get("symbol"),
        coin.get("name"),
        verdict.path or "news",
        verdict.match_score,
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
