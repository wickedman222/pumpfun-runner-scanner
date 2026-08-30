"""Kamino Lend paper liquidator. No real SOL.

Scan thin markets for leftover unhealthy obligations. Do not race Main-market
0.1% snipes. Paper-fill only after the position is still liquidatable on a
later poll (keepers missed it) and expected PnL after slip/prio is positive.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .httputil import rpc

log = logging.getLogger("runner")

KLEND = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
KAMINO_API = "https://api.kamino.finance"
SOL_MINT = "So11111111111111111111111111111111111111112"
FRACTION = 2**60
OBLIGATION_SIZE = 3344  # 8-byte disc + 3336-byte Obligation
MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"

# Account offsets (including discriminator). See klend-interface 0.6.0.
_OFF_DEPOSITED = 1192
_OFF_BF_DEBT = 2208
_OFF_BORROWED_MV = 2224
_OFF_UNHEALTHY = 2256
_OFF_HAS_DEBT = 2287
_OFF_ADL_TS = 2336
_OFF_DEPOSITS = 96
_DEP_STRIDE = 136
_OFF_BORROWS = 1208
_BOR_STRIDE = 200
_ZERO32 = b"\x00" * 32

_HTTP: httpx.AsyncClient | None = None
_markets: list[dict] = []
_markets_at = 0.0
_sol_usd = float(config.SOL_USD or 150)
_sol_at = 0.0
_gpa_ok: bool | None = None
_gpa_err = ""
_rotate = 0

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "kamino-paper/1",
}


def _http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(timeout=30.0, headers=HEADERS, follow_redirects=True)
    return _HTTP


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _u128(data: bytes, offset: int) -> int:
    if offset + 16 > len(data):
        return 0
    return int.from_bytes(data[offset : offset + 16], "little")


def _u64(data: bytes, offset: int) -> int:
    if offset + 8 > len(data):
        return 0
    return int.from_bytes(data[offset : offset + 8], "little")


def sf_usd(bits: int) -> float:
    if bits <= 0:
        return 0.0
    return bits / FRACTION


def bonus_bps(
    current_ltv: float,
    max_ltv: float,
    actual_ltv: float,
    min_bps: int = 10,
    max_bps: int = 1000,
) -> int:
    """Engine: max(min, current-maxLtv), cap max, solvency cap 1-actualLtv."""
    if current_ltv <= 0:
        return 0
    gap = max(0.0, current_ltv - max(0.0, max_ltv))
    raw = max(min_bps, int(round(gap * 10_000)))
    raw = min(raw, max_bps)
    headroom = max(0.0, 1.0 - actual_ltv)
    raw = min(raw, int(round(headroom * 10_000)))
    return max(0, raw)


def expected_pnl_sol(
    repay_usd: float,
    bonus: int,
    sol_usd: float,
    proto_fee_pct: float = 0.0,
    prio_sol: float = 0.0,
    entry_slip: float = 0.08,
    exit_slip: float = 0.05,
    fee: float = 0.01,
) -> tuple[float, float, float]:
    """Return (spent_sol, proceeds_sol, pnl_sol) for a paper liquidation."""
    if repay_usd <= 0 or sol_usd <= 0:
        return 0.0, 0.0, 0.0
    spent = repay_usd / sol_usd / max(1e-9, (1.0 - entry_slip) * (1.0 - fee))
    seize = repay_usd * (1.0 + bonus / 10_000.0) * (1.0 - proto_fee_pct / 100.0)
    proceeds = (seize / sol_usd) * (1.0 - exit_slip) * (1.0 - fee) - prio_sol
    return spent, proceeds, proceeds - spent


def _active_slots(data: bytes, start: int, stride: int, n: int) -> int:
    count = 0
    for i in range(n):
        off = start + i * stride
        if off + 32 > len(data):
            break
        if data[off : off + 32] != _ZERO32:
            count += 1
    return count


def parse_stale(data: bytes) -> dict | None:
    """First-pass from on-chain Obligation bytes. USD fields are often stale."""
    raw = data
    if len(raw) >= OBLIGATION_SIZE:
        pass
    elif len(raw) >= OBLIGATION_SIZE - 8:
        raw = b"\x00" * 8 + raw
    else:
        return None
    has_debt = raw[_OFF_HAS_DEBT] if _OFF_HAS_DEBT < len(raw) else 0
    n_dep = _active_slots(raw, _OFF_DEPOSITS, _DEP_STRIDE, 8)
    n_bor = _active_slots(raw, _OFF_BORROWS, _BOR_STRIDE, 5)
    deposited = sf_usd(_u128(raw, _OFF_DEPOSITED))
    bf_debt = sf_usd(_u128(raw, _OFF_BF_DEBT))
    borrowed = sf_usd(_u128(raw, _OFF_BORROWED_MV))
    unhealthy = sf_usd(_u128(raw, _OFF_UNHEALTHY))
    adl = _u64(raw, _OFF_ADL_TS)
    liquidatable = (has_debt == 1 or n_bor > 0) and unhealthy > 0 and bf_debt > unhealthy
    current_ltv = (bf_debt / deposited) if deposited > 0 else 0.0
    actual_ltv = (borrowed / deposited) if deposited > 0 else 0.0
    liq_ltv = (unhealthy / deposited) if deposited > 0 else 0.0
    return {
        "has_debt": has_debt == 1 or n_bor > 0,
        "n_dep": n_dep,
        "n_bor": n_bor,
        "deposited_usd": deposited,
        "debt_usd": borrowed,
        "bf_debt_usd": bf_debt,
        "unhealthy_usd": unhealthy,
        "current_ltv": current_ltv,
        "actual_ltv": actual_ltv,
        "liq_ltv": liq_ltv,
        "adl_ts": adl,
        "liquidatable": liquidatable,
    }


def loan_health(loan: dict) -> dict:
    info = loan.get("loanInfo") or {}
    deposits = ((info.get("collateral") or {}).get("deposits")) or []
    borrows = ((info.get("debt") or {}).get("borrows")) or []
    coll_usd = sum(_num(d.get("tokenValue")) for d in deposits)
    debt_usd = sum(_num(b.get("tokenValue")) for b in borrows)
    current = _num(info.get("currentLtv"))
    max_ltv = _num(info.get("maxLtv"))
    liq_ltv = _num(info.get("liquidationLtv"))
    close = _num(info.get("closeFactor"), 0.25)
    actual = (debt_usd / coll_usd) if coll_usd > 0 else 0.0
    if current <= 0 and coll_usd > 0:
        current = debt_usd / coll_usd
    liquidatable = current >= liq_ltv > 0 and debt_usd > 0
    coll = min(deposits, key=lambda d: _num(d.get("liquidationLtv"), 99), default=None)
    debt = max(borrows, key=lambda b: _num(b.get("borrowFactor"), 0), default=None)
    bps = bonus_bps(current, max_ltv, actual)
    return {
        "loan_id": loan.get("loanId") or "",
        "market_id": loan.get("marketId") or "",
        "user": loan.get("user") or "",
        "coll_usd": coll_usd,
        "debt_usd": debt_usd,
        "current_ltv": current,
        "max_ltv": max_ltv,
        "liq_ltv": liq_ltv,
        "actual_ltv": actual,
        "close_factor": close if close > 0 else 0.25,
        "liquidatable": liquidatable,
        "coll_sym": (coll or {}).get("tokenName") or "?",
        "debt_sym": (debt or {}).get("tokenName") or "?",
        "coll_mint": (coll or {}).get("tokenMint") or "",
        "debt_mint": (debt or {}).get("tokenMint") or "",
        "bonus_bps": bps,
        "deposits": deposits,
        "borrows": borrows,
    }


def paper_size_usd(health: dict, sol_usd: float, size_sol: float) -> float:
    debt = float(health.get("debt_usd") or 0)
    close = float(health.get("close_factor") or 0.25)
    cap = min(size_sol * sol_usd, debt * close, debt)
    return max(0.0, cap)


@dataclass
class Candidate:
    obligation: str
    market: str
    market_name: str
    health: dict
    first_seen: float
    confirmed: bool = False
    reason: str = ""


@dataclass
class ScanStats:
    market: str = ""
    scanned: int = 0
    stale_liq: int = 0
    verified: int = 0
    armed: int = 0
    ready: int = 0
    skipped: int = 0
    gpa_ok: bool | None = None
    gpa_err: str = ""
    fills: list[dict] = field(default_factory=list)


def skip_main(market: dict) -> bool:
    pk = market.get("lendingMarket") or ""
    if pk == MAIN_MARKET and not config.KAMINO_INCLUDE_MAIN:
        return True
    return False


async def _get(url: str) -> object | None:
    try:
        r = await _http().get(url)
        if r.status_code == 429:
            return None
        if r.status_code != 200:
            log.debug("Kamino GET %s %s", r.status_code, url)
            return None
        return r.json()
    except Exception as exc:
        log.debug("Kamino GET fail %s: %s", url, exc)
        return None


async def fetch_markets() -> list[dict]:
    global _markets, _markets_at
    if _markets and time.time() - _markets_at < 600:
        return _markets
    data = await _get(f"{KAMINO_API}/v2/kamino-market")
    rows = data if isinstance(data, list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("lendingMarket"):
            continue
        out.append(row)
    if out:
        _markets = out
        _markets_at = time.time()
    return _markets


def scan_markets(all_markets: list[dict]) -> list[dict]:
    rows = [m for m in all_markets if not skip_main(m)]
    rows.sort(key=lambda m: (not bool(m.get("isCurated")), m.get("name") or ""))
    return rows


async def sol_usd() -> float:
    global _sol_usd, _sol_at
    if time.time() - _sol_at < 30 and _sol_usd > 0:
        return _sol_usd
    data = await _get(f"{KAMINO_API}/oracles/prices?mints={SOL_MINT}")
    price = 0.0
    if isinstance(data, list) and data:
        price = _num((data[0] or {}).get("price"))
    elif isinstance(data, dict):
        price = _num(data.get("price"))
    if price > 0:
        _sol_usd = price
        _sol_at = time.time()
    return _sol_usd


async def fetch_loan(obligation: str) -> dict | None:
    data = await _get(f"{KAMINO_API}/klend/loans/{obligation}")
    return data if isinstance(data, dict) and data.get("loanId") else None


def _b64_account(row: dict) -> bytes:
    acc = row.get("account") or {}
    data = acc.get("data")
    if isinstance(data, list) and data:
        try:
            return base64.b64decode(data[0])
        except Exception:
            return b""
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:
            return b""
    return b""


async def gpa_market(market: str) -> tuple[list[tuple[str, bytes]], str]:
    """Obligation accounts for one market. Empty + err if RPC refuses GPA."""
    global _gpa_ok, _gpa_err
    params = [
        KLEND,
        {
            "encoding": "base64",
            "commitment": "confirmed",
            "filters": [
                {"memcmp": {"offset": 32, "bytes": market}},
                {"dataSize": OBLIGATION_SIZE},
            ],
        },
    ]
    js = await rpc("getProgramAccounts", params, timeout=config.KAMINO_GPA_TIMEOUT)
    if not js:
        _gpa_ok = False
        _gpa_err = _gpa_err or "rpc empty"
        return [], _gpa_err
    if js.get("error"):
        msg = str((js["error"] or {}).get("message") or js["error"])
        _gpa_ok = False
        _gpa_err = msg[:180]
        return [], _gpa_err
    result = js.get("result")
    if not isinstance(result, list):
        _gpa_ok = False
        _gpa_err = "bad result"
        return [], _gpa_err
    out: list[tuple[str, bytes]] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        pk = row.get("pubkey") or ""
        raw = _b64_account(row)
        if pk and raw:
            out.append((pk, raw))
    _gpa_ok = True
    _gpa_err = ""
    return out, ""


def gate_reason(health: dict, thin: bool) -> str:
    if not health.get("liquidatable"):
        return "healthy"
    if float(health.get("debt_usd") or 0) < config.KAMINO_MIN_DEBT_USD:
        return "dust"
    bps = int(health.get("bonus_bps") or 0)
    adl = int(health.get("adl_ts") or 0)
    if bps < config.KAMINO_MIN_BONUS_BPS and not thin and not adl:
        return f"bonus {bps}bps (need {config.KAMINO_MIN_BONUS_BPS} or thin market)"
    return ""


class Keeper:
    def __init__(self) -> None:
        self.armed: dict[str, Candidate] = {}
        self.filled_at: dict[str, float] = {}
        self.skip_logged: set[str] = set()

    def _note_skip(self, obligation: str, symbol: str, why: str) -> None:
        key = f"{obligation}:{why}"
        if key in self.skip_logged:
            return
        self.skip_logged.add(key)
        log.info("Skip kamino %s %s: %s", symbol, obligation[:6], why)

    async def cycle(self, state) -> ScanStats:
        stats = ScanStats(gpa_ok=_gpa_ok, gpa_err=_gpa_err)
        markets = scan_markets(await fetch_markets())
        if not markets:
            stats.gpa_err = "no markets"
            return stats
        global _rotate
        market = markets[_rotate % len(markets)]
        _rotate += 1
        mpk = market.get("lendingMarket") or ""
        mname = market.get("name") or mpk[:6]
        stats.market = mname
        thin = not bool(market.get("isPrimary"))
        px = await sol_usd()

        accounts, err = await gpa_market(mpk)
        stats.gpa_ok = _gpa_ok
        stats.gpa_err = err
        stats.scanned = len(accounts)
        if err:
            log.warning("Kamino GPA %s: %s", mname, err)
        stale_hits: list[tuple[str, dict]] = []
        for pk, raw in accounts:
            h = parse_stale(raw)
            if not h or not h.get("has_debt"):
                continue
            if h.get("liquidatable"):
                stats.stale_liq += 1
            stale_hits.append((pk, h))
        stale_hits.sort(
            key=lambda x: (
                0 if x[1].get("liquidatable") else 1,
                -float(x[1].get("deposited_usd") or 0),
            )
        )

        now = time.time()
        cap = config.KAMINO_VERIFY_N
        if stats.scanned <= 500:
            cap = max(cap, len(stale_hits))
        verify = stale_hits[:cap]
        # Always re-check already armed, even if this cycle's market is different.
        extra = [pk for pk in self.armed if pk not in {p for p, _ in verify}]
        for pk, _stale in verify:
            await asyncio.sleep(0.08)
            loan = await fetch_loan(pk)
            if not loan:
                continue
            health = loan_health(loan)
            stats.verified += 1
            await self._consider(state, stats, pk, mpk, mname, health, thin, px, now)
        for pk in extra:
            cand = self.armed.get(pk)
            if not cand:
                continue
            loan = await fetch_loan(pk)
            if not loan:
                self.armed.pop(pk, None)
                continue
            health = loan_health(loan)
            await self._consider(
                state, stats, pk, cand.market, cand.market_name, health, True, px, now
            )

        dead = [pk for pk, c in self.armed.items() if now - c.first_seen > 30 * 60]
        for pk in dead:
            self.armed.pop(pk, None)
        stats.armed = len(self.armed)
        return stats

    async def _consider(
        self,
        state,
        stats: ScanStats,
        pk: str,
        mpk: str,
        mname: str,
        health: dict,
        thin: bool,
        px: float,
        now: float,
    ) -> None:
        symbol = f"{health.get('coll_sym')}/{health.get('debt_sym')}"
        if not health.get("liquidatable"):
            self.armed.pop(pk, None)
            return
        why = gate_reason(health, thin)
        if why:
            stats.skipped += 1
            self._note_skip(pk, symbol, why)
            return
        repay = paper_size_usd(health, px, config.PAPER_SIZE_FIXED)
        if repay <= 0:
            return
        spent, proceeds, pnl = expected_pnl_sol(
            repay,
            int(health.get("bonus_bps") or 0),
            px,
            prio_sol=config.KAMINO_PRIO_SOL,
            entry_slip=config.PAPER_ENTRY_SLIP,
            exit_slip=config.PAPER_EXIT_SLIP,
            fee=config.PAPER_FEE,
        )
        if pnl <= 0 or spent <= 0:
            stats.skipped += 1
            self._note_skip(pk, symbol, f"pnl {pnl:.4f} SOL after slip")
            return
        cand = self.armed.get(pk)
        if cand is None:
            self.armed[pk] = Candidate(
                obligation=pk,
                market=mpk,
                market_name=mname,
                health=health,
                first_seen=now,
            )
            log.info(
                "Armed kamino %s %s LTV %.3f/%.3f bonus %sbps debt $%.0f",
                symbol,
                pk[:8],
                health["current_ltv"],
                health["liq_ltv"],
                health["bonus_bps"],
                health["debt_usd"],
            )
            return
        cand.health = health
        if now - cand.first_seen < config.KAMINO_CONFIRM_SEC:
            return
        last_fill = float(self.filled_at.get(pk) or 0)
        if now - last_fill < 120:
            return
        cand.confirmed = True
        stats.ready += 1
        stats.fills.append(
            {
                "mint": pk,
                "symbol": symbol,
                "name": mname,
                "url": f"https://solscan.io/account/{pk}",
                "usd_market_cap": repay,
                "ath_market_cap": repay,
                "path": "kamino",
                "spent_sol": spent,
                "proceeds_sol": proceeds,
                "pnl_sol": pnl,
                "bonus_bps": health["bonus_bps"],
                "debt_usd": health["debt_usd"],
                "coll_usd": health["coll_usd"],
                "current_ltv": health["current_ltv"],
                "liq_ltv": health["liq_ltv"],
                "close_factor": health["close_factor"],
                "prio_sol": config.KAMINO_PRIO_SOL,
                "market": mname,
                "kamino": 1,
            }
        )
        self.filled_at[pk] = now
        self.armed.pop(pk, None)
