"""Live runners the way we actually look at them: Dexscreener PumpSwap.

The missed set (fone, Greyson, STACY, RETIREMENT, inuzard) all printed
here after pump.fun graduation. pump.fun top-runners / SEED_RUNNERS are
old mega-caps from prior chats — do not use those as the live feed.
"""

from __future__ import annotations

import logging
import time

import httpx

from . import config
from .attention import extract_farm_reason
from .pump import fetch_coin

log = logging.getLogger("runner")

DEX = "https://api.dexscreener.com"
_DEX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://dexscreener.com",
    "Referer": "https://dexscreener.com/",
}

# User-pasted misses. Mine launch wallets once, never treat as a live buy list.
EXAMPLE_RUNNERS = (
    "CTPoyCwkjMvoJwU4xvZZqoD8tiYk6yDchySiN5gGpump",  # fone
    "AfGdjAp9djSaqJxzYo3t6jy8tJA3o2aDPHoZ57Egpump",  # Greyson
    "TRUEq13uwehY57S1Y62iTxas7ahpFgohc8kScbGNu1h",  # STACY
    "AjRVGoH8Tu8fEjyX6q3DzGusGxjKT68Y7DcgsSiPpump",  # RETIREMENT
    "ApZuxdpzMrbEYTGEzeY9afh5pj9d6qPRJCTgQYiipbKg",  # CYBERLEEK
    "4pnj9L8C1uJDhECSTHkToKHPmjLRfdSunENs2xtKpump",  # inuzard
)


async def _get(http: httpx.AsyncClient, path: str):
    try:
        r = await http.get(DEX + path, headers=_DEX_HEADERS, timeout=20.0)
        if r.status_code != 200:
            log.warning("dex %s HTTP %s", path[:60], r.status_code)
            return None
        return r.json()
    except Exception as exc:
        log.warning("dex %s fail: %s", path[:60], exc)
        return None


def pair_to_coin(p: dict) -> dict:
    b = p.get("baseToken") or {}
    mc = float(p.get("marketCap") or p.get("fdv") or 0)
    created = int(p.get("pairCreatedAt") or 0)
    return {
        "mint": b.get("address") or "",
        "symbol": (b.get("symbol") or "").lstrip("$"),
        "name": b.get("name") or "",
        "usd_market_cap": mc,
        "ath_market_cap": mc,
        "url": p.get("url") or "",
        "created_timestamp": created,
        "complete": True,
        "boost_mode": "NONE",
        "mayhem_state": "NONE",
        "is_cashback_enabled": False,
        "is_currently_live": False,
        "dex_id": p.get("dexId") or "",
        "pair_address": p.get("pairAddress") or "",
        "vol_h1": float((p.get("volume") or {}).get("h1") or 0),
        "chg_h1": float((p.get("priceChange") or {}).get("h1") or 0),
        "chg_h6": float((p.get("priceChange") or {}).get("h6") or 0),
        "buys_h1": int(((p.get("txns") or {}).get("h1") or {}).get("buys") or 0),
        "sells_h1": int(((p.get("txns") or {}).get("h1") or {}).get("sells") or 0),
    }


def entry_fail(coin: dict, now: float | None = None) -> str:
    """Empty = take it. These are the Dexscreener-style just-graduated rips."""
    now = now or time.time()
    created_ms = int(coin.get("created_timestamp") or 0)
    if created_ms > 1_000_000_000_000:
        age_h = (now * 1000 - created_ms) / 3_600_000
    else:
        age_h = (now - created_ms) / 3600 if created_ms else 99
    if age_h > config.DEX_MAX_AGE_H:
        return f"old {age_h:.1f}h"
    mc = float(coin.get("usd_market_cap") or 0)
    if mc < config.DEX_MIN_MC:
        return f"thin ${mc:,.0f}"
    if mc > config.DEX_MAX_MC:
        return f"late ${mc:,.0f}"
    chg = float(coin.get("chg_h1") or 0)
    if chg < config.DEX_MIN_CHG_H1:
        return f"cold {chg:.0f}%"
    if chg < -25:
        return f"dump {chg:.0f}%"
    vol = float(coin.get("vol_h1") or 0)
    if vol < config.DEX_MIN_VOL_H1:
        return f"thin vol ${vol:,.0f}"
    buys = int(coin.get("buys_h1") or 0)
    sells = int(coin.get("sells_h1") or 0)
    if sells and buys < sells * 0.7:
        return "more sells than buys"
    return ""


async def _search_pumpswap(http: httpx.AsyncClient) -> list[dict]:
    data = await _get(http, "/latest/dex/search?q=pumpswap")
    out = []
    for p in (data or {}).get("pairs") or []:
        if (p.get("chainId") or "") != "solana":
            continue
        if (p.get("dexId") or "") != "pumpswap":
            continue
        coin = pair_to_coin(p)
        if coin.get("mint"):
            out.append(coin)
    return out


async def _boost_pairs(http: httpx.AsyncClient) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        rows = await _get(http, path)
        if not isinstance(rows, list):
            continue
        for row in rows[:20]:
            if (row.get("chainId") or "") != "solana":
                continue
            mint = row.get("tokenAddress") or ""
            if not mint or mint in seen:
                continue
            seen.add(mint)
            data = await _get(http, f"/latest/dex/tokens/{mint}")
            for p in (data or {}).get("pairs") or []:
                if (p.get("chainId") or "") != "solana":
                    continue
                if (p.get("dexId") or "") not in ("pumpswap", "raydium"):
                    continue
                coin = pair_to_coin(p)
                if coin.get("mint"):
                    out.append(coin)
                    break
    return out


async def candidates(http: httpx.AsyncClient) -> list[dict]:
    now = time.time()
    seen: set[str] = set()
    keep: list[dict] = []
    skipped = 0
    for coin in await _search_pumpswap(http) + await _boost_pairs(http):
        mint = coin.get("mint") or ""
        if not mint or mint in seen:
            continue
        seen.add(mint)
        why = entry_fail(coin, now)
        if why:
            skipped += 1
            continue
        keep.append(coin)
    if keep:
        log.info(
            "Dex hot %s (%s skipped): %s",
            len(keep),
            skipped,
            ", ".join((c.get("symbol") or "?") for c in keep[:8]),
        )
    return keep


async def runner_mints(http: httpx.AsyncClient) -> list[dict]:
    """Recent Dexscreener PumpSwap names that already ran — for wallet mining."""
    now = time.time()
    out: list[dict] = []
    seen: set[str] = set()
    for coin in await _search_pumpswap(http):
        mint = coin.get("mint") or ""
        if not mint or mint in seen:
            continue
        created_ms = int(coin.get("created_timestamp") or 0)
        age_h = (now * 1000 - created_ms) / 3_600_000 if created_ms > 1e12 else 99
        mc = float(coin.get("usd_market_cap") or 0)
        if age_h > 72 or mc < 200_000:
            continue
        seen.add(mint)
        out.append(coin)
    return out
