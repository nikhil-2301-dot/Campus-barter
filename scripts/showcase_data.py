#!/usr/bin/env python3
"""Fill Campus Barter with showcase data for a demo (lab viva, screenshots).

What it adds, on top of whatever is already in the database:
  * 9 more students from 3 colleges (one profile is fully filled in: phone, link, address)
  * about 28 listings (swap / lend / give, every category, every condition, with photos)
  * exchange requests in EVERY status (pending, accepted, completed, declined, cancelled),
    both received and sent, made through the app's own services so every rule is respected
  * reports in every status, one removed listing, one suspended student, college suggestions

Run it from the project folder, after `flask --app app migrate`:

    python scripts/showcase_data.py

It is safe to run twice (the second run changes nothing). On a developer machine that is all
there is to it. With APP_ENV=production (a live site) it REFUSES unless you opt in on purpose:
set SHOWCASE_ALLOW_PRODUCTION=yes AND SEED_PASSWORD to your own password of 12+ characters
(the public default password is never used on a live site). It needs no new package. If Pillow happens to be installed the photos
get the item title written on them; without Pillow they are plain coloured cards.

Everyone gets the same demo password: Demo-pass-12345 (or the value of SEED_PASSWORD).
"""
import os
import secrets
import struct
import sys
import zlib
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import accounts, admin_service, create_app, exchange_service as svc  # noqa: E402
from app import report_service, seed as seed_mod  # noqa: E402
from app.db import connect  # noqa: E402
from app.migrate import migrate  # noqa: E402

DOMAIN = seed_mod.DEMO_DOMAIN
DEFAULT_PASSWORD = "Demo-pass-12345"
MAIN_USER = "nikhil.te"          # the account to log in with during the showcase
ADMIN_USER = "admin.demo"

DEC = "Demo Engineering College"
DIT = "Delhi Institute of Technology"
CEC = "Capital Engineering College"

# local part, name, branch, year, college, enrollment, phone, social link, address, days since signup
USERS = (
    (MAIN_USER, "Nikhil Kumar", "Information Technology", "TE", DEC, "DEC2023101", "9000000101",
     "@nikhil_codes", "Hostel B, Room 214, Demo Engineering College, Delhi", 45),
    ("priya.se", "Priya Sharma", "Computer Science", "SE", DIT, "DIT2023045", "9000000102",
     "u/priya_codes", None, 40),
    ("arjun.be", "Arjun Mehta", "Electronics and Communication", "BE", CEC, "CEC2021078", "9000000103",
     "@arjun.builds", None, 38),
    ("sneha.fe", "Sneha Gupta", "Civil Engineering", "FE", DEC, "DEC2025012", "9000000104", None, None, 30),
    ("rohan.te", "Rohan Malhotra", "Electrical Engineering", "TE", DIT, "DIT2022090", "9000000105",
     None, None, 28),
    ("kavya.se", "Kavya Nair", "Information Technology", "SE", CEC, "CEC2023033", "9000000106",
     "@kavya.n", None, 26),
    ("imran.be", "Imran Khan", "Mechanical", "BE", DEC, "DEC2021055", "9000000107", None, None, 24),
    ("tanya.fe", "Tanya Singh", "Computer Science", "FE", DIT, "DIT2025101", "9000000108", None, None, 12),
    ("vikas.te", "Vikas Yadav", "Information Technology", "TE", CEC, "CEC2022066", "9000000109", None, None, 10),
)

# key, owner, title, category, condition, mode, wanted in return, description, days ago, photo?
LISTINGS = (
    # --- Nikhil (the main demo account) ---
    ("n_dsa", MAIN_USER, "Data Structures using C (Reema Thareja)", "Books", "good", "swap",
     "A DBMS textbook", "Second edition, clean pages, a few pencil notes in chapter 4.", 20, True),
    ("n_notes", MAIN_USER, "Compiler Design handwritten notes (CIC-303)", "Notes", "good", "give", None,
     "Complete notes for all five units with solved examples. Photocopy-friendly.", 9, True),
    ("n_pyq", MAIN_USER, "DAA previous year question papers 2019-2025", "PYQs book", "new", "give", None,
     "Bound set of papers with answer hints. Never used.", 27, True),
    ("n_calc", MAIN_USER, "Casio fx-82MS scientific calculator", "Calculator", "fair", "swap",
     "A mini drafter", "Works perfectly. Cover is scratched.", 15, True),
    ("n_lab", MAIN_USER, "Lab coat (size L)", "Lab coats", "good", "lend", None,
     "Can lend for one semester. Please return it washed.", 6, True),
    ("n_ee", MAIN_USER, "Basic Electrical Engineering book", "Books", "worn", "give", None,
     "Old edition but the syllabus is the same. Cover is torn.", 33, False),
    ("n_os", MAIN_USER, "Operating System Concepts (Galvin, 10th ed.)", "Books", "good", "swap",
     "A Computer Networks book", "Hardbound, no markings.", 3, False),
    # --- Priya ---
    ("p_phys", "priya.se", "Engineering Physics (H. K. Malik)", "Books", "good", "swap",
     "A Data Structures book", "Covers the full first-year syllabus.", 18, True),
    ("p_arduino", "priya.se", "Arduino UNO starter kit", "Other", "good", "lend", None,
     "Board, breadboard, jumper wires and sensors. Lending for a project.", 11, True),
    ("p_dbms", "priya.se", "DBMS notes and ER diagram sheets", "Notes", "new", "give", None,
     "Typed notes with exam-style ER diagrams.", 22, True),
    # --- Arjun ---
    ("a_drafter", "arjun.be", "Mini drafter (Camel)", "Mini drafter", "good", "swap",
     "A scientific calculator", "Smooth arm, scale is clear. Used for two semesters.", 17, True),
    ("a_signals", "arjun.be", "Signals and Systems textbook", "Books", "fair", "swap",
     "A Digital Electronics book", "Some highlighting in units 2 and 3.", 25, True),
    ("a_meter", "arjun.be", "Digital multimeter", "Other", "good", "lend", None,
     "Good for lab work. Comes with probes and a fresh battery.", 5, True),
    # --- Sneha ---
    ("s_graphics", "sneha.fe", "Engineering Graphics sheets and set squares", "Other", "good", "give", None,
     "Ten blank sheets, two set squares and a compass.", 8, True),
    ("s_chem", "sneha.fe", "Chemistry lab manual and apron", "Lab coats", "worn", "give", None,
     "Apron size M. Manual has all experiments filled in.", 14, False),
    # --- Rohan ---
    ("r_circuits", "rohan.te", "Circuit Theory notes (Semester 4)", "Notes", "good", "give", None,
     "Neat notes with numericals solved step by step.", 13, True),
    ("r_calc", "rohan.te", "Casio fx-991EX calculator", "Calculator", "new", "swap",
     "An Arduino or Raspberry Pi kit", "Bought but my college allows only the old model.", 4, True),
    # --- Kavya ---
    ("k_maths", "kavya.se", "Engineering Mathematics-II PYQ book", "PYQs book", "good", "give", None,
     "Last eight years of papers, solved.", 29, True),
    ("k_cn", "kavya.se", "Computer Networks (Tanenbaum)", "Books", "good", "swap",
     "An Operating System book", "Fifth edition, very clean.", 2, True),
    # --- Imran ---
    ("i_thermo", "imran.be", "Thermodynamics handwritten notes", "Notes", "fair", "give", None,
     "Handwriting is messy but complete.", 19, False),
    ("i_labcoat", "imran.be", "Lab coat (size M)", "Lab coats", "good", "lend", None,
     "Lending for one semester.", 7, True),
    # --- Tanya ---
    ("t_c", "tanya.fe", "Let Us C (Yashavant Kanetkar)", "Books", "good", "give", None,
     "Good for first-year programming labs.", 1, True),
    # --- Vikas (will be suspended) ---
    ("v_fake", "vikas.te", "Brand new smartphone for Rs 500 swap", "Other", "new", "swap",
     "Rs 500 advance first", "Message me only on WhatsApp. Pay first.", 9, False),
)

# (key, requester, listing, offered listing, message, created days ago, [(action, actor, updated days ago)])
REQUESTS = (
    # received by Nikhil
    ("r1", "priya.se", "n_dsa", "p_phys", "Can we swap my Physics book for your Data Structures book?", 2, []),
    ("r2", "rohan.te", "n_dsa", None, "I only need it for the exam month and will return it after.", 1, []),
    ("r3", "tanya.fe", "n_notes", None, "These notes would help me a lot. Thank you!", 1, []),
    ("r4", "arjun.be", "n_calc", "a_drafter", "I will swap my Camel mini drafter for your calculator.", 6,
     [("accept", MAIN_USER, 5)]),
    ("r5", "kavya.se", "n_pyq", None, "Please give me the DAA papers, my exam is next week.", 14,
     [("accept", MAIN_USER, 13), ("complete", MAIN_USER, 10)]),
    ("r6", "imran.be", "n_lab", None, "Need a lab coat for the workshop lab starting Monday.", 3, []),
    ("r7", "rohan.te", "n_lab", None, "Can I borrow the coat for a week?", 4, [("decline", MAIN_USER, 3)]),
    ("r8", "sneha.fe", "n_ee", None, "I need this for my first semester.", 10, [("cancel", "sneha.fe", 9)]),
    # sent by Nikhil
    ("s1", MAIN_USER, "p_phys", "n_os", "I can swap my Galvin OS book for your Physics book.", 2, []),
    ("s2", MAIN_USER, "p_arduino", None, "I need the kit for my IoT mini project for two weeks.", 8,
     [("accept", "priya.se", 7)]),
    ("s3", MAIN_USER, "k_maths", None, "Could I have the maths PYQ book? Exams are close.", 20,
     [("accept", "kavya.se", 19), ("complete", "kavya.se", 16)]),
    ("s4", MAIN_USER, "a_signals", "n_ee", "Would you swap the Signals book for my Electrical book?", 12,
     [("decline", "arjun.be", 11)]),
    ("s5", MAIN_USER, "s_graphics", None, "Do you still have the graphics sheets?", 5,
     [("cancel", MAIN_USER, 4)]),
    # between other students
    ("o1", "rohan.te", "i_thermo", None, "Please share the thermodynamics notes.", 3, []),
    ("o2", "sneha.fe", "r_circuits", None, "Can I get the circuit theory notes?", 9, [("accept", "rohan.te", 8)]),
    ("o3", "kavya.se", "p_dbms", None, "DBMS notes please, I have a viva soon.", 21,
     [("accept", "priya.se", 20), ("complete", "priya.se", 18)]),
)

# (key, reporter, listing, reason, days ago, close with)
REPORTS = (
    ("x1", "arjun.be", "v_fake", "Asks for money before meeting. Looks like a scam.", 7, None),
    ("x2", "sneha.fe", "v_fake", "Fake offer: a phone cannot be swapped for Rs 500.", 6, None),
    ("x3", "imran.be", "r_calc", "The condition says new but the price looks too good to be true.", 3, None),
    ("x4", "kavya.se", "p_arduino", "The photo does not match the description of the kit.", 9, "dismissed"),
    ("x5", "tanya.fe", "i_labcoat", "No photo and the size in the title is unclear.", 2, None),
)

CAMPUSES = (
    ("Maharaja Surajmal Institute of Technology", "Janakpuri, Delhi", "engineering", 15),
    ("Guru Tegh Bahadur Polytechnic Institute", "Rajouri Garden, Delhi", "polytechnic", 12),
    ("Bhagwan Parshuram Institute of Technology", "Rohini, Delhi", "engineering", 8),
    ("Pusa Polytechnic", "Pusa, New Delhi", "polytechnic", 5),
    ("Netaji Subhas University of Technology", "Dwarka, Delhi", "engineering", 2),
)

PALETTE = {                      # category -> (top colour, bottom colour)
    "Books": ((52, 94, 168), (28, 52, 110)),
    "Notes": ((214, 150, 48), (160, 96, 24)),
    "PYQs book": ((128, 84, 176), (80, 48, 124)),
    "Mini drafter": ((38, 150, 148), (20, 96, 100)),
    "Lab coats": ((70, 150, 96), (36, 98, 60)),
    "Calculator": ((92, 104, 122), (50, 58, 72)),
    "Other": ((196, 86, 108), (130, 46, 68)),
}


# ---- small helpers -----------------------------------------------------------------------------

def stamp(days=0, hours=0):
    """UTC timestamp text in the format the database uses, `days` ago."""
    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _png_bytes(width, height, rows):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_photo(title, category, index):
    """A 480x320 placeholder photo as PNG bytes (stdlib only; title text when Pillow is there)."""
    top, bottom = PALETTE.get(category, PALETTE["Other"])
    width, height = 480, 320
    try:
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / (height - 1)
            draw.line([(0, y), (width, y)], fill=tuple(int(t + (b - t) * mix) for t, b in zip(top, bottom)))
        draw.rounded_rectangle((60, 50, 420, 270), 18, fill=(255, 255, 255), outline=(235, 235, 235), width=3)
        draw.rectangle((60, 50, 100, 270), fill=bottom)
        try:
            big = ImageFont.load_default(size=26)
            small = ImageFont.load_default(size=18)
        except TypeError:                       # very old Pillow: fixed-size font
            big = small = ImageFont.load_default()
        words, lines, line = title.split(), [], ""
        for word in words:
            if len(line) + len(word) + 1 > 22 and line:
                lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        lines.append(line)
        y = 120 - 14 * (len(lines) - 1)
        for text in lines[:4]:
            draw.text((120, y), text, fill=(30, 30, 40), font=big)
            y += 34
        draw.text((120, 232), category.upper(), fill=bottom, font=small)
        import io
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue()
    except ImportError:
        pass
    rows = []
    shift = (index * 23) % 60
    for y in range(height):
        mix = y / (height - 1)
        base = bytes(int(t + (b - t) * mix) for t, b in zip(top, bottom))
        row = bytearray(base * width)
        if 50 <= y < 270:
            row[(60 + shift // 4) * 3:(420 - shift // 4) * 3] = b"\xff\xff\xff" * (360 - shift // 2)
            row[(60 + shift // 4) * 3:(100 + shift // 4) * 3] = bytes(bottom) * 40
        rows.append(bytes(row))
    return _png_bytes(width, height, rows)


def photo_folder(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "photos")


def save_photo(db_path, data):
    folder = photo_folder(db_path)
    os.makedirs(folder, exist_ok=True)
    key = secrets.token_hex(16) + ".png"
    with open(os.path.join(folder, key), "xb") as handle:
        handle.write(data)
    return key


def row(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


# ---- the build ---------------------------------------------------------------------------------

def build(conn, db_path, password):
    # make sure the normal demo users (and the admin) exist; this does nothing if they already do
    seed_mod.seed(conn, env="development", domain=DOMAIN, password=password)
    conn.commit()

    ids = {}
    for local, name, branch, year, college, enrollment, phone, social, address, ago in USERS:
        ids[local] = accounts.create_user(
            conn, email=f"{local}@{DOMAIN}", password=password, full_name=name, branch=branch,
            year=year, phone=phone, college_name=college, enrollment_no=enrollment,
            social_link=social, address=address)
        t = stamp(days=ago)
        conn.execute("UPDATE users SET created_at = ?, updated_at = ? WHERE id = ?", (t, t, ids[local]))
    conn.commit()

    def user(local):
        return row(conn, "SELECT * FROM users WHERE id = ?", (ids[local],))

    admin = row(conn, "SELECT * FROM users WHERE email = ?", (f"{ADMIN_USER}@{DOMAIN}",))

    lids, ago_of = {}, {}
    for index, (key, owner, title, category, condition, mode, wanted, desc, ago, photo) in enumerate(LISTINGS):
        t = stamp(days=ago, hours=index % 7)
        cur = conn.execute(
            "INSERT INTO listings (owner_id, title, category, condition, mode, description, wanted_in_return, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ids[owner], title, category, condition, mode, desc, wanted, t, t))
        lids[key] = cur.lastrowid
        ago_of[key] = (ago, index % 7)
        if photo:
            conn.execute("UPDATE listings SET photo_key = ?, updated_at = ? WHERE id = ?",
                         (save_photo(db_path, make_photo(title, category, index)), t, lids[key]))
    conn.commit()

    # requests: go through the real service so listing statuses follow the real rules
    stamps = []
    for key, requester, listing, offered, message, created, steps in REQUESTS:
        rid = svc.send_request(conn, lids[listing], user(requester),
                               lids[offered] if offered else None, message)
        for action, actor, _updated in steps:
            svc.act(conn, rid, action, user(actor))
        updated = steps[-1][2] if steps else created
        stamps.append((rid, created, updated))
    for rid, created, updated in stamps:
        conn.execute("UPDATE exchange_requests SET created_at = ?, updated_at = ? WHERE id = ?",
                     (stamp(days=created, hours=2), stamp(days=updated, hours=1), rid))
    conn.commit()

    # reports and moderation
    report_ids = {}
    for key, reporter, listing, reason, ago, close in REPORTS:
        report_ids[key] = report_service.create_report(conn, lids[listing], user(reporter), reason)
    if admin is not None:
        for key, reporter, listing, reason, ago, close in REPORTS:
            if close:
                report_service.close_report(conn, report_ids[key], close, admin)
        admin_service.remove_listing_as_admin(conn, lids["v_fake"], admin)     # resolves its two reports
        admin_service.set_suspended(conn, ids["vikas.te"], True, admin)
    for key, reporter, listing, reason, ago, close in REPORTS:
        t = stamp(days=ago)
        conn.execute("UPDATE reports SET created_at = ?, updated_at = ? WHERE id = ?",
                     (t, stamp(days=max(ago - 1, 0)) if (close or key in ("x1", "x2")) else t, report_ids[key]))
    conn.commit()

    for name, area, kind, ago in CAMPUSES:
        conn.execute("INSERT OR IGNORE INTO campus_requests (college_name, area, kind, created_at) "
                     "VALUES (?, ?, ?, ?)", (name, area, kind, stamp(days=ago)))
    conn.commit()
    return ids


def main():
    environ = dict(os.environ)
    production = (environ.get("APP_ENV") or "development").strip().lower() == "production"
    password = environ.get("SEED_PASSWORD") or DEFAULT_PASSWORD
    if production:
        if (environ.get("SHOWCASE_ALLOW_PRODUCTION") or "").strip().lower() not in ("1", "yes", "true"):
            sys.exit("Refused: this is a live site (APP_ENV=production). To add showcase data on purpose "
                     "set SHOWCASE_ALLOW_PRODUCTION=yes and SEED_PASSWORD (12+ characters).")
        if len(environ.get("SEED_PASSWORD") or "") < 12:
            sys.exit("Refused: on a live site SEED_PASSWORD must be set to your own password, 12+ characters. "
                     "The public demo password is not used there.")
    app = create_app(environ=environ)
    db_path = app.config["DATABASE_PATH"]
    conn = connect(db_path)
    try:
        migrate(conn, app.config["MIGRATIONS_DIR"])          # no-op when the database is up to date
        if row(conn, "SELECT 1 FROM users WHERE email = ?", (f"{MAIN_USER}@{DOMAIN}",)):
            print("Showcase data is already there; nothing changed.")
            return
        with app.app_context():
            build(conn, db_path, password)
    finally:
        conn.close()
    print("Showcase data added.")
    print(f"  Log in as   : {MAIN_USER}@{DOMAIN}   (Nikhil Kumar, the main demo account)")
    print(f"  Admin login : {ADMIN_USER}@{DOMAIN}")
    print("  Password    : " + (password if not production else "the SEED_PASSWORD you set") +
          "   (same for every demo account)")


if __name__ == "__main__":
    main()
