"""SQLite connection helpers and schema bootstrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH, SCHEMA_PATH


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    - ``row_factory`` = ``sqlite3.Row`` so queries return dict-like rows.
    - Foreign keys enforced (off by default in SQLite).
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """(Re)create all tables and indexes from schema.sql.

    Drops existing tables first so seeding is idempotent.
    """
    ddl = Path(SCHEMA_PATH).read_text(encoding="utf-8")
    # Clean slate — the DB is a disposable projection of data/*.json.
    conn.executescript(
        """
        DROP TABLE IF EXISTS activity;
        DROP TABLE IF EXISTS memberships;
        DROP TABLE IF EXISTS circle_tags;
        DROP TABLE IF EXISTS circles;
        DROP TABLE IF EXISTS user_topic_interests;
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS topics;
        """
    )
    conn.executescript(ddl)
    conn.commit()


def database_is_populated(db_path: str | None = None) -> bool:
    """True if the DB file exists and has a non-empty ``circles`` table."""
    path = Path(db_path or DB_PATH)
    if not path.exists():
        return False
    try:
        conn = connect(str(path))
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM circles").fetchone()
            return bool(row and row["n"] > 0)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
