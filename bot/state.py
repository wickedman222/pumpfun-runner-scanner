from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_mints (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    creator TEXT,
    created_ts INTEGER,
    seen_at INTEGER
);
CREATE TABLE IF NOT EXISTS posted (
    mint TEXT PRIMARY KEY,
    posted_at INTEGER,
    symbol TEXT,
    name TEXT,
    url TEXT,
    story TEXT,
    entry_mc REAL,
    ath_mc REAL,
    last_mc REAL,
    last_checked INTEGER
);
CREATE TABLE IF NOT EXISTS pending (
    mint TEXT PRIMARY KEY,
    ready_at INTEGER,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_seen_created ON seen_mints(created_ts);
CREATE INDEX IF NOT EXISTS idx_seen_symbol ON seen_mints(symbol);
CREATE TABLE IF NOT EXISTS smart_wallet_mints (
    wallet TEXT NOT NULL,
    mint TEXT NOT NULL,
    pct REAL,
    seen_at INTEGER,
    PRIMARY KEY (wallet, mint)
);
CREATE INDEX IF NOT EXISTS idx_smart_wallet ON smart_wallet_mints(wallet);
CREATE TABLE IF NOT EXISTS tx_harvested (
    mint TEXT PRIMARY KEY,
    seen_at INTEGER
);
CREATE TABLE IF NOT EXISTS tape (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    creator TEXT,
    created_ts INTEGER,
    first_seen_at INTEGER,
    first_mc REAL,
    armed_mc REAL,
    armed_at INTEGER,
    last_mc REAL,
    ath_mc REAL,
    last_seen_at INTEGER,
    complete INTEGER,
    status TEXT,
    skip_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_tape_due ON tape(status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_tape_created ON tape(created_ts);
CREATE TABLE IF NOT EXISTS paper_wallet (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_sol REAL NOT NULL,
    starting_sol REAL NOT NULL,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS paper_positions (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    url TEXT,
    path TEXT,
    opened_at INTEGER,
    cost_sol REAL,
    original_qty_sol REAL,
    remaining_qty_sol REAL,
    remaining_frac REAL,
    entry_mc REAL,
    ath_mc REAL,
    last_mc REAL,
    realized_sol REAL,
    tp1_hit INTEGER DEFAULT 0,
    tp2_hit INTEGER DEFAULT 0,
    tp3_hit INTEGER DEFAULT 0,
    status TEXT,
    close_reason TEXT,
    closed_at INTEGER
);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT,
    ts INTEGER,
    side TEXT,
    reason TEXT,
    frac REAL,
    multiple REAL,
    sol REAL,
    cash_after REAL,
    mc REAL
);
"""


class State:
    def __init__(self, path: str | None = None) -> None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self.path = path or os.path.join(config.DATA_DIR, "scanner.db")
        with self._conn() as con:
            con.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        wanted = {
            "name": "TEXT",
            "url": "TEXT",
            "story": "TEXT",
            "entry_mc": "REAL",
            "ath_mc": "REAL",
            "last_mc": "REAL",
            "last_checked": "INTEGER",
        }
        with self._conn() as con:
            existing = {row[1] for row in con.execute("PRAGMA table_info(posted)")}
            for col, typ in wanted.items():
                if col not in existing:
                    con.execute(f"ALTER TABLE posted ADD COLUMN {col} {typ}")
            paper_cols = {row[1] for row in con.execute("PRAGMA table_info(paper_wallet)")}
            if paper_cols and "book_id" not in paper_cols:
                con.execute("ALTER TABLE paper_wallet ADD COLUMN book_id TEXT")
            posted_cols = {row[1] for row in con.execute("PRAGMA table_info(posted)")}
            if "book_id" not in posted_cols:
                con.execute("ALTER TABLE posted ADD COLUMN book_id TEXT")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS smart_wallet_mints (
                    wallet TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    pct REAL,
                    seen_at INTEGER,
                    PRIMARY KEY (wallet, mint)
                );
                CREATE INDEX IF NOT EXISTS idx_smart_wallet ON smart_wallet_mints(wallet);
                CREATE TABLE IF NOT EXISTS tx_harvested (
                    mint TEXT PRIMARY KEY,
                    seen_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS tape (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT,
                    name TEXT,
                    creator TEXT,
                    created_ts INTEGER,
                    first_seen_at INTEGER,
                    first_mc REAL,
                    armed_mc REAL,
                    armed_at INTEGER,
                    last_mc REAL,
                    ath_mc REAL,
                    last_seen_at INTEGER,
                    complete INTEGER,
                    status TEXT,
                    skip_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tape_due ON tape(status, last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_tape_created ON tape(created_ts);
                """
            )

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def mark_seen(self, coin: dict) -> bool:
        """Return True if this mint is new."""
        now = int(time.time())
        with self._conn() as con:
            try:
                con.execute(
                    "INSERT INTO seen_mints(mint, symbol, name, creator, created_ts, seen_at) VALUES (?,?,?,?,?,?)",
                    (
                        coin["mint"],
                        (coin.get("symbol") or "").upper(),
                        coin.get("name") or "",
                        coin.get("creator") or "",
                        int((coin.get("created_timestamp") or 0) / 1000),
                        now,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def same_symbol_copies(self, symbol: str, created_ts: int) -> int:
        symbol = (symbol or "").upper()
        if not symbol:
            return 0
        with self._conn() as con:
            row = con.execute(
                """
                SELECT COUNT(*) AS n FROM seen_mints
                WHERE symbol = ? AND created_ts > ? AND created_ts < ?
                """,
                (symbol, created_ts, created_ts + 2 * 3600),
            ).fetchone()
        return int(row["n"] if row else 0)

    def older_same_name(self, symbol: str, name: str, created_ts: int) -> dict | None:
        symbol = (symbol or "").upper()
        name_l = (name or "").strip().lower()
        with self._conn() as con:
            row = con.execute(
                """
                SELECT mint, symbol, name, created_ts FROM seen_mints
                WHERE created_ts < ? AND created_ts > ?
                  AND (symbol = ? OR lower(name) = ?)
                ORDER BY created_ts ASC LIMIT 1
                """,
                (created_ts, created_ts - 6 * 3600, symbol, name_l),
            ).fetchone()
        return dict(row) if row else None

    def creator_token_count(self, creator: str) -> int:
        if not creator:
            return 0
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM seen_mints WHERE creator = ?",
                (creator,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def upsert_tape(self, coin: dict) -> dict:
        now = int(time.time())
        usd = float(coin.get("usd_market_cap") or 0)
        ath = float(coin.get("ath_market_cap") or usd or 0)
        created = int(coin.get("created_timestamp") or 0)
        if created > 10_000_000_000:
            created //= 1000
        mint = coin.get("mint") or ""
        with self._conn() as con:
            prev = con.execute("SELECT * FROM tape WHERE mint = ?", (mint,)).fetchone()
            if not prev:
                con.execute(
                    """
                    INSERT INTO tape(
                        mint, symbol, name, creator, created_ts, first_seen_at,
                        first_mc, armed_mc, armed_at, last_mc, ath_mc,
                        last_seen_at, complete, status, skip_reason
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mint,
                        (coin.get("symbol") or "").upper(),
                        coin.get("name") or "",
                        coin.get("creator") or "",
                        created,
                        now,
                        usd,
                        0.0,
                        0,
                        usd,
                        max(ath, usd),
                        now,
                        1 if coin.get("complete") else 0,
                        "watching",
                        "",
                    ),
                )
            else:
                con.execute(
                    """
                    UPDATE tape SET
                        symbol = ?, name = ?, last_mc = ?,
                        ath_mc = MAX(ath_mc, ?), last_seen_at = ?,
                        complete = ?
                    WHERE mint = ?
                    """,
                    (
                        (coin.get("symbol") or "").upper(),
                        coin.get("name") or "",
                        usd,
                        max(ath, usd),
                        now,
                        1 if coin.get("complete") else 0,
                        mint,
                    ),
                )
            row = con.execute("SELECT * FROM tape WHERE mint = ?", (mint,)).fetchone()
        return dict(row) if row else {}

    def arm_tape(self, mint: str, mc: float) -> None:
        with self._conn() as con:
            con.execute(
                """
                UPDATE tape SET armed_mc = ?, armed_at = ?, status = 'armed'
                WHERE mint = ? AND (armed_mc IS NULL OR armed_mc <= 0)
                """,
                (mc, int(time.time()), mint),
            )

    def mark_tape(self, mint: str, status: str, reason: str = "") -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE tape SET status = ?, skip_reason = ? WHERE mint = ?",
                (status, reason, mint),
            )

    def touch_tape(self, mint: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE tape SET last_seen_at = ? WHERE mint = ?",
                (int(time.time()), mint),
            )

    def tape_due(self, limit: int = 50) -> list[dict]:
        cutoff = int(time.time()) - config.MAX_ACTIVE_AGE_SEC
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM tape
                WHERE created_ts >= ? AND status IN ('watching', 'armed')
                ORDER BY CASE status WHEN 'armed' THEN 0 ELSE 1 END,
                         last_seen_at ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def tape_stats(self) -> dict:
        cutoff = int(time.time()) - config.MAX_ACTIVE_AGE_SEC
        with self._conn() as con:
            total = con.execute("SELECT COUNT(*) n FROM tape").fetchone()["n"]
            young = con.execute(
                "SELECT COUNT(*) n FROM tape WHERE created_ts >= ?", (cutoff,)
            ).fetchone()["n"]
            armed = con.execute(
                "SELECT COUNT(*) n FROM tape WHERE status = 'armed' AND created_ts >= ?",
                (cutoff,),
            ).fetchone()["n"]
            watching = con.execute(
                "SELECT COUNT(*) n FROM tape WHERE status = 'watching' AND created_ts >= ?",
                (cutoff,),
            ).fetchone()["n"]
        return {
            "total": int(total),
            "young": int(young),
            "armed": int(armed),
            "watching": int(watching),
        }

    def note_smart_wallet(self, wallet: str, mint: str, pct: float) -> None:
        wallet = (wallet or "").strip()
        mint = (mint or "").strip()
        if not wallet or not mint:
            return
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO smart_wallet_mints(wallet, mint, pct, seen_at)
                VALUES (?,?,?,?)
                ON CONFLICT(wallet, mint) DO UPDATE SET pct = excluded.pct, seen_at = excluded.seen_at
                """,
                (wallet, mint, pct, int(time.time())),
            )

    def smart_wallet_runners(self, wallet: str, exclude_mint: str = "") -> int:
        wallet = (wallet or "").strip()
        if not wallet:
            return 0
        with self._conn() as con:
            if exclude_mint:
                row = con.execute(
                    "SELECT COUNT(*) n FROM smart_wallet_mints WHERE wallet = ? AND mint != ?",
                    (wallet, exclude_mint),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(*) n FROM smart_wallet_mints WHERE wallet = ?",
                    (wallet,),
                ).fetchone()
        return int(row["n"] if row else 0)

    def tx_harvested(self, mint: str) -> bool:
        with self._conn() as con:
            row = con.execute("SELECT 1 FROM tx_harvested WHERE mint = ?", (mint,)).fetchone()
        return bool(row)

    def mark_tx_harvested(self, mint: str) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO tx_harvested(mint, seen_at) VALUES (?,?)",
                (mint, int(time.time())),
            )

    def smart_wallet_count(self) -> int:
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(DISTINCT wallet) n FROM smart_wallet_mints"
            ).fetchone()
        return int(row["n"] if row else 0)

    def already_posted(self, mint: str) -> bool:
        with self._conn() as con:
            row = con.execute("SELECT 1 FROM posted WHERE mint = ?", (mint,)).fetchone()
        return bool(row)

    def mark_posted(self, coin: dict, story_title: str = "") -> None:
        now = int(time.time())
        entry = float(coin.get("usd_market_cap") or 0)
        ath = float(coin.get("ath_market_cap") or entry)
        with self._conn() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO posted(
                    mint, posted_at, symbol, name, url, story,
                    entry_mc, ath_mc, last_mc, last_checked, book_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    coin.get("mint") or "",
                    now,
                    (coin.get("symbol") or "").upper(),
                    coin.get("name") or "",
                    coin.get("url") or "",
                    story_title or "",
                    entry,
                    max(ath, entry),
                    entry,
                    now,
                    config.SIGNAL_BOOK_ID,
                ),
            )

    def list_posted(self) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM posted ORDER BY posted_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_quotes(self, mint: str, last_mc: float, ath_mc: float) -> None:
        with self._conn() as con:
            con.execute(
                """
                UPDATE posted
                SET last_mc = ?, ath_mc = ?, last_checked = ?
                WHERE mint = ?
                """,
                (last_mc, ath_mc, int(time.time()), mint),
            )

    def signals_today(self) -> int:
        day_ago = int(time.time()) - 86400
        with self._conn() as con:
            row = con.execute(
                """
                SELECT COUNT(*) AS n FROM posted
                WHERE posted_at >= ? AND book_id = ?
                """,
                (day_ago, config.SIGNAL_BOOK_ID),
            ).fetchone()
        return int(row["n"] if row else 0)

    def ensure_paper_wallet(self, starting: float) -> dict:
        with self._conn() as con:
            row = con.execute("SELECT * FROM paper_wallet WHERE id = 1").fetchone()
            if not row:
                now = int(time.time())
                con.execute(
                    "INSERT INTO paper_wallet(id, cash_sol, starting_sol, updated_at, book_id) VALUES (1,?,?,?,?)",
                    (starting, starting, now, config.PAPER_BOOK_ID),
                )
                return {
                    "cash_sol": starting,
                    "starting_sol": starting,
                    "updated_at": now,
                    "book_id": config.PAPER_BOOK_ID,
                }
        return dict(row)

    def reset_paper_book(self, starting: float, book_id: str, reason: str = "book reset") -> dict:
        now = int(time.time())
        with self._conn() as con:
            con.execute(
                """
                UPDATE paper_positions
                SET remaining_qty_sol = 0, remaining_frac = 0, status = 'closed',
                    close_reason = ?, closed_at = ?
                WHERE status IN ('open', 'moonbag')
                """,
                (reason, now),
            )
            row = con.execute("SELECT 1 FROM paper_wallet WHERE id = 1").fetchone()
            if row:
                con.execute(
                    """
                    UPDATE paper_wallet
                    SET cash_sol = ?, starting_sol = ?, updated_at = ?, book_id = ?
                    WHERE id = 1
                    """,
                    (starting, starting, now, book_id),
                )
            else:
                con.execute(
                    "INSERT INTO paper_wallet(id, cash_sol, starting_sol, updated_at, book_id) VALUES (1,?,?,?,?)",
                    (starting, starting, now, book_id),
                )
        return self.paper_wallet()

    def paper_wallet(self) -> dict:
        with self._conn() as con:
            row = con.execute("SELECT * FROM paper_wallet WHERE id = 1").fetchone()
        return dict(row) if row else {"cash_sol": 0.0, "starting_sol": 0.0, "updated_at": 0}

    def set_paper_cash(self, cash: float) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE paper_wallet SET cash_sol = ?, updated_at = ? WHERE id = 1",
                (cash, int(time.time())),
            )

    def paper_position(self, mint: str) -> dict | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM paper_positions WHERE mint = ?", (mint,)).fetchone()
        return dict(row) if row else None

    def open_paper_positions(self) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM paper_positions WHERE status IN ('open','moonbag') ORDER BY opened_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def all_paper_positions(self) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM paper_positions ORDER BY opened_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_paper_position(self, pos: dict) -> None:
        cols = (
            "mint", "symbol", "name", "url", "path", "opened_at", "cost_sol",
            "original_qty_sol", "remaining_qty_sol", "remaining_frac",
            "entry_mc", "ath_mc", "last_mc", "realized_sol",
            "tp1_hit", "tp2_hit", "tp3_hit", "status", "close_reason", "closed_at",
        )
        vals = [pos.get(c) for c in cols]
        placeholders = ",".join("?" * len(cols))
        assignments = ",".join(f"{c}=excluded.{c}" for c in cols if c != "mint")
        with self._conn() as con:
            con.execute(
                f"""
                INSERT INTO paper_positions({",".join(cols)}) VALUES ({placeholders})
                ON CONFLICT(mint) DO UPDATE SET {assignments}
                """,
                vals,
            )

    def add_paper_fill(self, fill: dict) -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO paper_fills(mint, ts, side, reason, frac, multiple, sol, cash_after, mc)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    fill.get("mint"),
                    int(fill.get("ts") or time.time()),
                    fill.get("side"),
                    fill.get("reason"),
                    fill.get("frac"),
                    fill.get("multiple"),
                    fill.get("sol"),
                    fill.get("cash_after"),
                    fill.get("mc"),
                ),
            )

    def paper_fills(self, limit: int = 40) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM paper_fills ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
