"""C12: the 403, 404 and 500 pages: right status code, a way back, one h1, and a 500 that never shows
the exception, a path or a trace. (Keep the words error / failed / skipped out of names and docstrings:
scripts/check.sh gates on them.)"""
import sqlite3
import unittest
from unittest import mock

from bs4 import BeautifulSoup
from flask import abort

import helpers
from app.db import connect
from test_auth import AuthCase

SECRET = "secret-detail /srv/campus/app/db.py line 42"


def soup(html):
    return BeautifulSoup(html, "html.parser")


class ProblemCase(AuthCase):
    def setUp(self):
        super().setUp()

        @self.app.get("/test-boom")
        def boom():
            raise RuntimeError(SECRET)

        @self.app.get("/test-db-boom")
        def db_boom():
            raise sqlite3.OperationalError("no such table: users")

        @self.app.get("/test-abort-500")
        def abort_500():
            abort(500)

    def other_listing(self):
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            owner = helpers.add_user(conn, "owner@college.example")
            lid = helpers.add_listing(conn, owner, "Not yours")
            conn.commit()
            return lid
        finally:
            conn.close()

    def crash(self, url="/test-boom", client=None):
        with self.assertLogs(self.app.logger, level="ERROR") as logs:
            r = (client or self.client).get(url)
        return r, "\n".join(logs.output)


class NotFoundPageTests(ProblemCase):
    def test_unknown_address_gets_a_404_page_with_a_way_back(self):
        r = self.client.get("/no-such-page")
        self.assertEqual(r.status_code, 404)
        doc = soup(r.get_data(as_text=True))
        self.assertEqual([h.get_text(strip=True) for h in doc.find_all("h1")], ["Page not found"])
        self.assertEqual(doc.select_one("main a.btn")["href"], "/listings/")

    def test_missing_listing_is_the_same_page_for_a_student(self):
        self.make_user()
        self.login()
        r = self.client.get("/listings/9999")
        self.assertEqual(r.status_code, 404)
        html = r.get_data(as_text=True)
        self.assertIn("Page not found", html)
        self.assertIn("Ravi Singh", html)   # the navigation still knows who is logged in

    def test_page_frame_is_complete(self):
        doc = soup(self.client.get("/no-such-page").get_data(as_text=True))
        self.assertEqual((len(doc.select("header.site-header")), len(doc.find_all("main")), len(doc.find_all("footer"))),
                         (1, 1, 1))
        self.assertEqual(doc.html["lang"], "en")


class ForbiddenPageTests(ProblemCase):
    def test_editing_someone_elses_listing_gets_a_403_page(self):
        lid = self.other_listing()
        self.make_user()
        self.login()
        r = self.client.get(f"/listings/{lid}/edit")
        self.assertEqual(r.status_code, 403)
        doc = soup(r.get_data(as_text=True))
        self.assertEqual(doc.h1.get_text(strip=True), "You can't open this page")
        self.assertEqual(doc.select_one("main a.btn")["href"], "/listings/")

    def test_expired_form_shows_our_message_and_a_log_in_link_for_guests(self):
        r = self.client.post("/login", data={"email": "a@b.c", "password": "x"})
        self.assertEqual(r.status_code, 403)
        doc = soup(r.get_data(as_text=True))
        self.assertIn("Your form has expired. Reload the page and try again.", doc.select_one(".page-lead").get_text())
        self.assertEqual(doc.select_one("main a.btn")["href"], "/login")

    def test_default_werkzeug_text_is_not_shown(self):
        lid = self.other_listing()
        self.make_user()
        self.login()
        html = self.client.get(f"/listings/{lid}/edit").get_data(as_text=True)
        self.assertNotIn("read-protected", html)
        self.assertNotIn("permission to access", html)


class ServerProblemPageTests(ProblemCase):
    def test_unhandled_exception_gets_a_500_page(self):
        r, _ = self.crash()
        self.assertEqual(r.status_code, 500)
        doc = soup(r.get_data(as_text=True))
        self.assertEqual(doc.h1.get_text(strip=True), "Something went wrong")
        self.assertEqual(doc.select_one("main .state-problem")["role"], "alert")
        self.assertEqual(doc.select_one("main a.btn")["href"], "/listings/")

    def test_page_shows_no_trace_no_path_no_exception(self):
        r, _ = self.crash()
        html = r.get_data(as_text=True)
        for leaked in (SECRET, "secret-detail", "RuntimeError", "Traceback", "/srv/", "db.py", 'File "'):
            self.assertNotIn(leaked, html, leaked)

    def test_database_problem_does_not_leak_sql_details(self):
        r, _ = self.crash("/test-db-boom")
        self.assertEqual(r.status_code, 500)
        html = r.get_data(as_text=True)
        self.assertNotIn("no such table", html)
        self.assertNotIn("OperationalError", html)

    def test_the_details_go_to_the_log_instead(self):
        _, log = self.crash()
        self.assertIn("RuntimeError", log)
        self.assertIn("secret-detail", log)

    def test_abort_500_uses_the_same_page(self):
        r = self.client.get("/test-abort-500")
        self.assertEqual(r.status_code, 500)
        self.assertIn("Something went wrong", r.get_data(as_text=True))

    def test_logged_in_student_sees_the_page_inside_the_normal_frame(self):
        self.make_user()
        self.login()
        r, _ = self.crash()
        self.assertEqual(r.status_code, 500)
        self.assertIn("Ravi Singh", r.get_data(as_text=True))

    def test_if_the_page_cannot_be_drawn_a_plain_answer_is_sent(self):
        with mock.patch("app.problem_pages.render_template", side_effect=RuntimeError(SECRET)):
            with self.assertLogs(self.app.logger, level="ERROR"):
                r = self.client.get("/test-boom")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.mimetype, "text/plain")
        self.assertNotIn("secret-detail", r.get_data(as_text=True))
        self.assertNotIn("Traceback", r.get_data(as_text=True))


class SharedRulesTests(ProblemCase):
    def pages(self):
        lid = self.other_listing()
        self.make_user()
        self.login()
        yield 404, self.client.get("/no-such-page")
        yield 403, self.client.get(f"/listings/{lid}/edit")
        yield 500, self.crash()[0]

    def test_status_codes_match_the_page(self):
        for code, r in self.pages():
            self.assertEqual(r.status_code, code)

    def test_each_page_has_one_h1_and_no_inline_styles_or_handlers(self):
        for code, r in self.pages():
            html = r.get_data(as_text=True)
            self.assertEqual(len(soup(html).find_all("h1")), 1, code)
            self.assertNotIn("style=", html)
            self.assertNotRegex(html, r"\son[a-z]+=")

    def test_other_http_responses_keep_their_normal_form(self):
        r = self.client.post("/health")
        self.assertEqual(r.status_code, 405)
        self.assertNotIn("Something went wrong", r.get_data(as_text=True))

    def test_pages_are_html(self):
        for code, r in self.pages():
            self.assertEqual(r.mimetype, "text/html", code)


if __name__ == "__main__":
    unittest.main()
