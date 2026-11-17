"""Home route: the landing page for guests; students go straight to the listings."""
from flask import Blueprint, g, redirect, render_template, url_for

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    if g.user:
        return redirect(url_for("listings.browse"), 303)
    return render_template("main/landing.html")
