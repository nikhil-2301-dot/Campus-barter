import os
import sqlite3
import unittest

import helpers
from app import create_app
from app.db import connect


class AppFactoryTests(helpers.TempDirMixin, unittest.TestCase):
    def test_health_ok_on_migrated_database(self):
        app = helpers.make_app(self.make_tmp())
        r = app.test_client().get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["db"], "ok")
        self.assertEqual(body["schema_version"], helpers.MIGRATION_VERSIONS[-1])
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_health_needs_no_login_and_allows_head(self):
        app = helpers.make_app(self.make_tmp())
        self.assertEqual(app.test_client().head("/health").status_code, 200)

    def test_health_reports_not_migrated_with_503(self):
        app = helpers.make_app(self.make_tmp(), migrated=False)
        r = app.test_client().get("/health")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["status"], "degraded")

    def test_health_hides_database_details_when_db_is_unreachable(self):
        tmp = self.make_tmp()
        bad_path = os.path.join(tmp, "missing-dir-is-a-file")
        open(bad_path, "w").close()  # a file where a directory is expected
        app = create_app({"DATABASE_PATH": os.path.join(bad_path, "x.db")},
                         environ=dict(helpers.DEV_ENV))
        with self.assertLogs(app.logger, level="WARNING"):
            r = app.test_client().get("/health")
        self.assertEqual(r.status_code, 503)
        text = r.get_data(as_text=True)
        self.assertNotIn(tmp, text)
        self.assertNotIn("sqlite", text.lower())

    def test_unknown_route_is_404(self):
        app = helpers.make_app(self.make_tmp())
        self.assertEqual(app.test_client().get("/nope").status_code, 404)

    def test_post_to_health_is_405(self):
        app = helpers.make_app(self.make_tmp())
        self.assertEqual(app.test_client().post("/health").status_code, 405)

    def test_two_apps_use_separate_databases(self):
        a = helpers.make_app(self.make_tmp())
        b = helpers.make_app(self.make_tmp())
        self.assertNotEqual(a.config["DATABASE_PATH"], b.config["DATABASE_PATH"])
        conn = connect(a.config["DATABASE_PATH"])
        helpers.add_user(conn)
        conn.commit()
        conn.close()
        other = connect(b.config["DATABASE_PATH"])
        self.assertEqual(other.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        other.close()

    def test_connection_is_closed_at_end_of_request(self):
        app = helpers.make_app(self.make_tmp())
        holder = {}
        from app import db as dbm

        @app.get("/_probe")
        def probe():
            holder["conn"] = dbm.get_db()
            return "ok"

        self.assertEqual(app.test_client().get("/_probe").status_code, 200)
        with self.assertRaises(sqlite3.ProgrammingError):
            holder["conn"].execute("SELECT 1")

    def test_connections_enforce_foreign_keys_and_use_wal_on_disk(self):
        app = helpers.make_app(self.make_tmp())
        conn = connect(app.config["DATABASE_PATH"])
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.close()

    def test_config_values_reach_the_app(self):
        app = helpers.make_app(self.make_tmp())
        self.assertNotIn("ALLOWED_EMAIL_DOMAINS", app.config)
        self.assertEqual(app.config["APP_ENV"], "development")
        self.assertTrue(app.config["SECRET_KEY"])

    def test_production_app_refuses_to_start_without_secrets(self):
        from app.config import ConfigError
        with self.assertRaises(ConfigError):
            create_app(environ={"APP_ENV": "production"})


if __name__ == "__main__":
    unittest.main()
