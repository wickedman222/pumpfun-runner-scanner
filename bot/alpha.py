"""Alpha wallet watchlist. Scores are research notes, not automatic size.

Seed profile AkK5… (pump username martinshkreli, 6.0k followers) holds ~1.46k SOL
and is live on-chain, but Kolscan prints 0 trades/PnL and it has created 0 pump
coins. Treat as whale/observe until we see repeated pump buys with hold time.

Copy book is June-2026 KOL Explorer names that still have SOL and a tx in the
last hour as of 2026-08-25 research. Bots (Cented-class, 400+/day) are observe
only — an 8s poll cannot copy them.
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
        "jason",
        "ACTbvbNm5qTLuofNRPxFPMtHAAtdH1CtzhCZatYHy831",
        False,
        0.85,
        "copy-5sol: 0 fills, cashback spray only — observe",
        wr=0.338,
        pnl_30d_usd=71_173,
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
        "trenchman",
        "Hw5UKBU5k3YudnGwaykj5E8cYUidNMPuEewRRar5Xoc7",
        True,
        0.85,
        "Repeated 15–23x on $200–300 entries, 163 SOL, live",
        wr=0.0,
        pnl_30d_usd=0,
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
    Alpha(
        "tdmilky",
        "AuPp4YTMTyqxYXQnHc5KUc6pUuCSsHQpBJhgnD45yqrf",
        True,
        0.7,
        "Jun-26 #5, 47% WR — only 3.4 SOL left, size down",
        wr=0.471,
        pnl_30d_usd=132_819,
    ),
    Alpha(
        "martinshkreli",
        "AkK5BtfBhj3cJi1f9LVXodbBLxRiePqffm5uiQYpDYQr",
        False,
        0.5,
        "Seed profile: 6.0k pump followers, 1463 SOL, 0 created coins, Kolscan 0 PnL — observe",
        wr=0.0,
        pnl_30d_usd=0,
    ),
    Alpha(
        "cented",
        "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        False,
        0.5,
        "Best 30d $615k / 64.5% WR but 12k trades — too fast to copy at 8s poll",
        wr=0.645,
        pnl_30d_usd=614_967,
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
