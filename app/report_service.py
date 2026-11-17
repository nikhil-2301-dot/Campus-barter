"""Reports: a student reports a listing; an admin resolves or dismisses the report.

Rules kept here (the permission rules are in permissions.can):
  - reason: 10 to 500 characters after trimming;
  - one OPEN report per student per listing (checked inside one transaction);
  - a report on listing A never blocks a report on listing B;
  - resolving or dismissing changes only the report, and only while it is open.
"""
from .tx import NOW, Tx
from .permissions import can

REASON_MIN, REASON_MAX = 10, 500


class ReportProblem(Exception):
    """The report cannot be made or changed (message is shown to the user)."""

    def __init__(self, message, status=409, field=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


def open_report_for(conn, listing_id, user_id):
    """The student's open report on this listing, or None."""
    return conn.execute(
        "SELECT * FROM reports WHERE listing_id = ? AND reporter_id = ? AND status = 'open'",
        (listing_id, user_id)).fetchone()


def validate_reason(raw):
    """Return (clean_reason, problem_or_None)."""
    reason = (raw or "").strip()
    if len(reason) < REASON_MIN:
        return reason, "Write at least %d characters so an admin can understand the problem." % REASON_MIN
    if len(reason) > REASON_MAX:
        return reason, "Keep the reason to %d characters or fewer." % REASON_MAX
    return reason, None


def create_report(conn, listing_id, reporter, raw_reason):
    """Store a report. Returns its id; raises ReportProblem (404 unknown/removed, 403 not allowed,
    400 bad reason, 409 already reported)."""
    reason, problem = validate_reason(raw_reason)
    if problem:
        raise ReportProblem(problem, 400, "reason")
    with Tx(conn):
        listing = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if listing is None or listing["status"] == "removed":
            raise ReportProblem("This listing does not exist any more.", 404)
        if not can("report_listing", listing, reporter):
            raise ReportProblem("You cannot report this listing.", 403)
        if open_report_for(conn, listing_id, reporter["id"]):
            raise ReportProblem("You already reported this listing. An admin will review it.", 409)
        cur = conn.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, ?)",
                           (listing_id, reporter["id"], reason))
        return cur.lastrowid


_REPORT_BASE = (
    "SELECT p.*, l.title AS listing_title, l.category AS listing_category, l.status AS listing_status, "
    "l.owner_id AS owner_id, ou.full_name AS owner_name, ou.college_name AS owner_college, "
    "ru.full_name AS reporter_name, ru.college_name AS reporter_college "
    "FROM reports p JOIN listings l ON l.id = p.listing_id "
    "JOIN users ou ON ou.id = l.owner_id JOIN users ru ON ru.id = p.reporter_id ")


def list_reports(conn, status="open", limit=100):
    """Newest first. `status` is one of REPORT_STATUSES or 'all' (callers pass allowed values only)."""
    if status == "all":
        return conn.execute(_REPORT_BASE + "ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
                            (limit,)).fetchall()
    return conn.execute(_REPORT_BASE + "WHERE p.status = ? ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
                        (status, limit)).fetchall()


def get_report(conn, report_id):
    return conn.execute(_REPORT_BASE + "WHERE p.id = ?", (report_id,)).fetchone()


def close_report(conn, report_id, new_status, admin):
    """Resolve or dismiss an open report. Raises ReportProblem (404 unknown, 403 not admin,
    409 already closed). Changes only the report."""
    if new_status not in ("resolved", "dismissed"):
        raise ReportProblem("Unknown report action.", 400)
    with Tx(conn):
        report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if report is None:
            raise ReportProblem("Report not found.", 404)
        if not can("view_admin", None, admin):
            raise ReportProblem("Only admins can do this.", 403)
        if not can("resolve_report", report, admin):
            raise ReportProblem("This report is already closed. Nothing was changed.", 409)
        conn.execute("UPDATE reports SET status = ?, updated_at = " + NOW + " WHERE id = ?",
                     (new_status, report_id))
