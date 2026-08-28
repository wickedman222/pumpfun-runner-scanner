"""Copy-trade pump.fun buys AND sells from the alpha watchlist.

Holding after the KOL dumps is how the last book bled. Edge: same buy,
same exit. Clip 2x/4x if it rips while they still hold.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from . import config
from .alpha import Alpha
from .attention import extract_farm_reason
from .httputil import rpc
from .pump import age_seconds, fetch_coin
from .state import State

log = logging.getLogger("runner")

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWftQeMjFwM6Uo"
WSOL = "So11111111111111111111111111111111111111112"


@dataclass
class CopyHit:
    alpha: Alpha
    mint: str
    sig: str
    ts: float
    coin: dict
    n_alphas: int
    size_sol: float
    frac: float
    thesis: str
    invalidation: str


@dataclass
class CopyExit:
    alpha: Alpha
    mint: str
    sig: str
    ts: float
    coin: dict
    reason: str


async def _rpc(_http: httpx.AsyncClient, method: str, params: list) -> dict | None:
    return await rpc(method, params)


def _pubkey(item) -> str:
    if isinstance(item, dict):
        return item.get("pubkey") or ""
    return str(item or "")


def _amt(b: dict) -> float:
    return float(((b.get("uiTokenAmount") or {}).get("uiAmount")) or 0)


def token_move(tx: dict, wallet: str) -> tuple[str, str]:
    """Largest pump/pumpswap token delta for this wallet. ('mint','buy'|'sell') or ('','')."""
    res = (tx or {}).get("result") or {}
    meta = res.get("meta") or {}
    if not res or meta.get("err"):
        return "", ""
    keys = ((res.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    pubs = [_pubkey(k) for k in keys]
    if PUMP_PROGRAM not in pubs and PUMPSWAP not in pubs:
        return "", ""
    pre = {
        (b.get("mint"), b.get("owner")): _amt(b)
        for b in (meta.get("preTokenBalances") or [])
    }
    post_rows = meta.get("postTokenBalances") or []
    post = {(b.get("mint"), b.get("owner")): _amt(b) for b in post_rows}
    keys_set = set(pre) | set(post)
    best_mint = ""
    best_side = ""
    best_abs = 1.0
    for mint, owner in keys_set:
        if not mint or mint == WSOL or owner != wallet:
            continue
        delta = float(post.get((mint, owner), 0) or 0) - float(pre.get((mint, owner), 0) or 0)
        if abs(delta) <= best_abs:
            continue
        best_abs = abs(delta)
        best_mint = str(mint)
        best_side = "buy" if delta > 0 else "sell"
    return best_mint, best_side


def copy_size(equity: float, cash: float, n_alphas: int, conv: float, peak: float) -> float:
    del n_alphas, conv, peak
    if equity < config.PAPER_MIN_EQUITY:
        return 0.0
    size = round(config.PAPER_SIZE_FIXED, 3)
    if cash < size:
        return 0.0
    return size


def entry_fail(coin: dict, now: float, lag_sec: float) -> str:
    farm = extract_farm_reason(coin)
    if farm:
        return farm
    if lag_sec > config.COPY_MAX_LAG_SEC:
        return f"late copy {lag_sec:.0f}s"
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    if usd < config.COPY_MIN_MC:
        return f"thin ${usd:,.0f}"
    if usd > config.COPY_MAX_MC:
        return f"chase ${usd:,.0f}"
    age = age_seconds(coin, now)
    if age > config.COPY_MAX_AGE_SEC:
        return f"too old {age/60:.0f}m"
    if ath > 0 and usd < ath * 0.70:
        return f"off highs ${usd:,.0f} vs ${ath:,.0f}"
    if coin.get("complete") and usd > 80_000:
        return f"grad chase ${usd:,.0f}"
    return ""


async def scan_wallet(
    http: httpx.AsyncClient, state: State, alpha: Alpha
) -> list[tuple[str, str, float, str]]:
    cursor = state.copy_cursor(alpha.address)
    js = await _rpc(
        http,
        "getSignaturesForAddress",
        [alpha.address, {"limit": 8, "commitment": "confirmed"}],
    )
    if js is None:
        log.warning("scan %s: rpc miss", alpha.name)
        return []
    rows = js.get("result") or []
    newest = (rows[0].get("signature") or "") if rows else ""
    if not cursor:
        if not newest:
            return []
        state.set_copy_cursor(alpha.address, newest)
        log.info("Cursor %s ready", alpha.name)
        return []
    out: list[tuple[str, str, float, str]] = []
    for row in rows:
        sig = row.get("signature") or ""
        if not sig or sig == cursor:
            break
        if row.get("err"):
            continue
        txj = await _rpc(
            http,
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        mint, side = token_move(txj or {}, alpha.address)
        ts = float(row.get("blockTime") or 0)
        if mint and side:
            out.append((mint, sig, ts, side))
        await _sleep()
    if newest and newest != cursor:
        state.set_copy_cursor(alpha.address, newest)
    return list(reversed(out))


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(0.45)


async def consider_buy(
    http: httpx.AsyncClient,
    state: State,
    alpha: Alpha,
    mint: str,
    sig: str,
    ts: float,
    now: float,
) -> CopyHit | None:
    if state.already_posted(mint) or state.paper_position(mint) or state.copy_seen(
        alpha.address, mint
    ):
        state.note_copy_hit(alpha.address, mint)
        return None
    coin = await fetch_coin(http, mint)
    if not coin:
        return None
    lag = now - ts if ts else 9e9
    why = entry_fail(coin, now, lag)
    if why:
        log.info("Skip copy %s %s: %s", alpha.name, coin.get("symbol"), why)
        state.note_copy_hit(alpha.address, mint)
        return None
    state.note_copy_hit(alpha.address, mint)
    n = max(1, state.copy_hit_count(mint, window_sec=20 * 60))
    from . import paper as paper_mod

    snap = paper_mod.snapshot(state)
    peak = float(state.get_meta("equity_peak") or snap["equity"] or 0)
    if snap["equity"] > peak:
        state.set_meta("equity_peak", str(snap["equity"]))
        peak = snap["equity"]
    size = copy_size(snap["equity"], snap["cash"], n, alpha.conv, peak)
    if size <= 0:
        log.info("Skip copy %s — cash floor", coin.get("symbol"))
        return None
    frac = size / snap["equity"] if snap["equity"] else 0
    thesis = (
        f"{n} alpha(s) led by {alpha.name} (WR {alpha.wr:.0%}) "
        f"bought ${(coin.get('usd_market_cap') or 0):,.0f} "
        f"lag {lag:.0f}s"
    )
    inv = f"flatten when {alpha.name} sells"
    return CopyHit(
        alpha=alpha,
        mint=mint,
        sig=sig,
        ts=ts,
        coin=coin,
        n_alphas=n,
        size_sol=size,
        frac=frac,
        thesis=thesis,
        invalidation=inv,
    )


async def consider_exit(
    http: httpx.AsyncClient,
    state: State,
    alpha: Alpha,
    mint: str,
    sig: str,
    ts: float,
) -> CopyExit | None:
    pos = state.paper_position(mint)
    if not pos or float(pos.get("remaining_frac") or 0) <= 0:
        return None
    if (pos.get("status") or "") not in ("open", "moonbag"):
        return None
    coin = await fetch_coin(http, mint)
    if not coin:
        coin = {
            "mint": mint,
            "symbol": pos.get("symbol") or "",
            "usd_market_cap": pos.get("last_mc") or 0,
            "url": pos.get("url") or "",
        }
    reason = f"alpha exit {alpha.name}"
    log.info("COPY EXIT %s %s — %s", alpha.name, pos.get("symbol"), reason)
    return CopyExit(
        alpha=alpha, mint=mint, sig=sig, ts=ts, coin=coin, reason=reason
    )


async def poll_all(
    http: httpx.AsyncClient, state: State
) -> tuple[list[CopyHit], list[CopyExit]]:
    now = time.time()
    hits: list[CopyHit] = []
    exits: list[CopyExit] = []
    seen_buy: set[str] = set()
    seen_sell: set[str] = set()
    from .alpha import copy_alphas

    for alpha in copy_alphas():
        try:
            moves = await scan_wallet(http, state, alpha)
        except Exception as exc:
            log.warning("scan %s failed: %s", alpha.name, exc)
            continue
        dumped = {m for m, _, _, s in moves if s == "sell"}
        for mint, sig, ts, side in moves:
            if side == "sell":
                if mint in seen_sell:
                    continue
                ex = await consider_exit(http, state, alpha, mint, sig, ts)
                if ex:
                    seen_sell.add(mint)
                    exits.append(ex)
                continue
            if mint in dumped:
                log.info("Skip copy %s %s — dumped in same window", alpha.name, mint[:8])
                state.note_copy_hit(alpha.address, mint)
                continue
            if mint in seen_buy:
                state.note_copy_hit(alpha.address, mint)
                continue
            hit = await consider_buy(http, state, alpha, mint, sig, ts, now)
            seen_buy.add(mint)
            if hit:
                hits.append(hit)
        await _sleep()
    log.info("Copy poll %s buys / %s exits", len(hits), len(exits))
    return hits, exits
