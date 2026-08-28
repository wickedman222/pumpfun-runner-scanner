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
    st.ensure_paper_wallet(5.0)

    assert abs(buy_size(5.0) - 0.30) < 1e-9, buy_size(5.0)
    assert abs(buy_size(5.0, 18_000) - 0.30) < 1e-9, buy_size(5.0, 18_000)
    assert abs(buy_size(5.0, 40_000) - 0.30) < 1e-9, buy_size(5.0, 40_000)
    assert buy_size(1.2) == 0.30
    assert buy_size(4.0) == 0.30
    assert buy_size(0.2) == 0.0

    fill = try_open(st, coin("m1", 18_000, symbol="SHOBON"), path="tape")
    assert fill and fill.side == "buy"
    snap = snapshot(st)
    assert abs(snap["cash"] - 4.70) < 1e-9, snap
    assert abs(fill.sol + 0.30) < 1e-9, fill.sol
    assert len(snap["open"]) == 1

    pos = st.paper_position("m1")
    pos["opened_at"] = int(time.time())
    # Hold test: a −50% print must stay open. No stop.
    acts = decide(pos, coin("m1", 9_000))
    assert acts == [], acts

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
    # Moonbag holds through an ATH giveback. No trail flatten.
    acts = decide(pos, coin("m1", 40_000, ath=80_000))
    assert not acts, acts
    acts = decide(pos, coin("m1", 20_000, ath=80_000))
    assert not acts, acts
    acts = decide(pos, coin("m1", 180_000, ath=180_000))
    assert any("moonbag clip" in a[1] for a in acts), acts

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

    dead = dict(pos)
    dead["entry_mc"] = 18_000
    dead["opened_at"] = int(time.time()) - 50 * 60
    acts = decide(dead, coin("m2", 2_000))
    assert acts and acts[0][1].startswith("stale bag"), acts

    print("paper rules ok")


if __name__ == "__main__":
    main()
