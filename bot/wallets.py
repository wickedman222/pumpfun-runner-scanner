"""Follow wallets that showed up in recent held runners.

Pump.fun trade history is flaky; holder lists are not. Harvest top non-LP
holders from coins that actually ran (ATH >= $200k, still near highs, <48h).
Buy a young book when two of those wallets are already sitting in it.
"""

from __future__ import annotations

import logging
import time

import httpx

from . import config
from .attention import extract_farm_reason
from .httputil import get_json
from .pump import age_seconds, normalize_coin
from .state import State

log = logging.getLogger("runner")

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


async def harvest_coin(
    http: httpx.AsyncClient, state: State, coin: dict, force: bool = False
) -> int:
    if not force and not is_held_runner(coin, time.time()):
        return 0
    if force and extract_farm_reason(coin):
        return 0
    holders = await fetch_holders(http, coin)
    n = 0
    mint = coin.get("mint") or ""
    for wallet, pct in holders[:15]:
        state.note_smart_wallet(wallet, mint, pct)
        n += 1
    if n:
        log.info(
            "Harvest %s %s holders from runner ATH $%s",
            n,
            coin.get("symbol"),
            f"{float(coin.get('ath_market_cap') or 0):,.0f}",
        )
    return n


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
    return added


_holder_cache: dict[str, tuple[float, list[tuple[str, float]]]] = {}
_overlap_left = 8


def reset_loop_budget() -> None:
    global _overlap_left
    _overlap_left = 8


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
    if state.smart_wallet_count() < 8:
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
    if age > config.MAX_ACTIVE_AGE_SEC:
        return f"too old ({age/60:.0f}m)"
    if usd < config.MIN_ARM_MC:
        return f"thin book ${usd:,.0f}"
    if usd > config.WALLET_MAX_BUY_MC:
        return f"chase ${usd:,.0f}"
    if ath > config.MIN_ARM_MC and _dd(usd, ath) > config.MAX_DD_AT_BUY:
        return f"off highs ${usd:,.0f} vs ATH ${ath:,.0f}"
    return ""
