"""C13: reporting a listing (student side) and the admin pages as built from components:
one h1, labels, captions, the states (success / empty / problem), escaped text, no inline style or handler,
destructive actions with both the dialog button and the plain link. There is no loading state: the pages
are built on the server in one or two queries.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import re
import sqlite3
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the project on the path)
from app import report_service
from test_admin import FROZEN, REASON, STRANGER, AdminWorld
from test_auth import DOMAIN
from test_browse_ui import ADMIN, ASKER, OWNER, SCRIPT, page


class ReportingTests(AdminWorld):
    def setUp(self):
        super().setUp()
        self.sql("DELETE FROM reports")
        self.student = self.client_for(STRANGER)

    def report(self, reason=REASON, lid=None, client=None):
        lid = lid or self.lid
        return self.post(f"/listings/{lid}/report", {"reason": reason}, client=client or self.student,
                         page=f"/listings/{lid}")

    def count(self):
        return self.db_rows("SELECT COUNT(*) FROM reports")[0][0]

    def test_a_valid_report_is_stored_and_the_student_is_sent_back(self):
        r = self.report()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], f"/listings/{self.lid}")
        row = self.db_rows("SELECT listing_id, reporter_id, reason, status FROM reports")[0]
        self.assertEqual(tuple(row), (self.lid, self.target_id, REASON, "open"))
        self.assertIn("Report sent", self.student.get(f"/listings/{self.lid}").get_data(as_text=True))

    def test_reason_too_short_is_refused_with_an_inline_problem_and_text_kept(self):
        r = self.report("too short")
        self.assertEqual(r.status_code, 400)
        doc = page(r.get_data(as_text=True))
        self.assertEqual(doc.select_one("#f-reason").get_text(), "too short")
        self.assertIn("at least 10", doc.select_one("#p-reason").get_text())
        self.assertEqual(self.count(), 0)

    def test_reason_too_long_is_refused(self):
        r = self.report("x" * (report_service.REASON_MAX + 1))
        self.assertEqual(r.status_code, 400)
        self.assertIn("500 characters or fewer", r.get_data(as_text=True))
        self.assertEqual(self.count(), 0)

    def test_spaces_do_not_count_towards_the_minimum(self):
        self.assertEqual(self.report("   a   b   ").status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_the_owner_cannot_report_their_own_listing(self):
        owner = self.client_for(OWNER)
        self.assertEqual(self.report(client=owner).status_code, 403)
        self.assertEqual(self.count(), 0)

    def test_a_removed_listing_cannot_be_reported(self):
        self.sql("UPDATE listings SET status = 'removed', deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                 (self.lid,))
        self.assertEqual(self.report().status_code, 404)
        self.assertEqual(self.count(), 0)

    def test_a_guest_is_sent_to_login(self):
        r = self.post(f"/listings/{self.lid}/report", {"reason": REASON}, page="/login")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].startswith("/login"))
        self.assertEqual(self.count(), 0)

    def test_an_admin_cannot_report(self):
        admin = self.client_for(ADMIN)
        self.assertEqual(self.report(client=admin).status_code, 403)
        self.assertEqual(self.count(), 0)

    def test_a_second_open_report_is_refused(self):
        self.report()
        r = self.report("Another long enough reason.")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.count(), 1)
        self.assertIn("already reported", self.student.get(f"/listings/{self.lid}").get_data(as_text=True))

    def test_a_closed_report_does_not_block_a_new_one(self):
        self.report()
        self.sql("UPDATE reports SET status = 'dismissed'")
        self.assertEqual(self.report("A new and different reason.").status_code, 303)
        self.assertEqual(self.count(), 2)

    def test_a_report_on_listing_a_does_not_block_listing_b(self):
        other = self.seed(1, prefix="Other")[0]
        self.report()
        self.assertEqual(self.report(lid=other).status_code, 303)
        self.assertEqual(self.count(), 2)

    def test_the_form_is_hidden_from_owner_guest_and_admin_and_replaced_after_reporting(self):
        def has_form(client):
            doc = page(client.get(f"/listings/{self.lid}").get_data(as_text=True))
            return doc.select_one(f"form[action='/listings/{self.lid}/report']") is not None
        self.assertFalse(has_form(self.app.test_client()))
        self.assertFalse(has_form(self.client_for(OWNER)))
        self.assertFalse(has_form(self.client_for(ADMIN)))
        self.assertTrue(has_form(self.student))
        self.report()
        self.assertFalse(has_form(self.student))

    def test_guest_sees_a_log_in_invite(self):
        html = self.client.get(f"/listings/{self.lid}").get_data(as_text=True)
        self.assertIn("Log in to report this listing", html)

    def test_the_reporter_is_never_shown_on_public_or_owner_pages(self):
        self.sql("UPDATE users SET full_name = 'Secret Reporter' WHERE id = ?", (self.target_id,))
        self.report()
        owner = self.client_for(OWNER)
        for client in (self.app.test_client(), owner):
            for url in (f"/listings/{self.lid}", "/listings/mine", "/listings/", "/requests/received"):
                self.assertNotIn("Secret Reporter", client.get(url).get_data(as_text=True))
        self.assertNotIn(REASON, owner.get(f"/listings/{self.lid}").get_data(as_text=True))


class AdminPageTests(AdminWorld):
    URLS = ("/admin/", "/admin/reports", "/admin/reports?status=all", "/admin/users")

    def setUp(self):
        super().setUp()
        self.admin = self.client_for(ADMIN)
        self.sql("UPDATE reports SET reason = ? WHERE id = ?", (SCRIPT + " is the reason", self.rid))
        self.sql("UPDATE listings SET title = ? WHERE id = ?", (SCRIPT + " title", self.lid))
        self.sql("UPDATE users SET full_name = ? WHERE id = ?", (SCRIPT + " name", self.asker_id))

    def pages(self):
        for url in self.URLS + (f"/admin/reports/{self.rid}", f"/admin/listings/{self.lid}/remove",
                                f"/admin/users/{self.target_id}/suspend"):
            r = self.admin.get(url)
            self.assertEqual(r.status_code, 200, url)
            yield url, r.get_data(as_text=True), page(r.get_data(as_text=True))

    def test_every_admin_page_has_one_h1_one_header_main_footer_and_a_title(self):
        for url, _html, doc in self.pages():
            with self.subTest(url=url):
                self.assertEqual(len(doc.select("h1")), 1)
                self.assertEqual(len(doc.select("header.site-header")), 1)
                self.assertEqual(len(doc.select("main")), 1)
                self.assertEqual(len(doc.select("footer")), 1)
                self.assertTrue(doc.title.get_text(strip=True))

    def test_tables_have_captions_and_controls_have_labels(self):
        for url, _html, doc in self.pages():
            with self.subTest(url=url):
                for table in doc.select("table"):
                    self.assertTrue(table.select_one("caption").get_text(strip=True))
                for control in doc.select("input:not([type=hidden]), select, textarea"):
                    self.assertTrue(doc.select_one(f"label[for='{control['id']}']"), control)

    def test_user_text_is_escaped_everywhere(self):
        for url, html, _doc in self.pages():
            with self.subTest(url=url):
                self.assertNotIn(SCRIPT, html)

    def test_no_inline_style_or_handler(self):
        for url, html, doc in self.pages():
            with self.subTest(url=url):
                self.assertIsNone(doc.select_one("[style]"))
                self.assertIsNone(re.search(r"\son[a-z]+\s*=", html))

    def test_destructive_actions_have_the_dialog_button_and_the_plain_link(self):
        doc = page(self.admin.get(f"/admin/reports/{self.rid}").get_data(as_text=True))
        self.assertEqual(doc.select_one("[data-no-js] a")["href"], f"/admin/listings/{self.lid}/remove")
        self.assertIsNotNone(doc.select_one("[data-needs-js] button[data-dialog-open='remove-listing']"))
        self.assertIsNotNone(doc.select_one("dialog#remove-listing"))
        users = page(self.admin.get("/admin/users").get_data(as_text=True))
        self.assertIsNotNone(users.select_one(f"[data-no-js] a[href='/admin/users/{self.target_id}/suspend']"))
        self.assertIsNotNone(users.select_one(f"dialog#suspend-{self.target_id}"))

    def test_dashboard_shows_counts_and_newest_open_reports(self):
        doc = page(self.admin.get("/admin/").get_data(as_text=True))
        cells = {c.select_one("dt").get_text(strip=True): c.select_one("dd").get_text(strip=True)
                 for c in doc.select(".title-block-cell")}
        self.assertEqual(cells, {"Open reports": "1", "Available listings": "1", "Suspended users": "1"})
        self.assertEqual(len(doc.select("table tbody tr")), 1)

    def test_dashboard_empty_state_when_no_report_is_open(self):
        self.sql("UPDATE reports SET status = 'resolved'")
        html = self.admin.get("/admin/").get_data(as_text=True)
        self.assertIn("No open reports", html)
        self.assertEqual(len(page(html).select("table")), 0)

    def test_reports_empty_state_when_there_are_none(self):
        self.sql("DELETE FROM reports")
        self.assertIn("No reports here", self.admin.get("/admin/reports?status=all").get_data(as_text=True))

    def test_every_list_has_a_problem_state_when_the_query_cannot_run(self):
        for url, text in (("/admin/", "The dashboard could not be loaded"),
                          ("/admin/reports", "Reports could not be loaded"),
                          ("/admin/users", "Users could not be loaded")):
            with self.subTest(url=url):
                with self.assertLogs(self.app.logger, level="ERROR"):     # C15: the problem is now logged
                    with mock.patch("app.admin.get_db", side_effect=sqlite3.OperationalError("boom")):
                        r = self.admin.get(url)
                self.assertEqual(r.status_code, 503)
                doc = page(r.get_data(as_text=True))
                self.assertIn(text, doc.get_text(" ", strip=True))
                self.assertIsNotNone(doc.select_one("[role=alert]"))
                self.assertEqual(len(doc.select("h1")), 1)

    def test_users_page_names_admins_and_hides_actions_for_them(self):
        doc = page(self.admin.get("/admin/users").get_data(as_text=True))
        rows = {tr.select("td")[1].get_text(strip=True): tr.get_text(" ", strip=True) for tr in doc.select("tbody tr")}
        self.assertIn("No actions available", rows[ADMIN])
        self.assertIn("Unsuspend", rows[FROZEN])
        self.assertNotIn("No actions available", rows[STRANGER])


if __name__ == "__main__":
    unittest.main()
