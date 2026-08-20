"""Spot every launch. Buy only confirmed strength — never the graduation fill.

Overnight book died because we bought `first print $X · graduated`. That is
the curve fill. Explore runners are the ones that *held and kept going*.

Spot: see it on-curve in the $8k–$50k band, near ATH.
Buy: still near ATH, +60% from *our* arm, at least 4 minutes later,
still under the chase line. Graduation alone is never a buy.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .attention import extract_farm_reason
from .pump import age_seconds


@dataclass
class Call:
    action: str  # watch | arm | trigger | skip
    reason: str
    path: str = "tape"
    armed_mc: float = 0.0


def _dd(usd: float, ath: float) -> float:
    if ath <= 0:
        return 0.0
    return max(0.0, 1.0 - usd / ath)


def decide(coin: dict, row: dict, *, older: dict | None, copies: int, now: float) -> Call:
    usd = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or usd or 0)
    complete = bool(coin.get("complete"))
    farm = extract_farm_reason(coin)
    if farm:
        return Call("skip", farm)
    age = age_seconds(coin, now)
    if age > config.MAX_ACTIVE_AGE_SEC:
        return Call("skip", f"too old ({age/60:.0f}m)")
    if older and older.get("mint") and older.get("mint") != coin.get("mint"):
        return Call("skip", f"older mint already exists: {str(older.get('mint'))[:8]}…")
    if copies >= config.META_COPY_MIN:
        return Call("skip", f"copy farm ({copies} same-ticker launches)")

    # Already dead vs ATH — not a buy, not an arm.
    if ath > config.MIN_ARM_MC and _dd(usd, ath) > 0.45:
        return Call("skip", f"already dumped (${usd:,.0f} vs ATH ${ath:,.0f})")

    armed_mc = float(row.get("armed_mc") or 0)
    armed_at = float(row.get("armed_at") or 0)

    # First look already graduated = we did not see the curve. That print is
    # exit liquidity (Abel $143k, HDUCK $99k, SLAPSTICK $53k).
    if armed_mc <= 0 and complete:
        return Call("skip", f"first seen already graduated ${usd:,.0f}")

    if armed_mc <= 0:
        if usd > config.MAX_ARM_MC:
            return Call("skip", f"too far up the curve to arm ${usd:,.0f}")
        if usd < config.MIN_ARM_MC:
            return Call("watch", f"thin book ${usd:,.0f}")
        if _dd(usd, ath) > config.MAX_DD_AT_BUY:
            return Call("watch", f"off highs ${usd:,.0f} vs ATH ${ath:,.0f}")
        return Call("arm", f"on-curve first print ${usd:,.0f}", armed_mc=usd)

    held = (now - armed_at) if armed_at else 0.0
    if held < config.MIN_ARM_HOLD_SEC:
        return Call(
            "watch",
            f"armed ${armed_mc:,.0f} now ${usd:,.0f} · waiting {config.MIN_ARM_HOLD_SEC - held:.0f}s",
            armed_mc=armed_mc,
        )

    if usd > config.MAX_FIRST_LOOK_MC:
        return Call("skip", f"chase ${usd:,.0f}", armed_mc=armed_mc)
    if _dd(usd, ath) > config.MAX_DD_AT_BUY:
        return Call(
            "watch",
            f"armed ${armed_mc:,.0f} now ${usd:,.0f} but off highs (ATH ${ath:,.0f})",
            armed_mc=armed_mc,
        )
    if usd < config.MIN_BUY_MC:
        return Call("watch", f"armed ${armed_mc:,.0f} now ${usd:,.0f}", armed_mc=armed_mc)

    expanded = usd >= armed_mc * config.EXPANSION_MULT
    if not expanded:
        return Call("watch", f"armed ${armed_mc:,.0f} now ${usd:,.0f}", armed_mc=armed_mc)

    return Call(
        "trigger",
        f"held {held/60:.1f}m · ${armed_mc:,.0f}→${usd:,.0f} · dd {_dd(usd, ath)*100:.0f}%",
        armed_mc=armed_mc,
    )
