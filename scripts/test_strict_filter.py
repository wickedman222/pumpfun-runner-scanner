"""Launch-time hard gates + strict paper exits."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import config
from bot.paper import decide
from bot.state import State
from bot.strict import bundle_pct, has_socials, opening_cluster, top5_pct


def main() -> None:
    assert has_socials({"twitter": "", "telegram": ""}) == "blank socials"
    assert has_socials({"twitter": "https://x.com/a", "telegram": ""}) == ""

    skip = {"curve"}
    owners = {"dev": 10_000_000, "a": 15_000_000, "b": 10_000_000, "curve": 900_000_000}
    cluster = opening_cluster(owners, "dev", skip)
    assert "a" in cluster and "b" in cluster
    pct = bundle_pct(owners, cluster - skip)
    assert pct > 3.0
    assert top5_pct({"w1": 50_000_000, "w2": 50_000_000, "w3": 40_000_000}, skip) > 12
    assert top5_pct({"w1": 20_000_000, "w2": 10_000_000}, skip) < 12

    tmp = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmp
    config.DATA_DIR = tmp
    st = State(os.path.join(tmp, "s.db"))
    del st
    pos = {
        "entry_mc": 20_000,
        "ath_mc": 20_000,
        "remaining_frac": 1.0,
        "opened_at": int(time.time()),
        "path": "strict",
        "tp1_hit": 0,
        "tp2_hit": 0,
        "tp3_hit": 0,
    }
    acts = decide(pos, {"usd_market_cap": 12_000, "ath_market_cap": 12_000})
    assert acts and acts[0][1].startswith("stop"), acts
    pos["ath_mc"] = 40_000
    acts = decide(pos, {"usd_market_cap": 30_000, "ath_market_cap": 40_000})
    assert acts and acts[0][1].startswith("trail"), acts
    pos2 = dict(pos)
    pos2["ath_mc"] = 22_000
    pos2["opened_at"] = int(time.time()) - 9 * 60
    acts = decide(pos2, {"usd_market_cap": 22_000, "ath_market_cap": 22_000})
    assert acts and acts[0][1].startswith("time"), acts
    print("strict filter ok")


if __name__ == "__main__":
    main()
