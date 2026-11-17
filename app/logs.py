"""Structured logging: one JSON object per line on stdout, a request id on every record and response.

Rules (tests in tests/test_logging.py prove them):
- Request bodies, query strings, cookies and headers are never logged. The access line holds the path only.
- Any value under a key like password, token, csrf, secret, cookie, session or authorization is masked,
  and the same words followed by `=` or `:` inside a message or a trace are masked too.
- Raw emails are never logged: events carry a user id, or `email_hash` (first 8 hex of SHA-256) for an
  address that is not a known user.
- Every "problem state" goes through `log_problem`, every notable event through `log_event`.

Stdlib only. The level comes from LOG_LEVEL (default INFO, see .env.example).
"""
import hashlib
import json
import logging
import re
import secrets
import sys
import time
from datetime import datetime, timezone

from flask import current_app, g, has_request_context, request

MASK = "[redacted]"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_WORDS = r"password|passwd|pwd|token|csrf|secret|cookie|session|authorization|api[_-]?key|credential"
SENSITIVE_KEY = re.compile(_WORDS, re.IGNORECASE)
SENSITIVE_TEXT = re.compile(
    r"((?:[\w-]*(?:" + _WORDS + r")[\w-]*)[\"']?\s*[=:]\s*[\"']?)(?:(?:bearer|basic)\s+)?[^\s,;&\"'}]+",
    re.IGNORECASE)
PATH_MAX = 200
_HANDLER_MARK = "_campus_barter_handler"


def redact_text(text):
    """Mask `password=abc`, `Authorization: Bearer abc`, `"csrf_token": "abc"` and similar in free text."""
    return SENSITIVE_TEXT.sub(lambda m: m.group(1) + MASK, str(text))


def redact_value(value, key=None):
    """Mask a value when its key is sensitive; walk dicts and lists; clean strings."""
    if key is not None and SENSITIVE_KEY.search(str(key)):
        return MASK
    if isinstance(value, dict):
        return {str(k): redact_value(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def email_hash(email):
    """Short stable fingerprint of an address (so repeated tries can be matched without the address)."""
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:8]


def new_request_id():
    return secrets.token_hex(8)


def clean_request_id(incoming):
    """The incoming X-Request-ID if it is 1-64 letters, digits, dot, underscore or dash; else a new one."""
    if incoming and REQUEST_ID_RE.fullmatch(incoming):
        return incoming
    return new_request_id()


class RequestContextFilter(logging.Filter):
    """Copies the request id, method, path (no query string) and user id onto every record."""

    def filter(self, record):
        if has_request_context():
            record.request_id = g.get("request_id")
            record.method = request.method
            record.path = request.path[:PATH_MAX]
            user = g.get("user")
            record.user_id = user["id"] if user is not None else None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        moment = datetime.fromtimestamp(record.created, timezone.utc)
        data = {
            "time": moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for name in ("request_id", "method", "path", "user_id"):
            value = getattr(record, name, None)
            if value is not None:
                data[name] = redact_value(value, name)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for name, value in redact_value(fields).items():
                data.setdefault(name, value)
        if record.exc_info:
            data["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(data, default=str, ensure_ascii=True)


def _remove_ours(logger):
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
    for flt in list(logger.filters):
        if isinstance(flt, RequestContextFilter):
            logger.removeFilter(flt)


def configure(app):
    """Install the JSON handler on app.logger. LOG_LEVEL and LOG_STREAM come from app.config.

    Under TESTING with no LOG_STREAM nothing is printed (tests capture logs with assertLogs or pass
    a stream), so the test log stays clean.
    """
    logger = app.logger
    _remove_ours(logger)
    logger.addFilter(RequestContextFilter())
    stream = app.config.get("LOG_STREAM")
    if stream is None and app.config.get("TESTING"):
        logger.setLevel(logging.WARNING)      # keep the test log quiet; assertLogs raises the level itself
        return
    from flask.logging import default_handler
    logger.removeHandler(default_handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO))
    logger.propagate = False
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # its access line carries the query string


# ---- request id, access line, cache header ------------------------------------------------------------

def _start_request():
    g.request_id = clean_request_id(request.headers.get("X-Request-ID"))
    g.request_started = time.perf_counter()


def _finish_request(response):
    """after_request (the first one registered, so it runs last): id header, access line."""
    request_id = g.get("request_id") or new_request_id()
    g.request_id = request_id
    response.headers["X-Request-ID"] = request_id
    started = g.get("request_started")
    millis = round((time.perf_counter() - started) * 1000, 1) if started is not None else None
    current_app.logger.info("request", extra={"fields": {"event": "request", "status": response.status_code,
                                                           "duration_ms": millis}})
    return response


def current_request_id():
    return g.get("request_id") if has_request_context() else None


def init_app(app):
    configure(app)
    app.before_request(_start_request)
    app.after_request(_finish_request)


# ---- events and problems -------------------------------------------------------------------------------

def log_event(name, level=logging.INFO, **fields):
    """One line for a notable event (login_failure, limit_hit, admin_action...). Never pass a raw email."""
    current_app.logger.log(level, name, extra={"fields": {"event": name, **fields}})


def log_problem(kind, exc=None, level=logging.ERROR):
    """The one place a problem state (browse, landing, inboxes, my listings, health, 500) is logged.

    Call it inside the `except` block; the trace is attached. `kind` is a short plain phrase.
    """
    info = (type(exc), exc, exc.__traceback__) if exc is not None else True
    current_app.logger.log(level, f"problem: {kind}", exc_info=info,
                           extra={"fields": {"event": "problem", "kind": kind}})
