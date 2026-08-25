from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from . import config
from .httputil import get_json
from .pump import creator_coins

log = logging.getLogger("runner")

RISK_KILL = {
    "rugged",
    "honeypot",
    "freeze authority",
    "mint authority",
    "bundled",
    "bundle",
    "insider",
}


@dataclass
class Structure:
    ok: bool
    reasons_fail: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    total_holders: int = 0
    top_holder_pct: float = 0.0
    top10_pct: float = 0.0
    insider_pct: float = 0.0
    creator_tokens: int = 0
    rug_score: int = 0
    rugged: bool = False


def _is_lp(owner: str, coin: dict) -> bool:
    if not owner:
        return True
    if owner in config.LP_OWNERS:
        return True
    if owner == coin.get("associated_bonding_curve"):
        return True
    if owner == coin.get("pool_address"):
        return True
    if owner == coin.get("creator"):
        return False
    return False


async def inspect(http: httpx.AsyncClient, coin: dict) -> Structure:
    out = Structure(ok=True)
    mint = coin["mint"]

    report = await get_json(http, f"{config.RUGCHECK_API}/tokens/{mint}/report")
    holders = []
    if isinstance(report, dict):
        out.rugged = bool(report.get("rugged"))
        out.rug_score = int(report.get("score") or 0)
        out.total_holders = int(report.get("totalHolders") or 0)
        holders = report.get("topHolders") or []
        risks = report.get("risks") or []
        for risk in risks:
            name = str(risk.get("name") or risk.get("description") or "").lower()
            level = str(risk.get("level") or "").lower()
            early = float(coin.get("usd_market_cap") or 0) < 30_000
            kills = RISK_KILL if not early else {
                "rugged", "honeypot", "freeze authority", "mint authority",
            }
            if any(k in name for k in kills) and level in {"danger", "warn", "critical", ""}:
                out.reasons_fail.append(f"rugcheck: {risk.get('name')}")
        if out.rugged:
            out.reasons_fail.append("rugcheck marked rugged")
        usd_now = float(coin.get("usd_market_cap") or 0)
        # Young curve books are always concentrated. Rug-score/holders only after size.
        if usd_now >= 30_000 and out.rug_score and out.rug_score > config.MAX_RUGCHECK_SCORE:
            out.reasons_fail.append(f"rugcheck score {out.rug_score} > {config.MAX_RUGCHECK_SCORE}")
    else:
        out.notes.append("rugcheck unavailable — using weak structure fallback")

    real_holders = []
    insider_pct = 0.0
    for h in holders:
        owner = h.get("owner") or h.get("address") or ""
        pct = float(h.get("pct") or 0)
        if _is_lp(owner, coin):
            continue
        real_holders.append((owner, pct, bool(h.get("insider"))))
        if h.get("insider"):
            insider_pct += pct

    real_holders.sort(key=lambda x: x[1], reverse=True)
    out.insider_pct = round(insider_pct, 2)
    if real_holders:
        out.top_holder_pct = round(real_holders[0][1], 2)
        out.top10_pct = round(sum(p for _, p, _ in real_holders[:10]), 2)

    usd_now = float(coin.get("usd_market_cap") or 0)
    if usd_now >= 30_000:
        if out.top_holder_pct > config.MAX_TOP_HOLDER_PCT:
            out.reasons_fail.append(
                f"top holder {out.top_holder_pct:.1f}% > {config.MAX_TOP_HOLDER_PCT}%"
            )
        if out.top10_pct > config.MAX_TOP10_PCT:
            out.reasons_fail.append(f"top10 {out.top10_pct:.1f}% > {config.MAX_TOP10_PCT}%")
        if out.insider_pct > config.MAX_INSIDER_PCT:
            out.reasons_fail.append(f"insider {out.insider_pct:.1f}% > {config.MAX_INSIDER_PCT}%")
        if out.total_holders and out.total_holders < config.MIN_UNIQUE_HOLDERS:
            out.reasons_fail.append(f"only {out.total_holders} holders at ${usd_now:.0f} MC")

    created = await creator_coins(http, coin.get("creator") or "")
    other = [c for c in created if c.get("mint") and c["mint"] != mint]
    out.creator_tokens = len(other)
    if out.creator_tokens > config.MAX_DEV_PRIOR_TOKENS:
        out.reasons_fail.append(
            f"dev launched {out.creator_tokens} other tokens (max {config.MAX_DEV_PRIOR_TOKENS})"
        )

    # dump already printed
    ath = coin.get("ath_market_cap") or 0
    usd = coin.get("usd_market_cap") or 0
    if ath > 8_000 and usd < 0.35 * ath:
        out.reasons_fail.append(f"already dumped (now ${usd:,.0f} vs ATH ${ath:,.0f})")

    out.ok = not out.reasons_fail
    return out
