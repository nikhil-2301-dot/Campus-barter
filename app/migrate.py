"""Tiny migration runner: numbered .sql files applied once, in order.

- Applied versions are recorded in schema_migrations together with a SHA-256 of
  the file. Editing an already-applied migration is detected and refused
  (rule: every schema change is a NEW numbered file).
- Each migration runs in one transaction together with its bookkeeping row, so a
  broken file leaves the database untouched.
"""
import hashlib
import os
import re
import sqlite3

from .db import connect

FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationProblem(Exception):
    pass


def _files(directory):
    found = []
    for name in sorted(os.listdir(directory)):
        m = FILE_RE.match(name)
        if m:
            found.append((m.group(1), name, os.path.join(directory, name)))
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationProblem("duplicate migration version numbers in " + directory)
    return found


def _digest(sql):
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migrate(conn, directory):
    """Apply pending migrations on `conn`. Returns the list of versions applied now."""
    previous_mode = conn.isolation_level
    conn.isolation_level = None  # we control transactions explicitly
    try:
        return _apply(conn, directory)
    finally:
        conn.isolation_level = previous_mode


def _apply(conn, directory):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 "version TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, "
                 "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
    applied = {r["version"]: r["sha256"] for r in
               conn.execute("SELECT version, sha256 FROM schema_migrations")}
    files = _files(directory)
    on_disk = {v for v, _, _ in files}
    missing = sorted(set(applied) - on_disk)
    if missing:
        raise MigrationProblem("applied migration file(s) missing from disk: " + ", ".join(missing))

    done_now = []
    for version, name, path in files:
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        digest = _digest(sql)
        if version in applied:
            if applied[version] != digest:
                raise MigrationProblem(f"migration {name} was edited after it was applied; "
                                       "add a new numbered file instead")
            continue
        try:
            conn.execute("BEGIN")
            for statement in _split(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version, name, sha256) VALUES (?, ?, ?)",
                         (version, name, digest))
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationProblem(f"migration {name} did not apply: {exc}") from exc
        done_now.append(version)
    return done_now


def _split(sql):
    """Split a script into complete statements (handles triggers with inner semicolons)."""
    buf, statements = "", []
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip() and not _only_comments(buf):
        raise MigrationProblem("migration ends with an incomplete statement")
    return [s for s in statements if not _only_comments(s)]


def _only_comments(statement):
    return all(not ln.strip() or ln.strip().startswith("--") for ln in statement.splitlines())


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return row["v"]


def available_versions(directory):
    """Version numbers of the migration files on disk, oldest first."""
    return [v for v, _, _ in _files(directory)]


def pending_versions(conn, directory):
    """Versions on disk that the database has not applied yet (needs the schema_migrations table)."""
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    return [v for v in available_versions(directory) if v not in applied]


def migrate_path(db_path, directory):
    conn = connect(db_path)
    try:
        return migrate(conn, directory)
    finally:
        conn.close()
