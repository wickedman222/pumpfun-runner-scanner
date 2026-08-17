from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

from . import config, health
from .attention import Attention
from .engine import Verdict, confirm_expansion, evaluate_new
from .httputil import client
from .pump import fetch_coin, latest_coins
from .state import State
from .telegram import boot_message, format_leaderboard, format_signal, send

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

                coins = await latest_coins(http, limit=40)
                for coin in coins:
                    if not coin.get("mint"):
                        continue
                    is_new = state.mark_seen(coin)
                    health.STATUS["seen"] = health.STATUS.get("seen", 0) + (1 if is_new else 0)
                    if not is_new:
                        continue
                    if state.already_posted(coin["mint"]):
                        continue
                    if coin["mint"] in pending:
                        continue

                    verdict = await evaluate_new(http, attention, state, coin)
                    if verdict.failed_gate in {"attention", "generic", "age", "quota"}:
                        continue
                    if verdict.failed_gate == "first-mover":
                        log.info("Skip %s first-mover: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "structure":
                        log.info("Skip %s structure: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "wait-expansion" and verdict.story:
                        pending[coin["mint"]] = Pending(
                            coin=coin,
                            ready_at=time.time() + config.EXPANSION_WAIT_SEC,
                            story_title=verdict.story.title,
                            match_score=verdict.match_score,
                            verdict_key=coin["mint"],
                        )
                        log.info(
                            "Watching %s ($%s) match=%s — %s",
                            coin.get("symbol"),
                            coin.get("name"),
                            verdict.match_score,
                            verdict.story.title[:80],
                        )
                        # keep verdict fields on the coin for the later post
                        coin["_story"] = verdict.story
                        coin["_match"] = verdict.match_score
                        coin["_structure"] = verdict.structure

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
                    else:
                        log.error("Failed to post %s", fresh.get("symbol"))

                if time.time() - last_leaderboard >= config.LEADERBOARD_SEC:
                    await _send_leaderboard(http, state, attention, health.STATUS.get("seen", 0))
                    last_leaderboard = time.time()

                health.STATUS["last_error"] = ""
            except Exception as exc:
                health.STATUS["last_error"] = str(exc)
                log.exception("Loop error: %s", exc)

            elapsed = time.time() - loop_start
            await asyncio.sleep(max(1.0, config.PUMP_POLL_SEC - elapsed))


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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
