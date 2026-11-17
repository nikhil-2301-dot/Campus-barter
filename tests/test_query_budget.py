"""C17: query-count budget per page, and a guard against queries that grow with the data (N+1).

How a page is counted: `app.db.connect` is replaced by a wrapper that opens the real connection and
attaches a SQLite trace callback; only SELECT / INSERT / UPDATE / DELETE / WITH statements count (the
module also traces PRAGMA, BEGIN and COMMIT, which are ignored). The pages come from
scripts/ui_audit.py (`page_plan`), so there is no second page list. Every endpoint is measured as every
actor listed for it; the HIGHEST count is compared with the ceiling in CEILINGS.

Ceiling rule (C17 decision): measured maximum + 1 for pages that need 3 queries or fewer,
measured maximum + 2 for pages that need more. A page that goes over its ceiling needs a reason:
fix the page, do not raise the number without a note in HANDOFF.md.
NOTE: scripts/check.sh gates on three words in the test log, so test names and docstrings avoid them.
"""
import sqlite3
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the project root on sys.path)
from app import db as app_db
from scripts import ui_audit

COUNTED = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")

# endpoint -> most SQL statements one request to it may run (any role, any row of the page plan)
CEILINGS = {
    "admin.campus_list": 2, "admin.dashboard": 4, "admin.listing_remove": 3, "admin.report_detail": 3, "admin.report_list": 3,
    "admin.user_list": 4, "admin.user_suspend": 3,
    "auth.login": 2, "auth.profile": 2, "auth.signup": 2,
    "campuses.new": 1,
    "design.index": 2,
    "exchange.change": 3, "exchange.detail": 4, "exchange.received": 3, "exchange.sent": 3,
    "listings.browse": 4, "listings.create": 2, "listings.delete": 3, "listings.edit": 3,
    "listings.my_listings": 3, "listings.view": 7,
    "main.index": 2,
    "photos.serve": 2,
}


class QueryLog:
    """Trace callback: counts the statements that read or write data."""

    def __init__(self):
        self.count = 0

    def __call__(self, statement):
        if statement.lstrip().upper().startswith(COUNTED):
            self.count += 1


def get_counted(app, client, path):
    """One GET with the statements counted. Returns (count, response)."""
    real = app_db.connect
    log = QueryLog()

    def counting(db_path):
        conn = real(db_path)
        conn.set_trace_callback(log)
        return conn

    with mock.patch.object(app_db, "connect", counting):
        response = client.get(path)
    return log.count, response


def measure_plan(app, ids):
    """{endpoint: (highest count, path, actor)} over every row and actor of the audit page plan."""
    adapter = app.url_map.bind("localhost")
    clients = {}
    best = {}
    for row in ui_audit.page_plan(ids):
        try:
            endpoint = adapter.match(row.path.split("?")[0], method="GET")[0]
        except Exception:  # the 404 row and the POST-only row match no GET page
            continue
        if endpoint.startswith("audit_"):  # test-only routes of the scratch app
            continue
        for actor in row.actors:
            if actor not in clients:
                clients[actor] = app.test_client()
                ui_audit._login(app, clients[actor], actor, ids)
            count, _ = get_counted(app, clients[actor], row.path)
            if endpoint not in best or count > best[endpoint][0]:
                best[endpoint] = (count, row.path, actor)
    return best


# --------------------------------------------------------------------------- growth data

def _add_rows(app, ids):
    """Many more rows of every kind the lists show. Plain sqlite, not the app, so no rate limit applies."""
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        ravi = ids["user"]["ravi.te"]
        drafter = ids["listing"]["drafter"]
        for i in range(30):  # 30 listings (browse, my listings), all owned by ravi
            conn.execute("INSERT INTO listings (owner_id, title, category, condition, mode) "
                         "VALUES (?, ?, 'Books', 'good', 'give')", (ravi, f"Growth listing {i + 1}"))
        new_users = []
        for i in range(30):  # 30 users (admin user list)
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, full_name, branch, year, college_name, enrollment_no) "
                "VALUES (?, 'x', ?, 'Civil', 'SE', 'Demo Engineering College', ?)",
                (f"growth{i}@college.example", f"Growth User {i}", f"GRW{i:04d}"))
            new_users.append(cur.lastrowid)
        for uid in new_users[:8]:  # 8 pending requests for one of ravi's listings (requests received)
            conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, message) VALUES (?, ?, 'hello')",
                         (drafter, uid))
        listing = ids["listing"]["math"]
        for uid in new_users[8:16]:  # 8 open reports (admin dashboard and report list)
            conn.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, 'Growth report reason text')",
                         (listing, uid))
        conn.commit()
    finally:
        conn.close()


# (name, path, actor, text that must show up once the rows exist, so the guard cannot pass on an empty page)
GROWTH_PAGES = (
    ("browse", "/listings/?q=Growth", "asha", "Growth listing"),  # empty before, a full page of 10 after
    ("requests received", "/requests/received", "ravi", "Growth User"),
    ("my listings", "/listings/mine", "ravi", "Growth listing"),
    ("admin reports", "/admin/reports", "admin", "Growth User"),
    ("admin users", "/admin/users", "admin", "Growth User"),
)


def setUpModule():
    global _tmp, APP, IDS, MEASURED, BEFORE, AFTER, TEXT_AFTER
    _tmp = tempfile.TemporaryDirectory()
    APP, IDS = ui_audit.build_audit_app(_tmp.name)
    MEASURED = measure_plan(APP, IDS)

    def measure_growth_pages():
        counts, texts = {}, {}
        for name, path, actor, _marker in GROWTH_PAGES:
            client = APP.test_client()
            ui_audit._login(APP, client, actor, IDS)
            count, response = get_counted(APP, client, path)
            assert response.status_code == 200, (name, response.status_code)
            counts[name] = count
            texts[name] = response.get_data(as_text=True)
        return counts, texts

    BEFORE, _ = measure_growth_pages()
    _add_rows(APP, IDS)
    AFTER, TEXT_AFTER = measure_growth_pages()


def tearDownModule():
    from app import logs
    logs._remove_ours(APP.logger)
    _tmp.cleanup()


class CeilingTableTests(unittest.TestCase):
    def test_every_page_in_the_plan_has_a_ceiling(self):
        missing = sorted(set(MEASURED) - set(CEILINGS))
        self.assertEqual(missing, [], "add a ceiling for these endpoints: " + ", ".join(missing))

    def test_the_table_lists_no_endpoint_that_is_gone(self):
        stale = sorted(set(CEILINGS) - set(MEASURED))
        self.assertEqual(stale, [], "remove these endpoints from CEILINGS: " + ", ".join(stale))

    def test_every_page_stays_within_its_ceiling(self):
        for endpoint, (count, path, actor) in sorted(MEASURED.items()):
            with self.subTest(endpoint=endpoint):
                ceiling = CEILINGS.get(endpoint)
                self.assertIsNotNone(ceiling, endpoint + " has no ceiling")
                self.assertLessEqual(count, ceiling,
                                     f"{endpoint} ran {count} statements for {path} as {actor}; the ceiling is {ceiling}")

    def test_no_ceiling_is_looser_than_the_rule_allows(self):
        for endpoint, (count, _path, _actor) in sorted(MEASURED.items()):
            with self.subTest(endpoint=endpoint):
                room = 1 if count <= 3 else 2
                self.assertLessEqual(CEILINGS.get(endpoint, 0), count + room,
                                     f"{endpoint}: ceiling above measured {count} + {room}")

    def test_the_counter_sees_the_statements_of_a_real_page(self):
        # the counter really sees statements: a page that reads the database is never counted as zero
        self.assertGreater(MEASURED["listings.browse"][0], 0)
        self.assertGreater(MEASURED["listings.view"][0], 1)


class CounterTests(unittest.TestCase):
    def test_only_data_statements_are_counted(self):
        log = QueryLog()
        for sql in ("PRAGMA foreign_keys = ON", "BEGIN IMMEDIATE", "COMMIT", "SELECT 1", "  select 2",
                    "INSERT INTO t VALUES (1)", "UPDATE t SET a = 1", "DELETE FROM t", "WITH x AS (SELECT 1) SELECT * FROM x"):
            log(sql)
        self.assertEqual(log.count, 6)


class GrowthGuardTests(unittest.TestCase):
    """The number of statements must not depend on how many rows a list shows."""

    def test_the_new_rows_really_show_up(self):
        for name, _path, _actor, marker in GROWTH_PAGES:
            with self.subTest(page=name):
                self.assertIn(marker, TEXT_AFTER[name])

    def test_browse_runs_the_same_statements_with_more_data(self):
        self.assertEqual(AFTER["browse"], BEFORE["browse"])

    def test_requests_received_runs_the_same_statements_with_more_data(self):
        self.assertEqual(AFTER["requests received"], BEFORE["requests received"])

    def test_my_listings_runs_the_same_statements_with_more_data(self):
        self.assertEqual(AFTER["my listings"], BEFORE["my listings"])

    def test_admin_reports_runs_the_same_statements_with_more_data(self):
        self.assertEqual(AFTER["admin reports"], BEFORE["admin reports"])

    def test_admin_users_runs_the_same_statements_with_more_data(self):
        self.assertEqual(AFTER["admin users"], BEFORE["admin users"])


if __name__ == "__main__":
    unittest.main()
