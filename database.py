"""
database.py — SQLite persistence for users, files and sharing permissions.

Why this file exists
--------------------
The original prototype kept users / files / shares in Python dictionaries,
so EVERYTHING (accounts, file metadata, sharing list) disappeared whenever
Flask restarted. For a cloud project that is a real weakness, so this
module stores them in a small SQLite database file instead.

SQLite is used because it is part of Python's standard library: no extra
service, no extra dependency, one single file (decentracloud.db).

Tables
------
users   : username, password_hash, created_at
files   : id, owner, filename, file_hash, size, uploaded_at, nodes(JSON)
shares  : file_id, username, shared_at   (which user may access which file)
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DATABASE_PATH", "").strip() or os.path.join(BASE_DIR, "decentracloud.db")

_write_lock = threading.Lock()


@contextmanager
def _conn():
    """One short-lived connection per call — safe with Flask's threaded server."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                owner      TEXT NOT NULL,
                filename   TEXT NOT NULL,
                file_hash  TEXT NOT NULL,
                size       INTEGER NOT NULL DEFAULT 0,
                uploaded_at REAL NOT NULL,
                nodes      TEXT NOT NULL DEFAULT '[]',
                UNIQUE (owner, filename)
            );

            CREATE TABLE IF NOT EXISTS shares (
                file_id   INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                username  TEXT NOT NULL,
                shared_at REAL NOT NULL,
                PRIMARY KEY (file_id, username)
            );
            """
        )


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------
def create_user(username: str, password_hash: str) -> bool:
    """Insert a user. Returns False if the username already exists."""
    with _write_lock, _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_user(username: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def list_usernames():
    with _conn() as conn:
        rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()
    return [r["username"] for r in rows]


def user_count() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------
def upsert_file(owner, filename, file_hash, size, nodes):
    """Insert a new file, or REPLACE the owner's previous version when the
    same owner uploads another file with the same name (duplicate handling).

    Returns the file id.
    """
    nodes_json = json.dumps(list(nodes))
    with _write_lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO files (owner, filename, file_hash, size, uploaded_at, nodes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (owner, filename) DO UPDATE SET
                file_hash  = excluded.file_hash,
                size       = excluded.size,
                uploaded_at= excluded.uploaded_at,
                nodes      = excluded.nodes
            """,
            (owner, filename, file_hash, size, time.time(), nodes_json),
        )
        row = conn.execute(
            "SELECT id FROM files WHERE owner = ? AND filename = ?", (owner, filename)
        ).fetchone()
    return row["id"]


def get_file(file_id):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return _file_dict(row) if row else None


def owned_files(owner):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE owner = ? ORDER BY uploaded_at DESC", (owner,)
        ).fetchall()
    return [_file_dict(r) for r in rows]


def shared_files(username):
    """Files that were explicitly shared WITH this user."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT f.* FROM files f
            JOIN shares s ON s.file_id = f.id
            WHERE s.username = ?
            ORDER BY f.uploaded_at DESC
            """,
            (username,),
        ).fetchall()
    return [_file_dict(r) for r in rows]


def shared_users(file_id):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT username FROM shares WHERE file_id = ? ORDER BY username", (file_id,)
        ).fetchall()
    return [r["username"] for r in rows]


def add_share(file_id, username) -> bool:
    """Grant access. Returns False if already shared (idempotent)."""
    with _write_lock, _conn() as conn:
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO shares (file_id, username, shared_at) VALUES (?, ?, ?)",
            (file_id, username, time.time()),
        )
        return conn.total_changes > before


def _file_dict(row):
    d = dict(row)
    try:
        d["nodes"] = json.loads(d.get("nodes") or "[]")
    except (TypeError, ValueError):
        d["nodes"] = []
    d["shared_with"] = shared_users(d["id"])
    return d
