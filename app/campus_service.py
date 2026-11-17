"""Campuses: the "Add your campus" page lets anyone suggest a college that is not on the board yet.

Rules kept here:
  - college name 3 to 120 characters, area 2 to 80 characters (whitespace collapsed, no control characters);
  - kind is one of CAMPUS_KINDS;
  - the same college in the same area is stored once, whatever the capitals ("added" or "exists");
  - nothing here reads a student: a suggestion carries no personal data.
"""
import sqlite3

from .accounts import clean_text

NAME_MIN, NAME_MAX = 3, 120
AREA_MIN, AREA_MAX = 2, 80
CAMPUS_KINDS = (("engineering", "Engineering college"), ("polytechnic", "Polytechnic"), ("other", "Other college"))
KIND_LABELS = dict(CAMPUS_KINDS)
FIELD_LIMITS = {"college_name": NAME_MAX, "area": AREA_MAX}


def validate(form):
    """Return (clean values, problems by field name). Values are always strings, ready to show again."""
    problems = {}
    name, problem = clean_text(form.get("college_name"), "College name", NAME_MIN, NAME_MAX)
    if problem:
        problems["college_name"] = problem
    area, problem = clean_text(form.get("area"), "Area", AREA_MIN, AREA_MAX)
    if problem:
        problems["area"] = problem
    kind = (form.get("kind") or "").strip()
    if kind not in KIND_LABELS:
        problems["kind"] = "Choose the type of college."
    return {"college_name": name, "area": area, "kind": kind}, problems


def add_campus(conn, clean):
    """Store one suggestion. Returns "added", or "exists" when that college and area are already stored."""
    try:
        conn.execute("INSERT INTO campus_requests (college_name, area, kind) VALUES (?, ?, ?)",
                     (clean["college_name"], clean["area"], clean["kind"]))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return "exists"
    return "added"


def list_campuses(conn, limit=200):
    """Newest first."""
    return conn.execute("SELECT * FROM campus_requests ORDER BY created_at DESC, id DESC LIMIT ?",
                        (limit,)).fetchall()


def count_campuses(conn):
    return conn.execute("SELECT COUNT(*) FROM campus_requests").fetchone()[0]
