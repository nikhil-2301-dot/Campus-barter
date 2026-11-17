"""Public numbers for the landing page. Counts only: nothing here names or identifies a student."""
from .constants import CATEGORIES


def landing_counts(conn):
    """Available listings (total and per item type) and completed exchanges.

    `available` uses the same rule as the browse page (status = 'available'). Item types that are
    not in CATEGORIES (old rows) count towards the total but get no tile.
    """
    rows = conn.execute(
        "SELECT category, COUNT(*) AS n FROM listings WHERE status = 'available' GROUP BY category"
    ).fetchall()
    per_type = {row["category"]: row["n"] for row in rows}
    completed = conn.execute(
        "SELECT COUNT(*) FROM exchange_requests WHERE status = 'completed'"
    ).fetchone()[0]
    return {
        "available": sum(per_type.values()),
        "completed": completed,
        "by_type": [(category, per_type.get(category, 0)) for category in CATEGORIES],
    }
