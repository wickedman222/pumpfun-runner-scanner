"""Local checks for paper size / stops / scale-outs."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.paper import buy_size, decide, snapshot, try_open
from bot.state import State


def coin(mint, mc, **kw):
    row = {
        "mint": mint,
        "symbol": kw.get("symbol", "TEST"),
        "name": "Test Character",
        "url": "https://pump.fun/coin/" + mint,
        "usd_market_cap": mc,
        "ath_market_cap": kw.get("ath", mc),
        "is_currently_live": kw.get("live", False),
        "boost_mode": "NONE",
    }
    return row


def main() -> None:
    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    config.DATA_DIR = tmp
    st = State(os.path.join(tmp, "t.db"))
    st.ensure_paper_wallet(2.0)

    assert abs(buy_size(2.0) - 0.12) < 1e-9, buy_size(2.0)
    assert abs(buy_size(2.0, 40_000) - 0.12) < 1e-9, buy_size(2.0, 40_000)
    assert abs(buy_size(2.0, 120_000) - 0.084) < 1e-9, buy_size(2.0, 120_000)
    assert buy_size(1.2) == 0.10
    assert buy_size(4.0) == 0.15
    assert buy_size(0.2) == 0.0

    fill = try_open(st, coin("m1", 20_000, symbol="SHOBON"), path="live")
    assert fill and fill.side == "buy"
    snap = snapshot(st)
    assert abs(snap["cash"] - 1.88) < 1e-9, snap
    assert len(snap["open"]) == 1

    pos = st.paper_position("m1")
    pos["opened_at"] = int(time.time())
    acts = decide(pos, coin("m1", 11_000))
    assert acts and acts[0][1].startswith("dead: stop"), acts

    acts = decide(pos, coin("m1", 40_000))
    reasons = [a[1] for a in acts]
    assert any(r.startswith("TP1") for r in reasons), acts

    pos["tp1_hit"] = 1
    acts = decide(pos, coin("m1", 80_000))
    assert any("TP2" in a[1] for a in acts), acts

    pos["tp1_hit"] = 1
    pos["tp2_hit"] = 1
    pos["ath_mc"] = 80_000
    pos["remaining_frac"] = 0.50
    # −65% of 80k = 28k. 20k should trail; 40k (50% off) should not.
    acts = decide(pos, coin("m1", 40_000, ath=80_000))
    assert not acts, acts
    acts = decide(pos, coin("m1", 20_000, ath=80_000))
    assert acts and acts[0][1].startswith("trail"), acts

    # 3h grind under 1.6x must stay open — no 2h no-go.
    pos = {
        "entry_mc": 20_000,
        "ath_mc": 22_000,
        "remaining_frac": 1.0,
        "opened_at": int(time.time()) - 3 * 3600,
        "tp1_hit": 0,
        "tp2_hit": 0,
        "tp3_hit": 0,
        "path": "tape",
    }
    acts = decide(pos, coin("m2", 22_000))
    assert acts == [], acts

    print("paper rules ok")


if __name__ == "__main__":
    main()
