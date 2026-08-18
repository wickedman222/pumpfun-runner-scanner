import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.attention import Story, culture_hit_ok, is_common_subject


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
print("strict buy rules ok")
