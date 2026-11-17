"""GET /health: liveness + database check for uptime monitors (no login, no secrets).

200 only when the database answers, the schema table exists and no migration file is waiting.
503 otherwise, with a short reason and no paths or SQL text (the server log has the cause).
"""
import logging
import os
import sqlite3

from flask import Blueprint, current_app, jsonify

from . import db, logs, migrate
from .config import REPO_ROOT

bp = Blueprint("health", __name__)


def _app_version():
    try:
        with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def _reply(body, status):
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/health")
def health():
    directory = current_app.config["MIGRATIONS_DIR"]
    try:
        conn = db.get_db()
        conn.execute("SELECT 1").fetchone()
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone()
        version = migrate.current_version(conn) if has_table else None
        pending = migrate.pending_versions(conn, directory) if has_table else []
        latest = migrate.available_versions(directory)[-1:]
    except (sqlite3.Error, OSError, migrate.MigrationProblem):  # OSError: database folder missing or not usable
        # Do not leak paths or SQL details to the public; the server log has the cause.
        logs.log_problem("health check: database problem", level=logging.WARNING)
        return _reply({"status": "degraded", "db": "unavailable"}, 503)
    if version is None:
        return _reply({"status": "degraded", "db": "not migrated"}, 503)
    body = {"status": "ok", "db": "ok", "schema_version": version,
            "latest_schema_version": latest[0] if latest else version,
            "pending_migrations": len(pending), "app_version": _app_version()}
    if pending:
        body.update(status="degraded", db="migrations pending")
        return _reply(body, 503)
    return _reply(body, 200)
