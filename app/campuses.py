"""The "Add your campus" page (open to everyone, signed in or not): suggest a college for the board.

Guests use it before they have an account, so the cap is per address (see ratelimit.campus_wait).
A suggestion that is already stored is accepted quietly and is not counted against the cap.
"""
import logging

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.exceptions import TooManyRequests

from . import campus_service as svc
from . import db, logs, ratelimit

bp = Blueprint("campuses", __name__, url_prefix="/campuses")


@bp.route("/new", methods=["GET", "POST"])
def new():
    values, problems = {}, {}
    if request.method == "POST":
        wait = ratelimit.campus_wait(db.get_db(), request.remote_addr)
        if wait:
            logs.log_event("campus_capped", logging.WARNING, ip=request.remote_addr, wait_seconds=wait)
            raise TooManyRequests(retry_after=wait)            # shown by the 429 page
        values, problems = svc.validate(request.form)
        if not problems:
            if svc.add_campus(db.get_db(), values) == "added":
                ratelimit.record_campus(db.get_db(), request.remote_addr)
            flash("Thank you. Your campus is on our list.", "success")
            if g.user:
                return redirect(url_for("listings.browse"), 303)
            flash("Now create your account to join the board.", "info")
            return redirect(url_for("auth.signup"), 303)
    return render_template("campuses/new.html", values=values, problems=problems,
                           kind_options=svc.CAMPUS_KINDS, limits=svc.FIELD_LIMITS), (400 if problems else 200)
