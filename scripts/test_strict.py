import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import (
    Story,
    extract_farm_reason,
    score_match,
)


def S(title: str) -> Story:
    return Story(title=title, url="https://example.com", source="test", seen_at=0.0)


# Name lists are not a strategy. USWS is not banned for being called USWS.
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
    "description": "Before the dog wore a hat, he wore pants. Meme from 2016.",
}
assert not extract_farm_reason(pants), extract_farm_reason(pants)

usws = {**pants, "symbol": "USWS", "name": "USWS"}
assert not extract_farm_reason(usws), extract_farm_reason(usws)

pepe = {**pants, "symbol": "PEPE", "name": "PEPE"}
assert not extract_farm_reason(pepe)

hope = {**pants, "symbol": "HOPE", "name": "Hope"}
assert not extract_farm_reason(hope)

nasa = {**pants, "symbol": "NASA", "name": "NASA"}
assert not extract_farm_reason(nasa)

# Program paint still is a farm, regardless of ticker.
assert extract_farm_reason({**pants, "mayhem_state": "ACTIVE"})
assert extract_farm_reason({**usws, "is_cashback_enabled": True})

# News match is a headline already in the window, not "this name looks good".
assert score_match(
    "ESTRIPER", "ESTRIPER", S("Kawasaki Cago Crico Estriper - youtu.be")
) >= 100
assert score_match("PEPE", "PEPE", S("Random sports recap with no overlap")) == 0


async def _engine_paths() -> None:
    import os
    import tempfile
    import time
    from unittest.mock import AsyncMock, patch

    from bot.attention import Attention
    from bot.engine import evaluate_new
    from bot.state import State
    from bot.structure import Structure

    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    st = State(os.path.join(tmp, "t.db"))
    att = Attention()
    now_ms = int(time.time() * 1000)

    async def run(coin):
        with patch(
            "bot.engine.inspect",
            new=AsyncMock(return_value=Structure(ok=True, total_holders=80)),
        ):
            return await evaluate_new(None, att, st, coin)

    pants_coin = {
        **pants,
        "mint": "FtateF34Xzawa91bpbVNdX72hZYo9cymRDYqBreHHbJi",
        "created_timestamp": now_ms,
    }
    v = await run(pants_coin)
    assert v.failed_gate == "wait-expansion", (v.failed_gate, v.fail_reason)
    assert v.path == "tape", v.path

    usws_coin = {
        **usws,
        "mint": "HWXdd6TB4T6VLbodX46EXZwEADWYUaENC2emLeBMpump",
        "created_timestamp": now_ms,
    }
    v = await run(usws_coin)
    assert v.failed_gate == "wait-expansion", (v.failed_gate, v.fail_reason)
    assert v.path == "tape", v.path

    thin = {
        **pants,
        "mint": "thin111111111111111111111111111111111111111",
        "usd_market_cap": 4_000,
        "ath_market_cap": 4_000,
        "created_timestamp": now_ms,
    }
    v = await run(thin)
    assert v.failed_gate == "attention", (v.failed_gate, v.fail_reason)

    painted = {
        **pants,
        "mint": "mayhem1111111111111111111111111111111111111",
        "mayhem_state": "ACTIVE",
        "created_timestamp": now_ms,
    }
    v = await run(painted)
    assert v.failed_gate == "farm", v.fail_reason

    from bot.engine import confirm_expansion

    first = {**pants_coin, "usd_market_cap": 106_000, "complete": True, "reply_count": 0}

    async def exp(fresh):
        with patch("bot.engine.fetch_coin", new=AsyncMock(return_value=fresh)):
            return await confirm_expansion(None, first, first)

    # Pullback that is not a dump — keep watching (FISHBONE 12:07→12:10).
    status, why, _ = await exp({**first, "usd_market_cap": 93_000, "ath_market_cap": 147_000})
    assert status == "wait", (status, why)

    # Later leg vs first print, not vs the local top.
    status, why, _ = await exp({**first, "usd_market_cap": 196_000, "ath_market_cap": 210_000})
    assert status == "post", (status, why)

    status, why, _ = await exp({**first, "usd_market_cap": 40_000, "ath_market_cap": 147_000})
    assert status == "drop", (status, why)


import asyncio

asyncio.run(_engine_paths())

# Old posted rows must not fill today's 3-call cap.
import os
import tempfile

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
