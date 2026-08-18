from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config
from .httputil import get_json

log = logging.getLogger("runner")

GRADUATE_USD = 69_000.0
GRADUATE_SOL_LAMPORTS = 85 * 1_000_000_000


def normalize_coin(raw: dict) -> dict:
    created_ms = int(raw.get("created_timestamp") or 0)
    usd = float(raw.get("usd_market_cap") or raw.get("market_cap_usd") or 0)
    ath = float(raw.get("ath_market_cap") or usd or 0)
    real_sol = int(raw.get("real_sol_reserves") or 0)
    curve_pct = 0.0
    if real_sol:
        curve_pct = min(100.0, 100.0 * real_sol / GRADUATE_SOL_LAMPORTS)
    elif usd:
        curve_pct = min(100.0, 100.0 * usd / GRADUATE_USD)

    return {
        "mint": raw.get("mint") or "",
        "name": (raw.get("name") or "").strip(),
        "symbol": (raw.get("symbol") or "").strip().lstrip("$"),
        "description": (raw.get("description") or "").strip(),
        "creator": raw.get("creator") or "",
        "created_timestamp": created_ms,
        "complete": bool(raw.get("complete")),
        "usd_market_cap": usd,
        "ath_market_cap": ath,
        "reply_count": int(raw.get("reply_count") or 0),
        "is_currently_live": bool(raw.get("is_currently_live")),
        "livestream_title": (raw.get("livestream_title") or "").strip(),
        "twitter": raw.get("twitter") or "",
        "telegram": raw.get("telegram") or "",
        "website": raw.get("website") or "",
        "image_uri": raw.get("image_uri") or "",
        "virtual_sol_reserves": int(raw.get("virtual_sol_reserves") or 0),
        "real_sol_reserves": real_sol,
        "associated_bonding_curve": raw.get("associated_bonding_curve") or "",
        "pool_address": raw.get("pool_address") or "",
        "curve_pct": round(curve_pct, 1),
        "boost_mode": str(raw.get("boost_mode") or "NONE").upper(),
        "mayhem_state": str(raw.get("mayhem_state") or raw.get("mayhem") or "").upper(),
        "is_cashback_enabled": bool(raw.get("is_cashback_enabled")),
        "url": f"{config.PUMP_WEB}/{raw.get('mint')}" if raw.get("mint") else "",
    }


async def latest_coins(http: httpx.AsyncClient, limit: int = 50) -> list[dict]:
    url = (
        f"{config.PUMP_API}/coins?offset=0&limit={limit}"
        "&sort=created_timestamp&order=DESC&includeNsfw=false"
    )
    data = await get_json(http, url)
    if not isinstance(data, list):
        return []
    return [normalize_coin(c) for c in data if c.get("mint")]


async def active_coins(http: httpx.AsyncClient, limit: int = 30) -> list[dict]:
    """Homepage-like tape: last trade, not brand-new spam. Farm filter still applies."""
    url = (
        f"{config.PUMP_API}/coins?offset=0&limit={limit}"
        "&sort=last_trade_timestamp&order=DESC&includeNsfw=false"
    )
    data = await get_json(http, url)
    if not isinstance(data, list):
        return []
    return [normalize_coin(c) for c in data if c.get("mint")]


async def live_coins(http: httpx.AsyncClient, limit: int = 20) -> list[dict]:
    url = (
        f"{config.PUMP_API}/coins/currently-live"
        f"?offset=0&limit={limit}&includeNsfw=false"
    )
    data = await get_json(http, url)
    if not isinstance(data, list):
        return []
    return [normalize_coin(c) for c in data if c.get("mint")]


async def fetch_coin(http: httpx.AsyncClient, mint: str) -> dict | None:
    data = await get_json(http, f"{config.PUMP_API}/coins/{mint}")
    if not isinstance(data, dict) or not data.get("mint"):
        return None
    return normalize_coin(data)


async def creator_coins(http: httpx.AsyncClient, creator: str, limit: int = 20) -> list[dict]:
    if not creator:
        return []
    paths = [
        f"{config.PUMP_API}/coins/user-created-coins/{creator}?offset=0&limit={limit}",
        f"{config.PUMP_API}/coins?offset=0&limit={limit}&creator={creator}",
    ]
    for url in paths:
        data = await get_json(http, url)
        rows: Any = data
        if isinstance(data, dict):
            rows = data.get("coins") or data.get("data") or data.get("results")
        if isinstance(rows, list) and rows:
            return [normalize_coin(c) for c in rows if isinstance(c, dict) and c.get("mint")]
    return []


def age_seconds(coin: dict, now: float) -> float:
    created = coin.get("created_timestamp") or 0
    if created > 10_000_000_000:
        created = created / 1000.0
    if created <= 0:
        return 0.0
    return max(0.0, now - created)
