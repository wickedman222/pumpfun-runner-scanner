from __future__ import annotations

import asyncio
import logging
import sys
import time
from . import config, health, paper
from .attention import Attention
from .engine import evaluate_new
from . import wallets as walletmod
from .httputil import client
from .pump import (
    active_coins,
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
    format_wallet_follow,
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


async def run() -> None:
    config.require_telegram()
    state = State()
    paper_reset = False
    paper_snap = None
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
            paper_reset = True
        paper_snap = paper.snapshot(state)
        health.STATUS["paper_equity"] = round(paper_snap["equity"], 4)
    attention = Attention()
    skip_logged: set[str] = set()
    last_attention = 0.0
    last_leaderboard = time.time()
    last_paper_report = time.time()
    last_feed_log = 0.0
    last_wallet_harvest = 0.0
    last_wallet_report = 0.0

    health.STATUS["ok"] = True
    health.start(config.PORT)

    async with client() as http:
        await boot_message(
            http,
            signals_today=state.signals_today(),
            snap=paper_snap,
            reset=paper_reset,
        )
        try:
            await attention.refresh(http)
            last_attention = time.time()
            health.STATUS["attention"] = len(attention.stories)
        except Exception as exc:
            log.warning("Initial attention refresh failed: %s", exc)

        log.info("Scanner loop started. spot on-curve + follow wallets from held runners")

        while True:
            loop_start = time.time()
            try:
                if time.time() - last_attention >= config.ATTENTION_POLL_SEC:
                    await attention.refresh(http)
                    last_attention = time.time()
                    health.STATUS["attention"] = len(attention.stories)

                fresh = await latest_coins(http, limit=50)
                streaming = await live_coins(http, limit=20)
                trading = await active_coins(http, limit=80)
                graduates = await graduated_coins(http, limit=25)
                followed = await _refresh_tracked(http, state)
                walletmod.reset_loop_budget()
                if time.time() - last_wallet_harvest >= config.WALLET_HARVEST_SEC:
                    harvested = 0
                    for mint in walletmod.SEED_RUNNERS:
                        c = await fetch_coin(http, mint)
                        if c:
                            harvested += await walletmod.harvest_coin(
                                http, state, c, force=True
                            )
                    runners = await walletmod.top_runners(http)
                    harvested += await walletmod.harvest(
                        http, state, runners + trading + graduates
                    )
                    last_wallet_harvest = time.time()
                    health.STATUS["smart_wallets"] = state.smart_wallet_count()
                    if harvested:
                        log.info(
                            "Wallet harvest +%s · following %s wallets",
                            harvested,
                            state.smart_wallet_count(),
                        )
                stats = state.tape_stats()
                health.STATUS["feeds"] = {
                    "latest": len(fresh),
                    "live": len(streaming),
                    "last_trade": len(trading),
                    "graduated": len(graduates),
                    "followed": len(followed),
                }
                health.STATUS["quota"] = state.signals_today()
                health.STATUS["tape"] = stats
                if time.time() - last_feed_log >= 60:
                    log.info(
                        "feeds latest=%s live=%s last_trade=%s graduated=%s follow=%s "
                        "tape young=%s armed=%s watching=%s posted_today=%s wallets=%s",
                        len(fresh),
                        len(streaming),
                        len(trading),
                        len(graduates),
                        len(followed),
                        stats.get("young"),
                        stats.get("armed"),
                        stats.get("watching"),
                        state.signals_today(),
                        state.smart_wallet_count(),
                    )
                    last_feed_log = time.time()
                seen_this_loop: set[str] = set()
                for coin in fresh + streaming + trading + graduates + followed:
                    mint = coin.get("mint")
                    if not mint or mint in seen_this_loop:
                        continue
                    seen_this_loop.add(mint)
                    is_new = state.mark_seen(coin)
                    if is_new:
                        health.STATUS["seen"] = health.STATUS.get("seen", 0) + 1
                    if state.already_posted(mint):
                        state.upsert_tape(coin)
                        continue

                    verdict = await evaluate_new(http, attention, state, coin)
                    usd = float(coin.get("usd_market_cap") or 0)
                    notable = usd >= config.MIN_ARM_MC
                    if verdict.post:
                        await _emit_buy(http, state, verdict)
                        continue
                    if verdict.failed_gate == "watch":
                        if verdict.story and str(verdict.story.title).startswith("Armed"):
                            key = f"arm:{mint}"
                            if key not in skip_logged:
                                skip_logged.add(key)
                                log.info("Armed %s %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if verdict.failed_gate == "structure":
                        log.info("Skip %s structure: %s", coin.get("symbol"), verdict.fail_reason)
                        continue
                    if notable or verdict.failed_gate == "quota":
                        _log_skip_once(
                            skip_logged,
                            mint if verdict.failed_gate != "quota" else "quota",
                            coin.get("symbol"),
                            verdict.failed_gate,
                            verdict.fail_reason,
                        )

                if config.PAPER_ENABLED:
                    await _manage_paper(http, state)
                    if time.time() - last_paper_report >= config.PAPER_REPORT_SEC:
                        await _send_paper_report(http, state)
                        last_paper_report = time.time()

                if time.time() - last_leaderboard >= config.LEADERBOARD_SEC:
                    await _send_leaderboard(http, state, attention, health.STATUS.get("seen", 0))
                    last_leaderboard = time.time()

                if time.time() - last_wallet_report >= config.WALLET_REPORT_SEC:
                    await _send_wallet_report(http, state)
                    last_wallet_report = time.time()

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


async def _refresh_tracked(http, state: State) -> list[dict]:
    due = state.tape_due(config.TAPE_REFRESH_LIMIT)
    if not due:
        return []
    sem = asyncio.Semaphore(8)

    async def one(row: dict):
        async with sem:
            coin = await fetch_coin(http, row["mint"])
            if not coin:
                state.touch_tape(row["mint"])
                return None
            return coin

    got = await asyncio.gather(*[one(row) for row in due], return_exceptions=True)
    out: list[dict] = []
    for item in got:
        if isinstance(item, dict) and item.get("mint"):
            out.append(item)
    return out


async def _emit_buy(http, state: State, verdict) -> None:
    coin = verdict.coin
    why = verdict.fail_reason or (verdict.story.title if verdict.story else "tape")
    text = format_signal(verdict, why)
    sent = await send(http, text, preview=True)
    if not sent:
        log.error("Failed to post %s", coin.get("symbol"))
        return
    story_title = getattr(verdict.story, "title", "") or ""
    state.mark_posted(coin, story_title)
    state.mark_tape(coin.get("mint") or "", "triggered", why)
    health.STATUS["posted"] = len(state.list_posted())
    log.info("POSTED %s — %s", coin.get("symbol"), why)
    if not config.PAPER_ENABLED:
        return
    fill = paper.try_open(state, coin, verdict.path or "tape")
    if not fill:
        return
    snap = paper.snapshot(state)
    health.STATUS["paper_equity"] = round(snap["equity"], 4)
    await send(http, format_paper_fill(fill, snap), preview=True)


async def _send_wallet_report(http, state: State) -> None:
    rep = state.wallet_report(config.WALLET_REPORT_SIZE)
    text = format_wallet_follow(rep)
    ok = await send(http, text)
    if ok:
        log.info(
            "Wallet follow report sent (%s wallets, %s mints)",
            rep.get("wallets"),
            rep.get("mints"),
        )
    else:
        log.error("Wallet follow report failed")


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
