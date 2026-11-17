"""Student side of reporting: POST /listings/<id>/report (the form is on the listing page)."""
from flask import Blueprint, abort, flash, g, redirect, request, url_for

from . import report_service as svc
from .db import get_db
from .ratelimit import limited
from .security import login_required

bp = Blueprint("reports", __name__)


@bp.post("/listings/<int:listing_id>/report")
@login_required
@limited("send_report")
def send(listing_id):
    from .listings import render_listing_page      # late import: listings imports nothing from here
    try:
        svc.create_report(get_db(), listing_id, g.user, request.form.get("reason"))
    except svc.ReportProblem as problem:
        if problem.status == 404:
            abort(404)
        if problem.status == 403:
            abort(403)
        if problem.field == "reason":                 # inline problem, typed text kept
            return render_listing_page(listing_id, report_text=(request.form.get("reason") or "")[:2000],
                                       report_problem=problem.message), 400
        flash(problem.message, "error")
        return redirect(url_for("listings.view", listing_id=listing_id), 303)
    flash("Report sent. An admin will review it. The owner is not told who reported.", "success")
    return redirect(url_for("listings.view", listing_id=listing_id), 303)
