from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .attention import (
    Attention,
    Story,
    culture_hit_ok,
    extract_farm_reason,
    is_common_subject,
    is_distinctive_name,
    is_generic_ticker,
    is_meme_name,
    score_match,
    search_query_for,
)
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


def _created_ts(coin: dict) -> int:
    raw = int(coin.get("created_timestamp") or 0)
    if raw > 10_000_000_000:
        raw = raw // 1000
    return raw


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
    path: str = ""


async def evaluate_new(
    http: httpx.AsyncClient,
    attention: Attention,
    state: State,
    coin: dict,
) -> Verdict:
    mint = coin["mint"]
    v = Verdict(post=False, mint=mint, coin=coin)
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd)

    if config.MAX_SIGNALS_PER_DAY > 0 and state.signals_today() >= config.MAX_SIGNALS_PER_DAY:
        v.failed_gate = "quota"
        v.fail_reason = "daily signal cap reached"
        return v

    if is_generic_ticker(coin.get("symbol", ""), coin.get("name", "")):
        v.failed_gate = "generic"
        v.fail_reason = "generic ticker/name"
        return v

    farm = extract_farm_reason(coin)
    if farm:
        v.failed_gate = "farm"
        v.fail_reason = farm
        return v

    age = age_seconds(coin, time.time())
    live = bool(coin.get("is_currently_live"))
    max_age = config.MAX_LIVE_AGE_SEC if live else config.MAX_TOKEN_AGE_SEC
    # Organic (non-BOOST) books can sit on the homepage for hours before they print.
    max_age = max(max_age, config.MAX_ACTIVE_AGE_SEC)
    if age > max_age:
        v.failed_gate = "age"
        v.fail_reason = f"too old ({age/60:.0f}m)"
        return v

    if ath > 8_000 and usd < 0.4 * ath:
        v.failed_gate = "dumped"
        v.fail_reason = f"already dumped (${usd:,.0f} vs ATH ${ath:,.0f})"
        return v

    created_ts = _created_ts(coin)
    older = state.older_same_name(coin["symbol"], coin["name"], created_ts)
    if older and older.get("mint") != mint:
        v.failed_gate = "first-mover"
        v.fail_reason = f"older mint already exists: {older['mint'][:8]}…"
        return v

    copies = state.same_symbol_copies(coin.get("symbol") or "", created_ts)
    if copies >= config.META_COPY_MIN:
        v.failed_gate = "farm"
        v.fail_reason = f"copy farm ({copies} same-ticker launches)"
        return v

    if usd > config.MAX_FIRST_LOOK_MC:
        v.failed_gate = "late"
        v.fail_reason = f"already ${usd:,.0f} on first look"
        return v

    hits = attention.match_coin(coin["symbol"], coin["name"])
    distinctive = is_distinctive_name(coin.get("symbol") or "", coin.get("name") or "")
    common = is_common_subject(coin.get("symbol") or "", coin.get("name") or "")
    if not hits and distinctive and not common and _allow_targeted_search():
        targeted = await attention.search_subject(http, search_query_for(coin))
        scored = []
        for item in targeted:
            s = score_match(coin["symbol"], coin["name"], item)
            if s >= config.MIN_MATCH_SCORE:
                scored.append((item, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        hits = scored
    path = ""
    story = None
    match_score = 0
    participants = int(coin.get("num_participants") or 0)
    if hits and culture_hit_ok(coin.get("symbol") or "", coin.get("name") or "", hits[0][0], hits[0][1]):
        path = "news"
        story, match_score = hits[0]
    elif (
        live
        and participants >= config.MIN_LIVE_PARTICIPANTS
        and usd >= config.MIN_LIVE_MC
    ):
        path = "live"
        match_score = 85
        title = coin.get("livestream_title") or coin.get("name") or coin.get("symbol")
        story = Story(
            title=f"Livestream ({participants} in room): {title}",
            url=coin.get("url") or "",
            source="live",
            seen_at=time.time(),
        )
    elif is_meme_name(coin.get("symbol") or "", coin.get("name") or ""):
        path = "meme"
        match_score = 80
        who = coin.get("name") or coin.get("symbol")
        story = Story(
            title=f"Meme first-mover: {who}",
            url=coin.get("url") or "",
            source="meme",
            seen_at=time.time(),
        )
    else:
        v.failed_gate = "attention"
        v.fail_reason = "no rare culture / live crowd / meme-name"
        return v

    structure = await inspect(http, coin)
    v.structure = structure
    if not structure.ok:
        v.failed_gate = "structure"
        v.fail_reason = "; ".join(structure.reasons_fail)
        return v

    v.path = path
    v.story = story
    v.match_score = match_score
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
    if prev_usd > 0 and now_usd >= prev_usd * 1.20:
        expanding = True
        reasons.append(f"MC ${prev_usd:,.0f}→${now_usd:,.0f}")
    if fresh.get("complete") and not prev.get("complete"):
        expanding = True
        reasons.append("graduated during wait")
    if prev.get("is_currently_live") and not fresh.get("is_currently_live") and not expanding:
        return False, "livestream died and book did not expand", fresh

    farm = extract_farm_reason(fresh)
    if farm:
        return False, farm, fresh

    if not expanding:
        return False, "attention did not expand after wait", fresh
    return True, ", ".join(reasons), fresh
