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
PUMP_POLL_SEC = _int("PUMP_POLL_SEC", 8)
ATTENTION_POLL_SEC = _int("ATTENTION_POLL_SEC", 120)
EXPANSION_WAIT_SEC = _int("EXPANSION_WAIT_SEC", 180)
# Keep the watch after the first check. FISHBONE pulled back for ~8m then ran.
EXPANSION_HOLD_SEC = _int("EXPANSION_HOLD_SEC", 30 * 60)
EXPANSION_RECHECK_SEC = _int("EXPANSION_RECHECK_SEC", 30)
MAX_TOKEN_AGE_SEC = _int("MAX_TOKEN_AGE_SEC", 20 * 60)
MAX_LIVE_AGE_SEC = _int("MAX_LIVE_AGE_SEC", 2 * 3600)
# Organic homepage books (boost=NONE) often print 1–6h after launch, not in 20m.
MAX_ACTIVE_AGE_SEC = _int("MAX_ACTIVE_AGE_SEC", 6 * 3600)
MIN_LIVE_PARTICIPANTS = _int("MIN_LIVE_PARTICIPANTS", 15)
MIN_LIVE_MC = _float("MIN_LIVE_MC", 8_000)
# Spot on-curve early. Buy confirmed strength. Graduation alone is not a buy.
MIN_ARM_MC = _float("MIN_ARM_MC", 8_000)
MAX_ARM_MC = _float("MAX_ARM_MC", 22_000)
MIN_TAPE_MC = _float("MIN_TAPE_MC", MIN_ARM_MC)
MIN_BUY_MC = _float("MIN_BUY_MC", 10_000)
MIN_ARM_HOLD_SEC = _int("MIN_ARM_HOLD_SEC", 90)
FAST_HOLD_SEC = _int("FAST_HOLD_SEC", 16)
FAST_MULT = _float("FAST_MULT", 1.25)
MAX_DD_AT_BUY = _float("MAX_DD_AT_BUY", 0.15)
GRADUATE_CONFIRM_MC = _float("GRADUATE_CONFIRM_MC", 60_000)
EXPANSION_MULT = _float("EXPANSION_MULT", 1.50)
TAPE_REFRESH_LIMIT = _int("TAPE_REFRESH_LIMIT", 80)
# 0 = no daily trade cap. Runners are rare enough; do not sit them out.
MAX_SIGNALS_PER_DAY = _int("MAX_SIGNALS_PER_DAY", 0)
# Only posts stamped with this id count toward the daily cap.
# Bump when the strat changes so leftover rows from an old loop do not sit us out.
SIGNAL_BOOK_ID = _env("SIGNAL_BOOK_ID", "copy-1")
MIN_MATCH_SCORE = _int("MIN_MATCH_SCORE", 100)
LEADERBOARD_SEC = _int("LEADERBOARD_SEC", 6 * 3600)
LEADERBOARD_SIZE = _int("LEADERBOARD_SIZE", 15)
WALLET_REPORT_SEC = _int("WALLET_REPORT_SEC", 2 * 3600)
WALLET_REPORT_SIZE = _int("WALLET_REPORT_SIZE", 12)
GATHER_MODE = _int("GATHER_MODE", 1) == 1
GATHER_SEC = _int("GATHER_SEC", 12 * 60)
EARLY_MIN_ATH = _float("EARLY_MIN_ATH", 200_000)
EARLY_MAX_AGE_SEC = _int("EARLY_MAX_AGE_SEC", 14 * 24 * 3600)
EARLY_MAX_RANK = _int("EARLY_MAX_RANK", 18)
EARLY_SIG_PAGES = _int("EARLY_SIG_PAGES", 18)
EARLY_PAGE_LIMIT = _int("EARLY_PAGE_LIMIT", 80)

# --- Structure gates ---
MAX_DEV_PRIOR_TOKENS = _int("MAX_DEV_PRIOR_TOKENS", 2)
MAX_TOP_HOLDER_PCT = _float("MAX_TOP_HOLDER_PCT", 10.0)
MAX_TOP10_PCT = _float("MAX_TOP10_PCT", 40.0)
MAX_INSIDER_PCT = _float("MAX_INSIDER_PCT", 20.0)
MAX_RUGCHECK_SCORE = _int("MAX_RUGCHECK_SCORE", 1500)
MIN_UNIQUE_HOLDERS = _int("MIN_UNIQUE_HOLDERS", 25)
# Chase line at buy. $200k first-prints were graduation snipes, not entries.
MAX_FIRST_LOOK_MC = _float("MAX_FIRST_LOOK_MC", 28_000)
# Full paper size at/under this. Between here and MAX_FIRST_LOOK we size down.
PAPER_FULL_SIZE_MC = _float("PAPER_FULL_SIZE_MC", 22_000)
# Same-ticker floods — farm mechanic, not a judgment of the letters.
META_COPY_MIN = _int("META_COPY_MIN", 2)

# Follow wallets that sat in recent held runners (rugcheck holders).
WALLET_HARVEST_SEC = _int("WALLET_HARVEST_SEC", 180)
WALLET_MIN_RUNNERS = _int("WALLET_MIN_RUNNERS", 2)
WALLET_MIN_OVERLAP = _int("WALLET_MIN_OVERLAP", 2)
# A mint only credits a wallet if it actually ran. EARNBOT-class $250k
# BOOST prints do not count; FISHBONE $350k does.
WALLET_MIN_SOURCE_ATH = _float("WALLET_MIN_SOURCE_ATH", 300_000)
WALLET_MIN_PCT = _float("WALLET_MIN_PCT", 0.05)
WALLET_MAX_PCT = _float("WALLET_MAX_PCT", 8.0)
RUNNER_MIN_ATH = _float("RUNNER_MIN_ATH", 500_000)
RUNNER_MAX_AGE_SEC = _int("RUNNER_MAX_AGE_SEC", 7 * 24 * 3600)
RUNNER_MAX_DD = _float("RUNNER_MAX_DD", 0.55)
RUNNER_MIN_DD = _float("RUNNER_MIN_DD", 0.08)
WALLET_MAX_BUY_MC = _float("WALLET_MAX_BUY_MC", 80_000)
WALLET_MAX_AGE_SEC = _int("WALLET_MAX_AGE_SEC", 90 * 60)
WALLET_BOOK_ID = _env("WALLET_BOOK_ID", "early-1")

# --- Paper book (no real SOL) ---
PAPER_ENABLED = _int("PAPER_ENABLED", 0) == 1
PAPER_START_SOL = _float("PAPER_START_SOL", 5.0)
# Bump this string to flatten the paper book and start from PAPER_START_SOL again.
PAPER_BOOK_ID = _env("PAPER_BOOK_ID", "copy-exit-1")
PAPER_SIZE_FIXED = _float("PAPER_SIZE_FIXED", 0.30)
PAPER_SIZE_FRAC = _float("PAPER_SIZE_FRAC", 0.0)  # unused — size is PAPER_SIZE_FIXED
PAPER_SIZE_MIN = _float("PAPER_SIZE_MIN", 0.30)
PAPER_SIZE_MAX = _float("PAPER_SIZE_MAX", 0.30)
PAPER_MAX_OPEN = _int("PAPER_MAX_OPEN", 24)
COPY_MODE = _int("COPY_MODE", 0) == 1
COPY_MAX_LAG_SEC = _int("COPY_MAX_LAG_SEC", 120)
COPY_MIN_MC = _float("COPY_MIN_MC", 8_000)
COPY_MAX_MC = _float("COPY_MAX_MC", 50_000)
COPY_MAX_AGE_SEC = _int("COPY_MAX_AGE_SEC", 4 * 3600)
COPY_POLL_SEC = _int("COPY_POLL_SEC", 8)
PAPER_MIN_EQUITY = _float("PAPER_MIN_EQUITY", 0.25)
PAPER_FEE = _float("PAPER_FEE", 0.01)
PAPER_ENTRY_SLIP = _float("PAPER_ENTRY_SLIP", 0.08)
PAPER_EXIT_SLIP = _float("PAPER_EXIT_SLIP", 0.05)
PAPER_STOP_FRAC = _float("PAPER_STOP_FRAC", 0.0)  # 0 = hold, no stop
# 0 = off. 2h-must-1.6x flattened Jimothy/ANSEM-class before the real leg.
PAPER_TIME_DEAD_SEC = _int("PAPER_TIME_DEAD_SEC", 0)
PAPER_TIME_DEAD_MULT = _float("PAPER_TIME_DEAD_MULT", 1.6)
PAPER_TP1_MULT = _float("PAPER_TP1_MULT", 2.0)
PAPER_TP1_SELL = _float("PAPER_TP1_SELL", 0.25)  # take a slice, leave the runner
PAPER_TP2_MULT = _float("PAPER_TP2_MULT", 4.0)
PAPER_TP2_SELL = _float("PAPER_TP2_SELL", 0.25)  # of original; leaves ~50% moonbag
PAPER_TP3_MULT = _float("PAPER_TP3_MULT", 10.0)
PAPER_TP3_SELL = _float("PAPER_TP3_SELL", 0.20)  # clip part of the moonbag
PAPER_TRAIL_GIVEBACK = _float("PAPER_TRAIL_GIVEBACK", 0.0)  # 0 = no trail
# Missed dump backstop: still full size and <0.22x after 40m → flatten.
PAPER_STALE_SEC = _int("PAPER_STALE_SEC", 40 * 60)
PAPER_STALE_MULT = _float("PAPER_STALE_MULT", 0.22)
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
