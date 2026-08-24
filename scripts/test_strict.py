import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import extract_farm_reason
from bot.tape import decide
from bot.wallets import harvest_coin, is_held_runner, is_real_runner

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
uotf = coin(
    287_000_000,
    ath=287_600_000,
    complete=True,
    mint="uotfMint",
    symbol="UOTF",
)
uotf["boost_mode"] = "COMPLETED"
uotf["reply_count"] = 0
uotf["created_timestamp"] = int((now - 3600) * 1000)
assert extract_farm_reason(uotf)
assert not is_held_runner(uotf, now)
wiggle = coin(134_400_000, ath=137_500_000, complete=True, mint="uotfWiggle", symbol="UOTF")
wiggle["boost_mode"] = "COMPLETED"
wiggle["reply_count"] = 0
wiggle["created_timestamp"] = int((now - 3600) * 1000)
assert extract_farm_reason(wiggle)
assert not is_held_runner(wiggle, now)
small = coin(930_000, ath=929_000, complete=True, mint="gptMint", symbol="ChatGPT")
small["boost_mode"] = "COMPLETED"
small["reply_count"] = 0
small["created_timestamp"] = int((now - 3600) * 1000)
assert extract_farm_reason(small)
assert not is_held_runner(small, now)
bull = coin(17_080_000, ath=27_975_000, complete=True, mint="bullMint", symbol="BULLBALLS")
bull["boost_mode"] = "COMPLETED"
bull["reply_count"] = 0
bull["created_timestamp"] = int((now - 3600) * 1000)
assert not extract_farm_reason(bull), extract_farm_reason(bull)
assert is_held_runner(bull, now)
assert not is_real_runner(bull, now)
organic = coin(480_000, ath=600_000, complete=True, mint="orgMint", symbol="HELD")
organic["boost_mode"] = "NONE"
organic["reply_count"] = 20
organic["created_timestamp"] = int((now - 3600) * 1000)
assert is_real_runner(organic, now)
earn = coin(208_000, ath=258_000, complete=True, mint="earnMint", symbol="EARNBOT")
earn["boost_mode"] = "COMPLETED"
earn["reply_count"] = 0
earn["created_timestamp"] = int((now - 3600) * 1000)
assert extract_farm_reason(earn)
assert not is_held_runner(earn, now)
live_boost = dict(uotf)
live_boost["is_currently_live"] = True
assert not extract_farm_reason(live_boost)

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

# One poll later even at 2x — still waiting.
armed = {"armed_mc": 12_000, "armed_at": now - 10}
c = decide(coin(24_000), armed, older=None, copies=0, now=now)
assert c.action == "watch" and "waiting" in c.reason, c

# Two+ polls, +40% near ATH — rip.
armed = {"armed_mc": 12_000, "armed_at": now - 30}
c = decide(coin(24_000, ath=24_000), armed, older=None, copies=0, now=now)
assert c.action == "trigger" and "rip" in c.reason, c

# 5 min later, +60%, near ATH — held expansion.
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
    assert v.post is True and v.path == "tape", (v.post, v.path, v.fail_reason)

    v = await run(coin(12_000, mint="arm2", complete=False))
    with st._conn() as con:
        con.execute(
            "UPDATE tape SET armed_at = ? WHERE mint = ?",
            (int(time.time()) - 200, "arm2"),
        )
    live_row = coin(15_000, mint="arm2", complete=False, ath=15_000)
    live_row["is_currently_live"] = True
    live_row["num_participants"] = 22
    v = await run(live_row)
    assert v.post is False, (v.post, v.path, v.fail_reason)


import asyncio

asyncio.run(_engine_paths())

from bot.state import State
from bot.wallets import wallet_buy_ok

tmp = tempfile.mkdtemp()
st = State(os.path.join(tmp, "w.db"))
st.note_smart_wallet("Wa", "mintA", 1.0, symbol="BULLBALLS", ath_mc=27_975_000)
st.note_smart_wallet("Wa", "mintB", 1.0, symbol="FISHBONE", ath_mc=350_000)
st.note_smart_wallet("Wb", "mintA", 0.8, symbol="BULLBALLS", ath_mc=27_975_000)
st.note_smart_wallet("Wb", "mintB", 0.8, symbol="FISHBONE", ath_mc=350_000)
st.note_smart_wallet("We", "earnMint", 1.0, symbol="EARNBOT", ath_mc=258_000)
st.note_smart_wallet("Wf", "uotfMint", 2.0, symbol="UOTF", ath_mc=287_000_000)
rep = st.wallet_report(5)
assert rep["wallets"] == 4
assert "uotfMint" in st.harvested_mints()
assert asyncio.run(harvest_coin(None, st, uotf)) == 0
assert asyncio.run(harvest_coin(None, st, earn)) == 0
assert st.smart_wallet_count() == 2
assert "uotfMint" not in st.harvested_mints()
assert "earnMint" not in st.harvested_mints()
assert st.drop_wallets_from_mint("uotfMint") == 0
assert any(r.get("symbol") == "BULLBALLS" for r in st.wallet_report(5)["top"][0]["runs"])
assert st.smart_wallet_runners("Wa", exclude_mint="mintC") == 2
assert st.smart_wallet_runners("We") == 0
assert not st.tx_harvested("mintA")
st.mark_tx_harvested("mintA")
assert st.tx_harvested("mintA")
assert wallet_buy_ok(coin(4_000), now).startswith("thin")
assert wallet_buy_ok(coin(25_000, ath=26_000), now) == ""
assert "boost" in wallet_buy_ok(uotf, now)

from bot.telegram import format_gather

g = format_gather(
    {
        "spots": [
            {
                "symbol": "FOO",
                "url": "https://pump.fun/coin/m",
                "entry_mc": 12_000,
                "last_mc": 48_000,
                "ath_mc": 52_000,
                "story": "rip 24s",
            }
        ],
        "armed": [
            {
                "symbol": "BAR",
                "mint": "mintbar",
                "armed_mc": 10_000,
                "last_mc": 11_000,
                "ath_mc": 12_000,
            }
        ],
        "skip_n": 40,
        "farm_n": 12,
    },
    6,
)
assert "6h gather" in g
assert "$FOO" in g
assert "4.3x" in g or "4.33x" in g
assert "no live TG" not in g

print("strict buy rules ok")
