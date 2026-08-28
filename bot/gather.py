"""Collect wallets that bought real runners at the start of the curve.

Paper copy is off until we have a list. Source: bonding-curve genesis
buyers on tokens whose ATH actually ran. A later sell on the same curve
is the PnL tell — they didn't just sit the bag.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import config
from .attention import extract_farm_reason
from .httputil import rpc
from .pump import age_seconds, fetch_coin, graduated_coins, market_cap_coins
from .state import State
from .wallets import SEED_RUNNERS, _is_lp, top_runners

log = logging.getLogger("runner")

_PROGRAMS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWftQeMjFwM6Uo",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMDkdrT4eFf4",
}


def _pubkey(item) -> str:
    if isinstance(item, dict):
        return item.get("pubkey") or ""
    return str(item or "")


def _tok(b: dict) -> float:
    return float(((b.get("uiTokenAmount") or {}).get("uiAmount")) or 0)


def is_winner(coin: dict, now: float) -> str:
    """Empty = mine this book. Else skip reason."""
    farm = extract_farm_reason(coin)
    if farm:
        return farm
    ath = float(coin.get("ath_market_cap") or coin.get("usd_market_cap") or 0)
    if ath < config.EARLY_MIN_ATH:
        return f"ath ${ath:,.0f}"
    age = age_seconds(coin, now)
    if age <= 0 or age > config.EARLY_MAX_AGE_SEC:
        return "age"
    return ""


async def _sigs(curve: str, before: str = "") -> list[dict]:
    params: dict = {"limit": config.EARLY_PAGE_LIMIT, "commitment": "confirmed"}
    if before:
        params["before"] = before
    js = await rpc("getSignaturesForAddress", [curve, params])
    return list((js or {}).get("result") or [])


async def _tx(sig: str) -> dict:
    js = await rpc(
        "getTransaction",
        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )
    return (js or {}).get("result") or {}


def _moves(tx: dict, mint: str) -> list[tuple[str, str, float, float]]:
    """(wallet, side, sol_abs, token_delta) for this mint."""
    meta = (tx or {}).get("meta") or {}
    if not tx or meta.get("err"):
        return []
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    pubs = [_pubkey(k) for k in keys]
    payer = pubs[0] if pubs else ""
    pre_lam = list(meta.get("preBalances") or [])
    post_lam = list(meta.get("postBalances") or [])
    payer_sol = 0.0
    if payer and pre_lam and post_lam:
        payer_sol = (float(pre_lam[0]) - float(post_lam[0])) / 1e9
    pre = {
        (b.get("mint"), b.get("owner")): _tok(b)
        for b in (meta.get("preTokenBalances") or [])
    }
    post = {
        (b.get("mint"), b.get("owner")): _tok(b)
        for b in (meta.get("postTokenBalances") or [])
    }
    out: list[tuple[str, str, float, float]] = []
    for mint_k, owner in set(pre) | set(post):
        if mint_k != mint or not owner:
            continue
        delta = float(post.get((mint_k, owner), 0) or 0) - float(
            pre.get((mint_k, owner), 0) or 0
        )
        if abs(delta) <= 1:
            continue
        side = "buy" if delta > 0 else "sell"
        sol = abs(payer_sol) if owner == payer else 0.0
        out.append((str(owner), side, sol, delta))
    return out


async def genesis_pages(curve: str) -> tuple[list[dict], list[dict], bool]:
    """Oldest page, newest page, True if we reached curve start."""
    before = ""
    newest: list[dict] = []
    last: list[dict] = []
    genesis = False
    for i in range(config.EARLY_SIG_PAGES):
        rows = await _sigs(curve, before)
        await asyncio.sleep(0.25)
        if not rows:
            genesis = True
            break
        if i == 0:
            newest = rows
        last = rows
        if len(rows) < config.EARLY_PAGE_LIMIT:
            genesis = True
            break
        before = rows[-1].get("signature") or ""
        if not before:
            break
    return last, newest, genesis


async def mine_coin(http: httpx.AsyncClient, state: State, coin: dict) -> int:
    del http
    mint = coin.get("mint") or ""
    curve = coin.get("bonding_curve") or ""
    if not mint or not curve or state.tx_harvested(mint):
        return 0
    why = is_winner(coin, time.time())
    if why:
        return 0
    oldest, newest, genesis = await genesis_pages(curve)
    if not genesis:
        log.info("Skip mine %s — curve too long to reach launch", coin.get("symbol"))
        state.mark_tx_harvested(mint)
        return 0
    skip = set(_PROGRAMS)
    skip.add(curve)
    skip.add(coin.get("associated_bonding_curve") or "")
    skip.add(coin.get("pool_address") or "")
    skip.add(coin.get("creator") or "")
    ath = float(coin.get("ath_market_cap") or coin.get("usd_market_cap") or 0)
    symbol = (coin.get("symbol") or "").upper()

    early: dict[str, dict] = {}
    rank = 0
    for row in reversed(oldest):
        sig = row.get("signature") or ""
        if not sig or row.get("err"):
            continue
        await asyncio.sleep(0.22)
        tx = await _tx(sig)
        for owner, side, sol, _delta in _moves(tx, mint):
            if side != "buy" or owner in skip or _is_lp(owner, coin):
                continue
            if owner in early:
                early[owner]["sol_in"] += sol
                continue
            rank += 1
            if rank > config.EARLY_MAX_RANK:
                continue
            early[owner] = {
                "rank": rank,
                "sol_in": sol,
                "sol_out": 0.0,
                "sold": 0,
            }

    for row in newest:
        sig = row.get("signature") or ""
        if not sig or row.get("err"):
            continue
        await asyncio.sleep(0.18)
        tx = await _tx(sig)
        for owner, side, sol, _delta in _moves(tx, mint):
            if owner not in early or side != "sell":
                continue
            early[owner]["sold"] = 1
            early[owner]["sol_out"] += sol

    n = 0
    for wallet, row in early.items():
        if row["rank"] > config.EARLY_MAX_RANK:
            continue
        state.note_early_hit(
            wallet,
            mint,
            symbol,
            row["rank"],
            row["sol_in"],
            row["sol_out"],
            row["sold"],
            ath,
        )
        n += 1
    state.mark_tx_harvested(mint)
    log.info(
        "Mined %s %s early wallets ATH $%s (sold %s)",
        n,
        symbol,
        f"{ath:,.0f}",
        sum(1 for r in early.values() if r["sold"] and r["rank"] <= config.EARLY_MAX_RANK),
    )
    return n


async def runner_pool(http: httpx.AsyncClient) -> list[dict]:
    now = time.time()
    pool: list[dict] = []
    seen: set[str] = set()
    chunks = [
        await top_runners(http),
        await market_cap_coins(http, limit=40),
        await graduated_coins(http, limit=30),
    ]
    for mint in SEED_RUNNERS:
        c = await fetch_coin(http, mint)
        if c:
            chunks.append([c])
    for group in chunks:
        for coin in group:
            mint = coin.get("mint") or ""
            if not mint or mint in seen:
                continue
            if is_winner(coin, now):
                continue
            seen.add(mint)
            pool.append(coin)
    pool.sort(
        key=lambda c: float(c.get("ath_market_cap") or c.get("usd_market_cap") or 0),
        reverse=True,
    )
    return pool[:16]


async def cycle(http: httpx.AsyncClient, state: State) -> int:
    coins = await runner_pool(http)
    added = 0
    for coin in coins:
        try:
            added += await mine_coin(http, state, coin)
        except Exception as exc:
            log.warning("mine %s failed: %s", coin.get("symbol"), exc)
    board = state.early_board()
    log.info(
        "Gather cycle +%s hits · %s wallets · %s runners · %s sold",
        added,
        board.get("wallets"),
        board.get("mints"),
        board.get("sold"),
    )
    return added
