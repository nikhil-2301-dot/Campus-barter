"""Admin area (admins only): dashboard, reports, removing a listing, users, suspend / unsuspend.

Every route uses @admin_required (guest -> 303 to login, student -> 403) and the services call
can() again, so the rule is checked in two places. Pages are built on the server in one or two
queries, so there is no loading state; the states are success, empty and problem (503).
"""
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import admin_service as svc
from . import campus_service as campuses
from . import listing_service
from . import logs
from . import report_service as reports
from .constants import REPORT_STATUS_KINDS, REPORT_STATUS_LABELS, REPORT_STATUSES
from .db import get_db
from .permissions import can
from .security import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

REPORT_FILTERS = ("open",) + tuple(s for s in REPORT_STATUSES if s != "open") + ("all",)
DONE_TEXT = {"resolved": "Report resolved.", "dismissed": "Report dismissed."}


@bp.app_template_filter("report_status_label")
def _report_status_label(value):
    return REPORT_STATUS_LABELS.get(value, value)


@bp.app_template_filter("report_status_kind")
def _report_status_kind(value):
    return REPORT_STATUS_KINDS.get(value, "neutral")


@bp.route("/")
@admin_required
def dashboard():
    counts = newest = None
    try:
        counts = svc.dashboard_counts(get_db())
        newest = reports.list_reports(get_db(), "open", limit=5)
    except sqlite3.Error:
        logs.log_problem("admin dashboard could not load")
    return render_template("admin/dashboard.html", counts=counts, newest=newest,
                           load_problem=counts is None), (503 if counts is None else 200)


@bp.route("/reports")
@admin_required
def report_list():
    status = request.args.get("status", "open")
    if status not in REPORT_FILTERS:
        status = "open"
    rows = None
    try:
        rows = reports.list_reports(get_db(), status)
    except sqlite3.Error:
        logs.log_problem("admin reports list could not load")
    return render_template("admin/reports.html", rows=rows, status=status, filters=REPORT_FILTERS,
                           load_problem=rows is None), (503 if rows is None else 200)


@bp.route("/campuses")
@admin_required
def campus_list():
    rows = None
    try:
        rows = campuses.list_campuses(get_db())
    except sqlite3.Error:
        logs.log_problem("admin campuses list could not load")
    return render_template("admin/campuses.html", rows=rows, kinds=campuses.KIND_LABELS,
                           load_problem=rows is None), (503 if rows is None else 200)


def _report_or_404(report_id):
    report = reports.get_report(get_db(), report_id)
    if report is None:
        abort(404)
    return report


@bp.route("/reports/<int:report_id>")
@admin_required
def report_detail(report_id):
    report = _report_or_404(report_id)
    return render_template("admin/report_detail.html", report=report,
                           can_close=can("resolve_report", report, g.user),
                           can_remove=report["listing_status"] != "removed")


def _close(report_id, new_status):
    _report_or_404(report_id)
    try:
        reports.close_report(get_db(), report_id, new_status, g.user)
    except reports.ReportProblem as problem:
        if problem.status in (403, 404):
            abort(problem.status)
        flash(problem.message, "error")
    else:
        logs.log_event("admin_action", action="resolve_report" if new_status == "resolved" else "dismiss_report",
                       admin_id=g.user["id"], target_kind="report", target_id=report_id)
        flash(DONE_TEXT[new_status], "success")
    return redirect(url_for("admin.report_detail", report_id=report_id), 303)


@bp.post("/reports/<int:report_id>/resolve")
@admin_required
def report_resolve(report_id):
    return _close(report_id, "resolved")


@bp.post("/reports/<int:report_id>/dismiss")
@admin_required
def report_dismiss(report_id):
    return _close(report_id, "dismissed")


@bp.route("/listings/<int:listing_id>/remove", methods=["GET", "POST"])
@admin_required
def listing_remove(listing_id):
    """GET = the plain confirm page (no JavaScript needed); POST removes the listing."""
    row = listing_service.get_listing(get_db(), listing_id)
    if row is None:
        abort(404)
    if not can("delete_listing", row, g.user):
        abort(403)
    if request.method == "GET":
        return render_template("admin/confirm_remove.html", listing=row)
    try:
        removed_now = svc.remove_listing_as_admin(get_db(), listing_id, g.user)
    except svc.AdminProblem as problem:
        if problem.status in (403, 404):
            abort(problem.status)
        flash(problem.message, "error")
    else:
        logs.log_event("admin_action", action="remove_listing", admin_id=g.user["id"], target_kind="listing",
                       target_id=listing_id, changed=bool(removed_now))
        flash("Listing removed. Its open reports are resolved." if removed_now
              else "This listing was already removed. Nothing was changed.",
              "success" if removed_now else "info")
    return redirect(url_for("admin.report_list"), 303)


@bp.route("/users")
@admin_required
def user_list():
    q = (request.args.get("q") or "").strip()[:100]
    rows, total = None, 0
    try:
        rows, total = svc.list_users(get_db(), q)
    except sqlite3.Error:
        logs.log_problem("admin users list could not load")
    return render_template("admin/users.html", rows=rows, total=total, q=q, limit=svc.USER_LIST_LIMIT,
                           me=g.user, load_problem=rows is None), (503 if rows is None else 200)


def _target_or_404(user_id):
    target = svc.get_user(get_db(), user_id)
    if target is None:
        abort(404)
    return target


@bp.route("/users/<int:user_id>/suspend", methods=["GET", "POST"])
@admin_required
def user_suspend(user_id):
    target = _target_or_404(user_id)
    if request.method == "GET":
        if not can("suspend_user", target, g.user):
            flash("This account cannot be suspended (yourself, an admin, or already suspended).", "error")
            return redirect(url_for("admin.user_list"), 303)
        return render_template("admin/confirm_suspend.html", target=target)
    return _set_suspended(user_id, True, "Account suspended.")


@bp.post("/users/<int:user_id>/unsuspend")
@admin_required
def user_unsuspend(user_id):
    _target_or_404(user_id)
    return _set_suspended(user_id, False, "Account unsuspended.")


def _set_suspended(user_id, suspend, done):
    try:
        svc.set_suspended(get_db(), user_id, suspend, g.user)
    except svc.AdminProblem as problem:
        if problem.status in (403, 404):
            abort(problem.status)
        flash(problem.message, "error")
    else:
        logs.log_event("admin_action", action="suspend_user" if suspend else "unsuspend_user",
                       admin_id=g.user["id"], target_kind="user", target_id=user_id)
        flash(done, "success")
    return redirect(url_for("admin.user_list"), 303)
