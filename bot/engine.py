from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .attention import (
    Attention,
    Story,
    extract_farm_reason,
)
from .pump import age_seconds, fetch_coin
from .state import State
from .structure import Structure, inspect

log = logging.getLogger("runner")

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

    # News only if a headline already exists in the window. Do not search
    # Wikipedia/Google for the ticker — that is following a name.
    hits = attention.match_coin(coin["symbol"], coin["name"])
    path = ""
    story = None
    match_score = 0
    participants = int(coin.get("num_participants") or 0)
    if hits and hits[0][1] >= config.MIN_MATCH_SCORE:
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
    elif usd >= config.MIN_TAPE_MC:
        path = "tape"
        match_score = 70
        story = Story(
            title=f"First-mover tape · MC ${usd:,.0f}",
            url=coin.get("url") or "",
            source="tape",
            seen_at=time.time(),
        )
    else:
        v.failed_gate = "attention"
        v.fail_reason = "no news map / live crowd / tape book"
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


async def confirm_expansion(
    http: httpx.AsyncClient, coin: dict, first_look: dict
) -> tuple[str, str, dict]:
    """Return (post|wait|drop, reason, fresh).

    Expansion is vs the first eligible print, not vs a 3-minute local top.
    FISHBONE sat $106k → $93k then printed $200k+ eight minutes later.
    """
    fresh = await fetch_coin(http, coin["mint"])
    if not fresh:
        return "wait", "could not refetch coin", coin

    first_usd = float(first_look.get("usd_market_cap") or 0)
    now_usd = float(fresh.get("usd_market_cap") or 0)
    ath = float(fresh.get("ath_market_cap") or now_usd)
    first_replies = int(first_look.get("reply_count") or 0)
    now_replies = int(fresh.get("reply_count") or 0)

    if ath > 5_000 and now_usd < 0.4 * ath:
        return "drop", f"dumped after first look (${now_usd:,.0f} vs ATH ${ath:,.0f})", fresh
    if first_usd > 3_000 and now_usd < 0.6 * first_usd:
        return "drop", f"MC rolled over ${first_usd:,.0f} → ${now_usd:,.0f}", fresh

    expanding = False
    reasons = []
    if now_replies > first_replies:
        expanding = True
        reasons.append(f"replies {first_replies}→{now_replies}")
    if first_usd > 0 and now_usd >= first_usd * 1.20:
        expanding = True
        reasons.append(f"MC ${first_usd:,.0f}→${now_usd:,.0f}")
    if fresh.get("complete") and not first_look.get("complete"):
        expanding = True
        reasons.append("graduated during wait")
    if first_look.get("is_currently_live") and not fresh.get("is_currently_live") and not expanding:
        return "drop", "livestream died and book did not expand", fresh

    farm = extract_farm_reason(fresh)
    if farm:
        return "drop", farm, fresh

    if not expanding:
        return "wait", f"still ${now_usd:,.0f} vs first ${first_usd:,.0f}", fresh
    return "post", ", ".join(reasons), fresh
