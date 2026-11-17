"""Rate limits kept in the `login_attempts` table, so they survive a restart (no migration needed).

One row = one counted event for a key. Keys:
  email:<address>       failed logins for that address     (LOGIN_EMAIL_MAX per LOGIN_WINDOW)
  ip:<address>          failed logins from that address    (LOGIN_IP_MAX per LOGIN_WINDOW)
  <action>:<user id>    a student action (ACTIONS below)   (limit per window, one hour)
  signup:<address>      accounts created from that address (SIGNUP_MAX per hour; only successes count)
  campus:<address>      campus suggestions stored from that address (CAMPUS_MAX per hour; only stored ones count)

Rules: a refused attempt is never recorded (so the lock cannot grow), old rows are ignored by time and
deleted once they are a day old, and only a successful login clears rows (the email's own key).
The client address is request.remote_addr. X-Forwarded-For is trusted only when TRUSTED_PROXIES is set (C23, app/__init__.py
adds werkzeug ProxyFix then); otherwise it is ignored (see docs/security/review-1.md R2).
"""
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from math import ceil

from flask import g, request
from werkzeug.exceptions import TooManyRequests

from . import logs
from .accounts import EMAIL_MAX, normalize_email

LOGIN_WINDOW = 15 * 60          # seconds
LOGIN_EMAIL_MAX = 5             # failures per email in the window
LOGIN_IP_MAX = 20               # failures per IP in the window
ACTION_WINDOW = 60 * 60
ACTIONS = {                     # name -> (events allowed, window in seconds)
    "send_request": (20, ACTION_WINDOW),
    "create_listing": (10, ACTION_WINDOW),
    "send_report": (5, ACTION_WINDOW),
}
CAMPUS_MAX = 10                 # "Add your campus" suggestions per IP per hour (only stored ones count)
SIGNUP_MAX = 30                 # new accounts per IP per hour (a class may sign up from one lab address)
KEEP_SECONDS = 24 * 60 * 60     # rows older than this are deleted when a new one is written
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


class RateLimited(Exception):
    """Over the limit. `wait` is the number of seconds until one more try is allowed."""

    def __init__(self, wait):
        super().__init__(wait)
        self.wait = wait


def now():
    """Current UTC time. Tests replace this function to move the clock."""
    return datetime.now(timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def minutes_text(seconds):
    minutes = max(1, ceil(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _wait(conn, key, limit, window):
    """Seconds to wait before `key` may act again (0 = allowed now)."""
    since = _stamp(now() - timedelta(seconds=window))
    row = conn.execute('SELECT COUNT(*) AS n FROM login_attempts WHERE "key" = ? AND attempted_at > ?',
                       (key, since)).fetchone()
    count = row["n"]
    if count < limit:
        return 0
    # The lock ends when enough old rows have left the window for the count to drop below the limit.
    oldest = conn.execute('SELECT attempted_at FROM login_attempts WHERE "key" = ? AND attempted_at > ? '
                          "ORDER BY attempted_at LIMIT 1 OFFSET ?", (key, since, count - limit)).fetchone()
    try:
        started = datetime.strptime(oldest["attempted_at"], _STAMP).replace(tzinfo=timezone.utc)
        return max(1, ceil((started + timedelta(seconds=window) - now()).total_seconds()))
    except (TypeError, ValueError):
        return window


def _insert(conn, keys):
    moment = now()
    conn.execute('DELETE FROM login_attempts WHERE attempted_at < ?',
                 (_stamp(moment - timedelta(seconds=KEEP_SECONDS)),))
    for key in keys:
        conn.execute('INSERT INTO login_attempts ("key", attempted_at) VALUES (?, ?)', (key, _stamp(moment)))


# ---- login ---------------------------------------------------------------------------------------

def _login_keys(email, ip):
    keys = [(f"ip:{(ip or 'unknown')[:64]}", LOGIN_IP_MAX)]
    address = normalize_email(email)[:EMAIL_MAX]
    if address:
        keys.append((f"email:{address}", LOGIN_EMAIL_MAX))
    return keys


def login_wait(conn, email, ip):
    """Seconds until this email/IP may try a password again (0 = allowed). Checked BEFORE the password."""
    return max(_wait(conn, key, limit, LOGIN_WINDOW) for key, limit in _login_keys(email, ip))


def record_login_failure(conn, email, ip):
    _insert(conn, [key for key, _limit in _login_keys(email, ip)])
    conn.commit()


def clear_login(conn, email):
    address = normalize_email(email)[:EMAIL_MAX]
    if address:
        key = f"email:{address}"
        conn.execute('DELETE FROM login_attempts WHERE "key" = ?', (key,))
        conn.commit()


# ---- signup ----------------------------------------------------------------------------------------

def _signup_key(ip):
    return "signup:" + (ip or "unknown")[:64]


def signup_wait(conn, ip):
    """Seconds until another account may be created from this address (0 = allowed)."""
    return _wait(conn, _signup_key(ip), SIGNUP_MAX, ACTION_WINDOW)


def record_signup(conn, ip):
    """Count one created account. Refused or invalid sign-ups are not counted."""
    _insert(conn, [_signup_key(ip)])
    conn.commit()


# ---- campus suggestions (guests may send them, so the key is the address) ---------------------------

def _campus_key(ip):
    return "campus:" + (ip or "unknown")[:64]


def campus_wait(conn, ip):
    """Seconds until another campus may be suggested from this address (0 = allowed)."""
    return _wait(conn, _campus_key(ip), CAMPUS_MAX, ACTION_WINDOW)


def record_campus(conn, ip):
    """Count one stored suggestion. Refused or repeated ones are not counted."""
    _insert(conn, [_campus_key(ip)])
    conn.commit()


# ---- student actions -----------------------------------------------------------------------------

def hit(conn, action, user_id):
    """Count one use of `action` by this user, or raise RateLimited (and store nothing)."""
    limit, window = ACTIONS[action]
    key = f"{action}:{int(user_id)}"
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")      # count + insert as one step, even with two workers
    wait = _wait(conn, key, limit, window)
    if wait:
        conn.rollback()
        raise RateLimited(wait)
    _insert(conn, [key])
    conn.commit()


def limited(action):
    """Route decorator (put it UNDER @login_required): every POST by a logged-in user counts as one use."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.method == "POST" and g.get("user") is not None:
                from .db import get_db
                try:
                    hit(get_db(), action, g.user["id"])
                except RateLimited as problem:
                    logs.log_event("limit_hit", logging.WARNING, action=action, user_id=g.user["id"],
                                   wait_seconds=problem.wait)
                    raise TooManyRequests(retry_after=problem.wait) from problem
            return view(*args, **kwargs)
        return wrapped
    return decorator
