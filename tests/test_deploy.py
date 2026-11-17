"""C23: production settings, proxy setting, start files, and backup + restore (the restore test).

NOTE: scripts/check.sh gates on three words in the test log, so names and docstrings avoid them.
"""
import importlib.util
import os
import sqlite3
import subprocess
import unittest
from unittest import mock

import helpers
from helpers import DEV_ENV, ROOT, TempDirMixin, add_listing, add_user, make_app
from app import create_app
from app.config import ConfigError, load_settings
from app.db import connect
from scripts import backup as backup_script

PROD = {"APP_ENV": "production", "SECRET_KEY": "k" * 40}


class ProductionSettingsTests(unittest.TestCase):
    def test_production_without_a_secret_key_refuses_to_start(self):
        with self.assertRaises(ConfigError) as cm:
            create_app(environ={"APP_ENV": "production"})
        self.assertIn("SECRET_KEY", str(cm.exception))

    def test_production_refuses_debug_mode(self):
        for value in ("1", "true", "on"):
            with self.assertRaises(ConfigError) as cm:
                load_settings({**PROD, "FLASK_DEBUG": value})
            self.assertIn("FLASK_DEBUG", str(cm.exception))

    def test_production_accepts_debug_switched_off(self):
        for value in ("", "0", "false", "no"):
            self.assertTrue(load_settings({**PROD, "FLASK_DEBUG": value}).is_production)

    def test_development_may_use_debug_mode(self):
        self.assertEqual(load_settings({"FLASK_DEBUG": "1"}).env, "development")

    def test_production_app_has_secure_cookies_and_no_design_page(self):
        app = create_app(environ=dict(PROD))
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertFalse(app.debug)
        self.assertEqual(app.test_client().get("/design").status_code, 404)

    def test_trusted_proxies_defaults_to_none(self):
        self.assertEqual(load_settings({}).trusted_proxies, 0)

    def test_trusted_proxies_reads_a_small_number(self):
        self.assertEqual(load_settings({"TRUSTED_PROXIES": "2"}).trusted_proxies, 2)

    def test_trusted_proxies_refuses_bad_values(self):
        for bad in ("-1", "6", "two", "1.5", "\u0661"):
            with self.assertRaises(ConfigError) as cm:
                load_settings({"TRUSTED_PROXIES": bad})
            self.assertIn("TRUSTED_PROXIES", str(cm.exception))


class ClientAddressTests(TempDirMixin, unittest.TestCase):
    def _address(self, env_extra):
        tmp = self.make_tmp()
        app = create_app({"DATABASE_PATH": os.path.join(tmp, "t.db"), "TESTING": True},
                         environ={**DEV_ENV, **env_extra})
        from flask import request
        app.add_url_rule("/_whoami", "whoami", lambda: request.remote_addr)
        resp = app.test_client().get("/_whoami", headers={"X-Forwarded-For": "203.0.113.9"},
                                     environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
        return resp.get_data(as_text=True)

    def test_header_is_ignored_without_a_trusted_proxy(self):
        self.assertEqual(self._address({}), "10.0.0.1")

    def test_header_is_used_with_one_trusted_proxy(self):
        self.assertEqual(self._address({"TRUSTED_PROXIES": "1"}), "203.0.113.9")

    def test_only_the_last_hops_are_believed(self):
        tmp = self.make_tmp()
        app = create_app({"DATABASE_PATH": os.path.join(tmp, "t.db"), "TESTING": True},
                         environ={**DEV_ENV, "TRUSTED_PROXIES": "1"})
        from flask import request
        app.add_url_rule("/_whoami", "whoami", lambda: request.remote_addr)
        resp = app.test_client().get("/_whoami", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"},
                                     environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
        self.assertEqual(resp.get_data(as_text=True), "203.0.113.9")


class StartFileTests(TempDirMixin, unittest.TestCase):
    def test_wsgi_module_exposes_the_app(self):
        tmp = self.make_tmp()
        spec = importlib.util.spec_from_file_location("wsgi_under_test", os.path.join(ROOT, "wsgi.py"))
        module = importlib.util.module_from_spec(spec)
        env = {"APP_ENV": "development", "SECRET_KEY": "test-only-key",
               "DATABASE_PATH": os.path.join(tmp, "w.db")}
        with mock.patch.dict(os.environ, env):
            spec.loader.exec_module(module)
        self.assertEqual(module.app.config["APP_ENV"], "development")

    def test_start_script_is_valid_shell_and_runs_gunicorn_on_wsgi(self):
        path = os.path.join(ROOT, "start.sh")
        text = open(path).read()
        self.assertIn("gunicorn", text)
        self.assertIn("wsgi:app", text)
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0)

    def test_gunicorn_is_pinned_and_every_new_variable_is_documented(self):
        self.assertRegex(open(os.path.join(ROOT, "requirements.txt")).read(), r"(?m)^gunicorn==\d+\.\d+\.\d+$")
        example = open(os.path.join(ROOT, ".env.example")).read()
        for name in ("TRUSTED_PROXIES", "WEB_WORKERS", "BIND", "SECRET_KEY", "APP_ENV", "DATABASE_PATH"):
            self.assertIn(name + "=", example)


class BackupRestoreTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.live = os.path.join(self.tmp, "live")
        os.makedirs(self.live)
        self.db_path = os.path.join(self.live, "test.db")
        self.photos = os.path.join(self.live, "photos")
        make_app(self.live)                       # creates and migrates test.db
        conn = connect(self.db_path)
        owner = add_user(conn, "owner@college.example")
        self.listing = add_listing(conn, owner, "Drafter set")
        conn.commit()
        conn.close()
        os.makedirs(self.photos)
        with open(os.path.join(self.photos, "a" * 32 + ".png"), "wb") as fh:
            fh.write(b"photo-bytes")
        self.dest = os.path.join(self.tmp, "backups")

    def _titles(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return [r[0] for r in conn.execute("SELECT title FROM listings ORDER BY id")]
        finally:
            conn.close()

    def test_backup_folder_holds_a_valid_database_and_the_photos(self):
        folder = backup_script.backup(self.db_path, self.dest)
        self.assertTrue(os.path.basename(folder).startswith("campus-barter-backup-"))
        self.assertIsNone(backup_script.check_database(os.path.join(folder, "campus_barter.db")))
        self.assertEqual(open(os.path.join(folder, "photos", "a" * 32 + ".png"), "rb").read(), b"photo-bytes")

    def test_backup_works_while_a_second_connection_holds_the_file_open(self):
        holder = connect(self.db_path)
        try:
            holder.execute("SELECT count(*) FROM listings").fetchall()
            folder = backup_script.backup(self.db_path, self.dest)
        finally:
            holder.close()
        self.assertIsNone(backup_script.check_database(os.path.join(folder, "campus_barter.db")))

    def test_restore_brings_back_rows_and_photos_after_the_live_data_is_damaged(self):
        folder = backup_script.backup(self.db_path, self.dest)
        # damage the live data: delete the listing, add a stray photo, remove the real one
        conn = connect(self.db_path)
        conn.execute("DELETE FROM listings")
        conn.commit()
        conn.close()
        os.remove(os.path.join(self.photos, "a" * 32 + ".png"))
        with open(os.path.join(self.photos, "b" * 32 + ".png"), "wb") as fh:
            fh.write(b"stray")
        self.assertEqual(self._titles(), [])

        moved = backup_script.restore(folder, self.db_path)

        self.assertEqual(self._titles(), ["Drafter set"])
        self.assertEqual(sorted(os.listdir(self.photos)), ["a" * 32 + ".png"])
        self.assertTrue(moved and all(os.path.exists(name) for name in moved))   # old data kept, not deleted
        self.assertIsNone(backup_script.check_database(self.db_path))

    def test_restored_database_works_with_the_app(self):
        folder = backup_script.backup(self.db_path, self.dest)
        os.remove(self.db_path)
        backup_script.restore(folder, self.db_path)
        app = create_app({"DATABASE_PATH": self.db_path, "TESTING": True}, environ=dict(DEV_ENV))
        self.assertEqual(app.test_client().get("/health").status_code, 200)

    def test_restore_ignores_a_leftover_wal_file_of_the_old_database(self):
        folder = backup_script.backup(self.db_path, self.dest)
        with open(self.db_path + "-wal", "wb") as fh:
            fh.write(b"junk that must not meet the restored file")
        moved = backup_script.restore(folder, self.db_path)
        wal = self.db_path + "-wal"
        self.assertTrue(not os.path.exists(wal) or os.path.getsize(wal) == 0)   # opening the file may make an empty one
        self.assertTrue(any(name.startswith(wal + ".before-restore-") for name in moved))
        self.assertEqual(self._titles(), ["Drafter set"])

    def test_damaged_backup_is_refused_and_live_data_stays(self):
        folder = backup_script.backup(self.db_path, self.dest)
        with open(os.path.join(folder, "campus_barter.db"), "r+b") as fh:
            fh.seek(0)
            fh.write(b"this is not an sqlite file at all")
        with self.assertRaises(backup_script.BackupProblem):
            backup_script.restore(folder, self.db_path)
        self.assertEqual(self._titles(), ["Drafter set"])
        self.assertTrue(os.path.isdir(self.photos))

    def test_folder_without_a_database_is_refused(self):
        os.makedirs(self.dest)
        with self.assertRaises(backup_script.BackupProblem):
            backup_script.restore(self.dest, self.db_path)

    def test_backup_of_a_missing_database_is_refused(self):
        with self.assertRaises(backup_script.BackupProblem):
            backup_script.backup(os.path.join(self.tmp, "nothing.db"), self.dest)

    def test_command_line_round_trip(self):
        self.assertEqual(backup_script.main(["backup", "--to", self.dest, "--db", self.db_path]), 0)
        folder = os.path.join(self.dest, os.listdir(self.dest)[0])
        self.assertEqual(backup_script.main(["restore", folder, "--db", self.db_path]), 0)
        self.assertEqual(backup_script.main(["restore", self.dest, "--db", self.db_path]), 1)
        self.assertEqual(self._titles(), ["Drafter set"])


if __name__ == "__main__":
    unittest.main()
