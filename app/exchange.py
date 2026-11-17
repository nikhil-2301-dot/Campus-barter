"""Exchange-request routes: send, lists, detail (with contact reveal), accept/decline/cancel/complete."""
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import exchange_service as svc
from . import logs
from .constants import REQUEST_STATUS_KINDS, REQUEST_STATUS_LABELS
from .db import get_db
from .permissions import can
from .ratelimit import limited
from .security import login_required

bp = Blueprint("exchange", __name__)

DONE_TEXT = {"accept": "Request accepted. Contact details are now visible to both of you.",
             "decline": "Request declined.",
             "cancel": "Request cancelled.",
             "complete": "Marked as completed."}


# Button text per action (the same words are used on the confirm page and in the dialog).
ACTION_LABELS = {"accept": "Accept", "decline": "Decline", "cancel": "Cancel request",
                 "complete": "Mark as completed"}
# Actions that ask "are you sure?" first (a dialog, or a plain confirm page without JavaScript).
CONFIRMED_ACTIONS = ("decline", "cancel")


@bp.app_template_filter("request_status_label")
def _request_status_label(value):
    return REQUEST_STATUS_LABELS.get(value, value)


@bp.app_template_filter("request_status_kind")
def _request_status_kind(value):
    return REQUEST_STATUS_KINDS.get(value, "neutral")


def _int_or_none(raw):
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() and len(raw) < 10 else None


@bp.route("/listings/<int:listing_id>/request", methods=["POST"])
@login_required
@limited("send_request")
def send(listing_id):
    offered = request.form.get("offered_listing_id", "")
    offered_id = _int_or_none(offered)
    if offered.strip() and offered_id is None:
        flash("Choose one of your own available listings to offer.", "error")
        return redirect(url_for("listings.view", listing_id=listing_id), 303)
    try:
        request_id = svc.send_request(get_db(), listing_id, g.user, offered_id, request.form.get("message"))
    except svc.ExchangeProblem as problem:
        if problem.status == 404:
            abort(404)
        flash(problem.message, "error")
        return redirect(url_for("listings.view", listing_id=listing_id), 303)
    flash("Request sent. The owner will answer it; you will see it under Requests sent.", "success")
    return redirect(url_for("exchange.detail", request_id=request_id), 303)


def _inbox(loader, kind, heading):
    """Both lists: success (rows), empty (no rows) or problem (the query could not run, HTTP 503).
    There is no loading state: the page is built on the server in one query."""
    try:
        rows = loader(get_db(), g.user["id"])
    except sqlite3.Error:
        logs.log_problem("request inbox could not load")
        return render_template("requests/list.html", rows=[], kind=kind, heading=heading,
                               load_problem=True), 503
    return render_template("requests/list.html", rows=rows, kind=kind, heading=heading, load_problem=False)


@bp.route("/requests/sent")
@login_required
def sent():
    return _inbox(svc.list_sent, "sent", "Requests I sent")


@bp.route("/requests/received")
@login_required
def received():
    return _inbox(svc.list_received, "received", "Requests I received")


@bp.route("/requests/<int:request_id>")
@login_required
def detail(request_id):
    db = get_db()
    req = svc.get_for_party(db, request_id, g.user)
    if req is None:
        abort(404)
    contact = svc.contact_of_other_party(db, req, g.user)
    actions = [a for a in svc.TRANSITIONS if can(a + "_request", req, g.user)]
    return render_template("requests/detail.html", req=req, contact=contact, actions=actions,
                           is_owner=req["owner_id"] == g.user["id"], labels=_labels(req),
                           confirmed_actions=CONFIRMED_ACTIONS)


def _labels(req):
    labels = dict(ACTION_LABELS)
    if req["listing_mode"] == "lend":
        labels["complete"] = "Mark as returned"
    return labels


@bp.route("/requests/<int:request_id>/<action>", methods=["GET", "POST"])
@login_required
def change(request_id, action):
    """POST does the action. GET exists only for decline and cancel: a plain confirmation page that
    works without JavaScript (the dialog on the request page posts to the same address)."""
    if action not in svc.TRANSITIONS:
        abort(404)
    if request.method == "GET":
        if action not in CONFIRMED_ACTIONS:
            abort(405)
        req = svc.get_for_party(get_db(), request_id, g.user)
        if req is None:
            abort(404)
        if not can(action + "_request", req, g.user):
            abort(403)
        return render_template("requests/confirm.html", req=req, action=action, labels=_labels(req),
                               is_owner=req["owner_id"] == g.user["id"])
    try:
        svc.act(get_db(), request_id, action, g.user)
    except LookupError:
        abort(404)
    except svc.NotAllowed:
        abort(403)
    except svc.ExchangeProblem as problem:
        flash(problem.message, "error")
    else:
        flash(DONE_TEXT[action], "success")
    return redirect(url_for("exchange.detail", request_id=request_id), 303)
