"""CSRF protection, safe redirects and the login_required decorator.

CSRF: every unsafe request (POST, PUT, PATCH, DELETE) must carry the per-session token, in the form
field `csrf_token` or the header `X-CSRF-Token`. There are no exemptions. The token is created when
a form is first rendered and is replaced on login (see auth.start_session).
"""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app, flash, g, redirect, request, session, url_for

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def csrf_token():
    """The token for the current session (made on first use). Available in templates."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _check_csrf():
    if request.method in SAFE_METHODS:
        return
    if request.routing_exception is not None:
        return  # no route matched: no view will run, so let Flask answer 404/405 itself
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf_token") or ""
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        abort(403, description="Your form has expired. Reload the page and try again.")


def safe_next(target, default):
    """Return `target` only if it is a local path; otherwise `default` (blocks open redirects)."""
    if not target or len(target) > 300 or not target.startswith("/"):
        return default
    if target.startswith("//") or "\\" in target or any(ord(ch) < 32 for ch in target):
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return default
    return target


def login_required(view):
    """Redirect anonymous visitors to the login page (remembering where they were going)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            flash("Please log in to continue.", "info")
            if request.method == "GET":
                return redirect(url_for("auth.login", next=request.full_path.rstrip("?")), 303)
            return redirect(url_for("auth.login"), 303)
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Guest -> 303 to login (like login_required); logged-in non-admin -> 403; admin -> the view.
    The rule itself lives in permissions.can("view_admin")."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        from .permissions import can
        if not can("view_admin", None, g.user):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
HSTS = "max-age=31536000"


def _add_security_headers(response):
    """after_request: every response (pages, redirects, problem pages, static files) gets the headers."""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if current_app.config.get("APP_ENV") == "production":      # HTTPS only in production
        response.headers["Strict-Transport-Security"] = HSTS
    return response


def _no_store_for_logged_in(response):
    """R6: pages built for a logged-in user are never kept by the browser (Back button after logout)."""
    if g.get("user") is not None and request.endpoint != "static":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def init_app(app):
    app.before_request(_check_csrf)
    app.after_request(_add_security_headers)
    app.after_request(_no_store_for_logged_in)
    app.jinja_env.globals["csrf_token"] = csrf_token
