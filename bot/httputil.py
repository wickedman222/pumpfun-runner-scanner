from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("runner")

HEADERS = {
    "User-Agent": "pumpfun-runner-scanner/1.0 (+github.com/wickedman222)",
    "Accept": "application/json, application/rss+xml, text/xml, */*",
    "Origin": "https://pump.fun",
}


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20.0, headers=HEADERS, follow_redirects=True)


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
