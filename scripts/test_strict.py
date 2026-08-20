import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import extract_farm_reason
from bot.tape import decide

now = time.time()
created_ms = int(now * 1000)
row0 = {"armed_mc": 0, "armed_at": 0}


def coin(mc, **kw):
    return {
        "symbol": kw.get("symbol", "PANTS"),
        "name": "x",
        "mint": kw.get("mint", "m1"),
        "usd_market_cap": mc,
        "ath_market_cap": kw.get("ath", mc),
        "complete": kw.get("complete", False),
        "created_timestamp": created_ms,
        "is_currently_live": False,
        "num_participants": 0,
        "mayhem_state": kw.get("mayhem", "NONE"),
        "is_cashback_enabled": kw.get("cash", False),
        "boost_mode": "NONE",
    }


assert not extract_farm_reason(coin(20_000))
assert extract_farm_reason(coin(20_000, mayhem="ACTIVE"))

# Graduation on first sight is the fill we kept buying. Skip.
c = decide(coin(75_000, complete=True), row0, older=None, copies=0, now=now)
assert c.action == "skip" and "graduated" in c.reason, c

c = decide(coin(143_000, complete=True), row0, older=None, copies=0, now=now)
assert c.action == "skip", c

# Too far up the curve to arm.
c = decide(coin(90_000, complete=False), row0, older=None, copies=0, now=now)
assert c.action == "skip" and "too far" in c.reason, c

# Thin book — only track.
c = decide(coin(4_000), row0, older=None, copies=0, now=now)
assert c.action == "watch", c

# On-curve in band — ARM, do not buy.
c = decide(coin(12_000), row0, older=None, copies=0, now=now)
assert c.action == "arm", c
assert c.armed_mc == 12_000

# Same mint 30s later at 2x — still waiting hold time.
armed = {"armed_mc": 12_000, "armed_at": now - 30}
c = decide(coin(24_000), armed, older=None, copies=0, now=now)
assert c.action == "watch" and "waiting" in c.reason, c

# 5 min later, +60%, near ATH — BUY.
armed = {"armed_mc": 12_000, "armed_at": now - 300}
c = decide(coin(22_000, ath=22_000), armed, older=None, copies=0, now=now)
assert c.action == "trigger", c

# Expanded but off highs — do not buy the dump. 22k vs 32k is ~31% off.
c = decide(coin(22_000, ath=32_000), armed, older=None, copies=0, now=now)
assert c.action == "watch" and "off highs" in c.reason, c

# Graduation after we armed on-curve and held — still need +60% and near highs.
c = decide(coin(22_000, complete=True, ath=22_000), armed, older=None, copies=0, now=now)
assert c.action == "trigger", c

c = decide(coin(13_000, complete=True, ath=13_000), armed, older=None, copies=0, now=now)
assert c.action == "watch", c

# Farms / copies / dump.
c = decide(coin(20_000, mayhem="ACTIVE"), row0, older=None, copies=0, now=now)
assert c.action == "skip"
c = decide(coin(20_000), row0, older={"mint": "olderxxx"}, copies=0, now=now)
assert c.action == "skip"


async def _engine_paths() -> None:
    from bot.attention import Attention
    from bot.engine import evaluate_new
    from bot.state import State
    from bot.structure import Structure

    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    st = State(os.path.join(tmp, "t.db"))
    att = Attention()

    async def run(row):
        with patch(
            "bot.engine.inspect",
            new=AsyncMock(return_value=Structure(ok=True, total_holders=80)),
        ):
            return await evaluate_new(None, att, st, row)

    v = await run(coin(75_000, mint="grad1", complete=True))
    assert v.post is False and v.failed_gate == "late", (v.failed_gate, v.fail_reason)

    v = await run(coin(12_000, mint="arm1", complete=False))
    assert v.failed_gate == "watch", (v.failed_gate, v.fail_reason)

    # Force hold time elapsed on the tape row.
    with st._conn() as con:
        con.execute(
            "UPDATE tape SET armed_at = ? WHERE mint = ?",
            (int(time.time()) - 400, "arm1"),
        )
    v = await run(coin(22_000, mint="arm1", complete=False, ath=22_000))
    assert v.post is False and v.failed_gate == "watch", (v.failed_gate, v.fail_reason)


import asyncio

asyncio.run(_engine_paths())

from bot.state import State
from bot.wallets import wallet_buy_ok

tmp = tempfile.mkdtemp()
st = State(os.path.join(tmp, "w.db"))
st.note_smart_wallet("Wa", "mintA", 1.0, symbol="BULLBALLS", ath_mc=1_560_000)
st.note_smart_wallet("Wa", "mintB", 1.0, symbol="FISHBONE", ath_mc=350_000)
st.note_smart_wallet("Wb", "mintA", 0.8, symbol="BULLBALLS", ath_mc=1_560_000)
st.note_smart_wallet("Wb", "mintB", 0.8, symbol="FISHBONE", ath_mc=350_000)
rep = st.wallet_report(5)
assert rep["wallets"] == 2
assert rep["top"][0]["n"] == 2
assert any(r.get("symbol") == "BULLBALLS" for r in rep["top"][0]["runs"])
assert st.smart_wallet_runners("Wa", exclude_mint="mintC") == 2
assert st.smart_wallet_count() == 2
assert not st.tx_harvested("mintA")
st.mark_tx_harvested("mintA")
assert st.tx_harvested("mintA")
assert wallet_buy_ok(coin(4_000), now).startswith("thin")
assert wallet_buy_ok(coin(25_000, ath=26_000), now) == ""

print("strict buy rules ok")
