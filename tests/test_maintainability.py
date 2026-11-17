"""C22: guards that keep the code layered and the project set-up documented.

Routes hold no SQL, modules do not reach into each other's private names, requirements are pinned and
cover every outside package the app imports, VERSION matches the changelog, and the docs name every module.
(Words the check gate rejects must not appear in names or docstrings here.)
"""
import ast
import os
import re
import sys
import unittest
from importlib import metadata

import helpers
from helpers import ROOT, add_listing, add_user, make_app

from app import listing_service
from app.db import connect
from app.permissions import can

APP_DIR = os.path.join(ROOT, "app")
# files that are routes: they may call services but never touch SQL themselves
ROUTE_FILES = ("listings", "exchange", "admin", "auth", "campuses", "photos", "reports", "main", "design")
SQL_CALLS = {"execute", "executemany", "executescript"}


def _tree(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), path)


def _app_files():
    return sorted(n for n in os.listdir(APP_DIR) if n.endswith(".py"))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _pins(name):
    """{normalised package name: version} from a requirements file (comments and -r lines ignored)."""
    pins = {}
    for line in _read(name).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r")):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\d+(?:\.\d+)*)", line)
        assert match, "not an exact pin: " + line
        pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


class LayeringTests(unittest.TestCase):
    def test_route_files_contain_no_sql_calls(self):
        for name in ROUTE_FILES:
            for node in ast.walk(_tree(os.path.join(APP_DIR, name + ".py"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, SQL_CALLS, f"{name}.py line {node.lineno} runs SQL")

    def test_no_module_imports_a_private_name_from_another_module(self):
        for name in _app_files():
            for node in ast.walk(_tree(os.path.join(APP_DIR, name))):
                if isinstance(node, ast.ImportFrom) and node.level >= 1:
                    for alias in node.names:
                        private = alias.name.startswith("_") and not alias.name.startswith("__")
                        self.assertFalse(private, f"{name} line {node.lineno} imports {alias.name}")

    def test_services_share_one_transaction_helper(self):
        from app import admin_service, exchange_service, report_service, tx
        for module in (admin_service, exchange_service, report_service):
            self.assertIs(module.Tx, tx.Tx)
            self.assertIs(module.NOW, tx.NOW)


class PermissionAnswerTests(unittest.TestCase):
    def test_restore_listing_answers_with_a_real_boolean(self):
        removed = {"owner_id": 1, "status": "removed"}
        admin = {"id": 9, "is_admin": 1, "is_suspended": 0}
        student = {"id": 2, "is_admin": 0, "is_suspended": 0}
        self.assertIs(can("restore_listing", removed, admin), True)
        self.assertIs(can("restore_listing", removed, student), False)
        self.assertIs(can("restore_listing", removed, None), False)

    def test_simple_answers_are_booleans_for_every_kind_of_user(self):
        users = [None, {"id": 2, "is_admin": 0, "is_suspended": 0}, {"id": 3, "is_admin": 0, "is_suspended": 1},
                 {"id": 9, "is_admin": 1, "is_suspended": 0}, {"id": 8, "is_admin": 1, "is_suspended": 1}]
        for user in users:
            for action in ("create_listing", "view_admin", "restore_listing"):
                self.assertIs(type(can(action, {"owner_id": 1, "status": "removed"}, user)), bool, action)


class ListingServiceTests(unittest.TestCase, helpers.TempDirMixin):
    def setUp(self):
        self.app = make_app(self.make_tmp())
        self.conn = connect(self.app.config["DATABASE_PATH"])
        self.addCleanup(self.conn.close)
        self.owner = add_user(self.conn, "owner@college.example")
        self.other = add_user(self.conn, "other@college.example")
        self.conn.commit()

    DATA = {"title": "Mini drafter", "category": "Mini drafter", "condition": "good", "mode": "swap",
            "description": "Barely used", "wanted_in_return": "Notes"}

    def test_create_then_read_then_update(self):
        new_id = listing_service.create_listing(self.conn, self.owner, self.DATA)
        row = listing_service.get_listing(self.conn, new_id)
        self.assertEqual((row["title"], row["owner_name"], row["status"]), ("Mini drafter", "Test User", "available"))
        listing_service.update_listing(self.conn, new_id, dict(self.DATA, title="Drafter v2", wanted_in_return=None))
        row = listing_service.get_listing(self.conn, new_id)
        self.assertEqual((row["title"], row["wanted_in_return"]), ("Drafter v2", None))

    def test_unknown_listing_is_none(self):
        self.assertIsNone(listing_service.get_listing(self.conn, 99999))

    def test_browse_shows_only_available_and_clamps_the_page(self):
        for i in range(listing_service.PAGE_SIZE + 3):
            add_listing(self.conn, self.owner, f"Item {i}")
        add_listing(self.conn, self.owner, "Hidden", status="reserved")
        self.conn.commit()
        rows, total, page, pages = listing_service.browse(self.conn, page=50)
        self.assertEqual((total, page, pages), (listing_service.PAGE_SIZE + 3, 2, 2))
        self.assertEqual(len(rows), 3)
        self.assertNotIn("Hidden", [r["title"] for r in rows])

    def test_search_treats_percent_and_underscore_as_plain_text(self):
        add_listing(self.conn, self.owner, "100% cotton coat")
        add_listing(self.conn, self.owner, "Plain coat")
        self.conn.commit()
        rows, total, _, _ = listing_service.browse(self.conn, q="100%")
        self.assertEqual([r["title"] for r in rows], ["100% cotton coat"])
        self.assertEqual(listing_service.like_escape("a_b%c\\"), "a\\_b\\%c\\\\")

    def test_offerable_and_own_lists(self):
        add_listing(self.conn, self.owner, "B item")
        add_listing(self.conn, self.owner, "A item")
        add_listing(self.conn, self.owner, "Taken", status="reserved")
        add_listing(self.conn, self.other, "Not mine")
        self.conn.commit()
        offerable = listing_service.offerable_by(self.conn, self.owner)
        self.assertEqual([r["title"] for r in offerable], ["A item", "B item"])
        own = listing_service.listings_of(self.conn, self.owner)
        self.assertEqual(len(own), 3)
        self.assertGreaterEqual(own[0]["id"], own[-1]["id"])


class SetUpTests(unittest.TestCase):
    def test_every_requirement_is_an_exact_pin(self):
        self.assertTrue(_pins("requirements.txt"))
        self.assertTrue(_pins("requirements-dev.txt"))

    def test_requirements_cover_every_outside_package_the_app_imports(self):
        owners = metadata.packages_distributions()
        pinned = _pins("requirements.txt")
        needed = set()
        for name in _app_files():
            for node in ast.walk(_tree(os.path.join(APP_DIR, name))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                for module in modules:
                    top = module.split(".")[0]
                    if top not in sys.stdlib_module_names and top != "app":
                        needed.update(owners.get(top, [top]))
        self.assertTrue(needed)
        for dist in needed:
            self.assertIn(dist.lower().replace("_", "-"), pinned, dist + " is imported but not pinned")

    def test_dev_requirements_include_the_runtime_file(self):
        self.assertIn("-r requirements.txt", _read("requirements-dev.txt"))

    def test_version_matches_the_newest_changelog_entry(self):
        version = _read("VERSION").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        headings = re.findall(r"^## (\d+\.\d+\.\d+)", _read("CHANGELOG.md"), re.M)
        self.assertEqual(headings[0], version)

    def test_architecture_page_names_every_module(self):
        text = _read("docs", "ARCHITECTURE.md")
        for name in _app_files():
            self.assertIn(name, text, name + " is not described in docs/ARCHITECTURE.md")

    def test_every_recipe_exists_is_linked_and_ends_with_the_gate(self):
        recipes = ("add-an-item-type", "add-a-listing-field", "add-a-page", "add-an-admin-action", "upgrade-flask")
        architecture = _read("docs", "ARCHITECTURE.md")
        for recipe in recipes:
            path = os.path.join("docs", "how-to", recipe + ".md")
            self.assertIn(path, architecture)
            self.assertIn("check.sh", _read(path))


if __name__ == "__main__":
    unittest.main()
