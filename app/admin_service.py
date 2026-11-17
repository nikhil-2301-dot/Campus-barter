"""Admin moderation: dashboard numbers, removing a listing, listing users, suspend / unsuspend.

Removing a listing REUSES exchange_service.remove_listing (so waiting requests are settled), then
resolves every open report on that listing. Doing it twice changes nothing the second time.
"""
from . import exchange_service
from .tx import NOW, Tx
from .permissions import can

USER_LIST_LIMIT = 100


class AdminProblem(Exception):
    """The admin action cannot be done (message is shown; status is the HTTP code)."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def dashboard_counts(conn):
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM reports WHERE status = 'open') AS open_reports, "
        "(SELECT COUNT(*) FROM listings WHERE status = 'available') AS available_listings, "
        "(SELECT COUNT(*) FROM users WHERE is_suspended = 1 AND deleted_at IS NULL) AS suspended_users"
    ).fetchone()
    return dict(row)


def remove_listing_as_admin(conn, listing_id, admin):
    """Remove a listing, settle its requests, resolve its open reports.
    Returns True if it was removed now, False if it had been removed already."""
    listing = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if listing is None:
        raise AdminProblem("Listing not found.", 404)
    if not can("delete_listing", listing, admin) or not can("view_admin", None, admin):
        raise AdminProblem("Only admins can do this.", 403)
    removed_now = exchange_service.remove_listing(conn, listing_id)
    with Tx(conn):
        conn.execute("UPDATE reports SET status = 'resolved', updated_at = " + NOW +
                     " WHERE listing_id = ? AND status = 'open'", (listing_id,))
    return removed_now


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()


def list_users(conn, q="", limit=USER_LIST_LIMIT):
    """(rows, total). `q` matches name or email (case-insensitive, LIKE characters escaped)."""
    where, params = "deleted_at IS NULL", []
    q = (q or "").strip()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (full_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\')"
        params += [like, like]
    total = conn.execute("SELECT COUNT(*) FROM users WHERE " + where, params).fetchone()[0]
    rows = conn.execute("SELECT * FROM users WHERE " + where + " ORDER BY full_name, id LIMIT ?",
                        params + [limit]).fetchall()
    return rows, total


def set_suspended(conn, user_id, suspend, admin):
    """Suspend (True) or unsuspend (False). Only the flag changes. Raises AdminProblem
    (404 unknown, 403 not admin, 400/409 rule refused)."""
    action = "suspend_user" if suspend else "unsuspend_user"
    with Tx(conn):
        target = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()
        if target is None:
            raise AdminProblem("User not found.", 404)
        if not can("view_admin", None, admin):
            raise AdminProblem("Only admins can do this.", 403)
        if not can(action, target, admin):
            if suspend and target["id"] == admin["id"]:
                raise AdminProblem("You cannot suspend your own account.", 400)
            if suspend and target["is_admin"]:
                raise AdminProblem("Admin accounts cannot be suspended.", 400)
            raise AdminProblem("This account is already suspended." if suspend
                               else "This account is not suspended.", 409)
        conn.execute("UPDATE users SET is_suspended = ?, updated_at = " + NOW + " WHERE id = ?",
                     (1 if suspend else 0, user_id))
