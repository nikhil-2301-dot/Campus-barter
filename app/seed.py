"""Demo data for development. REFUSES to run in production.

Idempotent: if the demo users already exist, nothing is changed.
Passwords are never stored in plain text; a random one is generated unless the
caller (or SEED_PASSWORD) supplies one.
"""
import secrets

from werkzeug.security import generate_password_hash

DEMO_DOMAIN = "college.example"   # demo data only; signup accepts any email address

DEMO_USERS = (
    # email local part, full name, branch, year, phone, is_admin, college_name, enrollment_no
    ("asha.fe", "Asha Verma", "Information Technology", "FE", "9000000001", 0,
     "Demo Engineering College", "DEC2024001"),
    ("ravi.te", "Ravi Singh", "Computer Engineering", "TE", "9000000002", 0,
     "Demo Engineering College", "DEC2022002"),
    ("meera.be", "Meera Kapoor", "Mechanical", "BE", "9000000003", 0,
     "Demo Engineering College", "DEC2021003"),
    ("admin.demo", "Demo Admin", "Administration", "BE", "9000000004", 1,
     "Demo Engineering College", "ADMIN001"),
)


class SeedRefused(Exception):
    pass


def seed(conn, *, env, domain, password=None):
    """Insert demo rows. Returns (created: bool, password: str | None)."""
    if env == "production":
        raise SeedRefused("seeding is disabled in production")
    domain = domain.lower()
    first = DEMO_USERS[0][0] + "@" + domain
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (first,)).fetchone():
        return False, None
    password = password or secrets.token_urlsafe(9)
    pw_hash = generate_password_hash(password)

    ids = {}
    conn.execute("SAVEPOINT demo_seed")  # all demo data or none; also works inside an open transaction
    try:
        for local, name, branch, year, phone, is_admin, college_name, enrollment_no in DEMO_USERS:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, full_name, branch, year, phone, is_admin, "
                "college_name, enrollment_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{local}@{domain}", pw_hash, name, branch, year, phone, is_admin,
                 college_name, enrollment_no))
            ids[local] = cur.lastrowid

        def listing(owner, title, category, condition, mode, desc, wanted=None, status="available"):
            cur = conn.execute(
                "INSERT INTO listings (owner_id, title, category, condition, mode, description, "
                "wanted_in_return, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ids[owner], title, category, condition, mode, desc, wanted, status))
            return cur.lastrowid

        listing("meera.be", "Engineering Mathematics textbook", "Books", "good", "give",
                      "Complete, light pencil marks.")
        drafter = listing("ravi.te", "Mini drafter (Omega)", "Mini drafter", "fair", "swap",
                          "Works fine, one loose screw.", wanted="Calculator")
        sheets = listing("meera.be", "Handwritten notes, Semester 3", "Notes", "good", "give",
                         "Neat notes for all subjects.")
        holder = listing("ravi.te", "Sheet holder", "Other", "good", "lend",
                         "Can lend for one semester.", status="reserved")
        calc = listing("asha.fe", "Scientific calculator (fx-991)", "Calculator", "good", "swap",
                       "Barely used.", wanted="Mini drafter")
        listing("asha.fe", "Old lab coat", "Lab coats", "worn", "give", "Size M.")

        conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, offered_listing_id, message, status) "
                     "VALUES (?, ?, ?, ?, 'pending')",
                     (drafter, ids["asha.fe"], calc, "Can we swap for my calculator?"))
        conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, message, status) "
                     "VALUES (?, ?, ?, 'accepted')",
                     (holder, ids["meera.be"], "Need it for the semester."))
        conn.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, ?)",
                     (sheets, ids["ravi.te"], "Demo report: description looks wrong."))
        conn.execute("RELEASE demo_seed")
    except Exception:
        conn.execute("ROLLBACK TO demo_seed")
        conn.execute("RELEASE demo_seed")
        raise
    return True, password
