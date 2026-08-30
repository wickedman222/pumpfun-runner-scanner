"""Launch-time rules filter. No prediction.

Pipeline (pump.fun latest feed + Solana RPC / Helius if HELIUS_API_KEY is set):

  t < 5s   socials, opening-block bundle, top-5 holders
  t = 30s  organic volume (ex-dev cluster)
  pass     isolated paper buy — never signs a real tx

RPC used:
  getSignaturesForAddress(bonding_curve)
  getTransaction (jsonParsed) for create + early buys
  getTokenLargestAccounts(mint) + getMultipleAccounts for owners
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import config
from .httputil import rpc
from .pump import age_seconds, latest_coins

log = logging.getLogger("runner")

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CURVE_OWNERS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    PUMP,
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWftQeMjFwM6Uo",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMDkdrT4eFf4",
}
SUPPLY = 1_000_000_000.0


@dataclass
class Launch:
    mint: str
    coin: dict
    cluster: set[str] = field(default_factory=set)
    ready_vol_at: float = 0.0
    inst_ok: bool = False


def has_socials(coin: dict) -> str:
    tw = (coin.get("twitter") or "").strip()
    tg = (coin.get("telegram") or "").strip()
    if tw or tg:
        return ""
    return "blank socials"


def bundle_pct(owners_amt: dict[str, float], cluster: set[str]) -> float:
    held = sum(owners_amt.get(w, 0.0) for w in cluster)
    return 100.0 * held / SUPPLY if SUPPLY else 0.0


def top5_pct(owners_amt: dict[str, float], skip: set[str]) -> float:
    ranked = sorted(
        ((w, a) for w, a in owners_amt.items() if w not in skip and a > 0),
        key=lambda x: x[1],
        reverse=True,
    )
    return 100.0 * sum(a for _, a in ranked[:5]) / SUPPLY if SUPPLY else 0.0


def _pubkey(item) -> str:
    if isinstance(item, dict):
        return item.get("pubkey") or ""
    return str(item or "")


def _amt(b: dict) -> float:
    return float(((b.get("uiTokenAmount") or {}).get("uiAmount")) or 0)


def mint_owners_from_tx(tx: dict, mint: str) -> dict[str, float]:
    meta = (tx or {}).get("meta") or {}
    if not tx or meta.get("err"):
        return {}
    out: dict[str, float] = {}
    for b in meta.get("postTokenBalances") or []:
        if (b.get("mint") or "") != mint:
            continue
        owner = b.get("owner") or ""
        if not owner:
            continue
        out[owner] = out.get(owner, 0.0) + _amt(b)
    return out


def opening_cluster(owners_amt: dict[str, float], creator: str, skip: set[str]) -> set[str]:
    cluster = set()
    if creator:
        cluster.add(creator)
    for w, amt in owners_amt.items():
        if w in skip or amt <= 0:
            continue
        cluster.add(w)
    return cluster


def organic_sol(tx: dict, mint: str, cluster: set[str]) -> float:
    """SOL spent by non-cluster fee-payer buys of this mint."""
    meta = (tx or {}).get("meta") or {}
    if not tx or meta.get("err"):
        return 0.0
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    pubs = [_pubkey(k) for k in keys]
    payer = pubs[0] if pubs else ""
    if not payer or payer in cluster:
        return 0.0
    pre = {
        (b.get("mint"), b.get("owner")): _amt(b)
        for b in (meta.get("preTokenBalances") or [])
    }
    bought = False
    for b in meta.get("postTokenBalances") or []:
        if (b.get("mint") or "") != mint:
            continue
        owner = b.get("owner") or ""
        if owner in cluster:
            continue
        after = _amt(b)
        before = float(pre.get((mint, owner), 0) or 0)
        if after > before + 1:
            bought = True
            break
    if not bought:
        return 0.0
    pre_lam = list(meta.get("preBalances") or [])
    post_lam = list(meta.get("postBalances") or [])
    if not pre_lam or not post_lam:
        return 0.0
    spent = (float(pre_lam[0]) - float(post_lam[0])) / 1e9
    return max(0.0, spent)


async def _tx(sig: str) -> dict:
    js = await rpc(
        "getTransaction",
        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )
    return (js or {}).get("result") or {}


async def _curve_sigs(curve: str, limit: int = 30) -> list[dict]:
    js = await rpc(
        "getSignaturesForAddress",
        [curve, {"limit": limit, "commitment": "confirmed"}],
    )
    return list((js or {}).get("result") or [])


async def largest_owners(mint: str, skip: set[str]) -> dict[str, float]:
    js = await rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
    rows = ((js or {}).get("result") or {}).get("value") or []
    accs = [r.get("address") for r in rows if r.get("address")]
    if not accs:
        return {}
    info = await rpc(
        "getMultipleAccounts",
        [accs, {"encoding": "jsonParsed", "commitment": "confirmed"}],
    )
    values = ((info or {}).get("result") or {}).get("value") or []
    out: dict[str, float] = {}
    for acc, val in zip(accs, values):
        del acc
        if not val:
            continue
        parsed = (((val.get("data") or {}).get("parsed") or {}).get("info") or {})
        owner = parsed.get("owner") or ""
        amt = float(((parsed.get("tokenAmount") or {}).get("uiAmount")) or 0)
        if not owner or owner in skip or amt <= 0:
            continue
        out[owner] = out.get(owner, 0.0) + amt
    return out


async def instant_fail(coin: dict) -> tuple[str, set[str]]:
    """Gates 1–3. Returns (reason, bundle cluster). Empty reason = pass."""
    why = has_socials(coin)
    if why:
        return why, set()
    mint = coin.get("mint") or ""
    curve = coin.get("bonding_curve") or ""
    abc = coin.get("associated_bonding_curve") or ""
    creator = coin.get("creator") or ""
    skip = set(CURVE_OWNERS)
    skip.update({curve, abc, coin.get("pool_address") or ""})
    skip.discard("")
    owners: dict[str, float] = {}
    cluster = {creator} if creator else set()
    if curve:
        sigs = await _curve_sigs(curve, 20)
        opening_slot = None
        # newest-first; opening block is the oldest slot in this young set
        slots = [int(s.get("slot") or 0) for s in sigs if s.get("slot")]
        if slots:
            opening_slot = min(slots)
        for row in sigs:
            if row.get("err"):
                continue
            if opening_slot and int(row.get("slot") or 0) != opening_slot:
                continue
            tx = await _tx(row.get("signature") or "")
            part = mint_owners_from_tx(tx, mint)
            for w, a in part.items():
                owners[w] = max(owners.get(w, 0.0), a)
    cluster = opening_cluster(owners, creator, skip)
    if len(cluster - skip) >= 2:
        pct = bundle_pct(owners, cluster - skip)
        if pct > config.STRICT_BUNDLE_PCT:
            return f"bundle {pct:.1f}% across {len(cluster - skip)} wallets", cluster
    held = await largest_owners(mint, skip)
    for w, a in owners.items():
        held[w] = max(held.get(w, 0.0), a)
    t5 = top5_pct(held, skip)
    if t5 > config.STRICT_TOP5_PCT:
        return f"top5 {t5:.1f}%", cluster
    return "", cluster


async def volume_usd(coin: dict, cluster: set[str], until_ts: float) -> float:
    mint = coin.get("mint") or ""
    curve = coin.get("bonding_curve") or ""
    created = int((coin.get("created_timestamp") or 0) / 1000)
    if not mint or not curve or created <= 0:
        return 0.0
    sol = 0.0
    for row in await _curve_sigs(curve, 40):
        bt = int(row.get("blockTime") or 0)
        if bt and bt > until_ts:
            continue
        if bt and bt < created:
            continue
        if row.get("err"):
            continue
        tx = await _tx(row.get("signature") or "")
        sol += organic_sol(tx, mint, cluster)
    return sol * float(config.SOL_USD)


async def scan_latest(http, state, armed: dict[str, Launch]) -> list[dict]:
    """Return coins that just cleared all four gates."""
    now = time.time()
    fresh = await latest_coins(http, limit=30)
    buys: list[dict] = []
    for coin in fresh:
        mint = coin.get("mint") or ""
        if not mint or state.already_posted(mint) or state.paper_position(mint):
            continue
        age = age_seconds(coin, now)
        if mint in armed:
            launch = armed[mint]
            if now < launch.ready_vol_at:
                continue
            vol = await volume_usd(coin, launch.cluster, launch.ready_vol_at)
            del armed[mint]
            if vol < config.STRICT_MIN_VOL_USD:
                log.info("Skip %s volume $%.0f", coin.get("symbol"), vol)
                state.mark_posted(coin, f"volume ${vol:.0f}")
                continue
            coin["strict_vol"] = vol
            buys.append(coin)
            continue
        if age < 0 or age > config.STRICT_CATCH_SEC:
            continue
        why, cluster = await instant_fail(coin)
        if why:
            log.info("Skip %s %s", coin.get("symbol"), why)
            state.mark_posted(coin, why)
            continue
        created = int((coin.get("created_timestamp") or 0) / 1000) or now
        armed[mint] = Launch(
            mint=mint,
            coin=coin,
            cluster=cluster,
            ready_vol_at=created + config.STRICT_VOL_SEC,
            inst_ok=True,
        )
        log.info(
            "Armed %s socials+bundle+top5 ok · vol check at +%ss",
            coin.get("symbol"),
            config.STRICT_VOL_SEC,
        )
    return buys
