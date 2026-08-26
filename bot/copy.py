"""Copy-trade pump.fun buys from the alpha watchlist.

We cannot outrun snipers. Edge is: a researched human KOL buys a still-cheap
curve book, we arrive within ~3 minutes, size by conviction, keep 25% cash.
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


async def _rpc(_http: httpx.AsyncClient, method: str, params: list) -> dict | None:
    # Dedicated RPC client — the pump.fun Origin on `http` 403s public Solana RPC.
    return await rpc(method, params)


def _pubkey(item) -> str:
    if isinstance(item, dict):
        return item.get("pubkey") or ""
    return str(item or "")


def _mint_bought(tx: dict, wallet: str) -> str:
    res = (tx or {}).get("result") or {}
    meta = res.get("meta") or {}
    if not res or meta.get("err"):
        return ""
    keys = ((res.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    pubs = [_pubkey(k) for k in keys]
    if not pubs or pubs[0] != wallet:
        return ""
    if PUMP_PROGRAM not in pubs and PUMPSWAP not in pubs:
        return ""
    pre = {
        (b.get("mint"), b.get("owner")): float(
            ((b.get("uiTokenAmount") or {}).get("uiAmount")) or 0
        )
        for b in (meta.get("preTokenBalances") or [])
    }
    for b in meta.get("postTokenBalances") or []:
        mint = b.get("mint") or ""
        owner = b.get("owner") or ""
        if not mint or mint == WSOL:
            continue
        after = float(((b.get("uiTokenAmount") or {}).get("uiAmount")) or 0)
        before = float(pre.get((mint, owner), 0) or 0)
        if after > before + 1:
            return mint
    return ""


def copy_size(equity: float, cash: float, n_alphas: int, conv: float, peak: float) -> float:
    if equity < config.PAPER_MIN_EQUITY:
        return 0.0
    frac = 0.08 if n_alphas < 2 else 0.12
    if n_alphas >= 3:
        frac = 0.15
    frac *= max(0.5, min(1.0, conv))
    if peak > 0 and equity < 0.75 * peak:
        frac *= 0.6
    frac = min(0.15, max(0.05, frac))
    size = round(equity * frac, 3)
    size = max(config.PAPER_SIZE_MIN, min(config.PAPER_SIZE_MAX, size))
    floor_cash = equity * 0.25
    if cash - size < floor_cash:
        size = round(max(0.0, cash - floor_cash), 3)
    if size < config.PAPER_SIZE_MIN * 0.8:
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
) -> list[tuple[str, str, float]]:
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
        # First run: pin the tip. Do not copy history. Empty pin does not count.
        if not newest:
            return []
        state.set_copy_cursor(alpha.address, newest)
        log.info("Cursor %s ready", alpha.name)
        return []
    out: list[tuple[str, str, float]] = []
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
        mint = _mint_bought(txj or {}, alpha.address)
        ts = float(row.get("blockTime") or 0)
        if mint:
            out.append((mint, sig, ts))
        await _sleep()
    if newest and newest != cursor:
        state.set_copy_cursor(alpha.address, newest)
    return list(reversed(out))


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(0.7)


async def consider(
    http: httpx.AsyncClient,
    state: State,
    alpha: Alpha,
    mint: str,
    sig: str,
    ts: float,
    now: float,
) -> CopyHit | None:
    if not alpha.copy:
        log.info("Observe %s buy %s (no copy)", alpha.name, mint[:8])
        return None
    if state.already_posted(mint) or state.paper_position(mint):
        state.note_copy_hit(alpha.address, mint)
        return None
    coin = await fetch_coin(http, mint)
    if not coin:
        return None
    lag = now - ts if ts else 9e9
    why = entry_fail(coin, now, lag)
    if why:
        log.info("Skip copy %s %s: %s", alpha.name, coin.get("symbol"), why)
        return None
    state.note_copy_hit(alpha.address, mint)
    n = state.copy_hit_count(mint, window_sec=20 * 60)
    snap_eq = float((state.paper_wallet() or {}).get("cash_sol") or 0)
    # equity approx cash if no marks; caller should pass snapshot. Use cash as floor.
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
    inv = "none — moonbag hold, no stop"
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


async def poll_all(http: httpx.AsyncClient, state: State) -> list[CopyHit]:
    now = time.time()
    hits: list[CopyHit] = []
    seen_mint: set[str] = set()
    from .alpha import all_alphas

    for alpha in all_alphas():
        try:
            buys = await scan_wallet(http, state, alpha)
        except Exception as exc:
            log.warning("scan %s failed: %s", alpha.name, exc)
            continue
        for mint, sig, ts in buys:
            if not alpha.copy:
                log.info("Observe %s tx %s %s", alpha.name, mint[:8], sig[:8])
                continue
            if mint in seen_mint:
                state.note_copy_hit(alpha.address, mint)
                continue
            hit = await consider(http, state, alpha, mint, sig, ts, now)
            if hit:
                seen_mint.add(mint)
                hits.append(hit)
        await _sleep()
    log.info("Copy poll %s new buys", len(hits))
    return hits
