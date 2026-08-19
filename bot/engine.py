from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .attention import Attention, Story
from .pump import fetch_coin
from .state import State
from .structure import Structure, inspect
from .tape import decide as tape_decide

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

    row = state.upsert_tape(coin)
    created_ts = _created_ts(coin)
    older = state.older_same_name(coin.get("symbol") or "", coin.get("name") or "", created_ts)
    copies = state.same_symbol_copies(coin.get("symbol") or "", created_ts)
    call = tape_decide(coin, row, older=older, copies=copies, now=time.time())
    v.path = call.path
    v.fail_reason = call.reason
    if call.armed_mc:
        v.extras = [f"armed ${call.armed_mc:,.0f}"]

    if call.action == "skip":
        gate = "skip"
        if "farm" in call.reason or "mayhem" in call.reason or "cashback" in call.reason or "copy" in call.reason:
            gate = "farm"
        elif call.reason.startswith("too old"):
            gate = "age"
        elif "dumped" in call.reason:
            gate = "dumped"
        elif "older mint" in call.reason:
            gate = "first-mover"
        elif "already $" in call.reason or "chase" in call.reason:
            gate = "late"
        v.failed_gate = gate
        state.mark_tape(mint, "skipped", call.reason)
        return v

    if call.action == "arm":
        state.arm_tape(mint, call.armed_mc or usd)
        v.failed_gate = "watch"
        v.story = Story(
            title=f"Armed first print ${call.armed_mc:,.0f}",
            url=coin.get("url") or "",
            source="tape",
            seen_at=time.time(),
        )
        return v

    if call.action == "watch":
        v.failed_gate = "watch"
        return v

    if config.MAX_SIGNALS_PER_DAY > 0 and state.signals_today() >= config.MAX_SIGNALS_PER_DAY:
        v.failed_gate = "quota"
        v.fail_reason = "daily signal cap reached"
        return v

    structure = await inspect(http, coin)
    v.structure = structure
    if not structure.ok:
        v.failed_gate = "structure"
        v.fail_reason = "; ".join(structure.reasons_fail)
        return v

    v.post = True
    v.path = "tape"
    v.match_score = 80
    v.failed_gate = ""
    v.story = Story(
        title=f"Tape buy-in · {call.reason}",
        url=coin.get("url") or "",
        source="tape",
        seen_at=time.time(),
    )
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
