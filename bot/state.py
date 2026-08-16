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
    symbol TEXT
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

    def mark_posted(self, mint: str, symbol: str) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO posted(mint, posted_at, symbol) VALUES (?,?,?)",
                (mint, int(time.time()), symbol),
            )

    def signals_today(self) -> int:
        day_ago = int(time.time()) - 86400
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM posted WHERE posted_at >= ?",
                (day_ago,),
            ).fetchone()
        return int(row["n"] if row else 0)
