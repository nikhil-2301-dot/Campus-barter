import os
import re
import sqlite3
import tempfile
import unittest

import helpers
from helpers import add_listing, add_user, migrated_memory_db
from app import constants
from app.config import MIGRATIONS_DIR
from app.db import connect
from app.migrate import MigrationProblem, current_version, migrate
from app.seed import SeedRefused, seed
from werkzeug.security import check_password_hash

TABLES = {"users", "listings", "exchange_requests", "reports", "login_attempts", "campus_requests"}


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.db = migrated_memory_db()

    def test_all_tables_exist(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(TABLES <= names)
        self.assertIn("schema_migrations", names)

    def test_version_recorded(self):
        self.assertEqual(current_version(self.db), helpers.MIGRATION_VERSIONS[-1])

    def test_design_indexes_exist(self):
        idx = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for name in ("ix_listings_owner", "ix_listings_status_cat", "ix_listings_created",
                     "ix_requests_requester", "ix_requests_offered", "ix_reports_listing",
                     "ix_reports_reporter", "uq_requests_one_open_per_requester",
                     "uq_requests_one_accepted_per_listing", "ix_login_attempts_lookup"):
            self.assertIn(name, idx)

    def test_every_foreign_key_column_is_indexed(self):
        for table in TABLES:
            for fk in self.db.execute(f"PRAGMA foreign_key_list({table})"):  # PRAGMA cannot take ? params
                col = fk["from"]
                found = False
                for ix in self.db.execute(f"PRAGMA index_list({table})"):
                    first = self.db.execute(f"PRAGMA index_info({ix['name']})").fetchone()
                    found = found or (first is not None and first["name"] == col)
                self.assertTrue(found, f"{table}.{col} has no index")

    def test_every_table_has_id_and_timestamps_as_designed(self):
        for table in ("users", "listings", "exchange_requests", "reports"):
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            self.assertTrue({"id", "created_at", "updated_at"} <= cols, table)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(login_attempts)")}
        self.assertEqual(cols, {"id", "key", "attempted_at"})

    def test_sql_check_values_match_constants(self):
        for ok in constants.YEARS:
            add_user(self.db, email=f"{ok.lower()}@college.example", year=ok)
        uid = add_user(self.db, email="o@college.example")
        for mode in constants.MODES:
            add_listing(self.db, uid, mode=mode)
        for cond in constants.CONDITIONS:
            add_listing(self.db, uid, condition=cond)
        for st in constants.LISTING_STATUSES:
            add_listing(self.db, uid, status=st, deleted_at="2026-01-01T00:00:00.000Z" if st == "removed" else None)
        lid = add_listing(self.db, uid)
        for i, st in enumerate(constants.REQUEST_STATUSES):
            r = add_user(self.db, email=f"r{i}@college.example")
            self.db.execute("INSERT INTO exchange_requests (listing_id, requester_id, status) VALUES (?, ?, ?)",
                            (lid, r, st))
        for st in constants.REPORT_STATUSES:
            self.db.execute("INSERT INTO reports (listing_id, reporter_id, reason, status) VALUES (?, ?, 'x', ?)",
                            (lid, uid, st))


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.db = migrated_memory_db()
        self.owner = add_user(self.db, email="owner@college.example")
        self.other = add_user(self.db, email="other@college.example")
        self.listing = add_listing(self.db, self.owner)

    def bad(self, sql, params=()):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, params)

    # users
    def test_duplicate_email_is_rejected(self):
        self.bad("INSERT INTO users (email, password_hash, full_name, branch, year) "
                 "VALUES ('owner@college.example', 'x', 'n', 'b', 'SE')")

    def test_uppercase_email_is_rejected(self):
        self.bad("INSERT INTO users (email, password_hash, full_name, branch, year) "
                 "VALUES ('Up@college.example', 'x', 'n', 'b', 'SE')")

    def test_bad_year_is_rejected(self):
        self.bad("INSERT INTO users (email, password_hash, full_name, branch, year) "
                 "VALUES ('y@college.example', 'x', 'n', 'b', 'XX')")

    def test_admin_flag_only_accepts_0_or_1(self):
        self.bad("UPDATE users SET is_admin = 2 WHERE id = ?", (self.owner,))

    def test_new_users_are_not_admin_or_suspended_by_default(self):
        row = self.db.execute("SELECT is_admin, is_suspended FROM users WHERE id = ?", (self.owner,)).fetchone()
        self.assertEqual((row["is_admin"], row["is_suspended"]), (0, 0))

    def test_required_user_fields_cannot_be_null(self):
        self.bad("INSERT INTO users (email, password_hash, full_name, branch, year) "
                 "VALUES ('n@college.example', NULL, 'n', 'b', 'SE')")

    # listings
    def test_listing_needs_existing_owner(self):
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, 9999)

    def test_bad_mode_condition_and_status_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, self.owner, mode="sell")
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, self.owner, condition="broken")
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, self.owner, status="sold")

    def test_removed_status_and_deleted_at_must_agree(self):
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, self.owner, status="removed")  # removed but no deleted_at
        with self.assertRaises(sqlite3.IntegrityError):
            add_listing(self.db, self.owner, status="available", deleted_at="2026-01-01T00:00:00.000Z")
        add_listing(self.db, self.owner, status="removed", deleted_at="2026-01-01T00:00:00.000Z")

    def test_category_is_not_constrained_by_the_database(self):
        add_listing(self.db, self.owner, category="Whatever the config says later")

    def test_owner_with_listings_cannot_be_hard_deleted(self):
        self.bad("DELETE FROM users WHERE id = ?", (self.owner,))

    def test_listing_with_requests_cannot_be_hard_deleted(self):
        self.db.execute("INSERT INTO exchange_requests (listing_id, requester_id) VALUES (?, ?)",
                        (self.listing, self.other))
        self.bad("DELETE FROM listings WHERE id = ?", (self.listing,))

    # exchange requests
    def req(self, listing, requester, status="pending", offered=None):
        return self.db.execute(
            "INSERT INTO exchange_requests (listing_id, requester_id, offered_listing_id, status) "
            "VALUES (?, ?, ?, ?)", (listing, requester, offered, status))

    def test_second_open_request_by_same_user_is_rejected(self):
        self.req(self.listing, self.other)
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, self.other)

    def test_pending_and_accepted_both_count_as_open(self):
        self.req(self.listing, self.other, "accepted")
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, self.other, "pending")

    def test_user_may_ask_again_after_decline_cancel_or_complete(self):
        for st in ("declined", "cancelled", "completed"):
            self.req(self.listing, self.other, st)
        self.req(self.listing, self.other, "pending")

    def test_different_users_can_each_have_a_pending_request(self):
        third = add_user(self.db, email="third@college.example")
        self.req(self.listing, self.other)
        self.req(self.listing, third)

    def test_only_one_accepted_request_per_listing(self):
        third = add_user(self.db, email="third@college.example")
        self.req(self.listing, self.other, "accepted")
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, third, "accepted")

    def test_accepted_on_a_different_listing_is_fine(self):
        second = add_listing(self.db, self.owner, title="Second")
        self.req(self.listing, self.other, "accepted")
        self.req(second, self.other, "accepted")

    def test_cannot_offer_the_listing_being_requested(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, self.other, offered=self.listing)

    def test_offered_listing_must_exist(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, self.other, offered=424242)

    def test_offered_listing_is_optional(self):
        self.req(self.listing, self.other, offered=None)

    def test_bad_request_status_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.req(self.listing, self.other, "maybe")

    # reports and login attempts
    def test_report_needs_real_listing_and_reporter(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (999, ?, 'x')", (self.other,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, 999, 'x')", (self.listing,))

    def test_new_report_starts_open(self):
        self.db.execute("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, 'spam')",
                        (self.listing, self.other))
        self.assertEqual(self.db.execute("SELECT status FROM reports").fetchone()[0], "open")

    def test_login_attempts_accept_any_key_and_stamp_time(self):
        self.db.execute('INSERT INTO login_attempts ("key") VALUES (?)', ("someone@college.example",))
        row = self.db.execute("SELECT attempted_at FROM login_attempts").fetchone()
        self.assertRegex(row[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_database_foreign_keys_are_consistent_after_inserts(self):
        self.req(self.listing, self.other)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])


class TimestampTests(unittest.TestCase):
    def test_updated_at_moves_on_update_but_created_at_does_not(self):
        db = migrated_memory_db()
        uid = add_user(db)
        lid = add_listing(db, uid)
        db.execute("UPDATE listings SET created_at='2020-01-01T00:00:00.000Z', "
                   "updated_at='2020-01-01T00:00:00.000Z' WHERE id=?", (lid,))
        db.execute("UPDATE listings SET title='New title' WHERE id=?", (lid,))
        row = db.execute("SELECT created_at, updated_at FROM listings WHERE id=?", (lid,)).fetchone()
        self.assertEqual(row["created_at"], "2020-01-01T00:00:00.000Z")
        self.assertGreater(row["updated_at"], "2020-01-01T00:00:00.000Z")

    def test_updated_at_trigger_exists_for_every_dated_table(self):
        db = migrated_memory_db()
        triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertEqual(triggers, {"trg_users_updated", "trg_listings_updated",
                                    "trg_requests_updated", "trg_reports_updated"})

    def test_explicit_updated_at_value_is_respected(self):
        db = migrated_memory_db()
        uid = add_user(db)
        db.execute("UPDATE users SET updated_at='2021-05-05T00:00:00.000Z' WHERE id=?", (uid,))
        self.assertEqual(db.execute("SELECT updated_at FROM users WHERE id=?", (uid,)).fetchone()[0],
                         "2021-05-05T00:00:00.000Z")


class MigrationRunnerTests(helpers.TempDirMixin, unittest.TestCase):
    def write(self, folder, name, sql):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(sql)

    def test_running_twice_applies_nothing_the_second_time(self):
        db = connect(":memory:")
        self.assertEqual(migrate(db, MIGRATIONS_DIR), helpers.MIGRATION_VERSIONS)
        self.assertEqual(migrate(db, MIGRATIONS_DIR), [])

    def test_migration_0003_gives_legacy_rows_placeholders_then_enforces_uniqueness(self):
        import shutil
        folder = self.make_tmp()
        for name in ("0001_init.sql", "0002_user_profile.sql"):
            shutil.copy(os.path.join(MIGRATIONS_DIR, name), folder)
        db = connect(":memory:")
        migrate(db, folder)
        for email in ("a@college.example", "b@college.example"):
            db.execute("INSERT INTO users (email, password_hash, full_name, branch, year) "
                       "VALUES (?, 'x', 'N N', 'IT', 'SE')", (email,))
        self.assertEqual(migrate(db, MIGRATIONS_DIR), helpers.MIGRATION_VERSIONS[2:])
        numbers = [r[0] for r in db.execute("SELECT enrollment_no FROM users ORDER BY id")]
        self.assertEqual(numbers, ["LEGACY-1", "LEGACY-2"])
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE users SET enrollment_no = 'LEGACY-1' WHERE id = 2")

    def test_runner_restores_the_connection_mode(self):
        db = connect(":memory:")
        before = db.isolation_level
        migrate(db, MIGRATIONS_DIR)
        self.assertEqual(db.isolation_level, before)

    def test_new_numbered_file_is_applied_after_the_old_ones(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        db = connect(":memory:")
        migrate(db, folder)
        self.write(folder, "0002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
        self.assertEqual(migrate(db, folder), ["0002"])
        self.assertEqual(current_version(db), "0002")

    def test_editing_an_applied_migration_is_refused(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        db = connect(":memory:")
        migrate(db, folder)
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT);")
        with self.assertRaises(MigrationProblem) as cm:
            migrate(db, folder)
        self.assertIn("edited", str(cm.exception))

    def test_deleting_an_applied_migration_file_is_refused(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        db = connect(":memory:")
        migrate(db, folder)
        os.remove(os.path.join(folder, "0001_a.sql"))
        with self.assertRaises(MigrationProblem):
            migrate(db, folder)

    def test_broken_migration_leaves_no_partial_changes(self):
        folder = self.make_tmp()
        self.write(folder, "0001_bad.sql",
                   "CREATE TABLE half (id INTEGER PRIMARY KEY);\nINSERT INTO nowhere VALUES (1);\n")
        db = connect(":memory:")
        with self.assertRaises(MigrationProblem):
            migrate(db, folder)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("half", tables)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)

    def test_duplicate_version_numbers_are_refused(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER);")
        self.write(folder, "0001_b.sql", "CREATE TABLE b (id INTEGER);")
        with self.assertRaises(MigrationProblem):
            migrate(connect(":memory:"), folder)

    def test_files_that_do_not_match_the_naming_rule_are_ignored(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER);")
        self.write(folder, "notes.sql", "THIS IS NOT SQL")
        self.write(folder, "2_short.sql", "THIS IS NOT SQL")
        self.assertEqual(migrate(connect(":memory:"), folder), ["0001"])

    def test_trailing_comment_after_last_statement_is_fine(self):
        folder = self.make_tmp()
        self.write(folder, "0001_a.sql", "CREATE TABLE a (id INTEGER);\n-- done\n")
        self.assertEqual(migrate(connect(":memory:"), folder), ["0001"])

    def test_cli_migrate_is_idempotent_on_a_real_file(self):
        app = helpers.make_app(self.make_tmp(), migrated=False)
        runner = app.test_cli_runner()
        first = runner.invoke(args=["migrate"])
        second = runner.invoke(args=["migrate"])
        self.assertEqual(first.exit_code, 0)
        self.assertIn("applied: 0001", first.output)
        self.assertIn("up to date", second.output)

    def test_existing_data_survives_a_second_migrate_run(self):
        app = helpers.make_app(self.make_tmp())
        conn = connect(app.config["DATABASE_PATH"])
        add_user(conn)
        conn.commit()
        migrate(conn, MIGRATIONS_DIR)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        conn.close()


class SeedTests(helpers.TempDirMixin, unittest.TestCase):
    def test_seed_creates_demo_data_and_hashes_passwords(self):
        db = migrated_memory_db()
        created, pw = seed(db, env="development", domain="college.example", password="demo-pass-123")
        self.assertTrue(created)
        self.assertEqual(pw, "demo-pass-123")
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # table names are literals here
                  for t in ("users", "listings", "exchange_requests", "reports")}
        self.assertEqual(counts, {"users": 4, "listings": 6, "exchange_requests": 2, "reports": 1})
        row = db.execute("SELECT password_hash FROM users LIMIT 1").fetchone()
        self.assertNotIn("demo-pass-123", row["password_hash"])
        self.assertTrue(check_password_hash(row["password_hash"], "demo-pass-123"))

    def test_seeded_data_is_consistent(self):
        db = migrated_memory_db()
        seed(db, env="development", domain="college.example")
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        reserved = db.execute("SELECT id FROM listings WHERE status='reserved'").fetchall()
        accepted = db.execute("SELECT listing_id FROM exchange_requests WHERE status='accepted'").fetchall()
        self.assertEqual({r[0] for r in reserved}, {r[0] for r in accepted})
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0], 1)

    def test_seed_covers_every_mode_and_several_categories(self):
        db = migrated_memory_db()
        seed(db, env="development", domain="college.example")
        modes = {r[0] for r in db.execute("SELECT DISTINCT mode FROM listings")}
        cats = {r[0] for r in db.execute("SELECT DISTINCT category FROM listings")}
        self.assertEqual(modes, set(constants.MODES))
        self.assertTrue(cats <= set(constants.CATEGORIES))
        self.assertGreaterEqual(len(cats), 4)

    def test_seed_twice_changes_nothing(self):
        db = migrated_memory_db()
        seed(db, env="development", domain="college.example")
        created, pw = seed(db, env="development", domain="college.example")
        self.assertEqual((created, pw), (False, None))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4)

    def test_seed_is_refused_in_production(self):
        db = migrated_memory_db()
        with self.assertRaises(SeedRefused):
            seed(db, env="production", domain="college.example")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_random_password_is_generated_when_none_given(self):
        a = seed(migrated_memory_db(), env="development", domain="college.example")[1]
        b = seed(migrated_memory_db(), env="development", domain="college.example")[1]
        self.assertGreaterEqual(len(a), 10)
        self.assertNotEqual(a, b)

    def test_half_finished_seed_is_rolled_back(self):
        db = migrated_memory_db()
        add_user(db, email="ravi.te@college.example")  # collides with the 2nd demo user
        with self.assertRaises(sqlite3.IntegrityError):
            seed(db, env="development", domain="college.example")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 0)

    def test_cli_seed_prints_password_once_and_is_idempotent(self):
        app = helpers.make_app(self.make_tmp())
        runner = app.test_cli_runner()
        first = runner.invoke(args=["seed"])
        self.assertEqual(first.exit_code, 0)
        self.assertIn("Demo password", first.output)
        second = runner.invoke(args=["seed"])
        self.assertIn("already present", second.output)
        self.assertNotIn("Demo password", second.output)

    def test_cli_seed_uses_the_configured_password(self):
        app = helpers.make_app(self.make_tmp(), SEED_PASSWORD="from-env-pass")
        out = app.test_cli_runner().invoke(args=["seed"]).output
        self.assertIn("from-env-pass", out)

    def test_cli_seed_refuses_production(self):
        app = helpers.make_app(self.make_tmp(), APP_ENV="production")
        result = app.test_cli_runner().invoke(args=["seed"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("disabled in production", result.output)


class SqlSafetyTests(unittest.TestCase):
    """Static rule from CLAUDE.md: raw SQL with ? parameters only, no f-string SQL."""
    SQL_WORDS = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b", re.I)

    def test_no_fstring_or_percent_or_format_sql_in_app_code(self):
        root = os.path.join(helpers.ROOT, "app")
        offenders = []
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]
                    is_f = re.search(r"""\bf["']""", code) is not None
                    formatted = re.search(r"""["']\s*(%|\.format\()""", code) is not None
                    if self.SQL_WORDS.search(code) and (is_f or formatted):
                        offenders.append(f"app/{name}:{n}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
