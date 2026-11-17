"""Exchange requests: the state machine and every database change it causes.

Request status:  pending -> accepted -> completed
                 pending -> declined | cancelled ;  accepted -> cancelled
Listing status:  available -> reserved (request accepted) -> closed (swap or give completed)
                 reserved -> available (accepted request cancelled, or a lend is returned)

Rules kept here (and backed by the unique indexes in migration 0001):
  - one open request per student per listing; one accepted request per listing;
  - a student cannot request their own listing; only `available` listings can be requested;
  - an offered item must be the requester's own, `available`, and only swap listings take an offer;
  - accepting reserves the listing AND the offered item, so one item cannot be promised twice;
  - completing a swap or give closes the listing (and the offered item) and declines the other
    pending requests for them; completing a lend means the item was returned, so it is available again;
  - removing a listing declines its pending requests and cancels its accepted one.
Every change runs in one transaction (BEGIN IMMEDIATE) so two people cannot race each other.
"""
import sqlite3

from . import photo_service
from .permissions import REQUEST_ACTION_ROLES, can, request_role
from .tx import NOW, Tx

MAX_MESSAGE_LEN = 500

# action -> (statuses it may start from, status it ends in); the single source for docs and tests
TRANSITIONS = {
    "accept": (("pending",), "accepted"),
    "decline": (("pending",), "declined"),
    "cancel": (("pending", "accepted"), "cancelled"),
    "complete": (("accepted",), "completed"),
}


class ExchangeProblem(Exception):
    """The action cannot be done in the current state (shown to the user; HTTP 409 or 400)."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


class NotAllowed(Exception):
    """The student is not on the right side of this request (HTTP 403)."""


def _set_listing_status(conn, listing_id, new, only_from):
    """Change a listing's status only if it is currently one of `only_from` (never touches 'removed')."""
    marks = ",".join("?" for _ in only_from)
    conn.execute(
        "UPDATE listings SET status = ?, updated_at = " + NOW + " WHERE id = ? AND status IN (" + marks + ")",
        (new, listing_id, *only_from))


def _decline_pending_for(conn, listing_id):
    """A listing that is no longer available cannot be asked for: decline its pending requests."""
    conn.execute(
        "UPDATE exchange_requests SET status = 'declined', updated_at = " + NOW +
        " WHERE status = 'pending' AND (listing_id = ? OR offered_listing_id = ?)",
        (listing_id, listing_id))


def send_request(conn, listing_id, requester, offered_listing_id, message):
    """Create a pending request. Returns its id; raises ExchangeProblem with a user-readable reason."""
    message = (message or "").strip()
    if len(message) > MAX_MESSAGE_LEN:
        raise ExchangeProblem("The message must be %d characters or fewer." % MAX_MESSAGE_LEN, 400)
    with Tx(conn):
        listing = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if listing is None or listing["status"] == "removed":
            raise ExchangeProblem("This listing does not exist any more.", 404)
        if listing["owner_id"] == requester["id"]:
            raise ExchangeProblem("You cannot request your own listing.", 400)
        if not can("request_listing", listing, requester):
            raise ExchangeProblem("This item is not available to request right now.")
        if offered_listing_id is not None:
            offered = conn.execute("SELECT * FROM listings WHERE id = ?", (offered_listing_id,)).fetchone()
            if listing["mode"] != "swap":
                raise ExchangeProblem("Only swap listings accept an item in return.", 400)
            if offered is None or offered["owner_id"] != requester["id"] or offered["status"] != "available":
                raise ExchangeProblem("Choose one of your own available listings to offer.", 400)
        try:
            cur = conn.execute(
                "INSERT INTO exchange_requests (listing_id, requester_id, offered_listing_id, message) "
                "VALUES (?, ?, ?, ?)", (listing_id, requester["id"], offered_listing_id, message))
        except sqlite3.IntegrityError as exc:
            raise ExchangeProblem("You already have an open request for this item.") from exc
        return cur.lastrowid


def _load(conn, request_id):
    return conn.execute(
        "SELECT r.*, l.owner_id AS owner_id, l.mode AS listing_mode, l.status AS listing_status "
        "FROM exchange_requests r JOIN listings l ON l.id = r.listing_id WHERE r.id = ?",
        (request_id,)).fetchone()


def act(conn, request_id, action, user):
    """Run accept / decline / cancel / complete. Returns the new status.

    Raises LookupError (no such request or not your request), NotAllowed (wrong side),
    ExchangeProblem (wrong state or the items changed meanwhile).
    """
    if action not in TRANSITIONS:
        raise LookupError(action)
    with Tx(conn):
        req = _load(conn, request_id)
        role = request_role(req, user) if req is not None else None
        if role is None:
            raise LookupError(request_id)             # strangers cannot even learn it exists
        if role not in REQUEST_ACTION_ROLES[action]:
            raise NotAllowed(action)
        if not can(action + "_request", req, user):
            raise ExchangeProblem("This request is %s, so it cannot be %s." % (
                req["status"], {"accept": "accepted", "decline": "declined",
                                "cancel": "cancelled", "complete": "completed"}[action]))
        new_status = TRANSITIONS[action][1]
        listing_id, offered_id = req["listing_id"], req["offered_listing_id"]

        if action == "accept":
            listing = conn.execute("SELECT status FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if listing["status"] != "available":
                raise ExchangeProblem("Your listing is not available (it is reserved, closed or removed).")
            if offered_id is not None:
                off = conn.execute("SELECT status FROM listings WHERE id = ?", (offered_id,)).fetchone()
                if off["status"] != "available":
                    raise ExchangeProblem("The item offered in return is no longer available. Decline this request.")
        try:
            conn.execute("UPDATE exchange_requests SET status = ?, updated_at = " + NOW + " WHERE id = ?",
                         (new_status, request_id))
        except sqlite3.IntegrityError as exc:
            raise ExchangeProblem("Another request for this item is already accepted.") from exc

        if action == "accept":
            _set_listing_status(conn, listing_id, "reserved", ("available",))
            if offered_id is not None:
                _set_listing_status(conn, offered_id, "reserved", ("available",))
        elif action == "cancel" and req["status"] == "accepted":
            _set_listing_status(conn, listing_id, "available", ("reserved",))
            if offered_id is not None:
                _set_listing_status(conn, offered_id, "available", ("reserved",))
        elif action == "complete":
            if req["listing_mode"] == "lend":         # returned to its owner
                _set_listing_status(conn, listing_id, "available", ("reserved",))
                if offered_id is not None:
                    _set_listing_status(conn, offered_id, "available", ("reserved",))
            else:                                      # swap or give: the item has left for good
                for lid in (listing_id, offered_id):
                    if lid is not None:
                        _set_listing_status(conn, lid, "closed", ("available", "reserved"))
                        _decline_pending_for(conn, lid)
        return new_status


def remove_listing(conn, listing_id):
    """Soft-delete a listing and settle its open requests. Returns False if it was already removed."""
    with Tx(conn):
        row = conn.execute("SELECT status FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if row is None or row["status"] == "removed":
            return False
        accepted = conn.execute(
            "SELECT offered_listing_id FROM exchange_requests WHERE listing_id = ? AND status = 'accepted'",
            (listing_id,)).fetchall()
        conn.execute(
            "UPDATE exchange_requests SET status = 'declined', updated_at = " + NOW +
            " WHERE listing_id = ? AND status = 'pending'", (listing_id,))
        conn.execute(
            "UPDATE exchange_requests SET status = 'cancelled', updated_at = " + NOW +
            " WHERE listing_id = ? AND status = 'accepted'", (listing_id,))
        for a in accepted:                              # the item offered in return is free again
            if a["offered_listing_id"] is not None:
                _set_listing_status(conn, a["offered_listing_id"], "available", ("reserved",))
        conn.execute(
            "UPDATE listings SET status = 'removed', deleted_at = " + NOW + ", updated_at = " + NOW +
            " WHERE id = ?", (listing_id,))
    photo_service.detach(conn, listing_id)      # C19: the photo file goes with the listing
    return True


# ---- reading -------------------------------------------------------------------------------

_BASE = (
    "SELECT r.*, l.title AS listing_title, l.mode AS listing_mode, l.status AS listing_status, "
    "l.owner_id AS owner_id, o.title AS offered_title, "
    "ou.full_name AS owner_name, ou.college_name AS owner_college, "
    "ru.full_name AS requester_name, ru.college_name AS requester_college, "
    "ru.branch AS requester_branch, ru.year AS requester_year "
    "FROM exchange_requests r JOIN listings l ON l.id = r.listing_id "
    "JOIN users ou ON ou.id = l.owner_id JOIN users ru ON ru.id = r.requester_id "
    "LEFT JOIN listings o ON o.id = r.offered_listing_id ")


def list_sent(conn, user_id):
    return conn.execute(_BASE + "WHERE r.requester_id = ? ORDER BY r.updated_at DESC, r.id DESC LIMIT 200",
                        (user_id,)).fetchall()


def list_received(conn, user_id):
    return conn.execute(_BASE + "WHERE l.owner_id = ? ORDER BY r.updated_at DESC, r.id DESC LIMIT 200",
                        (user_id,)).fetchall()


def open_request_for(conn, listing_id, user_id):
    """The student's own pending or accepted request for this listing, or None."""
    return conn.execute(
        "SELECT id, status FROM exchange_requests WHERE listing_id = ? AND requester_id = ? "
        "AND status IN ('pending', 'accepted') ORDER BY id DESC LIMIT 1", (listing_id, user_id)).fetchone()


def get_for_party(conn, request_id, user):
    """The request, but only for its owner or requester (None for everybody else)."""
    row = conn.execute(_BASE + "WHERE r.id = ?", (request_id,)).fetchone()
    if row is None or not can("view_request", row, user):
        return None
    return row


def contact_of_other_party(conn, req, user):
    """The other student's contact details, or None unless the request is accepted or completed."""
    if not can("view_contact", req, user):
        return None
    other_id = req["requester_id"] if user["id"] == req["owner_id"] else req["owner_id"]
    return conn.execute(
        "SELECT full_name, college_name, branch, year, email, phone, social_link, address "
        "FROM users WHERE id = ?", (other_id,)).fetchone()
