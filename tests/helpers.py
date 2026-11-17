"""Shared test helpers. NOTE: scripts/check.sh treats the words 'error', 'failed' and
'skipped' in the test log as a gate failure, so never use them in test names or docstrings."""
import zlib
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import create_app  # noqa: E402
from app.config import MIGRATIONS_DIR  # noqa: E402

# versions of every migration file, so tests need no edit when a new migration is added
MIGRATION_VERSIONS = sorted(n[:4] for n in os.listdir(MIGRATIONS_DIR)
                            if n.endswith(".sql") and n[:4].isdigit())
from app.db import connect  # noqa: E402
from app.migrate import migrate  # noqa: E402

DEV_ENV = {"APP_ENV": "development", "SECRET_KEY": "test-only-key"}


def migrated_memory_db():
    conn = connect(":memory:")
    migrate(conn, MIGRATIONS_DIR)
    return conn


def make_app(tmp_dir, migrated=True, **extra):
    path = os.path.join(tmp_dir, "test.db")
    app = create_app({"DATABASE_PATH": path, "TESTING": True, **extra}, environ=dict(DEV_ENV))
    if migrated:
        conn = connect(path)
        try:
            migrate(conn, MIGRATIONS_DIR)
        finally:
            conn.close()
    return app


class TempDirMixin:
    def make_tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name


def add_user(conn, email="u@college.example", year="SE", **kw):
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, full_name, branch, year, is_admin, is_suspended, "
        "college_name, enrollment_no) "
        "VALUES (?, 'x', 'Test User', 'IT', ?, ?, ?, ?, ?)",
        (email, year, kw.get("is_admin", 0), kw.get("is_suspended", 0),
         kw.get("college_name", "Test College"),
         kw.get("enrollment_no") or "TC" + str(zlib.crc32(email.encode()))))
    return cur.lastrowid


def add_listing(conn, owner_id, title="Item", **kw):
    cur = conn.execute(
        "INSERT INTO listings (owner_id, title, category, condition, mode, status, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, title, kw.get("category", "Books"), kw.get("condition", "good"),
         kw.get("mode", "give"), kw.get("status", "available"), kw.get("deleted_at")))
    return cur.lastrowid
