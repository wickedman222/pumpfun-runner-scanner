from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger("runner")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, application/rss+xml, text/xml, */*",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/",
}

# Public Solana RPC 403s the pump.fun Origin/Referer the rest of the bot sends.
_RPC_HTTP: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20.0, headers=HEADERS, follow_redirects=True)


def _rpc_client() -> httpx.AsyncClient:
    global _RPC_HTTP
    if _RPC_HTTP is None or _RPC_HTTP.is_closed:
        _RPC_HTTP = httpx.AsyncClient(
            timeout=90.0,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
    return _RPC_HTTP


async def rpc(method: str, params: list, timeout: float = 20.0) -> dict | None:
    last_status = 0
    for attempt in range(4):
        try:
            r = await _rpc_client().post(
                config.SOLANA_RPC_URL,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            last_status = r.status_code
            if r.status_code == 429:
                await asyncio.sleep(1.4 * (attempt + 1))
                continue
            if r.status_code != 200:
                log.warning("Solana RPC %s HTTP %s", method, r.status_code)
                return None
            js = r.json()
            if isinstance(js, dict) and js.get("error"):
                log.warning("Solana RPC %s: %s", method, js["error"])
                return None
            return js
        except Exception as exc:
            log.warning("Solana RPC %s fail: %s", method, exc)
            return None
    log.warning("Solana RPC %s HTTP %s", method, last_status or 429)
    return None


async def get_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
    try:
        r = await http.get(url, **kwargs)
        if r.status_code == 429:
            await asyncio.sleep(2.0)
            r = await http.get(url, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.debug("GET JSON fail %s: %s", url, exc)
        return None


async def get_text(http: httpx.AsyncClient, url: str, **kwargs: Any) -> str:
    try:
        r = await http.get(url, **kwargs)
        r.raise_for_status()
        return r.text
    except Exception as exc:
        log.debug("GET text fail %s: %s", url, exc)
        return ""
