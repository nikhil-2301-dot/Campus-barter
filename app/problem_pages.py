"""Pages for 400, 403, 404, 405, 429 and 500, with the right status code and a way back.

Small and reusable on purpose. A 500 page never shows the exception, a path or a trace (only the
request id as a reference); the details go to the log only (logs.log_problem). If even the page cannot be drawn, a
short plain-text answer is sent instead.
"""
from urllib.parse import urlsplit

from flask import Response, g, make_response, render_template, request, url_for
from werkzeug.exceptions import Forbidden, HTTPException

from . import logs
from .ratelimit import minutes_text
from .security import safe_next

PAGES = {
    400: {
        "title": "That request could not be used",
        "lead": "The request was incomplete or not in the form this page expects.",
        "state_title": "What you can do",
        "state_text": "Go back and try again from the page you came from.",
    },
    405: {
        "title": "That action is not available here",
        "lead": "This address cannot be used in that way.",
        "state_title": "What you can do",
        "state_text": "Go back to the listings and use the buttons on the page.",
    },
    403: {
        "title": "You can't open this page",
        "lead": "This page is not open to your account.",
        "state_title": "What you can do",
        "state_text": "Go back to the listings. If you think you should have access, ask an admin.",
    },
    404: {
        "title": "Page not found",
        "lead": "The address may be mistyped, or the item may have been removed.",
        "state_title": "Looking for an item?",
        "state_text": "Browse the listings, or filter them by item type.",
    },
    413: {
        "title": "That upload is too big",
        "lead": "The photo is larger than the 2 MB this site accepts.",
        "state_title": "What you can do",
        "state_text": "Go back and choose a smaller photo (JPEG, PNG or WebP). Nothing was saved by this last try.",
    },
    429: {
        "title": "Too many tries",
        "lead": "You have done this too many times in a short while.",
        "state_title": "Slow down for a moment",
        "state_text": "This limit protects the site from abuse and lifts by itself. Nothing was saved by this last try.",
    },
    500: {
        "title": "Something went wrong",
        "lead": "The page could not be shown. This is a fault on our side, not something you did.",
        "state_title": "Try again in a moment",
        "state_text": "Reload the page. If it keeps happening, tell an admin.",
    },
}
PLAIN = {413: "That upload is too big. Photos can be up to 2 MB.", 400: "That request could not be used.", 405: "That action is not available here.", 403: "You can't open this page.", 404: "Page not found.", 429: "Too many tries. Wait a little and try again.",
         500: "Something went wrong. Go back and try again."}


def render_problem_page(code, lead=None, action=None):
    """Return (response body, status code) for one of the pages (403, 404, 429, 500)."""
    page = PAGES[code]
    action = action or ("Browse listings", url_for("listings.browse"))
    if code == 403 and not g.get("user"):
        action = ("Log in", url_for("auth.login"))
    try:
        body = render_template("problem/page.html", code=code, page=page, lead=lead or page["lead"],
                               action_label=action[0], action_href=action[1],
                               request_id=logs.current_request_id() if code == 500 else None)
    except Exception:   # last resort: the page itself must never turn into a trace
        logs.log_problem("problem page did not render")
        return Response(PLAIN[code], status=code, mimetype="text/plain")
    return body, code


def forbidden(exc):
    # Our own messages (for example the expired-form text) are safe to show; werkzeug's default is not helpful.
    text = getattr(exc, "description", None)
    return render_problem_page(403, None if not text or text == Forbidden.description else text)


def _retry_href():
    """Where the Retry link goes: the page the student came from (same host only), else the listings."""
    ref = urlsplit(request.referrer or "")
    if ref.netloc and ref.netloc == request.host:
        return safe_next(ref.path + ("?" + ref.query if ref.query else ""), url_for("listings.browse"))
    return url_for("listings.browse")


def too_many(exc):
    wait = int(getattr(exc, "retry_after", None) or 60)
    lead = f"{PAGES[429]['lead']} Try again in {minutes_text(wait)}."
    response = make_response(render_problem_page(429, lead, ("Try again", _retry_href())))
    response.headers["Retry-After"] = str(wait)
    return response


def not_found(_exc):
    return render_problem_page(404)


def server_problem(exc):
    original = getattr(exc, "original_exception", None)
    if original is not None:
        logs.log_problem("unhandled exception in a view", original)
    return render_problem_page(500)


def too_large(_exc):
    return render_problem_page(413, None, ("Go back", _retry_href()))


def bad_request(_exc):
    return render_problem_page(400)


def not_allowed(exc):
    response = make_response(render_problem_page(405))
    allowed = getattr(exc, "valid_methods", None)
    if allowed:
        response.headers["Allow"] = ", ".join(sorted(allowed))
    return response


def unexpected(exc):
    """Any exception nobody handled. Other HTTP errors (400, 405...) keep their normal response."""
    if isinstance(exc, HTTPException):
        return exc
    logs.log_problem("unhandled exception in a view", exc)
    return render_problem_page(500)


def init_app(app):
    app.register_error_handler(400, bad_request)
    app.register_error_handler(405, not_allowed)
    app.register_error_handler(403, forbidden)
    app.register_error_handler(404, not_found)
    app.register_error_handler(413, too_large)
    app.register_error_handler(429, too_many)
    app.register_error_handler(500, server_problem)
    app.register_error_handler(Exception, unexpected)
