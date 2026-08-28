"""Early-buyer gather ranking."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.gather import is_winner, sold_from_hold
from bot.state import State


def coin(ath, **kw):
    return {
        "symbol": kw.get("symbol", "RUN"),
        "mint": kw.get("mint", "m1"),
        "usd_market_cap": kw.get("usd", ath),
        "ath_market_cap": ath,
        "created_timestamp": int((time.time() - kw.get("age", 3600)) * 1000),
        "boost_mode": kw.get("boost", "NONE"),
        "mayhem_state": "NONE",
        "is_cashback_enabled": kw.get("cash", False),
        "is_currently_live": False,
        "num_participants": 0,
        "complete": False,
    }


def main() -> None:
    now = time.time()
    assert is_winner(coin(50_000), now).startswith("ath")
    assert is_winner(coin(400_000), now) == ""
    assert is_winner(coin(400_000, cash=True), now)
    assert sold_from_hold(1000, 0)
    assert sold_from_hold(1000, 50)
    assert not sold_from_hold(1000, 800)

    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    config.DATA_DIR = tmp
    st = State(os.path.join(tmp, "g.db"))
    st.note_early_hit("WalAAA111", "mint1", "SAM", 3, 0.4, 2.1, 1, 900_000)
    st.note_early_hit("WalAAA111", "mint2", "FISH", 8, 0.2, 0.0, 0, 400_000)
    st.note_early_hit("WalBBB222", "mint1", "SAM", 1, 0.5, 0.0, 0, 900_000)
    board = st.early_board()
    assert board["wallets"] == 2
    assert board["hits"] == 3
    assert board["sold"] == 1
    top = board["top"]
    assert top[0]["wallet"] == "WalAAA111"
    assert top[0]["sold_n"] == 1
    assert top[0]["n"] == 2
    print("gather rules ok")


if __name__ == "__main__":
    main()
