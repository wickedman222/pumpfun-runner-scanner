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
                    entry_mc, ath_mc, last_mc, last_checked
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
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
                "SELECT COUNT(*) AS n FROM posted WHERE posted_at >= ?",
                (day_ago,),
            ).fetchone()
        return int(row["n"] if row else 0)
