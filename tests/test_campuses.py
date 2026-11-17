"""Landing follow-up: the "Add your campus" page (/campuses/new), its service and rate cap, and the admin list.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import unittest
from datetime import timedelta
from unittest import mock

from bs4 import BeautifulSoup

import helpers
from app import campus_service as svc
from app import ratelimit
from app.db import connect
from test_auth import AuthCase

GOOD = {"college_name": "Maharaja Surajmal Institute of Technology", "area": "Janakpuri, West Delhi",
        "kind": "engineering"}


def soup(response):
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


class CampusCase(AuthCase):
    def stored(self):
        return self.db_rows("SELECT * FROM campus_requests ORDER BY id")

    def send(self, client=None, **over):
        return self.post("/campuses/new", {**GOOD, **over}, client=client, page="/campuses/new")


class ValidationTests(unittest.TestCase):
    def test_good_input_is_clean_and_whitespace_is_collapsed(self):
        clean, problems = svc.validate({"college_name": "  Delhi   Technical  Campus ", "area": " Bawana ",
                                        "kind": "polytechnic"})
        self.assertEqual(problems, {})
        self.assertEqual(clean, {"college_name": "Delhi Technical Campus", "area": "Bawana", "kind": "polytechnic"})

    def test_length_limits_at_and_beyond_the_edges(self):
        for name, ok in (("a" * 2, False), ("abc", True), ("a" * 120, True), ("a" * 121, False)):
            _, problems = svc.validate({**GOOD, "college_name": name})
            self.assertEqual("college_name" not in problems, ok, len(name))
        for area, ok in (("a", False), ("ab", True), ("a" * 80, True), ("a" * 81, False)):
            _, problems = svc.validate({**GOOD, "area": area})
            self.assertEqual("area" not in problems, ok, len(area))

    def test_kind_must_be_one_of_the_three(self):
        for kind, ok in (("engineering", True), ("polytechnic", True), ("other", True),
                         ("", False), ("university", False), ("ENGINEERING", False)):
            _, problems = svc.validate({**GOOD, "kind": kind})
            self.assertEqual("kind" not in problems, ok, kind)

    def test_control_characters_are_refused(self):
        _, problems = svc.validate({**GOOD, "college_name": "Bad\x00Name College"})
        self.assertIn("college_name", problems)

    def test_missing_fields_give_one_problem_each(self):
        _, problems = svc.validate({})
        self.assertEqual(set(problems), {"college_name", "area", "kind"})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = helpers.migrated_memory_db()
        self.addCleanup(self.conn.close)

    def test_add_then_the_same_college_again_is_stored_once_whatever_the_capitals(self):
        self.assertEqual(svc.add_campus(self.conn, GOOD), "added")
        again = {**GOOD, "college_name": GOOD["college_name"].upper(), "area": GOOD["area"].lower()}
        self.assertEqual(svc.add_campus(self.conn, again), "exists")
        self.assertEqual(svc.count_campuses(self.conn), 1)

    def test_the_same_name_in_another_area_is_a_different_campus(self):
        svc.add_campus(self.conn, GOOD)
        self.assertEqual(svc.add_campus(self.conn, {**GOOD, "area": "Rohini"}), "added")
        self.assertEqual(svc.count_campuses(self.conn), 2)

    def test_list_is_newest_first(self):
        svc.add_campus(self.conn, {**GOOD, "college_name": "First College"})
        svc.add_campus(self.conn, {**GOOD, "college_name": "Second College"})
        self.assertEqual([r["college_name"] for r in svc.list_campuses(self.conn)],
                         ["Second College", "First College"])

    def test_the_database_itself_refuses_a_bad_kind_or_a_short_name(self):
        import sqlite3
        for row in (("Okay College", "Rohini", "university"), ("ab", "Rohini", "other"), ("Okay College", "r", "other")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute("INSERT INTO campus_requests (college_name, area, kind) VALUES (?, ?, ?)", row)

    def test_table_has_no_link_to_any_student(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(campus_requests)")}
        self.assertEqual(cols, {"id", "college_name", "area", "kind", "created_at"})
        self.assertEqual(list(self.conn.execute("PRAGMA foreign_key_list(campus_requests)")), [])


class PageTests(CampusCase):
    def test_guest_sees_the_form_with_three_fields_and_a_token(self):
        r = self.client.get("/campuses/new")
        self.assertEqual(r.status_code, 200)
        doc = soup(r)
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertEqual([i["name"] for i in doc.select("form [name]")],
                         ["csrf_token", "college_name", "area", "kind"])
        self.assertIn("Delhi", doc.get_text())

    def test_the_kind_menu_offers_exactly_the_three_kinds(self):
        doc = soup(self.client.get("/campuses/new"))
        self.assertEqual([o["value"] for o in doc.select("select[name=kind] option") if o.get("value")],
                         ["engineering", "polytechnic", "other"])

    def test_guest_adds_a_campus_and_is_sent_to_signup_with_a_message(self):
        r = self.send()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/signup")
        rows = self.stored()
        self.assertEqual([(x["college_name"], x["area"], x["kind"]) for x in rows],
                         [(GOOD["college_name"], GOOD["area"], "engineering")])
        page = self.client.get("/signup").get_data(as_text=True)
        self.assertIn("Your campus is on our list", page)
        self.assertIn("create your account", page.lower())

    def test_logged_in_student_is_sent_to_the_listings(self):
        self.make_user()
        self.login()
        r = self.send()
        self.assertEqual((r.status_code, r.headers["Location"]), (303, "/listings/"))
        self.assertEqual(len(self.stored()), 1)

    def test_a_repeat_is_accepted_quietly_stored_once_and_not_counted_against_the_cap(self):
        self.send()
        r = self.send()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(len(self.db_rows("SELECT * FROM login_attempts WHERE \"key\" LIKE 'campus:%'")), 1)

    def test_empty_form_gives_400_with_a_summary_and_one_message_per_field_and_stores_nothing(self):
        r = self.post("/campuses/new", {}, page="/campuses/new")
        self.assertEqual(r.status_code, 400)
        doc = soup(r)
        text = doc.get_text(" ", strip=True)
        for message in ("College name must be at least 3", "Area must be at least 2", "Choose the type of college"):
            self.assertIn(message, text)
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.db_rows("SELECT * FROM login_attempts WHERE \"key\" LIKE 'campus:%'"), [])

    def test_typed_text_is_kept_when_one_field_is_wrong(self):
        r = self.send(kind="")
        self.assertEqual(r.status_code, 400)
        doc = soup(r)
        self.assertEqual(doc.select_one("input[name=college_name]")["value"], GOOD["college_name"])
        self.assertEqual(doc.select_one("input[name=area]")["value"], GOOD["area"])

    def test_a_post_without_the_csrf_token_is_refused_and_stores_nothing(self):
        r = self.client.post("/campuses/new", data=GOOD)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.stored(), [])

    def test_text_is_escaped_on_the_page(self):
        r = self.send(college_name="<script>alert(1)</script> College", kind="")
        self.assertNotIn("<script>alert(1)", r.get_data(as_text=True))

    def test_get_does_not_store_anything(self):
        self.client.get("/campuses/new?college_name=X&area=Y&kind=other")
        self.assertEqual(self.stored(), [])


class CapTests(CampusCase):
    def numbered(self, i):
        return {"college_name": f"Campus number {i}", "area": f"Area {i}"}

    def test_the_cap_is_ten_stored_suggestions_per_address_per_hour_then_429(self):
        for i in range(ratelimit.CAMPUS_MAX):
            self.assertEqual(self.send(**self.numbered(i)).status_code, 303, i)
        r = self.send(**self.numbered(99))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.stored()), ratelimit.CAMPUS_MAX)       # the refused one was not stored
        self.assertEqual(len(self.db_rows("SELECT * FROM login_attempts WHERE \"key\" LIKE 'campus:%'")),
                         ratelimit.CAMPUS_MAX)                              # and a refusal is never recorded

    def test_the_lock_ends_when_the_old_rows_leave_the_window(self):
        for i in range(ratelimit.CAMPUS_MAX):
            self.send(**self.numbered(i))
        later = ratelimit.now() + timedelta(minutes=61)
        with mock.patch("app.ratelimit.now", return_value=later):
            self.assertEqual(self.send(**self.numbered(50)).status_code, 303)

    def test_another_address_is_not_locked(self):
        for i in range(ratelimit.CAMPUS_MAX):
            self.send(**self.numbered(i))
        other = self.app.test_client()
        other_env = {"REMOTE_ADDR": "10.9.8.7"}
        token = other.get("/campuses/new", environ_overrides=other_env).get_data(as_text=True)
        from test_auth import token_from
        r = other.post("/campuses/new", data={**GOOD, "college_name": "Other address college",
                                              "csrf_token": token_from(token)}, environ_overrides=other_env)
        self.assertEqual(r.status_code, 303)

    def test_the_cap_does_not_touch_the_login_or_signup_keys(self):
        for i in range(ratelimit.CAMPUS_MAX):
            self.send(**self.numbered(i))
        self.assertEqual(ratelimit.signup_wait(connect(self.app.config["DATABASE_PATH"]), "127.0.0.1"), 0)

    def test_the_429_page_says_how_long_to_wait(self):
        for i in range(ratelimit.CAMPUS_MAX):
            self.send(**self.numbered(i))
        r = self.send(**self.numbered(77))
        self.assertIn("Retry-After", r.headers)


class AdminListTests(CampusCase):
    def admin(self):
        self.make_user("admin@college.example", is_admin=1)
        client = self.app.test_client()
        self.login("admin@college.example", client=client)
        return client

    def test_empty_state_when_nothing_was_suggested(self):
        r = self.admin().get("/admin/campuses")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No campuses yet", r.get_data(as_text=True))

    def test_admin_sees_the_suggestions_newest_first_with_readable_types(self):
        self.send(college_name="First Poly College", kind="polytechnic")
        self.send(college_name="Second Engg College", kind="engineering")
        doc = soup(self.admin().get("/admin/campuses"))
        rows = [[td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])] for tr in doc.select("tbody tr")]
        self.assertEqual([r[0] for r in rows], ["Second Engg College", "First Poly College"])
        self.assertEqual([r[2] for r in rows], ["Engineering college", "Polytechnic"])

    def test_names_are_escaped_in_the_admin_list(self):
        conn = connect(self.app.config["DATABASE_PATH"])
        conn.execute("INSERT INTO campus_requests (college_name, area, kind) VALUES ('<b>Bold</b> College', 'Rohini', 'other')")
        conn.commit()
        conn.close()
        html = self.admin().get("/admin/campuses").get_data(as_text=True)
        self.assertNotIn("<b>Bold</b>", html)

    def test_problem_state_is_503_when_the_list_cannot_be_read(self):
        import sqlite3
        client = self.admin()
        with mock.patch("app.campus_service.list_campuses", side_effect=sqlite3.OperationalError("down")), \
                self.assertLogs(self.app.logger, level="ERROR"):     # the problem is logged, not shown
            r = client.get("/admin/campuses")
        self.assertEqual(r.status_code, 503)
        self.assertIn("Campuses could not be loaded", r.get_data(as_text=True))

    def test_the_dashboard_links_to_the_list(self):
        doc = soup(self.admin().get("/admin/"))
        self.assertIn("/admin/campuses", [a["href"] for a in doc.find_all("a")])


if __name__ == "__main__":
    unittest.main()
