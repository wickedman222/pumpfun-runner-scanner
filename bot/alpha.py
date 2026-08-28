"""Alpha wallet watchlist.

Copy-exit book: buy when they buy, flatten when they sell. Spray OBS wallets
and broke/low-fill names dropped so RPC spends on wallets that actually print.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alpha:
    name: str
    address: str
    copy: bool
    conv: float  # 0.5–1.0 multiplies size
    why: str
    wr: float = 0.0
    pnl_30d_usd: float = 0.0


# Ranked. copy=False means we log their buys but do not open paper.
WATCHLIST: tuple[Alpha, ...] = (
    Alpha(
        "decu",
        "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        True,
        1.0,
        "Jun-26 #3, 66.6% WR, $239k/30d, 360 SOL, live",
        wr=0.666,
        pnl_30d_usd=238_984,
    ),
    Alpha(
        "trunoest",
        "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT",
        True,
        1.0,
        "Jun-26 #7, 66.0% WR, $123k/30d, 275 SOL, live",
        wr=0.66,
        pnl_30d_usd=123_328,
    ),
    Alpha(
        "theo",
        "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
        True,
        0.9,
        "Jun-26 #2, 55% WR, $377k/30d, 345 SOL, live",
        wr=0.55,
        pnl_30d_usd=376_569,
    ),
    Alpha(
        "kev",
        "BTf4A2exGK9BCVDNzy65b9dUzXgMqB4weVkvTMFQsadd",
        True,
        0.9,
        "Jun-26 #9, 52.7% WR, $113k/30d, 395 SOL, live",
        wr=0.527,
        pnl_30d_usd=113_409,
    ),
    Alpha(
        "samsrep",
        "CUHBzSPSaNS3tArEtM3maSV6pNdJhHJFYZpurPPK9P7H",
        True,
        0.8,
        "35.7% WR but clustered 15–60x — copy small, trail long",
        wr=0.357,
        pnl_30d_usd=161_357,
    ),
    Alpha(
        "kaythedoc",
        "DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt",
        True,
        0.9,
        "Median hold ~45m (copyable), 15x $WEN, 45 SOL",
        wr=0.0,
        pnl_30d_usd=23_338,
    ),
    Alpha(
        "chester",
        "PMJA8UQDyWTFw2Smhyp9jGA6aTaP7jKHR7BPudrgyYN",
        True,
        0.8,
        "Jun-26 #6, 49% WR, $124k/30d, 40 SOL",
        wr=0.492,
        pnl_30d_usd=124_343,
    ),
)


def copy_alphas() -> list[Alpha]:
    return [a for a in WATCHLIST if a.copy]


def all_alphas() -> list[Alpha]:
    return list(WATCHLIST)


def by_address(addr: str) -> Alpha | None:
    for a in WATCHLIST:
        if a.address == addr:
            return a
    return None
