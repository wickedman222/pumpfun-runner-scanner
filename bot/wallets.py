"""Follow hidden early buyers from coins that actually ran.

Leftover top holders are bags. The edge, if any, is fee-payers who bought
the curve early on real runners (pullback, organic tape), then show up
together in a young book.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import config
from .attention import extract_farm_reason
from .httputil import get_json
from .pump import age_seconds, fetch_coin, normalize_coin
from .state import State

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

# Known recent runners to seed the book on first boot.
SEED_RUNNERS = (
    "897wEhKQtCKXuxxoDA8BH9LWnSpdTKLVzqh1J8xFpump",  # BULLBALLS
    "8KZhSTCKSLLMuBRAZDT4FohNmBBhdSS1aJT6DfrQpump",  # FISHBONE
    "FtateF34Xzawa91bpbVNdX72hZYo9cymRDYqBreHHbJi",  # PANTS
    "3TgcJCUGbL5yvTmQu9nHjoqaYVRUc6JV4j1SuSXFpump",  # ESTRIPER
)


def _is_lp(owner: str, coin: dict) -> bool:
    if not owner:
        return True
    if owner in config.LP_OWNERS:
        return True
    if owner == coin.get("associated_bonding_curve"):
        return True
    if owner == coin.get("pool_address"):
        return True
    if owner == "11111111111111111111111111111111":
        return True
    return False


def _dd(usd: float, ath: float) -> float:
    if ath <= 0:
        return 0.0
    return max(0.0, 1.0 - usd / ath)


def is_held_runner(coin: dict, now: float) -> bool:
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    if ath < config.RUNNER_MIN_ATH:
        return False
    age = age_seconds(coin, now)
    if age <= 0 or age > config.RUNNER_MAX_AGE_SEC:
        return False
    if extract_farm_reason(coin):
        return False
    if _dd(usd, ath) > config.RUNNER_MAX_DD:
        return False
    return True


def is_real_runner(coin: dict, now: float) -> bool:
    """Harvest source: actually ran, actually pulled back, not a painted tape."""
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    if ath < config.RUNNER_MIN_ATH:
        return False
    age = age_seconds(coin, now)
    if age <= 0 or age > config.RUNNER_MAX_AGE_SEC:
        return False
    if extract_farm_reason(coin):
        return False
    dd = _dd(usd, ath)
    if dd < config.RUNNER_MIN_DD or dd > config.RUNNER_MAX_DD:
        return False
    replies = int(coin.get("reply_count") or 0)
    live = bool(coin.get("is_currently_live"))
    boost = str(coin.get("boost_mode") or "NONE").upper()
    boost_on = boost not in {"", "NONE", "NULL", "FALSE", "0", "OFF"}
    if not (live or replies >= 10 or not boost_on):
        return False
    return True


async def harvest_coin(
    http: httpx.AsyncClient, state: State, coin: dict, force: bool = False
) -> int:
    mint = coin.get("mint") or ""
    is_seed = mint in SEED_RUNNERS
    farm = extract_farm_reason(coin)
    if farm and not is_seed:
        dropped = state.drop_wallets_from_mint(mint)
        if dropped:
            log.info(
                "Drop %s farm wallets from %s (%s)",
                dropped,
                coin.get("symbol"),
                farm,
            )
        return 0
    if not force and not is_real_runner(coin, time.time()):
        return 0
    ath = float(coin.get("ath_market_cap") or coin.get("usd_market_cap") or 0)
    hidden = await harvest_early_buyers(http, state, coin)
    if hidden:
        log.info(
            "Harvest %s %s early/hidden wallets ATH $%s",
            hidden,
            coin.get("symbol"),
            f"{ath:,.0f}",
        )
    return hidden


async def _rpc(http: httpx.AsyncClient, method: str, params: list) -> dict | None:
    try:
        r = await http.post(
            config.SOLANA_RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=25.0,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _pubkey(item) -> str:
    if isinstance(item, dict):
        return item.get("pubkey") or ""
    return str(item or "")


async def harvest_early_buyers(http: httpx.AsyncClient, state: State, coin: dict) -> int:
    """Fee-payers who bought the curve early — often gone from the holder list."""
    mint = coin.get("mint") or ""
    curve = coin.get("bonding_curve") or ""
    if not mint or not curve or state.tx_harvested(mint):
        return 0
    sigs_json = await _rpc(
        http,
        "getSignaturesForAddress",
        [curve, {"limit": 24, "commitment": "confirmed"}],
    )
    rows = (sigs_json or {}).get("result") or []
    sigs = [x.get("signature") for x in rows if x.get("signature")]
    sigs = list(reversed(sigs))[:16]
    skip = set(_PROGRAMS)
    skip.add(curve)
    skip.add(coin.get("associated_bonding_curve") or "")
    skip.add(coin.get("pool_address") or "")
    skip.add(coin.get("creator") or "")
    seen: set[str] = set()
    for sig in sigs:
        await asyncio.sleep(0.12)
        txj = await _rpc(
            http,
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        res = (txj or {}).get("result") or {}
        meta = res.get("meta") or {}
        if not res or meta.get("err"):
            continue
        keys = ((res.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
        payer = _pubkey(keys[0]) if keys else ""
        if not payer or payer in skip or payer in seen:
            continue
        pre = {
            b.get("owner"): b
            for b in (meta.get("preTokenBalances") or [])
            if b.get("mint") == mint
        }
        post = {
            b.get("owner"): b
            for b in (meta.get("postTokenBalances") or [])
            if b.get("mint") == mint
        }
        bought = False
        for owner, pb in post.items():
            before = float(((pre.get(owner) or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
            after = float((pb.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            if after > before + 1:
                bought = True
                break
        if not bought:
            continue
        seen.add(payer)
        state.note_smart_wallet(
            payer,
            mint,
            0.0,
            symbol=(coin.get("symbol") or "").upper(),
            ath_mc=float(coin.get("ath_market_cap") or coin.get("usd_market_cap") or 0),
        )
    state.mark_tx_harvested(mint)
    return len(seen)


async def top_runners(http: httpx.AsyncClient) -> list[dict]:
    data = await get_json(http, f"{config.PUMP_API}/coins/top-runners")
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for row in data:
        raw = row.get("coin") if isinstance(row, dict) else None
        if isinstance(raw, dict) and raw.get("mint"):
            out.append(normalize_coin(raw))
    return out


async def harvest(http: httpx.AsyncClient, state: State, coins: list[dict]) -> int:
    seen: set[str] = set()
    added = 0
    for coin in coins:
        mint = coin.get("mint") or ""
        if not mint or mint in seen:
            continue
        seen.add(mint)
        added += await harvest_coin(http, state, coin)
    # Re-check books we already harvested. A UOTF-class farm that aged off
    # last_trade still has to lose those wallets.
    for mint in state.harvested_mints(40):
        if mint in seen:
            continue
        coin = await fetch_coin(http, mint)
        if not coin:
            continue
        seen.add(mint)
        added += await harvest_coin(http, state, coin)
    return added


_holder_cache: dict[str, tuple[float, list[tuple[str, float]]]] = {}
_overlap_left = 16


def reset_loop_budget() -> None:
    global _overlap_left
    _overlap_left = 16


async def fetch_holders(http: httpx.AsyncClient, coin: dict) -> list[tuple[str, float]]:
    mint = coin.get("mint") or ""
    if not mint:
        return []
    cached = _holder_cache.get(mint)
    now = time.time()
    if cached and now - cached[0] < 90:
        return cached[1]
    report = await get_json(http, f"{config.RUGCHECK_API}/tokens/{mint}/report")
    if not isinstance(report, dict):
        return []
    out: list[tuple[str, float]] = []
    for h in report.get("topHolders") or []:
        owner = h.get("owner") or h.get("address") or ""
        pct = float(h.get("pct") or 0)
        if _is_lp(owner, coin) or owner == (coin.get("creator") or ""):
            continue
        if pct < config.WALLET_MIN_PCT or pct > config.WALLET_MAX_PCT:
            continue
        out.append((owner, pct))
    _holder_cache[mint] = (now, out)
    return out


async def overlap(
    http: httpx.AsyncClient, state: State, coin: dict
) -> list[tuple[str, float, int]]:
    """Wallets in this book that already sat in enough recent runners."""
    global _overlap_left
    if _overlap_left <= 0:
        return []
    if state.smart_wallet_count() < 4:
        return []
    _overlap_left -= 1
    holders = await fetch_holders(http, coin)
    hits: list[tuple[str, float, int]] = []
    mint = coin.get("mint") or ""
    for wallet, pct in holders:
        n = state.smart_wallet_runners(wallet, exclude_mint=mint)
        if n >= config.WALLET_MIN_RUNNERS:
            hits.append((wallet, pct, n))
    hits.sort(key=lambda x: x[2], reverse=True)
    return hits


def wallet_buy_ok(coin: dict, now: float) -> str:
    """Hard nos for copy-trading a wallet into this mint. Empty = ok."""
    farm = extract_farm_reason(coin)
    if farm:
        return farm
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    age = age_seconds(coin, now)
    if age > config.WALLET_MAX_AGE_SEC:
        return f"too old ({age/60:.0f}m)"
    if usd < config.MIN_ARM_MC:
        return f"thin book ${usd:,.0f}"
    if usd > config.WALLET_MAX_BUY_MC:
        return f"chase ${usd:,.0f}"
    if ath > config.MIN_ARM_MC and _dd(usd, ath) > config.MAX_DD_AT_BUY:
        return f"off highs ${usd:,.0f} vs ATH ${ath:,.0f}"
    return ""
