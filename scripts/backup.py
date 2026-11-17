"""Backup and restore for Campus Barter (C23). Standard library only.

  python3 scripts/backup.py backup  [--to DIR] [--db PATH] [--photos DIR]
  python3 scripts/backup.py restore BACKUP_FOLDER [--db PATH] [--photos DIR]

A backup is one dated folder holding `campus_barter.db` (a consistent copy made with SQLite's own backup
call, safe while the app is running) and `photos/` (a copy of the photo folder). Stop the app before a
restore. Restore moves the live files aside (renamed), it never deletes them.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.config import DEFAULT_DB_PATH  # noqa: E402

DB_NAME = "campus_barter.db"
PHOTO_NAME = "photos"
PREFIX = "campus-barter-backup-"


class BackupProblem(Exception):
    """Raised with a readable message; the command line prints it and exits with status 1."""


def default_db():
    return os.environ.get("DATABASE_PATH") or DEFAULT_DB_PATH


def default_photos(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), PHOTO_NAME)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def check_database(path):
    """Return None when the SQLite file passes `PRAGMA integrity_check`, else a short reason."""
    try:
        conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return "cannot be opened (" + str(exc) + ")"
    if rows != [("ok",)]:
        return "is damaged (integrity check did not pass)"
    return None


def backup(db_path, dest_root, photos_dir=None):
    """Write a dated backup folder inside `dest_root` and return its path."""
    if not os.path.isfile(db_path):
        raise BackupProblem("database file not found: " + db_path)
    photos_dir = photos_dir or default_photos(db_path)
    folder = os.path.join(dest_root, PREFIX + _stamp())
    if os.path.exists(folder):
        raise BackupProblem("a backup with this time stamp already exists; wait a second and run again")
    os.makedirs(folder)
    target = os.path.join(folder, DB_NAME)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)            # consistent copy even while the app writes (WAL)
    finally:
        dst.close()
        src.close()
    reason = check_database(target)
    if reason:
        shutil.rmtree(folder)
        raise BackupProblem("the new backup " + reason + "; nothing was kept")
    # photos are copied AFTER the database: a photo added in between is simply extra, never missing
    if os.path.isdir(photos_dir):
        shutil.copytree(photos_dir, os.path.join(folder, PHOTO_NAME))
    else:
        os.makedirs(os.path.join(folder, PHOTO_NAME))
    return folder


def _aside(path, stamp):
    """Move `path` out of the way (keeps it) and return the new name, or None if it does not exist."""
    if not os.path.exists(path):
        return None
    new = path + ".before-restore-" + stamp
    os.rename(path, new)
    return new


def restore(backup_folder, db_path, photos_dir=None):
    """Put a backup back. Live files are renamed with `.before-restore-<time>`; returns the list of those names."""
    source_db = os.path.join(backup_folder, DB_NAME)
    if not os.path.isfile(source_db):
        raise BackupProblem("not a backup folder (no " + DB_NAME + " inside): " + backup_folder)
    reason = check_database(source_db)
    if reason:
        raise BackupProblem("the backup " + reason + "; the live data was not touched")
    photos_dir = photos_dir or default_photos(db_path)
    stamp = _stamp()
    moved = []
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):      # a leftover -wal from the old file must never meet the restored one
        new = _aside(db_path + suffix, stamp)
        if new:
            moved.append(new)
    new = _aside(photos_dir, stamp)
    if new:
        moved.append(new)
    shutil.copy2(source_db, db_path)
    source_photos = os.path.join(backup_folder, PHOTO_NAME)
    if os.path.isdir(source_photos):
        shutil.copytree(source_photos, photos_dir)
    else:
        os.makedirs(photos_dir)
    reason = check_database(db_path)
    if reason:
        raise BackupProblem("the restored database " + reason + "; the old files are kept as " + ", ".join(moved))
    return moved


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up or restore the Campus Barter database and photos.")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("backup", help="make a dated backup folder")
    one.add_argument("--to", default="backups", help="folder that will hold the backup (default: ./backups)")
    two = sub.add_parser("restore", help="put a backup folder back (stop the app first)")
    two.add_argument("folder", help="a folder made by the backup command")
    for p in (one, two):
        p.add_argument("--db", default=None, help="database file (default: $DATABASE_PATH or instance/campus_barter.db)")
        p.add_argument("--photos", default=None, help="photo folder (default: a `photos` folder next to the database)")
    args = parser.parse_args(argv)
    db_path = args.db or default_db()
    try:
        if args.command == "backup":
            print("backup written to", backup(db_path, args.to, args.photos))
        else:
            moved = restore(args.folder, db_path, args.photos)
            print("restored from", args.folder)
            for name in moved:
                print("old file kept as", name)
    except BackupProblem as exc:
        print("backup script:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
