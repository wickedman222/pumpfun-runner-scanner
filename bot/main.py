from __future__ import annotations

import asyncio
import logging
import sys
import time
from . import config, dex, gather, health, paper
from .alpha import all_alphas, copy_alphas
from .attention import Attention, extract_farm_reason
from .copy import live_watchlist, poll_all as copy_poll
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
    format_copy_boot,
    format_copy_exit,
    format_dex_boot,
    format_copy_hit,
    format_copy_session,
    format_early_board,
    format_early_boot,
    format_gather,
    format_paper_book,
    format_paper_fill,
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
    if state.get_meta("wallet_book") != config.WALLET_BOOK_ID:
        dropped = state.clear_wallet_book()
        state.set_meta("wallet_book", config.WALLET_BOOK_ID)
        log.info(
            "Cleared %s leftover wallets for book %s",
            dropped,
            config.WALLET_BOOK_ID,
        )
        health.STATUS["smart_wallets"] = 0
    attention = Attention()
    skip_logged: set[str] = set()
    last_attention = 0.0
    last_paper_report = time.time()
    last_feed_log = 0.0
    last_wallet_harvest = 0.0
    last_gather_report = 0.0

    health.STATUS["ok"] = True
    health.start(config.PORT)

    async with client() as http:
        try:
            await attention.refresh(http)
            last_attention = time.time()
            health.STATUS["attention"] = len(attention.stories)
        except Exception as exc:
            log.warning("Initial attention refresh failed: %s", exc)

        async def _gather_bg() -> None:
            while True:
                try:
                    added = await gather.cycle(http, state)
                    board = state.early_board()
                    health.STATUS["smart_wallets"] = board.get("wallets") or 0
                    health.STATUS["posted"] = board.get("hits") or 0
                    if added:
                        log.info("Gather +%s · %s wallets", added, board.get("wallets"))
                except Exception as exc:
                    log.warning("gather bg: %s", exc)
                await asyncio.sleep(config.GATHER_SEC)

        if config.GATHER_MODE and (config.COPY_MODE or config.DEX_MODE):
            asyncio.create_task(_gather_bg())

        if config.DEX_MODE:
            log.info(
                "Dex loop. paper %s SOL book %s. Dexscreener PumpSwap.",
                f"{(paper_snap or {}).get('equity') or config.PAPER_START_SOL:.3f}",
                config.PAPER_BOOK_ID,
            )
            await send(http, format_dex_boot(paper_snap))
            last_gather_report = time.time()

        if config.GATHER_MODE and config.COPY_MODE and not config.DEX_MODE:
            watches = live_watchlist(state)
            log.info(
                "Live loop. gather + paper %s SOL book %s. copying %s early wallets.",
                f"{(paper_snap or {}).get('equity') or config.PAPER_START_SOL:.3f}",
                config.PAPER_BOOK_ID,
                len(watches),
            )
            await send(http, format_early_boot(paper_snap, watches))
            last_gather_report = time.time()
        elif config.GATHER_MODE and not config.DEX_MODE:
            log.info("Gather loop. early buyers on runners. paper off.")
            await send(http, format_early_boot())
        elif config.COPY_MODE:
            log.info(
                "Copy loop. paper %s SOL book %s. %s copy / %s observe wallets",
                f"{(paper_snap or {}).get('equity') or config.PAPER_START_SOL:.3f}",
                config.PAPER_BOOK_ID,
                len(copy_alphas()),
                len(all_alphas()) - len(copy_alphas()),
            )
            if paper_snap:
                await send(http, format_copy_boot(paper_snap, all_alphas()))
        else:
            log.info("Scanner loop started (tape mode)")

        while True:
            loop_start = time.time()
            try:
                if config.DEX_MODE:
                    coins = await dex.candidates(http)
                    for coin in coins:
                        mint = coin.get("mint") or ""
                        if not mint or state.already_posted(mint) or state.paper_position(mint):
                            continue
                        pumped = await fetch_coin(http, mint)
                        farm = extract_farm_reason(pumped) if pumped else ""
                        if farm:
                            log.info("Skip dex %s: %s", coin.get("symbol"), farm)
                            continue
                        if not config.PAPER_ENABLED:
                            continue
                        fill = paper.try_open(state, coin, path="dex")
                        if not fill:
                            continue
                        state.mark_posted(coin, "dex pumpswap")
                        snap = paper.snapshot(state)
                        health.STATUS["paper_equity"] = round(snap["equity"], 4)
                        log.info("DEX BUY %s %.3f SOL @ $%.0f", coin.get("symbol"), fill.sol, fill.mc)
                        await send(http, format_paper_fill(fill, snap))
                    if config.PAPER_ENABLED:
                        await _manage_paper(http, state)
                        health.STATUS["paper_equity"] = round(
                            paper.snapshot(state)["equity"], 4
                        )
                    health.STATUS["watches"] = len(coins)
                    if time.time() - last_gather_report >= config.WALLET_REPORT_SEC:
                        snap = paper.snapshot(state) if config.PAPER_ENABLED else {
                            "equity": 0, "start": 2, "cash": 0, "open": [], "closed_n": 0, "pnl": 0, "unreal": 0, "size": 0
                        }
                        await send(http, format_paper_book(snap))
                        await send(http, format_early_board(state.early_board()))
                        last_gather_report = time.time()
                    health.STATUS["last_error"] = ""
                    elapsed = time.time() - loop_start
                    await asyncio.sleep(max(1.0, config.DEX_POLL_SEC - elapsed))
                    continue
                if config.GATHER_MODE and not config.COPY_MODE:
                    if time.time() - last_wallet_harvest >= config.GATHER_SEC:
                        added = await gather.cycle(http, state)
                        last_wallet_harvest = time.time()
                        board = state.early_board()
                        health.STATUS["smart_wallets"] = board.get("wallets") or 0
                        health.STATUS["posted"] = board.get("hits") or 0
                        health.STATUS["watches"] = board.get("mints") or 0
                        if added:
                            log.info("Gather +%s", added)
                    if time.time() - last_gather_report >= config.WALLET_REPORT_SEC:
                        board = state.early_board()
                        ok = await send(http, format_early_board(board))
                        if ok:
                            log.info(
                                "Early list dump %s wallets",
                                board.get("wallets"),
                            )
                        last_gather_report = time.time()
                    health.STATUS["last_error"] = ""
                    await asyncio.sleep(20)
                    continue
                if config.COPY_MODE:
                    hits, exits = await copy_poll(http, state)
                    for hit in hits:
                        await _copy_fill(http, state, hit)
                    for ex in exits:
                        await _copy_exit(http, state, ex)
                    if config.PAPER_ENABLED:
                        await _manage_paper(http, state)
                    health.STATUS["paper_equity"] = round(
                        paper.snapshot(state)["equity"], 4
                    ) if config.PAPER_ENABLED else 0
                    health.STATUS["watches"] = len(live_watchlist(state))
                    if time.time() - last_gather_report >= config.WALLET_REPORT_SEC:
                        snap = paper.snapshot(state) if config.PAPER_ENABLED else {
                            "equity": 0, "start": 2, "cash": 0, "open": [], "closed_n": 0, "pnl": 0, "unreal": 0, "size": 0
                        }
                        board = state.early_board()
                        ok = await send(http, format_copy_session(snap, live_watchlist(state)))
                        if ok:
                            log.info("Copy session dump sent eq %.3f", snap["equity"])
                        ok2 = await send(http, format_early_board(board))
                        if ok2:
                            log.info("Early list dump %s wallets", board.get("wallets"))
                        last_gather_report = time.time()
                    health.STATUS["last_error"] = ""
                    elapsed = time.time() - loop_start
                    await asyncio.sleep(max(1.0, config.COPY_POLL_SEC - elapsed))
                    continue

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
                        await _note_spot(http, state, verdict)
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

                if time.time() - last_gather_report >= config.WALLET_REPORT_SEC:
                    await _send_gather(http, state)
                    last_gather_report = time.time()

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


async def _copy_fill(http, state: State, hit) -> None:
    coin = hit.coin
    log.info(
        "COPY %s %s %.3f SOL (%.0f%%) — %s",
        hit.alpha.name,
        coin.get("symbol"),
        hit.size_sol,
        hit.frac * 100,
        hit.thesis,
    )
    if not config.PAPER_ENABLED:
        return
    fill = paper.try_open(state, coin, path=f"copy:{hit.alpha.address}", size_sol=hit.size_sol)
    if not fill:
        return
    state.mark_posted(coin, hit.thesis)
    snap = paper.snapshot(state)
    health.STATUS["paper_equity"] = round(snap["equity"], 4)
    await send(http, format_copy_hit(hit, snap))
    await send(http, format_paper_fill(fill, snap))


async def _copy_exit(http, state: State, ex) -> None:
    pos = state.paper_position(ex.mint)
    if not pos:
        return
    fill = paper.flatten(state, pos, ex.coin, ex.reason)
    if not fill:
        return
    snap = paper.snapshot(state)
    health.STATUS["paper_equity"] = round(snap["equity"], 4)
    log.info(
        "PAPER FLATTEN %s %s @ $%.0f",
        pos.get("symbol"),
        ex.reason,
        float(ex.coin.get("usd_market_cap") or 0),
    )
    await send(http, format_copy_exit(ex, snap))
    await send(http, format_paper_fill(fill, snap))


async def _note_spot(http, state: State, verdict) -> None:
    coin = verdict.coin
    why = verdict.fail_reason or (verdict.story.title if verdict.story else "tape")
    story_title = getattr(verdict.story, "title", "") or why
    state.mark_posted(coin, story_title)
    state.mark_tape(coin.get("mint") or "", "triggered", why)
    health.STATUS["posted"] = len(state.list_posted())
    log.info("SPOT %s — %s", coin.get("symbol"), why)
    if not config.PAPER_ENABLED:
        return
    fill = paper.try_open(state, coin, verdict.path or "tape")
    if not fill:
        return
    snap = paper.snapshot(state)
    health.STATUS["paper_equity"] = round(snap["equity"], 4)
    await send(http, format_paper_fill(fill, snap))


async def _send_gather(http, state: State) -> None:
    since = int(time.time()) - config.WALLET_REPORT_SEC
    spots = state.gather_since(since).get("spots") or []
    for row in spots:
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
    for row in state.gather_since(since).get("armed") or []:
        mint = row.get("mint")
        if not mint:
            continue
        coin = await fetch_coin(http, mint)
        if not coin:
            continue
        state.upsert_tape(coin)
    rep = state.gather_since(since)
    hours = max(1, config.WALLET_REPORT_SEC // 3600)
    text = format_gather(rep, hours)
    ok = await send(http, text)
    if ok:
        log.info(
            "Gather dump sent (spots %s armed %s)",
            len(rep.get("spots") or []),
            len(rep.get("armed") or []),
        )
    else:
        log.error("Gather dump failed")


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
            await send(http, format_paper_fill(fill, snap))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
