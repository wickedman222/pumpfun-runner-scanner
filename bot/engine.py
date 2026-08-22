from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .attention import Attention, Story, extract_farm_reason
from .pump import fetch_coin
from .state import State
from .structure import Structure, inspect
from .tape import decide as tape_decide
from .wallets import overlap as wallet_overlap, wallet_buy_ok

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
        elif "already $" in call.reason or "chase" in call.reason or "too far" in call.reason or "graduated" in call.reason:
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

    row = state.upsert_tape(coin)
    return await _maybe_buy(http, state, coin, v, call, row)


def _live_ok(coin: dict, armed_mc: float) -> bool:
    if not coin.get("is_currently_live"):
        return False
    if int(coin.get("num_participants") or 0) < config.MIN_LIVE_PARTICIPANTS:
        return False
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    if usd < config.MIN_LIVE_MC or usd > config.WALLET_MAX_BUY_MC:
        return False
    if ath > 0 and usd < ath * (1.0 - config.MAX_DD_AT_BUY):
        return False
    if coin.get("complete") and armed_mc <= 0:
        return False
    return True


async def _maybe_buy(
    http: httpx.AsyncClient,
    state: State,
    coin: dict,
    v: Verdict,
    call,
    row: dict,
) -> Verdict:
    """Need two independent reasons. One wick is not a buy."""
    usd = float(coin.get("usd_market_cap") or 0)
    armed_mc = float(row.get("armed_mc") or 0)
    armed_at = float(row.get("armed_at") or 0)
    held = (time.time() - armed_at) if armed_at else 0.0
    live_ok = _live_ok(coin, armed_mc)
    tape_ready = call.action == "trigger"
    live_held = live_ok and armed_mc > 0 and held >= config.MIN_ARM_HOLD_SEC

    hits: list = []
    why = wallet_buy_ok(coin, time.time())
    if not why:
        hits = await wallet_overlap(http, state, coin)

    score = 0
    bits: list[str] = []
    if tape_ready:
        score += 1
        bits.append(call.reason)
    if live_held:
        score += 2
        bits.append(f"live {int(coin.get('num_participants') or 0)} in room")
    elif live_ok:
        score += 1
        bits.append(f"live {int(coin.get('num_participants') or 0)}")
    if len(hits) >= 1:
        score += 1
        bits.append(f"{len(hits)} sniper wallet(s)")
    if len(hits) >= 2:
        score += 1

    if score < 2:
        v.failed_gate = "watch"
        v.fail_reason = (call.reason or "watching") + " · need live/snipers/hold"
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

    if len(hits) >= 2:
        path = "wallet"
    elif live_held or live_ok:
        path = "live"
    else:
        path = "tape"
    names = ", ".join(f"{w[:6]}…×{n}" for w, _pct, n in hits[:4])
    extra = f" ({names})" if names else ""
    v.post = True
    v.path = path
    v.match_score = 50 + 20 * min(score, 4)
    v.failed_gate = ""
    v.fail_reason = "; ".join(bits) + extra
    v.story = Story(
        title=f"Buy {path} · " + "; ".join(bits),
        url=coin.get("url") or "",
        source=path,
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
