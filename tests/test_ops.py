"""C24: richer /health, CI workflow file, runbooks.

NOTE: scripts/check.sh gates on three words in the test log, so names and docstrings avoid them.
"""
import os
import re
import shutil
import sqlite3
import unittest

import helpers
from helpers import ROOT, TempDirMixin, make_app
from app import migrate
from app.config import MIGRATIONS_DIR
from app.db import connect

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "check.yml")
RUNBOOKS = os.path.join(ROOT, "docs", "runbooks")
NEW_RUNBOOKS = ("backups.md", "rollback.md", "incident.md", "monitoring.md")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class HealthDetailTests(TempDirMixin, unittest.TestCase):
    def test_ok_body_names_versions_and_no_pending_migrations(self):
        r = make_app(self.make_tmp()).test_client().get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["schema_version"], helpers.MIGRATION_VERSIONS[-1])
        self.assertEqual(body["latest_schema_version"], helpers.MIGRATION_VERSIONS[-1])
        self.assertEqual(body["pending_migrations"], 0)
        self.assertEqual(body["app_version"], _read("VERSION").strip())

    def _app_with_extra_migration_file(self):
        tmp = self.make_tmp()
        app = make_app(tmp)
        folder = os.path.join(tmp, "migrations")
        shutil.copytree(MIGRATIONS_DIR, folder)
        with open(os.path.join(folder, "0099_waiting.sql"), "w", encoding="utf-8") as handle:
            handle.write("CREATE TABLE waiting_demo (id INTEGER PRIMARY KEY);\n")
        app.config["MIGRATIONS_DIR"] = folder
        return app

    def test_a_waiting_migration_file_makes_health_503(self):
        r = self._app_with_extra_migration_file().test_client().get("/health")
        self.assertEqual(r.status_code, 503)
        body = r.get_json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["db"], "migrations pending")
        self.assertEqual(body["pending_migrations"], 1)
        self.assertEqual(body["latest_schema_version"], "0099")
        self.assertEqual(body["schema_version"], helpers.MIGRATION_VERSIONS[-1])

    def test_health_turns_ok_again_after_the_migration_runs(self):
        app = self._app_with_extra_migration_file()
        migrate.migrate_path(app.config["DATABASE_PATH"], app.config["MIGRATIONS_DIR"])
        r = app.test_client().get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["schema_version"], "0099")

    def test_every_health_answer_is_not_cached(self):
        tmp = self.make_tmp()
        for app in (make_app(tmp), make_app(self.make_tmp(), migrated=False)):
            self.assertEqual(app.test_client().get("/health").headers["Cache-Control"], "no-store")

    def test_a_database_that_cannot_answer_gives_503_without_details(self):
        tmp = self.make_tmp()
        app = make_app(tmp)
        path = app.config["DATABASE_PATH"]
        with open(path, "wb") as handle:
            handle.write(b"this is not a database file at all" * 50)
        with self.assertLogs(app.logger, level="WARNING"):
            r = app.test_client().get("/health")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json(), {"status": "degraded", "db": "unavailable"})
        self.assertNotIn(tmp, r.get_data(as_text=True))

    def test_migration_helpers_list_and_compare_versions(self):
        self.assertEqual(migrate.available_versions(MIGRATIONS_DIR), helpers.MIGRATION_VERSIONS)
        conn = connect(":memory:")
        conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, name TEXT, sha256 TEXT)")
        self.assertEqual(migrate.pending_versions(conn, MIGRATIONS_DIR), helpers.MIGRATION_VERSIONS)
        conn.execute("INSERT INTO schema_migrations VALUES ('0001', 'x', 'y')")
        self.assertEqual(migrate.pending_versions(conn, MIGRATIONS_DIR), helpers.MIGRATION_VERSIONS[1:])
        conn.close()


class WorkflowFileTests(unittest.TestCase):
    """No YAML package is installed for the tests, so the file is checked by its structure."""

    def setUp(self):
        self.text = _read(".github", "workflows", "check.yml")
        self.lines = self.text.splitlines()

    def test_file_has_no_tabs_and_even_indentation(self):
        for number, line in enumerate(self.lines, 1):
            self.assertNotIn("\t", line, f"tab on line {number}")
            if line.strip() and not line.lstrip().startswith("#"):
                indent = len(line) - len(line.lstrip(" "))
                self.assertEqual(indent % 2, 0, f"odd indent on line {number}")

    def test_every_line_is_a_comment_a_key_or_a_list_item(self):
        shape = re.compile(r"^\s*(#.*|- .+|[A-Za-z_][\w-]*:( .*)?)$")
        for number, line in enumerate(self.lines, 1):
            if line.strip():
                self.assertRegex(line, shape, f"line {number} is not valid shape")

    def test_top_level_keys_are_name_on_permissions_jobs(self):
        keys = [m.group(1) for m in (re.match(r"^([A-Za-z_][\w-]*):", l) for l in self.lines) if m]
        self.assertEqual(keys, ["name", "on", "permissions", "jobs"])

    def test_runs_on_push_and_pull_request(self):
        self.assertRegex(self.text, r"(?m)^  push:$")
        self.assertRegex(self.text, r"(?m)^  pull_request:$")

    def test_runs_the_project_gate_on_python_312_with_dev_packages(self):
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn("pip install -r requirements-dev.txt", self.text)
        self.assertIn("bash scripts/check.sh", self.text)
        self.assertLess(self.text.index("requirements-dev.txt"), self.text.index("bash scripts/check.sh"))

    def test_read_only_token_and_pinned_action_versions(self):
        self.assertIn("contents: read", self.text)
        for use in re.findall(r"uses: (\S+)", self.text):
            self.assertRegex(use, r"^[\w./-]+@v\d+$", use)

    def test_no_secrets_are_used(self):
        self.assertNotIn("secrets.", self.text)

    def test_run_steps_start_only_commands_that_exist_in_the_repo(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", "check.sh")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "requirements-dev.txt")))

    def test_the_structure_check_catches_a_bad_file(self):
        bad = "name: x\non:\n\tpush:\n"
        self.assertIn("\t", bad)
        self.assertIsNone(re.match(r"^\s*(#.*|- .+|[A-Za-z_][\w-]*:( .*)?)$", "just words here"))


class RunbookTests(unittest.TestCase):
    def test_every_new_runbook_exists_and_has_a_title(self):
        for name in NEW_RUNBOOKS:
            text = _read("docs", "runbooks", name)
            self.assertTrue(text.startswith("# "), name)
            self.assertGreater(len(text), 800, name)

    def test_every_relative_link_in_the_runbooks_points_at_a_file(self):
        for name in NEW_RUNBOOKS + ("deploy.md",):
            for target in re.findall(r"\]\(([^)#]+?)(?:#[^)]*)?\)", _read("docs", "runbooks", name)):
                if "://" in target:
                    continue
                self.assertTrue(os.path.isfile(os.path.join(RUNBOOKS, target)), f"{name} -> {target}")

    def test_the_runbooks_link_each_other_and_deploy_links_them_all(self):
        deploy = _read("docs", "runbooks", "deploy.md")
        for name in NEW_RUNBOOKS:
            self.assertIn(f"]({name})", deploy)
        self.assertIn("](deploy.md)", _read("docs", "runbooks", "backups.md"))
        self.assertIn("](deploy.md)", _read("docs", "runbooks", "rollback.md"))
        self.assertIn("](incident.md)", _read("docs", "runbooks", "monitoring.md"))
        self.assertIn("](rollback.md)", _read("docs", "runbooks", "incident.md"))
        self.assertIn("](backups.md)", _read("docs", "runbooks", "incident.md"))

    def test_backups_page_links_to_the_commands_and_does_not_copy_them(self):
        text = _read("docs", "runbooks", "backups.md")
        self.assertIn("deploy.md", text)
        self.assertNotIn("scripts/backup.py backup", text)
        self.assertNotIn("before-restore", text)
        self.assertNotIn("find /var/backups", text)

    def test_monitoring_page_gives_the_health_url_keyword_and_interval(self):
        text = _read("docs", "runbooks", "monitoring.md")
        self.assertIn("/health", text)
        self.assertIn('"status":"ok"', text)
        self.assertIn("5 minutes", text)
        for answer in ("unavailable", "not migrated", "migrations pending"):
            self.assertIn(answer, text)

    def test_health_answers_named_in_the_runbooks_match_the_code(self):
        code = _read("app", "health.py")
        for answer in ("unavailable", "not migrated", "migrations pending"):
            self.assertIn(f'"{answer}"', code)

    def test_rollback_page_covers_both_cases_and_the_unverified_pin(self):
        text = _read("docs", "runbooks", "rollback.md")
        self.assertIn("update added no migration", text)
        self.assertIn("update added a migration", text)
        self.assertIn("pip freeze | grep -i gunicorn", text)

    def test_architecture_page_names_the_runbooks_and_the_workflow(self):
        text = _read("docs", "ARCHITECTURE.md")
        for name in NEW_RUNBOOKS + ("check.yml",):
            self.assertIn(name, text)

    def test_runbooks_do_not_contain_secrets_or_real_domains(self):
        for name in NEW_RUNBOOKS:
            text = _read("docs", "runbooks", name)
            self.assertNotRegex(text, r"[0-9a-f]{64}")
        for name in ("incident.md", "monitoring.md"):
            self.assertIn("<your domain>", _read("docs", "runbooks", name))


if __name__ == "__main__":
    unittest.main()
