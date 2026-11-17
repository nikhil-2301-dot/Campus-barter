"""C10: create/edit listing form, delete with and without JavaScript, My listings page, search wildcards.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import re
import sqlite3
import subprocess
import unittest
from unittest import mock

import helpers
import test_browse_ui as base
from app import listings
from app.constants import CATEGORIES, CONDITION_LABELS, CONDITIONS, MODE_LABELS, MODES, STATUS_LABELS
from test_browse_ui import ADMIN, ASKER, OWNER, SCRIPT, page

GOOD = {"title": "Mini drafter", "category": "Mini drafter", "condition": "good", "mode": "swap",
        "description": "Barely used.", "wanted_in_return": "Maths book"}


class FormCase(base.BrowseCase):
    def setUp(self):
        super().setUp()
        self.owner = self.app.test_client()
        self.login(email=OWNER, client=self.owner)

    def submit(self, url, data, client=None, status=None):
        r = self.post(url, data, client=client or self.owner, page=url)
        if status is not None:
            self.assertEqual(r.status_code, status)
        return r, page(r.get_data(as_text=True))

    def listing_rows(self):
        return self.db_rows("SELECT * FROM listings ORDER BY id")


class CreateFormTests(FormCase):
    def test_form_has_labelled_fields_with_limits_from_constants(self):
        doc = page(self.owner.get("/listings/new").get_data(as_text=True))
        self.assertEqual(len(doc.select("h1")), 1)
        for name, limit in (("title", listings.MAX_TITLE_LEN), ("description", listings.MAX_DESC_LEN),
                            ("wanted_in_return", listings.MAX_WANTED_LEN)):
            control = doc.select_one(f"#f-{name}")
            self.assertEqual(control["maxlength"], str(limit), name)
            self.assertIsNotNone(doc.select_one(f'label[for="f-{name}"]'), name)
        self.assertEqual(doc.select_one('input[name="csrf_token"]')["value"] != "", True)

    def test_selects_show_labels_and_keep_stored_values(self):
        doc = page(self.owner.get("/listings/new").get_data(as_text=True))
        modes = {o["value"]: o.get_text(strip=True) for o in doc.select("#f-mode option") if o["value"]}
        self.assertEqual(modes, {m: MODE_LABELS[m] for m in MODES})
        conds = {o["value"]: o.get_text(strip=True) for o in doc.select("#f-condition option") if o["value"]}
        self.assertEqual(conds, {c: CONDITION_LABELS[c] for c in CONDITIONS})
        cats = [o["value"] for o in doc.select("#f-category option") if o["value"]]
        self.assertEqual(cats, list(CATEGORIES))

    def test_empty_post_shows_a_problem_next_to_each_required_field(self):
        r, doc = self.submit("/listings/new", {}, status=400)
        for name in ("title", "category", "condition", "mode"):
            problem = doc.select_one(f"#p-{name}")
            self.assertIsNotNone(problem, name)
            self.assertIn(problem["id"], doc.select_one(f"#f-{name}")["aria-describedby"].split())
            self.assertEqual(problem.parent.select_one(f"#f-{name}") is not None, True, name)
        self.assertEqual(listing_count(self), 0)

    def test_typed_values_are_kept_after_a_problem(self):
        data = dict(GOOD, category="not a type")
        _r, doc = self.submit("/listings/new", data, status=400)
        self.assertEqual(doc.select_one("#f-title")["value"], "Mini drafter")
        self.assertEqual(doc.select_one("#f-description").get_text(), "Barely used.")
        self.assertEqual(doc.select_one("#f-wanted_in_return")["value"], "Maths book")
        self.assertEqual(doc.select_one("#f-mode option[selected]")["value"], "swap")
        self.assertEqual(doc.select_one("#f-condition option[selected]")["value"], "good")
        self.assertIsNotNone(doc.select_one("#p-category"))
        self.assertIsNone(doc.select_one("#p-title"))

    def test_too_long_values_get_their_own_message(self):
        data = dict(GOOD, title="t" * (listings.MAX_TITLE_LEN + 1),
                    description="d" * (listings.MAX_DESC_LEN + 1),
                    wanted_in_return="w" * (listings.MAX_WANTED_LEN + 1))
        _r, doc = self.submit("/listings/new", data, status=400)
        for name in ("title", "description", "wanted_in_return"):
            self.assertIsNotNone(doc.select_one(f"#p-{name}"), name)
        self.assertIsNone(doc.select_one("#p-category"))
        self.assertEqual(listing_count(self), 0)

    def test_valid_post_redirects_with_303_and_stores_the_stored_values(self):
        r, _doc = self.submit("/listings/new", GOOD, status=303)
        row = self.listing_rows()[0]
        self.assertTrue(r.headers["Location"].endswith(f"/listings/{row['id']}"))
        self.assertEqual((row["mode"], row["condition"], row["category"]), ("swap", "good", "Mini drafter"))
        self.assertEqual(row["wanted_in_return"], "Maths book")

    def test_wanted_in_return_is_dropped_unless_swap(self):
        self.submit("/listings/new", dict(GOOD, mode="give"), status=303)
        self.assertIsNone(self.listing_rows()[0]["wanted_in_return"])

    def test_post_without_csrf_token_is_refused_and_nothing_is_stored(self):
        r = self.owner.post("/listings/new", data=GOOD)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(listing_count(self), 0)

    def test_guest_is_sent_to_login(self):
        r = self.client.get("/listings/new")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["Location"])

    def test_script_in_a_typed_value_is_escaped_in_the_form(self):
        _r, doc = self.submit("/listings/new", dict(GOOD, title=SCRIPT, category="x"), status=400)
        self.assertEqual(doc.select_one("#f-title")["value"], SCRIPT)
        self.assertNotIn(SCRIPT, _r.get_data(as_text=True))


class EditFormTests(FormCase):
    def setUp(self):
        super().setUp()
        self.lid = self.seed(1, mode="swap")[0]
        self.url = f"/listings/{self.lid}/edit"

    def test_edit_form_is_filled_from_the_listing(self):
        doc = page(self.owner.get(self.url).get_data(as_text=True))
        self.assertEqual(doc.select_one("#f-title")["value"], "Item 0")
        self.assertEqual(doc.select_one("#f-mode option[selected]")["value"], "swap")
        self.assertEqual(doc.select_one("h1").get_text(strip=True), "Edit listing")

    def test_edit_with_a_problem_keeps_typed_values(self):
        _r, doc = self.submit(self.url, dict(GOOD, title="", description="kept text"), status=400)
        self.assertIsNotNone(doc.select_one("#p-title"))
        self.assertEqual(doc.select_one("#f-description").get_text(), "kept text")
        self.assertEqual(self.listing_rows()[0]["title"], "Item 0")

    def test_edit_saves_and_redirects_with_303(self):
        self.submit(self.url, dict(GOOD, title="New title"), status=303)
        self.assertEqual(self.listing_rows()[0]["title"], "New title")

    def test_other_student_cannot_open_or_save_an_edit(self):
        other = self.student_client()
        self.assertEqual(other.get(self.url).status_code, 403)
        token = base.re.search(r'name="csrf_token" value="([^"]+)"', other.get("/listings/").get_data(as_text=True))
        r = other.post(self.url, data=dict(GOOD, title="Hijacked", csrf_token=token.group(1) if token else ""))
        self.assertIn(r.status_code, (403,))
        self.assertEqual(self.listing_rows()[0]["title"], "Item 0")


class DeleteTests(FormCase):
    def setUp(self):
        super().setUp()
        self.lid = self.seed(1)[0]
        self.url = f"/listings/{self.lid}/delete"

    def status_of(self):
        return self.listing_rows()[0]["status"]

    def test_confirm_page_works_without_javascript_and_changes_nothing(self):
        r = self.owner.get(self.url)
        self.assertEqual(r.status_code, 200)
        doc = page(r.get_data(as_text=True))
        self.assertEqual(len(doc.select("h1")), 1)
        form = doc.select_one(f'form[action="{self.url}"]')
        self.assertEqual(form["method"], "post")
        self.assertTrue(form.select_one('input[name="csrf_token"]')["value"])
        self.assertEqual(self.status_of(), "available")

    def test_confirm_page_post_removes_with_303_to_my_listings(self):
        r, _doc = self.submit(self.url, {}, status=303)
        self.assertTrue(r.headers["Location"].endswith("/listings/mine"))
        self.assertEqual(self.status_of(), "removed")

    def test_other_student_cannot_see_the_confirm_page_or_delete(self):
        other = self.student_client()
        self.assertEqual(other.get(self.url).status_code, 403)
        self.assertEqual(self.status_of(), "available")

    def test_guest_is_sent_to_login_and_nothing_changes(self):
        self.assertEqual(self.client.get(self.url).status_code, 303)
        self.assertEqual(self.status_of(), "available")

    def test_removed_listing_has_no_confirm_page(self):
        self.sql("UPDATE listings SET status = 'removed', deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (self.lid,))
        self.assertEqual(self.owner.get(self.url).status_code, 404)

    def test_post_without_csrf_token_is_refused(self):
        self.assertEqual(self.owner.post(self.url).status_code, 403)
        self.assertEqual(self.status_of(), "available")

    def test_detail_page_offers_link_and_dialog_and_no_inline_handler(self):
        html = self.owner.get(f"/listings/{self.lid}").get_data(as_text=True)
        doc = page(html)
        link = doc.select_one("[data-no-js] a")
        self.assertEqual(link["href"], self.url)
        opener = doc.select_one("[data-needs-js][hidden] button[data-dialog-open]")
        self.assertIsNotNone(opener)
        dialog = doc.select_one(f"dialog#{opener['data-dialog-open']}")
        self.assertEqual(dialog.select_one("form")["action"], self.url)
        self.assertIsNone(re.search(r"\sonclick\s*=", html))

    def test_admin_still_gets_the_delete_controls_on_someone_elses_listing(self):
        self.make_user(ADMIN)
        self.sql("UPDATE users SET is_admin = 1 WHERE email = ?", (ADMIN,))
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        doc = page(admin.get(f"/listings/{self.lid}").get_data(as_text=True))
        self.assertEqual(doc.select_one("[data-no-js] a")["href"], self.url)
        self.assertEqual(admin.get(self.url).status_code, 200)


class MyListingsTests(FormCase):
    def rows(self, doc):
        return doc.select("table tbody tr")

    def test_empty_state_invites_the_first_post(self):
        r = self.owner.get("/listings/mine")
        doc = page(r.get_data(as_text=True))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(doc.select("h1")), 1)
        self.assertIn("You have no listings yet", doc.select_one(".state").get_text())
        self.assertEqual(doc.select_one(".state a")["href"], "/listings/new")
        self.assertIsNone(doc.select_one("table"))

    def test_problem_state_when_the_query_cannot_run(self):
        with self.assertLogs(self.app.logger, level="ERROR"):
            with mock.patch("app.listings.get_db", side_effect=sqlite3.DatabaseError("db unavailable")):
                r = self.owner.get("/listings/mine")
        doc = page(r.get_data(as_text=True))
        self.assertEqual(r.status_code, 503)
        self.assertIsNotNone(doc.select_one(".state-problem"))
        self.assertIsNone(doc.select_one("table"))
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_each_status_gets_its_stamp_and_only_live_items_get_actions(self):
        ids = {}
        for status in ("available", "reserved", "closed", "removed"):
            extra = {"deleted_at": "2026-01-01 00:00:00"} if status == "removed" else {}
            ids[status] = self.seed(1, prefix=status, status=status, **extra)[0]
        doc = page(self.owner.get("/listings/mine").get_data(as_text=True))
        by_title = {}
        for tr in self.rows(doc):
            cells = tr.find_all("td")
            by_title[cells[0].get_text(strip=True)] = tr
        self.assertEqual(len(by_title), 4)
        for status in ids:
            tr = by_title[f"{status} 0"]
            stamp = tr.select_one(".stamp")
            self.assertEqual(stamp.get_text(strip=True), STATUS_LABELS[status])
            self.assertIn(f"stamp-{ {'available': 'ok', 'reserved': 'warning', 'closed': 'neutral', 'removed': 'danger'}[status] }", stamp["class"])
            hrefs = [a["href"] for a in tr.select("a.btn")]
            if status == "removed":
                self.assertEqual(hrefs, [])
                self.assertIsNone(doc.select_one(f"#delete-{ids[status]}"))
            else:
                self.assertIn(f"/listings/{ids[status]}/edit", hrefs)
                self.assertIn(f"/listings/{ids[status]}/delete", hrefs)
                self.assertEqual(doc.select_one(f"#delete-{ids[status]} form")["action"], f"/listings/{ids[status]}/delete")

    def test_cells_show_labels_not_stored_values(self):
        self.seed(1, mode="lend", condition="fair")
        text = page(self.owner.get("/listings/mine").get_data(as_text=True)).select_one("tbody").get_text(" ", strip=True)
        self.assertIn("Lend", text)
        self.assertNotIn(" lend ", text)

    def test_only_my_listings_are_shown_and_titles_are_escaped(self):
        self.seed(1, prefix=SCRIPT)
        other = self.make_user(ASKER)
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        helpers.add_listing(conn, other, title="Somebody else's thing")
        conn.commit()
        conn.close()
        r = self.owner.get("/listings/mine")
        html = r.get_data(as_text=True)
        self.assertNotIn("Somebody else", html)
        self.assertNotIn(SCRIPT, html)

    def test_no_inline_handler_and_delete_works_through_the_page_form(self):
        lid = self.seed(1)[0]
        html = self.owner.get("/listings/mine").get_data(as_text=True)
        self.assertIsNone(re.search(r"\sonclick\s*=", html))
        r = self.post(f"/listings/{lid}/delete", {}, client=self.owner, page="/listings/mine")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.listing_rows()[0]["status"], "removed")

    def test_guest_is_sent_to_login(self):
        self.assertEqual(self.client.get("/listings/mine").status_code, 303)


class SearchWildcardTests(base.BrowseCase):
    def titles(self, q):
        _r, doc = self.browse("?q=" + q)
        return sorted(a.get_text(strip=True) for a in doc.select(".listing-card-title a"))

    def setUp(self):
        super().setUp()
        for title in ("100% cotton coat", "Plain notes", "snake_case notes", "Back\\slash book", "Maths book"):
            conn = helpers.connect(self.app.config["DATABASE_PATH"])
            helpers.add_listing(conn, self.owner_id, title=title)
            conn.commit()
            conn.close()

    def test_percent_matches_only_titles_with_a_percent_sign(self):
        self.assertEqual(self.titles("%25"), ["100% cotton coat"])

    def test_underscore_matches_only_titles_with_an_underscore(self):
        self.assertEqual(self.titles("_"), ["snake_case notes"])

    def test_backslash_matches_only_titles_with_a_backslash(self):
        self.assertEqual(self.titles("%5C"), ["Back\\slash book"])

    def test_percent_and_underscore_also_apply_to_the_description(self):
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE listings SET description = 'half_price 50%' WHERE title = 'Plain notes'")
        conn.commit()
        conn.close()
        self.assertEqual(self.titles("_price"), ["Plain notes"])
        self.assertEqual(self.titles("50%25"), ["Plain notes"])

    def test_normal_search_still_works(self):
        self.assertEqual(self.titles("maths"), ["Maths book"])
        self.assertEqual(self.titles("NOTES"), ["Plain notes", "snake_case notes"])

    def test_sql_in_the_search_text_is_just_text(self):
        self.assertEqual(self.titles("x'%20OR%201=1--"), [])


def listing_count(case):
    return len(case.db_rows("SELECT id FROM listings"))


class DialogScriptTests(unittest.TestCase):
    def test_script_swaps_the_link_for_the_dialog_button_only_when_dialogs_work(self):
        with open(helpers.os.path.join(helpers.ROOT, "app", "static", "js", "dialog.js")) as handle:
            text = handle.read()
        for needle in ("data-needs-js", "data-no-js", 'typeof document.createElement("dialog").showModal'):
            self.assertIn(needle, text)

    def test_no_template_keeps_an_inline_handler(self):
        out = subprocess.run(["grep", "-rEl", r"\son[a-z]+\s*=", helpers.os.path.join(helpers.ROOT, "app", "templates")],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
