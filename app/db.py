"""SQLite access. Raw SQL with `?` parameters ONLY (never f-string SQL).

tests/test_db.py scans this package for f-string SQL and fails if it finds any.
"""
import os
import sqlite3

from flask import current_app, g


def connect(path):
    """Open a connection with the pragmas every connection must have."""
    if path != ":memory:":
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # off by default in SQLite, per connection
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db():
    """One connection per request, stored on flask.g and closed at teardown."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def execute(sql, params=()):
    """Run one write and commit. Returns the cursor (use .lastrowid / .rowcount)."""
    db = get_db()
    try:
        cur = db.execute(sql, params)
        db.commit()
        return cur
    except sqlite3.Error:
        db.rollback()
        raise
