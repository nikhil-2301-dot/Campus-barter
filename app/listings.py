"""Listings blueprint: create, view, edit, delete (soft), browse, my-listings."""
import sqlite3

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, url_for
)

from . import exchange_service, listing_service, logs, photo_service, report_service
from .constants import (
    CATEGORIES, CONDITION_LABELS, CONDITIONS, MODE_LABELS, MODES, STATUS_KINDS, STATUS_LABELS
)
from .db import get_db
from .permissions import can
from .ratelimit import limited
from .security import login_required

bp = Blueprint("listings", __name__, url_prefix="/listings")

PAGE_SIZE = listing_service.PAGE_SIZE
MAX_TITLE_LEN = 120
MAX_DESC_LEN = 2000
MAX_WANTED_LEN = 300
MAX_SEARCH_LEN = 100  # R7: the browse search text is cut to this many characters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_listing_or_404(listing_id: int, *, include_removed=False):
    """Return a listing row or abort 404.  Soft-deleted rows are 404 unless include_removed."""
    row = listing_service.get_listing(get_db(), listing_id)
    if row is None:
        abort(404)
    if row["status"] == "removed" and not include_removed:
        abort(404)
    return row


def _validate_listing_form(form):
    """Return (data_dict, problems) from a submitted form.

    `problems` maps a field name to the message shown next to that field (empty when valid).
    """
    problems = {}
    title = (form.get("title") or "").strip()
    if not title:
        problems["title"] = "Title is required."
    elif len(title) > MAX_TITLE_LEN:
        problems["title"] = f"Title must be {MAX_TITLE_LEN} characters or fewer."

    category = form.get("category") or ""
    if category not in CATEGORIES:
        problems["category"] = "Please choose a valid category."

    condition = form.get("condition") or ""
    if condition not in CONDITIONS:
        problems["condition"] = "Please choose a valid condition."

    mode = form.get("mode") or ""
    if mode not in MODES:
        problems["mode"] = "Please choose a valid mode."

    description = (form.get("description") or "").strip()
    if len(description) > MAX_DESC_LEN:
        problems["description"] = f"Description must be {MAX_DESC_LEN} characters or fewer."

    wanted = (form.get("wanted_in_return") or "").strip()
    if len(wanted) > MAX_WANTED_LEN:
        problems["wanted_in_return"] = f"'Wanted in return' must be {MAX_WANTED_LEN} characters or fewer."
    if mode != "swap" and wanted:
        wanted = ""  # ignore wanted for non-swap modes

    return {
        "title": title,
        "category": category,
        "condition": condition,
        "mode": mode,
        "description": description,
        "wanted_in_return": wanted or None,
    }, problems


def _form_context():
    """Choices and length limits the listing form needs (values stay the stored ones)."""
    return {
        "category_options": [(c, c) for c in CATEGORIES],
        "condition_options": [(c, CONDITION_LABELS[c]) for c in CONDITIONS],
        "mode_options": [(m, MODE_LABELS[m]) for m in MODES],
        "photo_hint": photo_service.HINT,
        "limits": {"title": MAX_TITLE_LEN, "description": MAX_DESC_LEN,
                   "wanted_in_return": MAX_WANTED_LEN},
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.app_template_filter("photo_alt")
def _photo_alt(title):
    return photo_service.alt_text(title)


@bp.app_template_filter("mode_label")
def _mode_label(value):
    return MODE_LABELS.get(value, value)


@bp.app_template_filter("condition_label")
def _condition_label(value):
    return CONDITION_LABELS.get(value, value)


@bp.app_template_filter("status_label")
def _status_label(value):
    return STATUS_LABELS.get(value, value)


@bp.app_template_filter("status_kind")
def _status_kind(value):
    return STATUS_KINDS.get(value, "neutral")


def _browse_filters(args):
    """Read the browse filters from the query string.

    Returns (filters, ignored): `filters` holds only values that are allowed; `ignored` lists the
    names (as shown to the student) of filters that had a value we do not recognise.
    """
    filters = {"q": (args.get("q") or "").strip()[:MAX_SEARCH_LEN]}
    ignored = []
    for key, allowed, shown in (
        ("category", CATEGORIES, "item type"),
        ("condition", CONDITIONS, "condition"),
        ("mode", MODES, "offered as"),
    ):
        value = args.get(key) or ""
        if value in allowed:
            filters[key] = value
        else:
            filters[key] = ""
            if value:
                ignored.append(shown)
    return filters, ignored


@bp.route("/")
def browse():
    """Public browse with optional filter + pagination."""
    filters, ignored = _browse_filters(request.args)
    q, cat, cond, mode_filter = filters["q"], filters["category"], filters["condition"], filters["mode"]
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
        ignored.append("page")

    try:
        listings, total, page, total_pages = listing_service.browse(
            get_db(), q=q, category=cat, condition=cond, mode=mode_filter, page=page)
    except sqlite3.Error:
        logs.log_problem("browse list could not load")
        return render_template("listings/browse.html", load_problem=True, listings=[], total=0,
                               page=1, total_pages=1, ignored=[], q=q, cat=cat, cond=cond,
                               mode_filter=mode_filter, **_browse_options()), 503

    filtered = bool(q or cat or cond or mode_filter)
    return render_template(
        "listings/browse.html",
        load_problem=False,
        listings=listings,
        q=q,
        cat=cat,
        cond=cond,
        mode_filter=mode_filter,
        filtered=filtered,
        ignored=ignored,
        page=page,
        total_pages=total_pages,
        total=total,
        **_browse_options(),
    )


def _browse_options():
    """(value, label) pairs for the three filter dropdowns."""
    return {
        "search_max": MAX_SEARCH_LEN,
        "category_options": [(c, c) for c in CATEGORIES],
        "condition_options": [(c, CONDITION_LABELS[c]) for c in CONDITIONS],
        "mode_options": [(m, MODE_LABELS[m]) for m in MODES],
    }


def _store_photo(db, listing_id, upload, old_key=None):
    """Save the photo and point the listing at it. False when the disk or database refused (logged)."""
    try:
        photo_service.attach(db, listing_id, upload, old_key)
    except (OSError, sqlite3.Error):
        logs.log_problem("a listing photo could not be saved")
        return False
    return True


@bp.route("/new", methods=["GET", "POST"])
@login_required
@limited("create_listing")
def create():
    if not can("create_listing", None, g.user):
        abort(403)

    problems = {}
    form_data = {}

    if request.method == "POST":
        data, problems = _validate_listing_form(request.form)
        upload, photo_problem = photo_service.read_upload(request.files.get("photo"))
        if photo_problem:
            problems["photo"] = photo_problem
        form_data = request.form.to_dict()
        if not problems:
            db = get_db()
            new_id = listing_service.create_listing(db, g.user["id"], data)
            if upload and not _store_photo(db, new_id, upload):
                flash("Listing created, but the photo could not be saved. Add it again by editing the listing.",
                      "warning")
                return redirect(url_for("listings.view", listing_id=new_id), 303)
            flash("Listing created.", "success")
            return redirect(url_for("listings.view", listing_id=new_id), 303)

    return render_template(
        "listings/form.html",
        action="create",
        problems=problems,
        form_data=form_data,
        **_form_context(),
    ), (400 if problems else 200)  # C17: a refused form is 400, like signup and profile


def render_listing_page(listing_id, report_text="", report_problem=None):
    """The listing detail page. The report form values are passed in when a report was refused."""
    listing = _get_listing_or_404(listing_id)
    my_offerable = []
    if g.user and g.user["id"] != listing["owner_id"] and listing["mode"] == "swap":
        my_offerable = listing_service.offerable_by(get_db(), g.user["id"])
    my_offerable_options = [(str(o["id"]), o["title"]) for o in my_offerable]
    my_open_request = my_open_report = None
    if g.user and g.user["id"] != listing["owner_id"]:
        my_open_request = exchange_service.open_request_for(get_db(), listing["id"], g.user["id"])
        if not g.user["is_admin"]:
            my_open_report = report_service.open_report_for(get_db(), listing["id"], g.user["id"])
    return render_template("listings/view.html", listing=listing, my_offerable=my_offerable,
                           my_offerable_options=my_offerable_options, my_open_request=my_open_request,
                           my_open_report=my_open_report, can_report=can("report_listing", listing, g.user),
                           report_text=report_text, report_problem=report_problem,
                           reason_max=report_service.REASON_MAX)


@bp.route("/<int:listing_id>")
def view(listing_id):
    return render_listing_page(listing_id)


@bp.route("/<int:listing_id>/edit", methods=["GET", "POST"])
@login_required
def edit(listing_id):
    listing = _get_listing_or_404(listing_id)
    if not can("edit_listing", listing, g.user):
        abort(403)

    problems = {}
    form_data = {}

    if request.method == "POST":
        data, problems = _validate_listing_form(request.form)
        upload, photo_problem = photo_service.read_upload(request.files.get("photo"))
        if photo_problem:
            problems["photo"] = photo_problem
        form_data = request.form.to_dict()
        if not problems:
            db = get_db()
            listing_service.update_listing(db, listing_id, data)
            if upload and not _store_photo(db, listing_id, upload, listing["photo_key"]):
                flash("Listing updated, but the photo could not be saved. Try the photo again.", "warning")
                return redirect(url_for("listings.edit", listing_id=listing_id), 303)
            flash("Listing updated.", "success")
            return redirect(url_for("listings.view", listing_id=listing_id), 303)
    else:
        form_data = dict(listing)

    return render_template(
        "listings/form.html",
        action="edit",
        listing=listing,
        problems=problems,
        form_data=form_data,
        **_form_context(),
    ), (400 if problems else 200)  # C17: a refused form is 400


@bp.route("/<int:listing_id>/photo/remove", methods=["POST"])
@login_required
def remove_photo(listing_id):
    """Take the photo off a listing (the file is deleted too). Same permission as editing."""
    listing = _get_listing_or_404(listing_id)
    if not can("edit_listing", listing, g.user):
        abort(403)
    if photo_service.detach(get_db(), listing_id):
        flash("Photo removed.", "success")
    else:
        flash("This listing has no photo.", "info")
    return redirect(url_for("listings.edit", listing_id=listing_id), 303)


@bp.route("/<int:listing_id>/delete", methods=["GET", "POST"])
@login_required
def delete(listing_id):
    """GET shows a plain confirmation page (works without JavaScript); POST removes the listing."""
    listing = _get_listing_or_404(listing_id)
    if not can("delete_listing", listing, g.user):
        abort(403)

    if request.method == "GET":
        return render_template("listings/confirm_delete.html", listing=listing)

    exchange_service.remove_listing(get_db(), listing_id)   # also settles its open requests
    flash("Listing removed.", "success")
    return redirect(url_for("listings.my_listings"), 303)


@bp.route("/mine")
@login_required
def my_listings():
    """The signed-in student's own listings. Rendered on the server in one query, so there is no
    loading state; the states are success, empty and problem (503 when the query cannot run)."""
    try:
        listings = listing_service.listings_of(get_db(), g.user["id"])
    except sqlite3.Error:
        logs.log_problem("my listings could not load")
        return render_template("listings/mine.html", load_problem=True, listings=[]), 503
    return render_template("listings/mine.html", load_problem=False, listings=listings)
