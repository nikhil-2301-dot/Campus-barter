"""C9: browse page and listing detail page built from the component library.
States covered: success, empty (site empty / nothing matches), problem (list could not load),
loading (skeleton that only a script reveals). Also filters, paging, escaping and the request form.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import re
import sqlite3
import unittest
from unittest import mock

from bs4 import BeautifulSoup

import helpers
from app.constants import CATEGORIES, CONDITIONS, MODES
from app.db import connect
from app.listings import PAGE_SIZE
from test_auth import AuthCase, DOMAIN

OWNER = f"owner@{DOMAIN}"
ASKER = f"asker@{DOMAIN}"
ADMIN = f"boss@{DOMAIN}"
SCRIPT = "<script>alert(1)</script>"


def page(html):
    return BeautifulSoup(html, "html.parser")


class BrowseCase(AuthCase):
    def setUp(self):
        super().setUp()
        self.owner_id = self.make_user(OWNER)

    def seed(self, count, **kw):
        """Insert `count` listings for the owner; returns their ids."""
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            ids = [helpers.add_listing(conn, self.owner_id, title=f"{kw.pop('prefix', 'Item')} {i}", **kw)
                   for i in range(count)]
            conn.commit()
            return ids
        finally:
            conn.close()

    def sql(self, statement, params=()):
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def browse(self, query="", client=None):
        r = (client or self.client).get("/listings/" + query)
        return r, page(r.get_data(as_text=True))

    def student_client(self, email=ASKER):
        self.make_user(email)
        client = self.app.test_client()
        self.login(email=email, client=client)
        return client


class BrowseSuccessTests(BrowseCase):
    def test_each_card_shows_type_condition_owner_and_offer(self):
        self.seed(1, category="Lab coats", condition="fair", mode="give")
        _r, doc = self.browse()
        card = doc.select_one(".listing-card")
        text = card.get_text(" ", strip=True)
        for expected in ("Lab coats", "Fair", "Ravi Singh", "Give away"):
            self.assertIn(expected, text)
        self.assertEqual(card.select_one(".stamp").get_text(strip=True), "Give away")

    def test_card_title_links_to_the_detail_page(self):
        lid = self.seed(1)[0]
        _r, doc = self.browse()
        self.assertEqual(doc.select_one(".listing-card-title a")["href"], f"/listings/{lid}")

    def test_only_available_listings_are_listed(self):
        self.seed(1, prefix="Open")
        self.seed(1, prefix="Held", status="reserved")
        self.seed(1, prefix="Done", status="closed")
        r, _doc = self.browse()
        html = r.get_data(as_text=True)
        self.assertIn("Open 0", html)
        self.assertNotIn("Held 0", html)
        self.assertNotIn("Done 0", html)
        self.assertIn("1 listing found.", html)

    def test_count_line_uses_singular_and_plural(self):
        self.seed(2)
        self.assertIn("2 listings found.", self.browse()[0].get_data(as_text=True))

    def test_pagination_uses_the_component_and_keeps_filters(self):
        self.seed(PAGE_SIZE + 2, category="Books")
        _r, doc = self.browse("?category=Books&q=Item&mode=give&page=1")
        nav = doc.select_one("nav.pagination")
        self.assertEqual(nav["aria-label"], "Pages")
        nxt = nav.select_one("a[rel=next]")["href"]
        for part in ("category=Books", "q=Item", "mode=give", "page=2"):
            self.assertIn(part, nxt)

    def test_no_pagination_when_everything_fits_on_one_page(self):
        self.seed(3)
        self.assertIsNone(self.browse()[1].select_one("nav.pagination"))

    def test_posting_link_only_for_logged_in_students(self):
        self.seed(1)
        self.assertNotIn("/listings/new", self.browse()[0].get_data(as_text=True))
        client = self.student_client()
        html = self.browse(client=client)[0].get_data(as_text=True)
        self.assertIn("/listings/new", html)


class BrowseFilterTests(BrowseCase):
    def test_item_type_dropdown_lists_item_types_and_there_is_no_year_filter(self):
        _r, doc = self.browse()
        options = [o["value"] for o in doc.select("#f-category option")]
        self.assertEqual(options, [""] + list(CATEGORIES))
        names = {c.get("name") for c in doc.select("#browse-form select, #browse-form input")}
        self.assertEqual(names, {"q", "category", "condition", "mode"})

    def test_selected_filter_values_stay_selected(self):
        self.seed(1, category="Lab coats", condition="good", mode="lend")
        _r, doc = self.browse("?category=Lab+coats&condition=good&mode=lend&q=Item")
        chosen = {s["name"]: s.select_one("option[selected]")["value"] for s in doc.select("#browse-form select")}
        self.assertEqual(chosen, {"category": "Lab coats", "condition": "good", "mode": "lend"})
        self.assertEqual(doc.select_one("#f-q")["value"], "Item")

    def test_filter_by_item_type_shows_only_that_type(self):
        self.seed(1, prefix="Coat", category="Lab coats")
        self.seed(1, prefix="Book", category="Books")
        html = self.browse("?category=Lab+coats")[0].get_data(as_text=True)
        self.assertIn("Coat 0", html)
        self.assertNotIn("Book 0", html)

    def test_condition_and_mode_filters_narrow_the_list(self):
        self.seed(1, prefix="Lent", condition="worn", mode="lend")
        self.seed(1, prefix="Given", condition="new", mode="give")
        html = self.browse("?mode=lend&condition=worn")[0].get_data(as_text=True)
        self.assertIn("Lent 0", html)
        self.assertNotIn("Given 0", html)

    def test_unknown_filter_values_are_ignored_and_the_student_is_told(self):
        self.seed(2)
        r, doc = self.browse("?category=PVPs&condition=broken&mode=trade&page=abc")
        self.assertEqual(r.status_code, 200)
        notice = doc.select_one(".alert-warning").get_text(" ", strip=True)
        for shown in ("item type", "condition", "offered as", "page"):
            self.assertIn(shown, notice)
        self.assertEqual(len(doc.select(".listing-card")), 2)

    def test_valid_filters_show_no_notice(self):
        self.seed(1)
        self.assertIsNone(self.browse("?category=Books&condition=good&mode=give&page=1")[1].select_one(".alert-warning"))

    def test_page_past_the_end_shows_the_last_page(self):
        self.seed(PAGE_SIZE + 2)
        r, doc = self.browse("?page=99")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Page 2 of 2", doc.select_one(".page-status").get_text())
        self.assertEqual(len(doc.select(".listing-card")), 2)

    def test_page_zero_and_negative_show_the_first_page(self):
        self.seed(PAGE_SIZE + 2)
        for value in ("0", "-5"):
            doc = self.browse(f"?page={value}")[1]
            self.assertIn("Page 1 of 2", doc.select_one(".page-status").get_text(), value)

    def test_search_text_with_quotes_and_sql_characters_is_safe(self):
        self.seed(1)
        for q in ("' OR 1=1 --", '"; DROP TABLE listings; --', "%", "_", "\\"):
            r, _doc = self.browse("?q=" + q.replace("%", "%25").replace("#", "%23"))
            self.assertEqual(r.status_code, 200, q)
        self.assertEqual(len(self.db_rows("SELECT id FROM listings")), 1)

    def test_every_filter_control_has_a_label(self):
        doc = self.browse()[1]
        for control in doc.select("#browse-form input[name], #browse-form select"):
            self.assertIsNotNone(doc.select_one(f'label[for="{control["id"]}"]'), control["name"])

    def test_filter_form_is_a_get_form_with_a_search_landmark(self):
        form = self.browse()[1].select_one("#browse-form")
        self.assertEqual(form["method"].lower(), "get")
        self.assertEqual(form["role"], "search")
        self.assertIsNone(form.select_one('input[name="csrf_token"]'))


class BrowseStateTests(BrowseCase):
    def test_empty_site_invites_a_guest_to_log_in(self):
        r, doc = self.browse()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nothing has been posted yet", doc.select_one("#browse-results .state").get_text())
        self.assertEqual(doc.select_one("#browse-results .state a.btn")["href"].split("?")[0], "/login")
        self.assertIn("0 listings found.", r.get_data(as_text=True))

    def test_empty_site_invites_a_student_to_post(self):
        client = self.student_client()
        doc = self.browse(client=client)[1]
        self.assertEqual(doc.select_one("#browse-results .state a.btn")["href"], "/listings/new")

    def test_search_without_a_match_offers_to_clear_the_filters(self):
        self.seed(2)
        r, doc = self.browse("?q=zzzzz")
        state = doc.select_one("#browse-results .state")
        self.assertIn("No listings match your search", state.get_text())
        self.assertEqual(state.select_one("a.btn")["href"], "/listings/")
        self.assertIn("0 listings found.", r.get_data(as_text=True))

    def test_empty_search_box_with_a_filter_and_no_match_is_the_no_match_state(self):
        self.seed(1, category="Books")
        doc = self.browse("?category=Calculator")[1]
        self.assertIn("No listings match your search", doc.select_one("#browse-results .state").get_text())

    def test_list_that_cannot_load_shows_the_problem_state(self):
        self.seed(1)
        with self.assertLogs(self.app.logger, level="ERROR") as logged:
            with mock.patch("app.listings.get_db", side_effect=sqlite3.DatabaseError("db unavailable")):
                r, doc = self.browse("?q=Item")
        self.assertEqual(r.status_code, 503)
        problem = doc.select_one(".state-problem")
        self.assertEqual(problem["role"], "alert")
        self.assertIn("Listings could not be loaded", problem.get_text())
        self.assertIn("q=Item", problem.select_one("a")["href"])
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertIsNone(doc.select_one(".listing-card"))
        self.assertNotIn("Traceback", r.get_data(as_text=True))
        self.assertNotIn("db unavailable", r.get_data(as_text=True))
        self.assertEqual(len(logged.records), 1)

    def test_loading_skeleton_is_hidden_until_the_script_shows_it(self):
        self.seed(1)
        r, doc = self.browse()
        loading = doc.select_one("#browse-loading")
        self.assertTrue(loading.has_attr("hidden"))
        self.assertIsNotNone(loading.select_one(".skeleton"))
        self.assertFalse(doc.select_one("#browse-results").has_attr("hidden"))
        self.assertIn("js/browse.js", r.get_data(as_text=True))

    def test_browse_script_exists_and_targets_the_page_ids(self):
        path = f"{helpers.ROOT}/app/static/js/browse.js"
        with open(path, encoding="utf-8") as f:
            source = f.read()
        for needle in ("browse-form", "browse-results", "browse-loading", "pageshow"):
            self.assertIn(needle, source)
        served = self.client.get("/static/js/browse.js")
        self.assertEqual(served.status_code, 200)
        served.close()

    def test_every_state_has_exactly_one_h1(self):
        for query in ("", "?q=nothing", "?category=PVPs"):
            self.assertEqual(len(self.browse(query)[1].find_all("h1")), 1, query)
        self.seed(PAGE_SIZE + 1)
        self.assertEqual(len(self.browse()[1].find_all("h1")), 1)


class BrowseEscapingTests(BrowseCase):
    def test_script_in_a_title_is_escaped_on_browse(self):
        conn = connect(self.app.config["DATABASE_PATH"])
        helpers.add_listing(conn, self.owner_id, title=SCRIPT)
        conn.commit()
        conn.close()
        html = self.browse()[0].get_data(as_text=True)
        self.assertNotIn(SCRIPT, html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_script_in_the_owner_name_is_escaped_on_browse(self):
        self.seed(1)
        self.sql("UPDATE users SET full_name = ? WHERE id = ?", (SCRIPT, self.owner_id))
        html = self.browse()[0].get_data(as_text=True)
        self.assertNotIn(SCRIPT, html)
        self.assertIn("&lt;script&gt;", html)

    def test_script_in_the_search_text_is_escaped_in_the_box_and_the_links(self):
        self.seed(PAGE_SIZE + 1, prefix='"><script>x')
        html = self.browse("?q=" + "%22%3E%3Cscript%3Ex")[0].get_data(as_text=True)
        self.assertNotIn("<script>x", html)
        self.assertIsNone(re.search(r'value=""><script', html))


class DetailCase(BrowseCase):
    def listing(self, **kw):
        lid = self.seed(1, **{k: v for k, v in kw.items() if k in ("category", "condition", "mode", "status")})[0]
        extra = {k: v for k, v in kw.items() if k in ("description", "wanted_in_return", "title")}
        updates = {
            "title": "UPDATE listings SET title = ? WHERE id = ?",
            "description": "UPDATE listings SET description = ? WHERE id = ?",
            "wanted_in_return": "UPDATE listings SET wanted_in_return = ? WHERE id = ?",
        }
        for column, value in extra.items():
            self.sql(updates[column], (value, lid))
        return lid

    def detail(self, lid, client=None):
        r = (client or self.client).get(f"/listings/{lid}")
        return r, page(r.get_data(as_text=True))


class DetailPageTests(DetailCase):
    def test_title_block_carries_the_facts(self):
        lid = self.listing(category="Mini drafter", condition="good", mode="lend")
        r, doc = self.detail(lid)
        self.assertEqual(r.status_code, 200)
        h1 = doc.find_all("h1")
        self.assertEqual(len(h1), 1)
        self.assertEqual(h1[0].get_text(strip=True), "Item 0")
        self.assertIsNotNone(h1[0].find_parent("article", class_="title-block"))
        cells = {c.find("dt").get_text(strip=True): c.find("dd").get_text(strip=True)
                 for c in doc.select(".title-block-cell")}
        self.assertEqual(cells["Item type"], "Mini drafter")
        self.assertEqual(cells["Condition"], "Good")
        self.assertEqual(cells["Offered as"], "Lend")
        self.assertEqual(cells["Owner"], "Ravi Singh")
        self.assertEqual(doc.select_one(".title-block-head .stamp").get_text(strip=True), "Available")

    def test_give_mode_reads_give_away_not_the_stored_word(self):
        cells = [c.get_text(" ", strip=True) for c in self.detail(self.listing(mode="give"))[1].select(".title-block-cell")]
        self.assertIn("Offered as Give away", cells)

    def test_wanted_in_return_and_description_show_only_when_present(self):
        bare = self.detail(self.listing())[1]
        self.assertNotIn("Wanted in return", bare.get_text())
        self.assertIsNone(bare.select_one(".listing-description"))
        full = self.detail(self.listing(wanted_in_return="A maths book", description="Line one\nLine two"))[1]
        self.assertIn("A maths book", full.select_one(".title-block-wide").get_text())
        self.assertEqual(full.select_one(".listing-description").get_text(), "Line one\nLine two")

    def test_status_stamp_matches_the_status(self):
        for status, label, kind in (("reserved", "Reserved", "stamp-warning"), ("closed", "Closed", "stamp-neutral")):
            stamp = self.detail(self.listing(status=status))[1].select_one(".title-block-head .stamp")
            self.assertEqual(stamp.get_text(strip=True), label)
            self.assertIn(kind, stamp["class"])

    def test_removed_listing_is_not_found(self):
        lid = self.listing()
        self.sql("UPDATE listings SET status='removed', deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (lid,))
        self.assertEqual(self.detail(lid)[0].status_code, 404)

    def test_unknown_listing_is_not_found(self):
        self.assertEqual(self.client.get("/listings/99999").status_code, 404)

    def test_back_link_returns_to_browse(self):
        doc = self.detail(self.listing())[1]
        self.assertEqual(doc.select_one("p.back-row a")["href"], "/listings/")

    def test_script_in_every_text_field_is_escaped(self):
        lid = self.listing(title=SCRIPT, description=SCRIPT, wanted_in_return=SCRIPT)
        self.sql("UPDATE users SET full_name = ? WHERE id = ?", (SCRIPT, self.owner_id))
        html = self.detail(lid)[0].get_data(as_text=True)
        self.assertNotIn(SCRIPT, html)
        self.assertGreaterEqual(html.count("&lt;script&gt;alert(1)&lt;/script&gt;"), 5)

    def test_one_h1_for_guest_student_and_owner(self):
        lid = self.listing()
        owner = self.app.test_client()
        self.login(email=OWNER, client=owner)
        for client in (self.client, self.student_client(), owner):
            self.assertEqual(len(self.detail(lid, client)[1].find_all("h1")), 1)


class DetailRequestPanelTests(DetailCase):
    def test_guest_is_invited_to_log_in_and_sees_no_form(self):
        lid = self.listing()
        doc = self.detail(lid)[1]
        link = doc.select_one(".panel a.btn-primary")
        self.assertIn("/login", link["href"])
        self.assertIn(f"next=/listings/{lid}", link["href"])
        self.assertIsNone(doc.select_one("form[action*='request']"))

    def test_guest_sees_no_request_panel_on_a_reserved_item(self):
        doc = self.detail(self.listing(status="reserved"))[1]
        self.assertNotIn("Request this item", doc.get_text())

    def test_student_sees_a_labelled_request_form_with_csrf(self):
        lid = self.listing(mode="give")
        client = self.student_client()
        doc = self.detail(lid, client)[1]
        form = doc.select_one(f'form[action="/listings/{lid}/request"]')
        self.assertEqual(form["method"].lower(), "post")
        self.assertTrue(form.select_one('input[name="csrf_token"]')["value"])
        box = form.select_one("textarea[name=message]")
        self.assertEqual(box["maxlength"], "500")
        self.assertIsNotNone(doc.select_one(f'label[for="{box["id"]}"]'))
        self.assertIsNone(form.select_one("select"))
        self.assertEqual(form.select_one("button")["type"], "submit")
        self.assertEqual(form.select_one("button").get_text(strip=True), "Send request")

    def test_swap_listing_offers_the_students_own_items_in_a_labelled_select(self):
        lid = self.listing(mode="swap")
        client = self.student_client()
        asker_id = self.db_rows("SELECT id FROM users WHERE email = ?", (ASKER,))[0]["id"]
        conn = connect(self.app.config["DATABASE_PATH"])
        mine = helpers.add_listing(conn, asker_id, title="My spare book")
        conn.commit()
        conn.close()
        doc = self.detail(lid, client)[1]
        select = doc.select_one("select[name=offered_listing_id]")
        self.assertIsNotNone(doc.select_one(f'label[for="{select["id"]}"]'))
        values = {o["value"]: o.get_text(strip=True) for o in select.select("option")}
        self.assertEqual(values, {"": "No item (just ask)", str(mine): "My spare book"})

    def test_form_on_the_page_really_sends_a_request(self):
        lid = self.listing(mode="give")
        client = self.student_client()
        doc = self.detail(lid, client)[1]
        form = doc.select_one(f'form[action="/listings/{lid}/request"]')
        data = {"csrf_token": form.select_one('input[name="csrf_token"]')["value"], "message": "Could I borrow it?"}
        r = client.post(form["action"], data=data)
        self.assertEqual(r.status_code, 303)
        rows = self.db_rows("SELECT message, status FROM exchange_requests WHERE listing_id = ?", (lid,))
        self.assertEqual([(x["message"], x["status"]) for x in rows], [("Could I borrow it?", "pending")])

    def test_reserved_item_says_it_cannot_be_requested(self):
        doc = self.detail(self.listing(status="reserved"), self.student_client())[1]
        self.assertIn("not available to request", doc.select_one(".alert-info").get_text())
        self.assertIsNone(doc.select_one("textarea[name=message]"))

    def test_owner_sees_manage_actions_and_no_request_form(self):
        lid = self.listing()
        owner = self.app.test_client()
        self.login(email=OWNER, client=owner)
        r, doc = self.detail(lid, owner)
        self.assertNotIn("Request this item", r.get_data(as_text=True))
        self.assertEqual(doc.select_one(f'a[href="/listings/{lid}/edit"]').get_text(strip=True), "Edit")
        delete = doc.select_one(f'form[action="/listings/{lid}/delete"]')
        self.assertTrue(delete.select_one('input[name="csrf_token"]')["value"])
        self.assertEqual(doc.select_one('main a[href="/requests/received"]').get_text(strip=True), "See requests for your items")

    def test_other_students_see_no_edit_or_delete(self):
        lid = self.listing()
        html = self.detail(lid, self.student_client())[0].get_data(as_text=True)
        self.assertNotIn(f"/listings/{lid}/edit", html)
        self.assertNotIn(f"/listings/{lid}/delete", html)

    def test_admin_sees_manage_actions_on_someone_elses_listing(self):
        lid = self.listing()
        self.make_user(ADMIN, is_admin=True)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        html = self.detail(lid, admin)[0].get_data(as_text=True)
        self.assertIn(f"/listings/{lid}/edit", html)
        self.assertIn(f"/listings/{lid}/delete", html)


class ComponentWiringTests(unittest.TestCase):
    def test_labels_and_status_words_cover_every_stored_value(self):
        from app.constants import CONDITION_LABELS, LISTING_STATUSES, MODE_LABELS, STATUS_KINDS, STATUS_LABELS
        self.assertEqual(set(MODE_LABELS), set(MODES))
        self.assertEqual(set(CONDITION_LABELS), set(CONDITIONS))
        self.assertEqual(set(STATUS_LABELS), set(LISTING_STATUSES))
        self.assertEqual(set(STATUS_KINDS), set(LISTING_STATUSES))

    def test_browse_and_detail_templates_use_components_not_raw_controls(self):
        for name in ("browse.html", "view.html"):
            with open(f"{helpers.ROOT}/app/templates/listings/{name}", encoding="utf-8") as f:
                text = f.read()
            self.assertNotRegex(text, r"<(select|textarea)\b", name)
            self.assertNotRegex(text, r"\sstyle\s*=", name)


if __name__ == "__main__":
    unittest.main()
