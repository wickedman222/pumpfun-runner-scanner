"""Exogenous attention: news + reddit. Token-promo headlines do not count."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from .httputil import get_json, get_text

log = logging.getLogger("runner")

GENERIC_TICKERS = {
    "PEPE", "DOGE", "CAT", "DOG", "MOON", "PUMP", "ELON", "TRUMP", "BIDEN",
    "WIF", "BONK", "AI", "GPT", "SOL", "BTC", "ETH", "MEME", "COIN", "TOKEN",
    "BABY", "INU", "SHIB", "FLOKI", "WOJAK", "CHAD", "BASED", "RIZZ", "SIGMA",
    "GIGA", "PUSSY", "SEX", "PORN", "FUCK", "SHIT", "POOP", "FART", "ASS",
    "TITS", "DICK", "COCK", "CUM", "NIGGER", "HITLER", "JEW", "GAY", "FAG",
    "TEST", "NEW", "THE", "THIS", "THAT", "LOL", "OMG", "WTF", "LMAO",
    "USD", "USDC", "USDT", "PUMPFUN", "LAUNCH", "MOONSHOT", "DEGEN",
    "CHILL", "GUY", "GIRL", "KING", "QUEEN", "GOD", "DEVIL", "ANGEL",
}

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "from", "by", "at", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "it", "its", "his", "her", "their",
    "after", "over", "into", "about", "after", "just", "more", "than",
    "will", "can", "new", "old", "says", "said", "year", "years", "day",
    "video", "watch", "here", "what", "when", "where", "who", "why", "how",
    "not", "but", "out", "up", "down", "off", "has", "have", "had",
}

PROMO_BITS = (
    "memecoin", "meme coin", "meme-coin", "crypto token", "token launch",
    "pump.fun", "pump fun", "bonding curve", "presale", "goes to the moon",
    "market cap", "price surges", "all-time high", "dexscreener", "raydium",
    "pumpswap", "buy the dip", "new crypto", "solana token", "airdrop",
    "coinmarketcap", "coingecko", "bybit", "coinpedia", "price prediction",
    "price today", "live price", " to usd", "historical data", "know your meme",
)

WEAK_WORDS = {
    "black", "white", "green", "bull", "bear", "bean", "build", "alive",
    "online", "stars", "nice", "working", "hold", "king", "jungle", "silent",
    "leader", "market", "retail", "magic", "block", "time", "chain", "love",
    "coin", "token", "cat", "dog", "wolf", "fish", "bird", "moon", "sun",
}

NEWS_FEEDS = [
    "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=viral+OR+squirrel+OR+hippo+OR+celebrity+died+OR+mascot&hl=en-US&gl=US&ceid=US:en",
]

REDDIT = [
    "https://www.reddit.com/r/news/hot.json?limit=25",
    "https://www.reddit.com/r/worldnews/hot.json?limit=15",
    "https://www.reddit.com/r/nottheonion/hot.json?limit=15",
    "https://www.reddit.com/r/offbeat/hot.json?limit=15",
    "https://www.reddit.com/r/todayilearned/hot.json?limit=15",
]

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9']{2,}")


@dataclass
class Story:
    title: str
    url: str
    source: str
    seen_at: float

    @property
    def words(self) -> set[str]:
        return extract_words(self.title)


def extract_words(text: str) -> set[str]:
    return {
        w.lower()
        for w in WORD_RE.findall(text or "")
        if w.lower() not in STOP and len(w) >= 3
    }


def is_token_promo(title: str) -> bool:
    t = (title or "").lower()
    return any(bit in t for bit in PROMO_BITS)


def is_generic_ticker(symbol: str, name: str) -> bool:
    sym = (symbol or "").upper().lstrip("$")
    if not sym or len(sym) < 3:
        return True
    if sym in GENERIC_TICKERS:
        return True
    name_l = (name or "").strip().lower()
    if name_l in {"cat", "dog", "pepe", "moon", "coin", "token", "the z coin", "z coin"}:
        return True
    if name_l in WEAK_WORDS:
        return True
    return False


def is_distinctive_name(symbol: str, name: str) -> bool:
    """Jimothy / SHOBON / Gorikun — a real word or character, not USWS or $Z."""
    if is_generic_ticker(symbol, name) or is_fake_official(symbol, name):
        return False
    n = (name or "").strip()
    s = (symbol or "").strip().lstrip("$")
    if len(n) >= 6:
        return True
    if len(s) >= 5 and re.search(r"[AEIOUaeiou]", s):
        return True
    return False


def search_query_for(coin: dict) -> str:
    name = (coin.get("name") or "").strip()
    symbol = (coin.get("symbol") or "").strip()
    words = extract_words(name)
    if words and not name.startswith("("):
        return name
    return symbol or name


def has_character_identity(coin: dict) -> bool:
    """Real recent runners: a character/culture, live crowd, or both.

    SHOBON = live + 49 people + shobon.xyz
    Gorikun = distinctive name + site + lore
    Jimothy = raccoon + IG/YT
    USWS/EYE = BOOST, no identity, ATH glued to spot
    """
    if not is_distinctive_name(coin.get("symbol") or "", coin.get("name") or ""):
        return False
    parts = int(coin.get("num_participants") or 0)
    if coin.get("is_currently_live") and parts >= 5:
        return True
    twitter = bool(coin.get("twitter"))
    website = bool(coin.get("website"))
    lore = len(coin.get("description") or "") >= 60
    if website and (twitter or lore):
        return True
    if twitter and lore:
        return True
    return False


FAKE_OFFICIAL_TICKERS = {
    "USWS", "USWR", "UOTF", "UATF", "WWR", "NTDA", "Z500", "ZTERM",
    "EYE", "LAYOOO",
}

FAKE_OFFICIAL_PHRASES = (
    "united states", "united oil", "united american", "united water",
    "trust fund", "water supply", "water reserve", "world water",
    "digital account", "national trump", "federal reserve",
    "bulls's eye", "bulls eye", "strategic reserve", "treasury fund",
    "oil trust", "american trust",
)


def is_fake_official(symbol: str, name: str) -> bool:
    """Manufactured 'US agency / fund / reserve' extract metas. Contract is clean; the play is not."""
    sym = re.sub(r"[^A-Z0-9]", "", (symbol or "").upper())
    if sym in FAKE_OFFICIAL_TICKERS:
        return True
    blob = f"{symbol or ''} {name or ''}".lower()
    return any(p in blob for p in FAKE_OFFICIAL_PHRASES)


def _boost_on(coin: dict) -> bool:
    mode = str(coin.get("boost_mode") or "NONE").upper()
    return mode not in {"", "NONE", "NULL", "FALSE", "0", "OFF"}


def extract_farm_reason(coin: dict) -> str:
    """Skip painted extract books. Do not skip every BOOST coin.

    USWS/EYE: BOOST, fake-official name, no site, ATH glued to spot.
    SHOBON/Gorikun: BOOST too, but a real character + site / live crowd.
    Mayhem stays banned. Empty chat is not a signal either way.
    """
    if is_fake_official(coin.get("symbol") or "", coin.get("name") or ""):
        return "fake official / fund-reserve meta"
    mayhem = str(coin.get("mayhem_state") or "").upper()
    if mayhem and mayhem not in {"", "NONE", "NULL", "FALSE", "0", "OFF"}:
        return f"mayhem painted book ({mayhem})"
    if _boost_on(coin) and not has_character_identity(coin):
        usd = float(coin.get("usd_market_cap") or 0)
        ath = float(coin.get("ath_market_cap") or usd or 0)
        if ath > 0 and usd >= 0.95 * ath and usd >= 20_000:
            return "boost one-way tape (no character/live identity)"
        return "boost book with no character/live identity"
    return ""


def parse_rss(xml_text: str, source: str) -> list[Story]:
    if not xml_text:
        return []
    stories: list[Story] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    now = time.time()
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or is_token_promo(title):
            continue
        stories.append(Story(title=title, url=link, source=source, seen_at=now))
    return stories


def parse_reddit(payload: dict, source: str) -> list[Story]:
    stories: list[Story] = []
    now = time.time()
    children = (((payload or {}).get("data") or {}).get("children")) or []
    for child in children:
        data = child.get("data") or {}
        title = (data.get("title") or "").strip()
        permalink = data.get("permalink") or ""
        url = f"https://www.reddit.com{permalink}" if permalink else (data.get("url") or "")
        if not title or is_token_promo(title):
            continue
        stories.append(Story(title=title, url=url, source=source, seen_at=now))
    return stories


class Attention:
    def __init__(self) -> None:
        self.stories: list[Story] = []

    async def refresh(self, http: httpx.AsyncClient) -> int:
        found: list[Story] = []
        for url in NEWS_FEEDS:
            xml = await get_text(http, url)
            found.extend(parse_rss(xml, "news"))
        for url in REDDIT:
            data = await get_json(http, url, headers={"User-Agent": "pumpfun-runner-scanner/1.0"})
            if isinstance(data, dict):
                found.extend(parse_reddit(data, "reddit"))
        # de-dupe by title
        uniq: dict[str, Story] = {}
        for s in found:
            key = s.title.lower()
            if key not in uniq:
                uniq[key] = s
        self.stories = list(uniq.values())
        log.info("Attention window: %s exogenous headlines", len(self.stories))
        return len(self.stories)

    async def search_subject(self, http: httpx.AsyncClient, query: str) -> list[Story]:
        q = (query or "").strip()
        if len(q) < 3:
            return []
        found: list[Story] = []
        q_plus = re.sub(r"\s+", "+", q)
        xml = await get_text(
            http, f"https://news.google.com/rss/search?q={q_plus}&hl=en-US&gl=US&ceid=US:en"
        )
        found.extend(parse_rss(xml, "news-search"))

        wiki = await get_json(
            http,
            "https://en.wikipedia.org/w/api.php"
            f"?action=opensearch&search={q_plus}&limit=5&namespace=0&format=json",
        )
        if isinstance(wiki, list) and len(wiki) >= 4:
            titles, descs, links = wiki[1], wiki[2], wiki[3]
            now = time.time()
            for title, desc, link in zip(titles, descs, links):
                blob = f"{title} {desc}"
                if not title or is_token_promo(blob):
                    continue
                found.append(Story(title=f"{title} — {desc}" if desc else title, url=link or "", source="wiki", seen_at=now))

        reddit = await get_json(
            http,
            f"https://www.reddit.com/search.json?q={q_plus}&limit=8&sort=relevance",
            headers={"User-Agent": "pumpfun-runner-scanner/1.0"},
        )
        if isinstance(reddit, dict):
            found.extend(parse_reddit(reddit, "reddit-search"))

        return [s for s in found if s.title and not is_token_promo(s.title)]

    def match_coin(self, symbol: str, name: str) -> list[tuple[Story, int]]:
        hits: list[tuple[Story, int]] = []
        for story in self.stories:
            score = score_match(symbol, name, story)
            if score >= 40:
                hits.append((story, score))
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:5]


def score_match(symbol: str, name: str, story: Story) -> int:
    """Higher = cleaner mapping of this mint onto an exogenous headline."""
    if is_token_promo(story.title) or is_token_promo(story.url):
        return 0
    title = story.title.lower()
    title_words = story.words
    sym = (symbol or "").lower().lstrip("$")
    name_l = (name or "").strip().lower()
    name_words = {w for w in extract_words(name) if w not in WEAK_WORDS}

    name_in = bool(name_l) and len(name_l) >= 5 and name_l in title
    ticker_in = len(sym) >= 4 and (sym in title_words or f" {sym} " in f" {title} ")
    overlap = name_words & title_words
    rare = {w for w in overlap if len(w) >= 5}
    phrase = bool(name_l) and " " in name_l and name_l in title

    # One shared weak word is not a story. Need the name/ticker or two rare words.
    if not (name_in or ticker_in or phrase or len(rare) >= 2):
        return 0

    score = 0
    if ticker_in:
        score += 45
    if name_in:
        score += 40
    score += 18 * len(rare)
    if phrase:
        score += 20
    if is_generic_ticker(symbol, name):
        return 0
    return score
