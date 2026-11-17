"""Signup, login, logout and profile routes, plus the per-request user loader.

Sessions are signed cookies holding only the user id (and the CSRF token). The user row is loaded
from the database on every request, so a suspended or deleted account loses access immediately.
"""
from flask import (Blueprint, current_app, flash, g, make_response, redirect, render_template, request,
                   session, url_for)
from werkzeug.exceptions import TooManyRequests

import logging

from . import accounts, db, logs, ratelimit
from .constants import YEAR_LABELS, YEARS
from .security import login_required, safe_next

bp = Blueprint("auth", __name__)
YEAR_OPTIONS = [(y, YEAR_LABELS[y]) for y in YEARS]   # (value, label) pairs for the year select

BAD_LOGIN = "Wrong email or password."


def load_user():
    """before_request: put the logged-in user (or None) on flask.g."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    row = db.query_one("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL AND is_suspended = 0",
                       (user_id,))
    if row is None:
        session.clear()
        return
    g.user = row


def start_session(user_id):
    """Log a user in. Clearing first drops the old CSRF token (no session fixation)."""
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


def _form_values(source, names):
    return {n: (source.get(n) or "") for n in names}


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("auth.profile"), 303)
    values, problems = {}, {}
    if request.method == "POST":
        wait = ratelimit.signup_wait(db.get_db(), request.remote_addr)
        if wait:
            logs.log_event("signup_capped", logging.WARNING, ip=request.remote_addr, wait_seconds=wait)
            raise TooManyRequests(retry_after=wait)        # shown by the 429 page
        clean, problems = accounts.validate_signup(request.form)
        values = {k: (v or "") for k, v in clean.items()}
        if not problems:
            try:
                user_id = accounts.create_user(db.get_db(), password=request.form["password"], **clean)
            except accounts.EmailTaken:
                problems["email"] = "An account with this email already exists. Log in instead."
            except accounts.EnrollmentTaken:
                problems["enrollment_no"] = "An account with this enrollment number already exists. Each student can have only one account."
            else:
                ratelimit.record_signup(db.get_db(), request.remote_addr)
                start_session(user_id)
                flash("Welcome to Campus Barter! Your account is ready.", "success")
                return redirect(url_for("auth.profile"), 303)
    return render_template("auth/signup.html", values=values, problems=problems, year_options=YEAR_OPTIONS, limits=accounts.FIELD_LIMITS), (400 if problems else 200)


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next", "")
    if g.user:
        return redirect(safe_next(next_url, url_for("auth.profile")), 303)
    problem, status, email = None, 200, ""
    if request.method == "POST":
        email = request.form.get("email", "")
        conn, ip = db.get_db(), request.remote_addr
        wait = ratelimit.login_wait(conn, email, ip)     # checked first: no password is looked at while locked
        if wait:
            logs.log_event("login_locked", logging.WARNING, ip=ip, email_hash=logs.email_hash(email),
                           wait_seconds=wait)
            problem, status = f"Too many attempts. Try again in {ratelimit.minutes_text(wait)}.", 429
        else:
            user, result = accounts.authenticate(conn, email, request.form.get("password", ""))
            if result == "ok":
                ratelimit.clear_login(conn, email)
                start_session(user["id"])
                logs.log_event("login_success", user_id=user["id"], ip=ip)
                return redirect(safe_next(next_url, url_for("auth.profile")), 303)
            if result == "suspended":
                logs.log_event("login_suspended", logging.WARNING, ip=ip, email_hash=logs.email_hash(email))
                problem, status = "This account is suspended. Contact the administrator.", 403
            else:
                ratelimit.record_login_failure(conn, email, ip)
                logs.log_event("login_failure", logging.WARNING, ip=ip, email_hash=logs.email_hash(email))
                problem, status = BAD_LOGIN, 401
    # The next value is only ever echoed after passing safe_next.
    response = make_response(render_template("auth/login.html", email=email.strip()[:accounts.EMAIL_MAX],
                                             problem=problem, limits=accounts.FIELD_LIMITS,
                                             next_url=safe_next(next_url, "")), status)
    if status == 429:
        response.headers["Retry-After"] = str(wait)
    return response


@bp.post("/logout")
def logout():
    session.clear()
    flash("You are logged out.", "info")
    return redirect(url_for("auth.login"), 303)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    problems = {}
    values = {k: (g.user[k] or "")
              for k in ("full_name", "branch", "year", "phone",
                        "college_name", "enrollment_no", "social_link", "address")}
    status = 200
    if request.method == "POST":
        clean, problems = accounts.validate_profile(request.form)
        values = {k: (v or "") for k, v in clean.items()}
        if not problems:
            try:
                accounts.update_profile(db.get_db(), g.user["id"], clean)
            except accounts.EnrollmentTaken:
                problems["enrollment_no"] = "An account with this enrollment number already exists. Each student can have only one account."
            else:
                flash("Profile saved.", "success")
                return redirect(url_for("auth.profile"), 303)
        status = 400
    return render_template("auth/profile.html", values=values, problems=problems, year_options=YEAR_OPTIONS, limits=accounts.FIELD_LIMITS, email=g.user["email"]), status
