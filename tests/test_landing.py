"""C12: landing page at "/" (guests), student redirect, live counts with their empty and problem states,
and the counts service. (Keep the words error / failed / skipped out of names and docstrings:
scripts/check.sh gates on them.)"""
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from flask import render_template_string

import helpers
from app import stats_service
from app.constants import CATEGORIES
from app.db import connect
from test_auth import AuthCase


def soup(html):
    return BeautifulSoup(html, "html.parser")


class LandingBase(AuthCase):
    def db(self):
        conn = connect(self.app.config["DATABASE_PATH"])
        self.addCleanup(conn.close)
        return conn

    def stock(self):
        """Two Books and one Mini drafter available, plus a reserved, a removed and a completed exchange."""
        conn = self.db()
        owner = helpers.add_user(conn, "owner@college.example")
        buyer = helpers.add_user(conn, "buyer@college.example")
        helpers.add_listing(conn, owner, "Book one", category="Books")
        helpers.add_listing(conn, owner, "Book two", category="Books")
        helpers.add_listing(conn, owner, "Drafter", category="Mini drafter")
        helpers.add_listing(conn, owner, "Held", category="Notes", status="reserved")
        helpers.add_listing(conn, owner, "Gone", category="Notes", status="removed", deleted_at="2026-01-01T00:00:00Z")
        done = helpers.add_listing(conn, owner, "Done", category="Calculator", status="closed")
        conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, status) VALUES (?, ?, 'completed')",
                     (done, buyer))
        conn.commit()


class GuestLandingTests(LandingBase):
    """The landing page follows the approved screenshots (navy header and hero, five more sections)."""

    def page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        return soup(r.get_data(as_text=True))

    def test_guest_gets_the_landing_page_with_one_h1(self):
        doc = self.page()
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertIn("Get more done with your skills", doc.h1.get_text(" ", strip=True))

    def test_page_has_one_header_one_main_one_footer(self):
        doc = self.page()
        self.assertEqual(len(doc.select("header.lp-header")), 1)
        self.assertEqual(len(doc.find_all("main")), 1)
        self.assertEqual(len(doc.find_all("footer")), 1)

    def test_header_has_the_three_anchor_links_and_each_target_exists(self):
        doc = self.page()
        links = {a.get_text(strip=True): a["href"] for a in doc.select(".lp-nav a")}
        self.assertEqual(list(links), ["How it works", "Why us", "FAQ"])
        for href in links.values():
            self.assertIsNotNone(doc.find(id=href[1:]), href)

    def test_hero_buttons_go_to_add_campus_and_login(self):
        doc = self.page()
        hrefs = {a.get_text(" ", strip=True).rstrip(" \u2192"): a["href"] for a in doc.select(".lp-actions a")}
        self.assertEqual(hrefs, {"Join the board": "/campuses/new", "Sign in": "/login"})

    def test_join_your_campus_board_goes_to_add_campus_and_the_last_button_to_signup(self):
        doc = self.page()
        by_text = {a.get_text(" ", strip=True): a["href"] for a in doc.select("a.lp-btn")}
        self.assertEqual(by_text["Join your campus board"], "/campuses/new")
        self.assertEqual(by_text["Get started for free"], "/signup")

    def test_the_proof_line_and_copy_speak_about_delhi_not_nigeria(self):
        doc = self.page()
        self.assertEqual(doc.select_one(".lp-proof").get_text(" ", strip=True),
                         "Many engineering colleges and polytechnics across Delhi")
        text = doc.get_text(" ", strip=True)
        for old in ("Nigeria", "330"):
            self.assertNotIn(old, text)
        self.assertIn("Delhi", doc.select_one("ol.steps").get_text())

    def test_faq_about_the_institution_links_to_add_campus(self):
        doc = self.page()
        link = doc.select(".lp-faq details")[3].find("a")
        self.assertEqual(link["href"], "/campuses/new")

    def test_smooth_scroll_is_off_for_people_who_ask_for_less_motion(self):
        css = open(os.path.join(helpers.ROOT, "app", "static", "css", "landing.css"), encoding="utf-8").read()
        self.assertIn("prefers-reduced-motion: no-preference", css)
        self.assertRegex(css, r"@media \(prefers-reduced-motion: no-preference\) \{ html \{ scroll-behavior: smooth; \} \}")
        self.assertIn("scroll-margin-top", css)

    def test_sections_appear_in_the_designed_order(self):
        doc = self.page()
        headings = [h.get_text(" ", strip=True) for h in doc.find_all("h2")]
        self.assertEqual(headings, ["Matched with people who want you back", "Your whole campus, one board",
                                    "Four steps to your first swap", "A better way to learn and grow on campus",
                                    "Questions students actually ask", "Try Campus Barter today"])

    def test_four_steps_and_five_reasons(self):
        doc = self.page()
        self.assertEqual(len(doc.select("ol.steps > li")), 4)
        self.assertEqual(len(doc.select("ul.why-list > li")), 5)

    def test_faq_has_four_questions_closed_at_first(self):
        details = self.page().select(".lp-faq details")
        self.assertEqual(len(details), 4)
        self.assertTrue(all(not d.has_attr("open") for d in details))

    def test_no_inline_styles_or_handlers(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("style=", html)
        self.assertNotRegex(html, r"\son[a-z]+=")

    def test_guest_page_contains_nothing_private(self):
        self.stock()
        conn = self.db()
        conn.execute("UPDATE users SET phone = '9876500000', address = '12 Hidden Lane', social_link = '@secretname'")
        conn.commit()
        html = self.client.get("/").get_data(as_text=True)
        for private in ("owner@college.example", "buyer@college.example", "Test User", "9876500000",
                        "Hidden Lane", "@secretname", "Book one", "Drafter"):
            self.assertNotIn(private, html, private)


class StudentHomeTests(LandingBase):
    def test_student_is_sent_to_the_listings(self):
        self.make_user()
        self.login()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/listings/"))

    def test_admin_is_sent_to_the_listings_too(self):
        self.make_user("admin@college.example", is_admin=1)
        self.login("admin@college.example")
        self.assertTrue(self.client.get("/").headers["Location"].endswith("/listings/"))

    def test_logged_out_user_sees_the_landing_again(self):
        self.make_user()
        self.login()
        self.post("/logout", {}, page="/profile")
        self.assertEqual(self.client.get("/").status_code, 200)


class CountsServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = helpers.migrated_memory_db()
        self.addCleanup(self.conn.close)
        self.owner = helpers.add_user(self.conn)

    def test_empty_database(self):
        counts = stats_service.landing_counts(self.conn)
        self.assertEqual((counts["available"], counts["completed"]), (0, 0))
        self.assertEqual(counts["by_type"], [(c, 0) for c in CATEGORIES])

    def test_only_available_listings_count(self):
        for status in ("available", "reserved", "closed"):
            helpers.add_listing(self.conn, self.owner, status, status=status)
        helpers.add_listing(self.conn, self.owner, "gone", status="removed", deleted_at="2026-01-01T00:00:00Z")
        self.assertEqual(stats_service.landing_counts(self.conn)["available"], 1)

    def test_a_retired_item_type_counts_in_the_total_but_gets_no_tile(self):
        helpers.add_listing(self.conn, self.owner, "old", category="PVPs")
        counts = stats_service.landing_counts(self.conn)
        self.assertEqual(counts["available"], 1)
        self.assertEqual([c for c, _ in counts["by_type"]], list(CATEGORIES))

    def test_only_completed_requests_are_counted(self):
        other = helpers.add_user(self.conn, "other@college.example")
        for status in ("pending", "accepted", "completed", "declined", "cancelled"):
            lid = helpers.add_listing(self.conn, self.owner, status)
            self.conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, status) VALUES (?, ?, ?)",
                              (lid, other, status))
        self.assertEqual(stats_service.landing_counts(self.conn)["completed"], 1)


class LandingMacroTests(LandingBase):
    def render(self, source):
        with self.app.test_request_context():
            return render_template_string('{% from "components/landing.html" import step_list, type_grid, stamp_row %}' + source)

    def test_text_is_escaped(self):
        html = self.render('{{ step_list([("<b>t</b>", "<i>x</i>")]) }}{{ type_grid([("<u>l</u>", "/x", "<s>n</s>")]) }}'
                           '{{ stamp_row(["<em>s</em>"], "<q>c</q>") }}')
        for raw in ("<b>t</b>", "<i>x</i>", "<u>l</u>", "<s>n</s>", "<em>s</em>", "<q>c</q>"):
            self.assertNotIn(raw, html)

    def test_stamp_row_is_named_for_screen_readers(self):
        doc = soup(self.render('{{ stamp_row(["Swap"], "Ways to offer an item") }}'))
        self.assertEqual(doc.ul["aria-label"], "Ways to offer an item")

    def test_design_page_shows_the_new_components(self):
        doc = soup(self.client.get("/design").get_data(as_text=True))
        for selector in ("ol.steps", "ul.type-grid", "ul.stamp-row"):
            self.assertIsNotNone(doc.select_one(selector), selector)


if __name__ == "__main__":
    unittest.main()
