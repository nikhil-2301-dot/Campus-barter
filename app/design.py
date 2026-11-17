"""Developer page /design: every component in every state, for eyeballing and for tests.

Registered only when APP_ENV is not production, so in production the URL is a plain 404.
It changes nothing: the POST handler exists only so the demo forms can show that CSRF is checked.
"""
from flask import Blueprint, redirect, render_template, request, url_for

bp = Blueprint("design", __name__)

DEMO_PAGES = 5


def _page_number(raw):
    """Page from the query string, always inside 1..DEMO_PAGES (bad values fall back to 1)."""
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return min(max(page, 1), DEMO_PAGES)


@bp.route("/design", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return redirect(url_for("design.index"), 303)
    return render_template("design/index.html",
                           page=_page_number(request.args.get("page")),
                           pages=DEMO_PAGES)
