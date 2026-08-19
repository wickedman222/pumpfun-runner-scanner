import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import (
    Story,
    culture_hit_ok,
    extract_farm_reason,
    is_common_subject,
    is_meme_name,
)


def S(title: str) -> Story:
    return Story(title=title, url="https://example.com", source="test", seen_at=0.0)


assert is_common_subject("KEPLER", "Kepler")
assert is_common_subject("HOPE", "Hope")
assert is_common_subject("ROOM", "Room")
assert not is_common_subject("ESTRIPER", "ESTRIPER")
assert not is_common_subject("SHOBON", "(´・ω・｀)")
assert not is_common_subject("Jimothy", "Jimothy The Raccoon")

assert culture_hit_ok(
    "ESTRIPER", "ESTRIPER", S("Kawasaki Cago Crico Estriper - youtu.be"), 103
)
assert not culture_hit_ok("KEPLER", "Kepler", S("Johannes Kepler — astronomer"), 120)
assert not culture_hit_ok("HOPE", "Hope", S("Hope remains after the storm"), 110)
assert culture_hit_ok(
    "Jimothy", "Jimothy The Raccoon", S("Jimothy The Raccoon spotted in Seattle"), 120
)
assert is_meme_name("PANTS", "dogwifpants")
assert is_meme_name("Jimothy", "Jimothy The Raccoon")
assert is_meme_name("CHONKETHA", "Chonketha the Wet Beaver")
assert not is_meme_name("USWS", "USWS")
assert not is_meme_name("KEPLER", "Kepler")
assert not is_meme_name("NASA", "NASA")
assert not is_meme_name("HOPE", "Hope")

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
    "description": "Before the dog wore a hat, he wore pants. Meme from 2016.",
}
assert not extract_farm_reason(pants)
usws = {**pants, "symbol": "USWS", "name": "USWS"}
assert extract_farm_reason(usws)
print("strict buy rules ok")
