"""Dexscreener PumpSwap entry rules."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.dex import entry_fail, pair_to_coin


def pair(**kw):
    now_ms = int(time.time() * 1000)
    age_h = kw.get("age_h", 2.0)
    return {
        "chainId": "solana",
        "dexId": "pumpswap",
        "url": "https://dexscreener.com/solana/x",
        "pairAddress": "x",
        "baseToken": {"address": "Mint111", "symbol": kw.get("sym", "RUN"), "name": "Run"},
        "marketCap": kw.get("mc", 80_000),
        "fdv": kw.get("mc", 80_000),
        "pairCreatedAt": now_ms - int(age_h * 3600 * 1000),
        "volume": {"h1": kw.get("vol", 40_000)},
        "priceChange": {"h1": kw.get("chg", 40), "h6": 80},
        "txns": {"h1": {"buys": kw.get("buys", 200), "sells": kw.get("sells", 100)}},
    }


def main() -> None:
    hot = pair_to_coin(pair())
    assert entry_fail(hot) == "", entry_fail(hot)
    late = pair_to_coin(pair(mc=900_000))
    assert "late" in entry_fail(late)
    old = pair_to_coin(pair(age_h=20))
    assert "old" in entry_fail(old)
    dump = pair_to_coin(pair(chg=-40))
    assert "cold" in entry_fail(dump) or "dump" in entry_fail(dump)
    print("dex rules ok")


if __name__ == "__main__":
    main()
