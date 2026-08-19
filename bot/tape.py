"""Universe tape: every launch is tracked. Buy-in is the backtested trigger.

Backtest pick_entry: first print in 6h with MC $8k–$200k, then +20% or
graduation (~$60k / complete). Live does the same on the prints we actually
recorded, not on a random last-trade snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .attention import extract_farm_reason
from .pump import age_seconds


def _created_ts(coin: dict) -> int:
    raw = int(coin.get("created_timestamp") or 0)
    if raw > 10_000_000_000:
        raw = raw // 1000
    return raw


@dataclass
class Call:
    action: str  # watch | arm | trigger | skip
    reason: str
    path: str = "tape"
    armed_mc: float = 0.0


def decide(coin: dict, row: dict, *, older: dict | None, copies: int, now: float) -> Call:
    """Core buy rule. Name is not an input."""
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    farm = extract_farm_reason(coin)
    if farm:
        return Call("skip", farm)
    age = age_seconds(coin, now)
    if age > config.MAX_ACTIVE_AGE_SEC:
        return Call("skip", f"too old ({age/60:.0f}m)")
    if ath > 8_000 and usd < 0.4 * ath:
        return Call("skip", f"already dumped (${usd:,.0f} vs ATH ${ath:,.0f})")
    if older and older.get("mint") and older.get("mint") != coin.get("mint"):
        return Call("skip", f"older mint already exists: {str(older.get('mint'))[:8]}…")
    if copies >= config.META_COPY_MIN:
        return Call("skip", f"copy farm ({copies} same-ticker launches)")

    live = bool(coin.get("is_currently_live"))
    parts = int(coin.get("num_participants") or 0)
    live_ok = live and parts >= config.MIN_LIVE_PARTICIPANTS and usd >= config.MIN_LIVE_MC

    armed_mc = float(row.get("armed_mc") or 0)
    if armed_mc <= 0:
        if usd > config.MAX_FIRST_LOOK_MC:
            return Call("skip", f"already ${usd:,.0f} on first print in band")
        if usd < config.MIN_ARM_MC:
            return Call("watch", f"thin book ${usd:,.0f}")
        confirm = (
            bool(coin.get("complete"))
            or usd >= config.GRADUATE_CONFIRM_MC
            or live_ok
        )
        if confirm:
            why = "graduated" if coin.get("complete") or usd >= config.GRADUATE_CONFIRM_MC else f"live crowd ({parts})"
            return Call("trigger", f"first print ${usd:,.0f} · {why}", armed_mc=usd)
        return Call("arm", f"first print in band ${usd:,.0f}", armed_mc=usd)

    if usd > config.MAX_FIRST_LOOK_MC and usd < armed_mc * config.EXPANSION_MULT:
        return Call("skip", f"chase ${usd:,.0f} without +20% from ${armed_mc:,.0f}", armed_mc=armed_mc)

    expanded = usd >= armed_mc * config.EXPANSION_MULT
    graduated = bool(coin.get("complete")) or usd >= config.GRADUATE_CONFIRM_MC
    if expanded or graduated or live_ok:
        bits = []
        if expanded:
            bits.append(f"MC ${armed_mc:,.0f}→${usd:,.0f}")
        if graduated:
            bits.append("graduated")
        if live_ok:
            bits.append(f"live {parts}")
        return Call("trigger", " · ".join(bits), armed_mc=armed_mc)
    return Call("watch", f"armed ${armed_mc:,.0f} now ${usd:,.0f}", armed_mc=armed_mc)
