"""Email rules after the college-domain restriction was removed: dots, Gmail spellings, any provider."""
import sqlite3
import unittest

from helpers import migrated_memory_db  # noqa: E402  (puts the repo root on sys.path)
from app import accounts  # noqa: E402


def _make(conn, email, enrol):
    return accounts.create_user(conn, email=email, password="Str0ng-pass-77", full_name="Asha Patil",
                                branch="IT", year="SE", college_name="Some College", enrollment_no=enrol)


class LocalPartTests(unittest.TestCase):
    def test_double_dot_is_refused(self):
        self.assertIsNotNone(accounts.check_email("a..b@gmail.com"))

    def test_trailing_dot_is_refused(self):
        self.assertIsNotNone(accounts.check_email("ab.@gmail.com"))

    def test_leading_dot_is_refused(self):
        self.assertIsNotNone(accounts.check_email(".ab@gmail.com"))

    def test_normal_addresses_are_accepted(self):
        for email in ("a.b@gmail.com", "ab+tag@gmail.com", "x@yahoo.co.in", "n@sub.domain.edu", "a_b-c@outlook.com"):
            self.assertIsNone(accounts.check_email(email), email)


class InboxKeyTests(unittest.TestCase):
    def test_gmail_spellings_share_one_key(self):
        keys = {accounts.inbox_key(e) for e in ("a.b@gmail.com", "ab@gmail.com", "ab+x@gmail.com", "a.b+y@googlemail.com")}
        self.assertEqual(keys, {"ab@gmail.com"})

    def test_other_providers_keep_their_dots(self):
        self.assertEqual(accounts.inbox_key("a.b@college.edu"), "a.b@college.edu")
        self.assertNotEqual(accounts.inbox_key("a.b@college.edu"), accounts.inbox_key("ab@college.edu"))


class SameInboxTests(unittest.TestCase):
    def setUp(self):
        self.conn = migrated_memory_db()
        self.addCleanup(self.conn.close)
        _make(self.conn, "ab@gmail.com", "ENR20261")

    def test_dotted_spelling_is_refused(self):
        with self.assertRaises(accounts.EmailTaken):
            _make(self.conn, "a.b@gmail.com", "ENR20262")

    def test_plus_tag_spelling_is_refused(self):
        with self.assertRaises(accounts.EmailTaken):
            _make(self.conn, "ab+swap@gmail.com", "ENR20263")

    def test_googlemail_spelling_is_refused(self):
        with self.assertRaises(accounts.EmailTaken):
            _make(self.conn, "a.b@googlemail.com", "ENR20264")

    def test_a_different_inbox_is_accepted(self):
        _make(self.conn, "abc@gmail.com", "ENR20265")
        _make(self.conn, "ab@yahoo.com", "ENR20266")
        count = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 3)

    def test_non_gmail_dots_are_different_people(self):
        _make(self.conn, "a.b@college.edu", "ENR20267")
        _make(self.conn, "ab@college.edu", "ENR20268")

    def test_refused_spelling_leaves_no_row(self):
        try:
            _make(self.conn, "a.b@gmail.com", "ENR20269")
        except accounts.EmailTaken:
            pass
        rows = self.conn.execute("SELECT email FROM users").fetchall()
        self.assertEqual([r["email"] for r in rows], ["ab@gmail.com"])


if __name__ == "__main__":
    unittest.main()
