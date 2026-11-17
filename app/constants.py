"""Fixed vocabularies shared by the schema, the app and the tests.

The SQL CHECK constraints in migrations/0001_init.sql must match these values;
tests/test_db.py proves they do. Categories are a config list (not a table, see
docs/system-design.md section 5), so the database does NOT constrain them: the
backend validates against CATEGORIES.
"""

YEARS = ("FE", "SE", "TE", "BE")
CATEGORIES = (
    "Books",
    "Notes",
    "PYQs book",
    "Mini drafter",
    "Lab coats",
    "Calculator",
    "Other",
)
CONDITIONS = ("new", "good", "fair", "worn")
MODES = ("swap", "lend", "give")
LISTING_STATUSES = ("available", "reserved", "closed", "removed")
# How the values above are written on screen (the stored values never change).
MODE_LABELS = {"swap": "Swap", "lend": "Lend", "give": "Give away"}
CONDITION_LABELS = {"new": "New", "good": "Good", "fair": "Fair", "worn": "Worn"}
STATUS_LABELS = {"available": "Available", "reserved": "Reserved", "closed": "Closed", "removed": "Removed"}
# Stamp colour per listing status (see the stamp kinds in components/display.html).
STATUS_KINDS = {"available": "ok", "reserved": "warning", "closed": "neutral", "removed": "danger"}
REQUEST_STATUSES = ("pending", "accepted", "completed", "declined", "cancelled")
REQUEST_OPEN_STATUSES = ("pending", "accepted")
# Request status on screen. Same stamp colours as listings: green = good news, yellow = waiting, grey = finished.
REQUEST_STATUS_LABELS = {"pending": "Pending", "accepted": "Accepted", "completed": "Completed",
                         "declined": "Declined", "cancelled": "Cancelled"}
REQUEST_STATUS_KINDS = {"pending": "warning", "accepted": "ok", "completed": "neutral",
                        "declined": "danger", "cancelled": "neutral"}
REPORT_STATUSES = ("open", "resolved", "dismissed")
REPORT_STATUS_LABELS = {"open": "Open", "resolved": "Resolved", "dismissed": "Dismissed"}
REPORT_STATUS_KINDS = {"open": "warning", "resolved": "ok", "dismissed": "neutral"}
YEAR_LABELS = {"FE": "FE (first year)", "SE": "SE (second year)", "TE": "TE (third year)", "BE": "BE (final year)"}
