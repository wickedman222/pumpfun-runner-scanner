"""Copy-book size, filters, and cursor pin."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.copy import copy_size, entry_fail
from bot.state import State


def coin(mc, **kw):
    now_ms = int(time.time() * 1000)
    return {
        "symbol": kw.get("symbol", "TEST"),
        "name": "x",
        "mint": "m1",
        "usd_market_cap": mc,
        "ath_market_cap": kw.get("ath", mc),
        "complete": kw.get("complete", False),
        "created_timestamp": kw.get("created", now_ms),
        "is_currently_live": False,
        "boost_mode": "NONE",
        "mayhem_state": "NONE",
        "is_cashback_enabled": False,
        "num_participants": 0,
    }


def main() -> None:
    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    config.DATA_DIR = tmp
    st = State(os.path.join(tmp, "c.db"))

    assert abs(copy_size(5.0, 5.0, 1, 1.0, 5.0) - 0.30) < 1e-9
    assert abs(copy_size(5.0, 5.0, 3, 1.0, 5.0) - 0.30) < 1e-9
    assert copy_size(5.0, 0.20, 1, 1.0, 5.0) == 0.0

    now = time.time()
    assert entry_fail(coin(18_000), now, 20) == ""
    assert "late copy" in entry_fail(coin(18_000), now, 200)
    assert "thin" in entry_fail(coin(5_000), now, 20)
    assert "chase" in entry_fail(coin(100_000), now, 20)

    assert st.copy_cursor("w1") == ""
    st.set_copy_cursor("w1", "")
    assert st.copy_cursor("w1") == ""
    st.set_copy_cursor("w1", "sigABC")
    assert st.copy_cursor("w1") == "sigABC"
    assert not st.copy_seen("w1", "mintA")
    st.note_copy_hit("w1", "mintA")
    assert st.copy_seen("w1", "mintA")

    print("copy rules ok")


if __name__ == "__main__":
    main()
