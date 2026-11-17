"""Listing photos: the rules (type, size, names), the files on disk and the two SQL statements.

One photo per listing. The type is read from the first bytes of the file (never from the file
name or the browser's Content-Type). Files get a random name, `<32 hex>.<jpg|png|webp>`, and live
in a `photos` folder next to the database file. Only a name that matches KEY_RE ever reaches the
disk, so no path can be built from user input.
"""
import os
import re
import secrets

from flask import current_app

from . import logs

MAX_BYTES = 2 * 1024 * 1024               # one photo
FORM_ALLOWANCE = 64 * 1024                # room for the other form fields in the same request
MAX_REQUEST_BYTES = MAX_BYTES + FORM_ALLOWANCE
KEY_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp)")
CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
TYPE_WORDS = "JPEG, PNG or WebP"
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
HINT = f"{TYPE_WORDS}, up to 2 MB. One photo per listing."


def detect_type(head):
    """'jpg', 'png' or 'webp' from the first bytes, or None when the file is none of these."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def alt_text(title):
    """The alt text of every photo: automatic, from the listing title."""
    return f"Photo of {title}"


def read_upload(storage):
    """Check an uploaded file. Returns (upload, problem): upload is (bytes, ext) or None.

    No file chosen is not a problem (upload None, problem None).
    """
    if storage is None or not (storage.filename or "").strip():
        return None, None
    data = storage.stream.read(MAX_BYTES + 1)
    if not data:
        return None, "The chosen file is empty. Choose another photo."
    if len(data) > MAX_BYTES:
        return None, "That photo is larger than 2 MB. Choose a smaller one."
    ext = detect_type(data[:12])
    if ext is None:
        return None, f"That file is not a photo this site accepts. Use {TYPE_WORDS}."
    return (data, ext), None


def photo_dir():
    configured = current_app.config.get("PHOTO_DIR")
    if configured:
        return configured
    db_path = os.path.abspath(current_app.config["DATABASE_PATH"])
    return os.path.join(os.path.dirname(db_path), "photos")


def save_file(upload):
    """Write the photo under a new random name and return the key."""
    data, ext = upload
    folder = photo_dir()
    os.makedirs(folder, exist_ok=True)
    key = secrets.token_hex(16) + "." + ext
    with open(os.path.join(folder, key), "xb") as fh:
        fh.write(data)
    return key


def delete_file(key):
    """Remove one photo file. A missing file or a key of the wrong shape is ignored."""
    if not key or not KEY_RE.fullmatch(key):
        return
    path = os.path.join(photo_dir(), key)
    if not os.path.isfile(path):
        return
    try:
        os.remove(path)
    except OSError:
        logs.log_problem("a listing photo file could not be removed")


def file_exists(key):
    return bool(key) and KEY_RE.fullmatch(key) is not None and os.path.isfile(os.path.join(photo_dir(), key))


# ---- the two statements --------------------------------------------------------------------

def set_key(conn, listing_id, key):
    conn.execute("UPDATE listings SET photo_key = ?, updated_at = " + NOW + " WHERE id = ?",
                 (key, listing_id))


def visible_key(conn, key):
    """The key again when a listing that is not removed uses it, else None."""
    row = conn.execute("SELECT photo_key FROM listings WHERE photo_key = ? AND status != 'removed'",
                       (key,)).fetchone()
    return row["photo_key"] if row else None


# ---- what the routes call ------------------------------------------------------------------

def attach(conn, listing_id, upload, old_key=None):
    """Save the file, point the listing at it, delete the old file. Returns the new key.

    If the database refuses, the new file is removed again and the problem is raised.
    """
    key = save_file(upload)
    try:
        set_key(conn, listing_id, key)
        conn.commit()
    except Exception:
        delete_file(key)
        raise
    delete_file(old_key)
    return key


def detach(conn, listing_id):
    """Remove the photo of a listing (column and file). True when there was one."""
    row = conn.execute("SELECT photo_key FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None or not row["photo_key"]:
        return False
    set_key(conn, listing_id, None)
    conn.commit()
    delete_file(row["photo_key"])
    return True
