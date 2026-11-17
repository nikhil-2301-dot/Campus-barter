"""User accounts: validation, creation and authentication.

No Flask request objects in here: functions take plain values and a sqlite3 connection, so the
signup route, the profile route and the `create-admin` CLI command share one set of rules.
Raw SQL with `?` parameters only.
"""
import re
import sqlite3
import unicodedata

from werkzeug.security import check_password_hash, generate_password_hash

from .constants import YEARS

MIN_PASSWORD, MAX_PASSWORD = 8, 128
NAME_MIN, NAME_MAX = 2, 80
BRANCH_MIN, BRANCH_MAX = 2, 60
COLLEGE_MIN, COLLEGE_MAX = 2, 120
ENROLLMENT_MIN, ENROLLMENT_MAX = 3, 30
EMAIL_MAX = 254
PHONE_INPUT_MAX, SOCIAL_MAX, ADDRESS_MAX = 20, 200, 300
# The one place the forms get their maxlength values from (templates never hard-code them).
FIELD_LIMITS = {"email": EMAIL_MAX, "password": MAX_PASSWORD, "full_name": NAME_MAX, "branch": BRANCH_MAX,
                "college_name": COLLEGE_MAX, "enrollment_no": ENROLLMENT_MAX, "phone": PHONE_INPUT_MAX,
                "social_link": SOCIAL_MAX, "address": ADDRESS_MAX}
LOCAL_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
DOMAIN_PART_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
ENROLLMENT_RE = re.compile(r"^[A-Za-z0-9\-/]+$")
# Social link: Reddit u/username, Instagram @handle, or a bare https:// URL (≤200 chars)
SOCIAL_RE = re.compile(r"^(?:u/[A-Za-z0-9_-]{3,20}|@[A-Za-z0-9_.]{1,30}|https?://\S{1,180})$")
# Tiny deny-list of the passwords people pick first (compared in lower case).
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "11111111",
    "qwertyui", "qwertyuiop", "iloveyou", "abcd1234", "abc12345", "admin123", "welcome1",
    "letmein1", "college123", "student123",
})
# Checked when the email is unknown, so a missing account takes about as long as a wrong password.
_DUMMY_HASH = generate_password_hash("not-a-real-password-just-for-timing")


class EmailTaken(Exception):
    """The email is already registered."""


class EnrollmentTaken(Exception):
    """The enrollment number already belongs to another account (unique university-wide)."""


def normalize_email(raw):
    return (raw or "").strip().lower()


def _has_bad_characters(text):
    """Control, unassigned and private-use characters, plus bidi overrides, are refused.

    Zero-width joiners stay allowed: they are part of some Indian-language names.
    """
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs", "Co", "Cn") or (cat == "Cf" and ch not in "\u200c\u200d"):
            return True
    return False


def clean_text(raw, label, low, high):
    """Collapse whitespace, then check length and characters. Returns (value, problem)."""
    value = " ".join((raw or "").split())
    if len(value) < low:
        return value, f"{label} must be at least {low} characters."
    if len(value) > high:
        return value, f"{label} must be at most {high} characters."
    if _has_bad_characters(value):
        return value, f"{label} contains characters that are not allowed."
    return value, None


def check_email(email):
    """Return a problem message or None. Any valid address is accepted (Gmail, college, any provider)."""
    if not email or len(email) > EMAIL_MAX or email.count("@") != 1:
        return "Enter a valid email address."
    local, domain = email.split("@")
    if not LOCAL_RE.fullmatch(local) or ".." in local or local.endswith("."):
        return "Enter a valid email address."
    if not DOMAIN_PART_RE.fullmatch(domain):
        return "Enter a valid email address."
    return None


GMAIL_HOSTS = ("gmail.com", "googlemail.com")


def inbox_key(email):
    """The address as the mail provider sees the inbox: Gmail ignores dots and a +tag, and gmail.com = googlemail.com."""
    local, _, domain = email.partition("@")
    if domain in GMAIL_HOSTS:
        return local.split("+")[0].replace(".", "") + "@gmail.com"
    return email


def _inbox_taken(conn, email):
    """True when another account already uses the same Gmail inbox under a different spelling."""
    key = inbox_key(email)
    if key == email and email.rpartition("@")[2] not in GMAIL_HOSTS:
        return False
    rows = conn.execute(
        "SELECT email FROM users WHERE email LIKE ? OR email LIKE ?",
        tuple("%@" + host for host in GMAIL_HOSTS)).fetchall()
    return any(inbox_key(r["email"]) == key for r in rows)


def check_password(password, confirm, email=""):
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Password must be at most {MAX_PASSWORD} characters."
    local = email.split("@")[0] if email else ""
    if password.lower() in COMMON_PASSWORDS or password.lower() in (email, local):
        return "That password is too easy to guess. Choose another."
    if confirm is not None and password != confirm:
        return "The two passwords do not match."
    return None


def validate_profile(form):
    """Validate the editable profile fields. Returns (clean, problems)."""
    clean, problems = {}, {}
    clean["full_name"], p = clean_text(form.get("full_name"), "Name", NAME_MIN, NAME_MAX)
    if p:
        problems["full_name"] = p
    clean["branch"], p = clean_text(form.get("branch"), "Branch", BRANCH_MIN, BRANCH_MAX)
    if p:
        problems["branch"] = p
    year = (form.get("year") or "").strip()
    clean["year"] = year
    if year not in YEARS:
        problems["year"] = "Choose your year."
    phone = re.sub(r"[ \-]", "", (form.get("phone") or "").strip())
    clean["phone"] = phone or None
    if phone and not PHONE_RE.match(phone):
        problems["phone"] = "Enter 10 to 15 digits (a leading + is allowed), or leave it empty."

    # New fields
    clean["college_name"], p = clean_text(
        form.get("college_name"), "College name", COLLEGE_MIN, COLLEGE_MAX)
    if p:
        problems["college_name"] = p

    enroll = " ".join((form.get("enrollment_no") or "").split()).upper()
    clean["enrollment_no"] = enroll
    if len(enroll) < ENROLLMENT_MIN:
        problems["enrollment_no"] = "Enrollment number is required."
    elif len(enroll) > ENROLLMENT_MAX:
        problems["enrollment_no"] = f"Enrollment number must be at most {ENROLLMENT_MAX} characters."
    elif not ENROLLMENT_RE.match(enroll):
        problems["enrollment_no"] = "Enrollment number may only contain letters, digits, hyphens and slashes."

    social = (form.get("social_link") or "").strip()
    clean["social_link"] = social or None
    if social and not SOCIAL_RE.match(social):
        problems["social_link"] = (
            "Enter a Reddit username (u/name), an Instagram handle (@name), "
            "or a full https:// link.")

    address = " ".join((form.get("address") or "").split())
    clean["address"] = address[:ADDRESS_MAX] or None   # private, no hard validation beyond length

    return clean, problems


def validate_signup(form):
    """Validate the signup form. Returns (clean, problems); `clean` never holds the password."""
    clean, problems = validate_profile(form)
    email = normalize_email(form.get("email"))
    clean["email"] = email
    p = check_email(email)
    if p:
        problems["email"] = p
    p = check_password(form.get("password") or "", form.get("password_confirm") or "", email)
    if p:
        problems["password"] = p
    return clean, problems


def create_user(conn, *, email, password, full_name, branch, year, phone=None, is_admin=False,
                college_name="", enrollment_no="", social_link=None, address=None):
    """Insert a user with a hashed password. Returns the new id; raises EmailTaken or EnrollmentTaken if either exists."""
    if _inbox_taken(conn, email):
        raise EmailTaken(email)
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, full_name, branch, year, phone, is_admin, "
            "college_name, enrollment_no, social_link, address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (email, generate_password_hash(password), full_name, branch, year, phone,
             1 if is_admin else 0, college_name, enrollment_no, social_link, address))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "users.email" in str(exc):
            raise EmailTaken(email) from exc
        if "users.enrollment_no" in str(exc):
            raise EnrollmentTaken(enrollment_no) from exc
        raise
    return cur.lastrowid


def authenticate(conn, email, password):
    """Check a login. Returns (user_row_or_None, status) with status 'ok', 'invalid' or 'suspended'.

    'suspended' is only returned after the password was right, so it does not reveal which emails
    have accounts. Deleted (anonymised) accounts always look like 'invalid'.
    """
    email = normalize_email(email)
    password = password or ""
    user = conn.execute("SELECT * FROM users WHERE email = ? AND deleted_at IS NULL", (email,)).fetchone()
    if len(password) > MAX_PASSWORD:      # never hash huge input; no real password is this long
        return None, "invalid"
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None, "invalid"
    if not check_password_hash(user["password_hash"], password):
        return None, "invalid"
    if user["is_suspended"]:
        return None, "suspended"
    return user, "ok"


def update_profile(conn, user_id, clean):
    """Save the profile. Raises EnrollmentTaken if another account has that enrollment number."""
    try:
        conn.execute(
            "UPDATE users SET full_name = ?, branch = ?, year = ?, phone = ?, "
            "college_name = ?, enrollment_no = ?, social_link = ?, address = ? WHERE id = ?",
            (clean["full_name"], clean["branch"], clean["year"], clean["phone"],
             clean["college_name"], clean["enrollment_no"], clean["social_link"],
             clean["address"], user_id))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "users.enrollment_no" in str(exc):
            raise EnrollmentTaken(clean["enrollment_no"]) from exc
        raise
