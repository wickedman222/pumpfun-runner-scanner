"""Paper book. No real SOL — MC multiples stand in for fills."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import config
from .state import State

log = logging.getLogger("runner")


@dataclass
class Fill:
    mint: str
    symbol: str
    side: str
    reason: str
    frac: float
    multiple: float
    sol: float
    cash_after: float
    mc: float
    pos: dict
    equity: float


def buy_size(equity: float, mc: float = 0.0) -> float:
    del mc
    if equity < config.PAPER_MIN_EQUITY:
        return 0.0
    return round(config.PAPER_SIZE_FIXED, 3)


def _mult(pos: dict, mc: float) -> float:
    entry = float(pos.get("entry_mc") or 0)
    if entry <= 0:
        return 0.0
    return max(0.0, float(mc) / entry)


def mark_value(pos: dict, mc: float | None = None) -> float:
    last = float(mc if mc is not None else pos.get("last_mc") or 0)
    return float(pos.get("remaining_qty_sol") or 0) * _mult(pos, last)


def snapshot(state: State) -> dict:
    wallet = state.paper_wallet()
    cash = float(wallet.get("cash_sol") or 0)
    start = float(wallet.get("starting_sol") or config.PAPER_START_SOL)
    opens = state.open_paper_positions()
    unreal = sum(mark_value(p) for p in opens)
    equity = cash + unreal
    closed = [p for p in state.all_paper_positions() if p.get("status") == "closed"]
    return {
        "cash": cash,
        "unreal": unreal,
        "equity": equity,
        "start": start,
        "pnl": equity - start,
        "open": opens,
        "closed_n": len(closed),
        "size": buy_size(equity),
    }


def try_open(state: State, coin: dict, path: str = "", size_sol: float | None = None) -> Fill | None:
    mint = coin.get("mint") or ""
    if not mint or state.paper_position(mint):
        return None
    snap = snapshot(state)
    opens = snap["open"]
    if len(opens) >= config.PAPER_MAX_OPEN:
        log.info("Paper skip %s — already %s open", coin.get("symbol"), len(opens))
        return None
    size = float(size_sol) if size_sol is not None else buy_size(
        snap["equity"], float(coin.get("usd_market_cap") or 0)
    )
    if size <= 0 or snap["cash"] < size:
        log.info("Paper skip %s — cash %.3f size %.3f", coin.get("symbol"), snap["cash"], size)
        return None
    entry_mc = float(coin.get("usd_market_cap") or 0)
    if entry_mc <= 0:
        return None
    qty = size * (1.0 - config.PAPER_FEE) * (1.0 - config.PAPER_ENTRY_SLIP)
    cash = snap["cash"] - size
    now = int(time.time())
    pos = {
        "mint": mint,
        "symbol": (coin.get("symbol") or "").upper(),
        "name": coin.get("name") or "",
        "url": coin.get("url") or "",
        "path": path or "",
        "opened_at": now,
        "cost_sol": size,
        "original_qty_sol": qty,
        "remaining_qty_sol": qty,
        "remaining_frac": 1.0,
        "entry_mc": entry_mc,
        "ath_mc": max(float(coin.get("ath_market_cap") or 0), entry_mc),
        "last_mc": entry_mc,
        "realized_sol": 0.0,
        "tp1_hit": 0,
        "tp2_hit": 0,
        "tp3_hit": 0,
        "status": "open",
        "close_reason": "",
        "closed_at": None,
    }
    state.upsert_paper_position(pos)
    state.set_paper_cash(cash)
    fill = {
        "mint": mint,
        "ts": now,
        "side": "buy",
        "reason": f"signal {path or 'call'}",
        "frac": 1.0,
        "multiple": 1.0,
        "sol": -size,
        "cash_after": cash,
        "mc": entry_mc,
    }
    state.add_paper_fill(fill)
    log.info("PAPER BUY %s %.3f SOL @ $%.0f", pos["symbol"], size, entry_mc)
    snap2 = snapshot(state)
    return Fill(
        mint=mint,
        symbol=pos["symbol"],
        side="buy",
        reason=fill["reason"],
        frac=1.0,
        multiple=1.0,
        sol=-size,
        cash_after=cash,
        mc=entry_mc,
        pos=pos,
        equity=snap2["equity"],
    )


def _sell(state: State, pos: dict, frac_of_original: float, reason: str, mc: float) -> Fill | None:
    orig = float(pos.get("original_qty_sol") or 0)
    left = float(pos.get("remaining_qty_sol") or 0)
    if orig <= 0 or left <= 0 or frac_of_original <= 0:
        return None
    want = orig * frac_of_original
    qty = min(left, want)
    if qty <= 0:
        return None
    multiple = _mult(pos, mc)
    proceeds = qty * multiple * (1.0 - config.PAPER_FEE) * (1.0 - config.PAPER_EXIT_SLIP)
    wallet = state.paper_wallet()
    cash = float(wallet.get("cash_sol") or 0) + proceeds
    left -= qty
    pos["remaining_qty_sol"] = left
    pos["remaining_frac"] = left / orig if orig else 0.0
    pos["realized_sol"] = float(pos.get("realized_sol") or 0) + proceeds
    pos["last_mc"] = mc
    if left <= orig * 0.001:
        pos["remaining_qty_sol"] = 0.0
        pos["remaining_frac"] = 0.0
        pos["status"] = "closed"
        pos["close_reason"] = reason
        pos["closed_at"] = int(time.time())
    elif int(pos.get("tp1_hit") or 0):
        pos["status"] = "moonbag"
    now = int(time.time())
    state.upsert_paper_position(pos)
    state.set_paper_cash(cash)
    fill = {
        "mint": pos["mint"],
        "ts": now,
        "side": "sell",
        "reason": reason,
        "frac": qty / orig,
        "multiple": multiple,
        "sol": proceeds,
        "cash_after": cash,
        "mc": mc,
    }
    state.add_paper_fill(fill)
    log.info(
        "PAPER SELL %s %s @ %.2fx +%.3f SOL (left %.0f%%)",
        pos.get("symbol"),
        reason,
        multiple,
        proceeds,
        pos["remaining_frac"] * 100,
    )
    snap = snapshot(state)
    return Fill(
        mint=pos["mint"],
        symbol=pos.get("symbol") or "",
        side="sell",
        reason=reason,
        frac=qty / orig,
        multiple=multiple,
        sol=proceeds,
        cash_after=cash,
        mc=mc,
        pos=pos,
        equity=snap["equity"],
    )


def flatten(state: State, pos: dict, coin: dict, reason: str) -> Fill | None:
    mc = float(coin.get("usd_market_cap") or pos.get("last_mc") or 0)
    left = float(pos.get("remaining_frac") or 0)
    if left <= 0 or mc <= 0:
        return None
    return _sell(state, pos, left, reason, mc)


def decide(pos: dict, coin: dict) -> list[tuple[float, str]]:
    """Clip 2x/4x/10x while they hold. Flatten stale bags we missed the dump on."""
    mc = float(coin.get("usd_market_cap") or 0)
    if mc <= 0:
        return []
    multiple = _mult(pos, mc)
    left = float(pos.get("remaining_frac") or 0)
    if left <= 0:
        return []
    opened = int(pos.get("opened_at") or 0)
    held = time.time() - opened if opened else 0
    tp1 = bool(int(pos.get("tp1_hit") or 0))
    tp2 = bool(int(pos.get("tp2_hit") or 0))
    tp3 = bool(int(pos.get("tp3_hit") or 0))
    actions: list[tuple[float, str]] = []

    if (
        config.PAPER_STALE_SEC > 0
        and not tp1
        and held >= config.PAPER_STALE_SEC
        and multiple < config.PAPER_STALE_MULT
    ):
        return [(left, f"stale bag {multiple:.2f}x")]

    if not tp1 and multiple >= config.PAPER_TP1_MULT:
        take = min(config.PAPER_TP1_SELL, left)
        actions.append((take, f"TP1 {config.PAPER_TP1_MULT:.1f}x"))
        left -= take
        tp1 = True
    if tp1 and not tp2 and multiple >= config.PAPER_TP2_MULT and left > 0:
        take = min(config.PAPER_TP2_SELL, left)
        actions.append((take, f"TP2 {config.PAPER_TP2_MULT:.1f}x"))
        left -= take
        tp2 = True
    if tp2 and not tp3 and multiple >= config.PAPER_TP3_MULT and left > 0:
        take = min(config.PAPER_TP3_SELL, left)
        actions.append((take, f"moonbag clip {config.PAPER_TP3_MULT:.0f}x"))
    return actions


def on_tick(state: State, pos: dict, coin: dict) -> list[Fill]:
    mc = float(coin.get("usd_market_cap") or 0)
    if mc <= 0:
        return []
    ath = max(float(pos.get("ath_mc") or 0), float(coin.get("ath_market_cap") or 0), mc)
    pos["ath_mc"] = ath
    pos["last_mc"] = mc
    fills: list[Fill] = []
    for frac, reason in decide(pos, coin):
        if "TP1" in reason:
            pos["tp1_hit"] = 1
        if "TP2" in reason:
            pos["tp2_hit"] = 1
        if "moonbag clip" in reason:
            pos["tp3_hit"] = 1
        fill = _sell(state, pos, frac, reason, mc)
        if fill:
            fills.append(fill)
            pos = fill.pos
    if not fills:
        state.upsert_paper_position(pos)
    return fills
