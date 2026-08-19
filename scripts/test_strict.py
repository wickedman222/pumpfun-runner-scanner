import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import extract_farm_reason
from bot.tape import decide


pants = {
    "symbol": "PANTS",
    "name": "dogwifpants",
    "boost_mode": "COMPLETED",
    "twitter": "",
    "website": "",
    "is_currently_live": False,
    "num_participants": 0,
    "usd_market_cap": 75_000,
    "ath_market_cap": 75_000,
    "is_cashback_enabled": False,
    "mayhem_state": "NONE",
    "complete": True,
    "description": "Before the dog wore a hat, he wore pants.",
}
assert not extract_farm_reason(pants)
assert not extract_farm_reason({**pants, "symbol": "USWS", "name": "USWS"})
assert extract_farm_reason({**pants, "mayhem_state": "ACTIVE"})
assert extract_farm_reason({**pants, "is_cashback_enabled": True})

now = time.time()
created_ms = int(now * 1000)
row0 = {"armed_mc": 0}


def coin(mc, **kw):
    c = {
        **pants,
        "mint": kw.get("mint", "m1"),
        "usd_market_cap": mc,
        "ath_market_cap": kw.get("ath", mc),
        "complete": kw.get("complete", False),
        "created_timestamp": created_ms,
        "is_currently_live": kw.get("live", False),
        "num_participants": kw.get("parts", 0),
        "mayhem_state": kw.get("mayhem", "NONE"),
        "is_cashback_enabled": kw.get("cash", False),
        "symbol": kw.get("symbol", "PANTS"),
    }
    return c


# Thin book — track, do not buy.
c = decide(coin(4_000), row0, older=None, copies=0, now=now)
assert c.action == "watch", c

# First print in band, not graduated — ARM only (backtest).
c = decide(coin(12_000), row0, older=None, copies=0, now=now)
assert c.action == "arm", c
assert c.armed_mc == 12_000

# Same mint later +20% — TRIGGER.
c = decide(coin(15_000), {"armed_mc": 12_000}, older=None, copies=0, now=now)
assert c.action == "trigger", c

# FISHBONE: arm 37k, dip 30k still watch, 45k trigger.
c = decide(coin(37_000), row0, older=None, copies=0, now=now)
assert c.action == "arm", c
c = decide(coin(30_000, ath=37_000), {"armed_mc": 37_000}, older=None, copies=0, now=now)
assert c.action == "watch", c
c = decide(coin(45_000, ath=45_000), {"armed_mc": 37_000}, older=None, copies=0, now=now)
assert c.action == "trigger", c

# Already graduated in band on first print — buy (PANTS / FISHBONE discovered late).
c = decide(coin(75_000, complete=True), row0, older=None, copies=0, now=now)
assert c.action == "trigger", c

# Farm / copy / dump / late.
c = decide(coin(40_000, mayhem="ACTIVE"), row0, older=None, copies=0, now=now)
assert c.action == "skip" and "mayhem" in c.reason
c = decide(coin(40_000), row0, older={"mint": "olderxxx"}, copies=0, now=now)
assert c.action == "skip" and "older mint" in c.reason
c = decide(coin(20_000, ath=80_000), row0, older=None, copies=0, now=now)
assert c.action == "skip" and "dumped" in c.reason
c = decide(coin(250_000), row0, older=None, copies=0, now=now)
assert c.action == "skip" and "already $" in c.reason


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

    first = coin(12_000, mint="arm1", complete=False)
    v = await run(first)
    assert v.failed_gate == "watch", (v.failed_gate, v.fail_reason)

    later = coin(16_000, mint="arm1", complete=False)
    v = await run(later)
    assert v.post is True, (v.failed_gate, v.fail_reason)
    assert v.path == "tape"

    v = await run(coin(75_000, mint="pants1", complete=True))
    assert v.post is True, (v.failed_gate, v.fail_reason)

    v = await run(coin(4_000, mint="thin1"))
    assert v.failed_gate == "watch", (v.failed_gate, v.fail_reason)

    v = await run(coin(40_000, mint="mh1", mayhem="ACTIVE"))
    assert v.failed_gate == "farm", v.fail_reason


import asyncio

asyncio.run(_engine_paths())

from bot import config as cfg
from bot.state import State

cfg.SIGNAL_BOOK_ID = "tape-1"
tmp = tempfile.mkdtemp()
st = State(os.path.join(tmp, "quota.db"))
st.mark_posted({"mint": "old1", "symbol": "OLD", "usd_market_cap": 10_000}, "legacy")
with st._conn() as con:
    con.execute("UPDATE posted SET book_id = 'legacy' WHERE mint = 'old1'")
assert st.signals_today() == 0, st.signals_today()
st.mark_posted({"mint": "new1", "symbol": "NEW", "usd_market_cap": 20_000}, "tape")
assert st.signals_today() == 1, st.signals_today()

print("strict buy rules ok")
