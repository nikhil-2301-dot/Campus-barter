"""Listings: every SQL statement for reading and writing a listing row (routes call these, never SQL).

Photo columns are handled by photo_service; status changes (reserve, close, remove) by exchange_service.
Functions take an open connection and may raise sqlite3.Error; the route decides how to show that.
"""

PAGE_SIZE = 10


def like_escape(text):
    """Make typed text safe to use inside LIKE ... ESCAPE '\\' (backslash, % and _ match themselves)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_listing(conn, listing_id):
    """One listing with its owner's name, or None."""
    return conn.execute(
        "SELECT l.*, u.full_name AS owner_name FROM listings l "
        "JOIN users u ON u.id = l.owner_id WHERE l.id = ?",
        (listing_id,)
    ).fetchone()


def browse(conn, *, q="", category="", condition="", mode="", page=1):
    """Available listings, newest first. Returns (rows, total, page, total_pages).

    A page past the end is moved back to the last page.
    """
    where_clauses = ["l.status = 'available'"]
    params: list = []
    if q:
        where_clauses.append("(l.title LIKE ? ESCAPE '\\' OR l.description LIKE ? ESCAPE '\\')")
        like = "%" + like_escape(q) + "%"
        params += [like, like]
    if category:
        where_clauses.append("l.category = ?")
        params.append(category)
    if condition:
        where_clauses.append("l.condition = ?")
        params.append(condition)
    if mode:
        where_clauses.append("l.mode = ?")
        params.append(mode)
    where_sql = " AND ".join(where_clauses)

    total = conn.execute("SELECT COUNT(*) FROM listings l WHERE " + where_sql, params).fetchone()[0]
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * PAGE_SIZE
    rows = conn.execute(
        "SELECT l.*, u.full_name AS owner_name FROM listings l "
        "JOIN users u ON u.id = l.owner_id WHERE " + where_sql +
        " ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset]
    ).fetchall()
    return rows, total, page, total_pages


def create_listing(conn, owner_id, data):
    """Insert a listing from validated form data and return its new id."""
    cur = conn.execute(
        "INSERT INTO listings (owner_id, title, category, condition, mode, "
        "description, wanted_in_return) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, data["title"], data["category"], data["condition"],
         data["mode"], data["description"], data["wanted_in_return"])
    )
    conn.commit()
    return cur.lastrowid


def update_listing(conn, listing_id, data):
    """Change the form fields of a listing from validated form data."""
    conn.execute(
        "UPDATE listings SET title=?, category=?, condition=?, mode=?, "
        "description=?, wanted_in_return=? WHERE id=?",
        (data["title"], data["category"], data["condition"], data["mode"],
         data["description"], data["wanted_in_return"], listing_id)
    )
    conn.commit()


def offerable_by(conn, owner_id):
    """The student's own available listings, offered in a swap request (at most 100)."""
    return conn.execute(
        "SELECT id, title FROM listings WHERE owner_id = ? AND status = 'available' "
        "ORDER BY title LIMIT 100", (owner_id,)
    ).fetchall()


def listings_of(conn, owner_id):
    """Every listing of one student, newest first (removed ones included; the page shows their stamp)."""
    return conn.execute(
        "SELECT * FROM listings WHERE owner_id=? ORDER BY created_at DESC, id DESC",
        (owner_id,)
    ).fetchall()
