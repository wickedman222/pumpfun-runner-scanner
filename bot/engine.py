from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .attention import Attention, Story, is_generic_ticker, score_match
from .pump import age_seconds, fetch_coin
from .state import State
from .structure import Structure, inspect

log = logging.getLogger("runner")

_targeted_hits: list[float] = []


def _allow_targeted_search() -> bool:
    now = time.time()
    while _targeted_hits and now - _targeted_hits[0] > 60:
        _targeted_hits.pop(0)
    if len(_targeted_hits) >= 8:
        return False
    _targeted_hits.append(now)
    return True


@dataclass
class Verdict:
    post: bool
    mint: str
    coin: dict
    story: Story | None = None
    match_score: int = 0
    structure: Structure | None = None
    failed_gate: str = ""
    fail_reason: str = ""
    extras: list[str] = field(default_factory=list)


async def evaluate_new(http: httpx.AsyncClient, attention: Attention, state: State, coin: dict) -> Verdict:
    mint = coin["mint"]
    v = Verdict(post=False, mint=mint, coin=coin)

    if config.MAX_SIGNALS_PER_DAY > 0 and state.signals_today() >= config.MAX_SIGNALS_PER_DAY:
        v.failed_gate = "quota"
        v.fail_reason = "daily signal cap reached"
        return v

    age = age_seconds(coin, time.time())
    if age > config.MAX_TOKEN_AGE_SEC:
        v.failed_gate = "age"
        v.fail_reason = f"too old ({age/60:.0f}m)"
        return v

    if is_generic_ticker(coin.get("symbol", ""), coin.get("name", "")):
        v.failed_gate = "generic"
        v.fail_reason = "generic ticker/name"
        return v

    # Gate 0+1: exogenous story + first-mover
    hits = attention.match_coin(coin["symbol"], coin["name"])
    name = coin.get("name") or ""
    distinctive = (" " in name.strip() and len(name) >= 6) or len(name) >= 8
    if not hits and distinctive and _allow_targeted_search():
        targeted = await attention.search_subject(
            http, f'"{name}"' if " " in name else name
        )
        scored = []
        for story in targeted:
            s = score_match(coin["symbol"], coin["name"], story)
            if s >= config.MIN_MATCH_SCORE:
                scored.append((story, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        hits = scored

    if not hits or hits[0][1] < config.MIN_MATCH_SCORE:
        v.failed_gate = "attention"
        v.fail_reason = "no exogenous first-mover story"
        return v

    story, match_score = hits[0]
    v.story = story
    v.match_score = match_score

    created_ts = int((coin.get("created_timestamp") or 0) / 1000)
    older = state.older_same_name(coin["symbol"], coin["name"], created_ts)
    if older and older.get("mint") != mint:
        v.failed_gate = "first-mover"
        v.fail_reason = f"older mint already exists: {older['mint'][:8]}…"
        return v

    # Gate 2+3: structure
    structure = await inspect(http, coin)
    v.structure = structure
    if not structure.ok:
        v.failed_gate = "structure"
        v.fail_reason = "; ".join(structure.reasons_fail)
        return v

    # Candidate — caller waits, then confirm_expansion
    v.failed_gate = "wait-expansion"
    v.fail_reason = "passed screens; waiting for expansion"
    return v


async def confirm_expansion(http: httpx.AsyncClient, coin: dict, prev: dict) -> tuple[bool, str, dict]:
    fresh = await fetch_coin(http, coin["mint"])
    if not fresh:
        return False, "could not refetch coin", coin

    prev_usd = float(prev.get("usd_market_cap") or 0)
    now_usd = float(fresh.get("usd_market_cap") or 0)
    ath = float(fresh.get("ath_market_cap") or now_usd)
    prev_replies = int(prev.get("reply_count") or 0)
    now_replies = int(fresh.get("reply_count") or 0)

    if ath > 5_000 and now_usd < 0.4 * ath:
        return False, f"dumped after first look (${now_usd:,.0f} vs ATH ${ath:,.0f})", fresh
    if prev_usd > 3_000 and now_usd < 0.6 * prev_usd:
        return False, f"MC rolled over ${prev_usd:,.0f} → ${now_usd:,.0f}", fresh

    expanding = False
    reasons = []
    if now_replies > prev_replies:
        expanding = True
        reasons.append(f"replies {prev_replies}→{now_replies}")
    if now_usd >= prev_usd * 1.05:
        expanding = True
        reasons.append(f"MC ${prev_usd:,.0f}→${now_usd:,.0f}")
    if fresh.get("complete") and not prev.get("complete"):
        expanding = True
        reasons.append("graduated during wait")
    if fresh.get("is_currently_live"):
        reasons.append("livestream live")

    if not expanding and now_usd >= prev_usd and now_replies >= prev_replies and now_usd >= 8_000:
        # flat-but-held with real size is allowed if attention was strong
        expanding = True
        reasons.append("held bid after wait")

    if not expanding:
        return False, "attention did not expand after wait", fresh
    return True, ", ".join(reasons), fresh
