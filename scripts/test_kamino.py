"""Kamino paper keeper: health, bonus, confirm-style PnL, isolated roundtrip."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.kamino import (
    OBLIGATION_SIZE,
    bonus_bps,
    expected_pnl_sol,
    gate_reason,
    loan_health,
    paper_size_usd,
    parse_stale,
    sf_usd,
    skip_main,
)
from bot.paper import roundtrip, snapshot
from bot.state import State


def _put_u128(buf: bytearray, off: int, usd: float) -> None:
    bits = int(usd * (2**60))
    buf[off : off + 16] = bits.to_bytes(16, "little")


def _put_u64(buf: bytearray, off: int, n: int) -> None:
    buf[off : off + 8] = int(n).to_bytes(8, "little")


def test_bonus() -> None:
    assert bonus_bps(0.80, 0.75, 0.78) >= 10
    # barely past max LTV, floor min_bps
    assert bonus_bps(0.751, 0.75, 0.74, min_bps=10) >= 10
    # solvency cap: 2% headroom
    bps = bonus_bps(0.99, 0.75, 0.98, min_bps=500, max_bps=1000)
    assert bps <= 200, bps
    assert bonus_bps(0.50, 0.75, 0.50) == 10  # not liquidatable; still floor if called


def test_parse_stale() -> None:
    buf = bytearray(OBLIGATION_SIZE)
    buf[2287] = 1  # has_debt
    _put_u128(buf, 1192, 1000.0)  # deposited
    _put_u128(buf, 2208, 820.0)  # bf debt
    _put_u128(buf, 2224, 800.0)  # borrowed mv
    _put_u128(buf, 2256, 800.0)  # unhealthy
    h = parse_stale(bytes(buf))
    assert h and h["liquidatable"], h
    assert abs(h["deposited_usd"] - 1000) < 1, h
    assert h["current_ltv"] > h["liq_ltv"]

    buf2 = bytearray(buf)
    _put_u128(buf2, 2208, 700.0)
    h2 = parse_stale(bytes(buf2))
    assert h2 and not h2["liquidatable"]


def test_loan_and_gates() -> None:
    loan = {
        "loanId": "ob1",
        "marketId": "m1",
        "user": "u1",
        "loanInfo": {
            "collateral": {
                "deposits": [
                    {
                        "tokenName": "JLP",
                        "tokenMint": "jlp",
                        "tokenValue": "1000",
                        "liquidationLtv": 0.80,
                    }
                ]
            },
            "debt": {
                "borrows": [
                    {
                        "tokenName": "USDC",
                        "tokenMint": "usdc",
                        "tokenValue": "850",
                        "borrowFactor": 1,
                    }
                ]
            },
            "currentLtv": 0.85,
            "maxLtv": 0.75,
            "liquidationLtv": 0.80,
            "closeFactor": 0.25,
        },
    }
    h = loan_health(loan)
    assert h["liquidatable"]
    assert h["coll_sym"] == "JLP"
    assert h["debt_sym"] == "USDC"
    assert h["bonus_bps"] >= 50
    assert gate_reason(h, thin=True) == ""
    tiny = dict(h)
    tiny["debt_usd"] = 5
    assert gate_reason(tiny, True) == "dust"
    low = dict(h)
    low["bonus_bps"] = 10
    assert "bonus" in gate_reason(low, thin=False)
    assert gate_reason(low, thin=True) == ""
    assert skip_main({"lendingMarket": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"})
    assert not skip_main({"lendingMarket": "DxXdAyU3kCjnyggvHmY5nAwg5cRbbmdyX3npfDMjjMek"})


def test_pnl_and_size() -> None:
    repay = paper_size_usd(
        {"debt_usd": 400.0, "close_factor": 0.25}, sol_usd=100.0, size_sol=0.30
    )
    assert abs(repay - 30.0) < 1e-9, repay
    spent, proceeds, pnl = expected_pnl_sol(
        30.0, 200, 100.0, prio_sol=0.0002, entry_slip=0.08, exit_slip=0.05, fee=0.01
    )
    assert spent > 0.30  # slip means more SOL to acquire the debt
    assert proceeds > 0
    # 2% bonus cannot overcome 8%+5%+1%+1% costs
    spent2, proceeds2, pnl2 = expected_pnl_sol(30.0, 10, 100.0)
    assert pnl2 < 0, (spent2, proceeds2, pnl2)


def test_roundtrip() -> None:
    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    config.DATA_DIR = tmp
    st = State(os.path.join(tmp, "k.db"))
    st.ensure_paper_wallet(5.0)
    coin = {
        "mint": "ob-1",
        "symbol": "JLP/USDC",
        "name": "JLP Market",
        "url": "https://solscan.io/account/ob-1",
        "usd_market_cap": 40.0,
    }
    fill = roundtrip(st, coin, 0.32, 0.34, "kamino JLP · bonus 200bps", path="kamino")
    assert fill and fill.side == "sell"
    assert abs(fill.sol - 0.02) < 1e-9, fill.sol
    snap = snapshot(st)
    assert abs(snap["cash"] - 5.02) < 1e-9, snap
    assert snap["open"] == []
    assert snap["closed_n"] == 1
    # same obligation can be hit again after close
    fill2 = roundtrip(st, coin, 0.32, 0.31, "kamino JLP round 2", path="kamino")
    assert fill2
    snap2 = snapshot(st)
    assert abs(snap2["cash"] - 5.01) < 1e-9, snap2
    # cash floor: 5.01 - 4.80 = 0.21 < 0.40
    fill3 = roundtrip(st, {**coin, "mint": "ob-2"}, 4.80, 4.90, "too big", path="kamino")
    assert fill3 is None


def main() -> None:
    test_bonus()
    test_parse_stale()
    test_loan_and_gates()
    test_pnl_and_size()
    test_roundtrip()
    print("kamino ok")


if __name__ == "__main__":
    main()
