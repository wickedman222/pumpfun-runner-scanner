"""Runtime config. Telegram channel credentials come from env vars only."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_any(*names: str, default: str = "") -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return default


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- Telegram (fill these in Railway Variables) ---
TELEGRAM_BOT_TOKEN = _env_any(
    "TELEGRAM_BOT_TOKEN",
    "TG_TOKEN",
    "TELEGRAM_TOKEN",
)
TELEGRAM_CHAT_ID = _env_any(
    "TELEGRAM_CHAT_ID",
    "CHAT_ID",
    "SIGNAL_CHAT_ID",
)

# --- Optional data upgrades ---
HELIUS_API_KEY = _env("HELIUS_API_KEY")
SOLANA_RPC_URL = _env(
    "SOLANA_RPC_URL",
    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com",
)

# --- Timing ---
PUMP_POLL_SEC = _int("PUMP_POLL_SEC", 12)
ATTENTION_POLL_SEC = _int("ATTENTION_POLL_SEC", 120)
EXPANSION_WAIT_SEC = _int("EXPANSION_WAIT_SEC", 180)
MAX_TOKEN_AGE_SEC = _int("MAX_TOKEN_AGE_SEC", 20 * 60)
MAX_LIVE_AGE_SEC = _int("MAX_LIVE_AGE_SEC", 6 * 3600)
# Organic homepage books (boost=NONE) often print 1–6h after launch, not in 20m.
MAX_ACTIVE_AGE_SEC = _int("MAX_ACTIVE_AGE_SEC", 6 * 3600)
MIN_LIVE_PARTICIPANTS = _int("MIN_LIVE_PARTICIPANTS", 5)
# 0 = no daily cap. Strictness comes from the gates, not a quota.
MAX_SIGNALS_PER_DAY = _int("MAX_SIGNALS_PER_DAY", 0)
LEADERBOARD_SEC = _int("LEADERBOARD_SEC", 6 * 3600)
LEADERBOARD_SIZE = _int("LEADERBOARD_SIZE", 15)

# --- Structure gates ---
MAX_DEV_PRIOR_TOKENS = _int("MAX_DEV_PRIOR_TOKENS", 2)
MAX_TOP_HOLDER_PCT = _float("MAX_TOP_HOLDER_PCT", 10.0)
MAX_TOP10_PCT = _float("MAX_TOP10_PCT", 40.0)
MAX_INSIDER_PCT = _float("MAX_INSIDER_PCT", 20.0)
MAX_RUGCHECK_SCORE = _int("MAX_RUGCHECK_SCORE", 1500)
MIN_UNIQUE_HOLDERS = _int("MIN_UNIQUE_HOLDERS", 25)
MIN_MATCH_SCORE = _int("MIN_MATCH_SCORE", 90)
# First look above this is already late (the run already happened).
MAX_FIRST_LOOK_MC = _float("MAX_FIRST_LOOK_MC", 80_000)
# Copy farms (USWS/EYE style) — skip the whole ticker family, do not promote the original.
META_COPY_MIN = _int("META_COPY_MIN", 2)

# --- Paper book (no real SOL) ---
PAPER_ENABLED = _int("PAPER_ENABLED", 1) == 1
PAPER_START_SOL = _float("PAPER_START_SOL", 2.0)
# Bump this string to flatten the paper book and start from PAPER_START_SOL again.
PAPER_BOOK_ID = _env("PAPER_BOOK_ID", "overnight-1")
PAPER_SIZE_FRAC = _float("PAPER_SIZE_FRAC", 0.075)  # 2 SOL → 0.15
PAPER_SIZE_MIN = _float("PAPER_SIZE_MIN", 0.10)
PAPER_SIZE_MAX = _float("PAPER_SIZE_MAX", 0.20)
PAPER_MAX_OPEN = _int("PAPER_MAX_OPEN", 3)
PAPER_MAX_ENTRY_MC = _float("PAPER_MAX_ENTRY_MC", 35_000)
PAPER_MIN_EQUITY = _float("PAPER_MIN_EQUITY", 0.25)
PAPER_FEE = _float("PAPER_FEE", 0.01)
PAPER_ENTRY_SLIP = _float("PAPER_ENTRY_SLIP", 0.08)
PAPER_EXIT_SLIP = _float("PAPER_EXIT_SLIP", 0.05)
PAPER_STOP_FRAC = _float("PAPER_STOP_FRAC", 0.55)  # flatten at −45%
PAPER_TIME_DEAD_SEC = _int("PAPER_TIME_DEAD_SEC", 2 * 3600)
PAPER_TIME_DEAD_MULT = _float("PAPER_TIME_DEAD_MULT", 1.6)
PAPER_TP1_MULT = _float("PAPER_TP1_MULT", 2.0)
PAPER_TP1_SELL = _float("PAPER_TP1_SELL", 0.40)
PAPER_TP2_MULT = _float("PAPER_TP2_MULT", 4.0)
PAPER_TP2_SELL = _float("PAPER_TP2_SELL", 0.30)  # of original; leaves ~30% moonbag
PAPER_TP3_MULT = _float("PAPER_TP3_MULT", 10.0)
PAPER_TP3_SELL = _float("PAPER_TP3_SELL", 0.15)  # half the moonbag
PAPER_TRAIL_GIVEBACK = _float("PAPER_TRAIL_GIVEBACK", 0.50)  # sell rest if −50% off post-entry ATH after TP1
PAPER_REPORT_SEC = _int("PAPER_REPORT_SEC", 2 * 3600)

# Known AMM / pool authorities — not "holders"
LP_OWNERS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium
    "WLHv2UAZm6z4KyaaELi5pjdbJh6RESMva1Rnn8pJVVh",  # Pump AMM / PumpSwap-related
    "7YttLkHDoNj9wyDur5pM1ejNaAvT9X4eqaYcHQqtj2G5",
}

PUMP_API = _env("PUMP_API", "https://frontend-api-v3.pump.fun")
RUGCHECK_API = _env("RUGCHECK_API", "https://api.rugcheck.xyz/v1")
PUMP_WEB = "https://pump.fun/coin"

PORT = _int("PORT", 8080)
DATA_DIR = _env("DATA_DIR", "/app/data")
if os.name == "nt" and not os.path.isdir("/app"):
    DATA_DIR = _env("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))


def require_telegram() -> None:
    if not TELEGRAM_BOT_TOKEN or ":" not in TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "Missing TELEGRAM_BOT_TOKEN. In Railway → service → Variables set "
            "TELEGRAM_BOT_TOKEN to the full BotFather string (123456:AA...)."
        )
    if not TELEGRAM_CHAT_ID:
        raise SystemExit(
            "Missing TELEGRAM_CHAT_ID. Set it to your channel/group/user id "
            "(channel ids usually look like -100xxxxxxxxxx)."
        )
