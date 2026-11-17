"""C11: requests UI. Listing request area (form / already requested / admin note), the two inboxes,
the request page, Decline and Cancel confirmation (dialog + plain page), contact privacy, double-submit.
States covered: success, empty, problem (list could not load). There is no loading state: the pages
are built on the server in one query.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import re
import sqlite3
import subprocess
import unittest
from unittest import mock

import helpers
from app import exchange_service
from app.constants import REQUEST_STATUS_KINDS, REQUEST_STATUS_LABELS, REQUEST_STATUSES
from app.db import connect
from test_auth import DOMAIN
from test_browse_ui import ADMIN, ASKER, OWNER, SCRIPT, BrowseCase, page
from test_layout import all_templates

STRANGER = f"stranger@{DOMAIN}"
OWNER_PHONE, OWNER_ADDRESS = "90000 11111", "7 Owner Street, Pune"
ASKER_PHONE, ASKER_ADDRESS = "80000 22222", "9 Asker Lane, Pune"
SCRIPT_FILE = "app/static/js/submit-once.js"


class RequestCase(BrowseCase):
    def setUp(self):
        super().setUp()
        self.sql("UPDATE users SET phone=?, address=?, social_link=? WHERE id=?",
                 (OWNER_PHONE, OWNER_ADDRESS, "u/ownerreddit", self.owner_id))
        self.asker_id = self.make_user(ASKER, phone=ASKER_PHONE, address=ASKER_ADDRESS, social_link="@askergram")
        self.owner = self.app.test_client()
        self.asker = self.app.test_client()
        self.login(email=OWNER, client=self.owner)
        self.login(email=ASKER, client=self.asker)
        self.lid = self.seed(1, prefix="Drafter", mode="swap")[0]

    def send(self, lid=None, message="Please", client=None, offered=None):
        lid = lid or self.lid
        data = {"message": message}
        if offered:
            data["offered_listing_id"] = str(offered)
        return self.post(f"/listings/{lid}/request", data, client=client or self.asker, page=f"/listings/{lid}")

    def new_request(self, **kw):
        r = self.send(**kw)
        self.assertEqual(r.status_code, 303)
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def act(self, rid, action, who=None):
        return self.post(f"/requests/{rid}/{action}", {}, client=who or self.owner, page=f"/requests/{rid}")

    def status(self, rid):
        return self.db_rows("SELECT status FROM exchange_requests WHERE id=?", (rid,))[0][0]

    def view(self, url, client=None):
        r = (client or self.owner).get(url)
        return r, page(r.get_data(as_text=True))

    def private_values(self):
        return (OWNER_PHONE, OWNER_ADDRESS, ASKER_PHONE, ASKER_ADDRESS, "u/ownerreddit", "@askergram")


class ListingRequestAreaTests(RequestCase):
    def test_student_without_a_request_sees_the_form(self):
        _r, doc = self.view(f"/listings/{self.lid}", self.asker)
        self.assertIsNotNone(doc.select_one(f'form[action="/listings/{self.lid}/request"]'))
        self.assertIsNone(doc.find(string=re.compile("You already requested this")))

    def test_pending_request_replaces_the_form_with_a_status_and_link(self):
        rid = self.new_request()
        _r, doc = self.view(f"/listings/{self.lid}", self.asker)
        self.assertIsNone(doc.select_one("form[action$='/request']"))
        self.assertIn("You already requested this", doc.get_text())
        self.assertEqual(doc.select_one(".panel .stamp").get_text(strip=True), "Pending")
        self.assertEqual(doc.find("a", string="View your request")["href"], f"/requests/{rid}")

    def test_accepted_request_shows_the_accepted_stamp(self):
        rid = self.new_request()
        self.act(rid, "accept")
        _r, doc = self.view(f"/listings/{self.lid}", self.asker)
        self.assertEqual(doc.select_one(".panel .stamp").get_text(strip=True), "Accepted")

    def test_a_closed_request_does_not_block_a_new_one(self):
        rid = self.new_request()
        self.act(rid, "decline")
        _r, doc = self.view(f"/listings/{self.lid}", self.asker)
        self.assertIsNotNone(doc.select_one(f'form[action="/listings/{self.lid}/request"]'))
        self.assertIsNone(doc.find(string=re.compile("You already requested this")))

    def test_other_students_still_see_the_form(self):
        self.new_request()
        other = self.student_client(STRANGER)
        _r, doc = self.view(f"/listings/{self.lid}", other)
        self.assertIsNotNone(doc.select_one(f'form[action="/listings/{self.lid}/request"]'))

    def test_admin_who_is_not_the_owner_gets_a_note_and_no_form(self):
        self.make_user(ADMIN, is_admin=True)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        _r, doc = self.view(f"/listings/{self.lid}", admin)
        self.assertIsNone(doc.select_one("form[action$='/request']"))
        self.assertIn("do not take part in swaps", doc.get_text())

    def test_admin_cannot_send_a_request_even_by_posting(self):
        self.make_user(ADMIN, is_admin=True)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        r = self.send(client=admin)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.db_rows("SELECT id FROM exchange_requests"), [])

    def test_owner_sees_a_link_to_received_requests_and_no_form(self):
        _r, doc = self.view(f"/listings/{self.lid}", self.owner)
        self.assertIsNone(doc.select_one("form[action$='/request']"))
        self.assertEqual(doc.find("a", string="See requests for your items")["href"], "/requests/received")

    def test_reserved_item_shows_the_not_available_note(self):
        rid = self.new_request()
        self.act(rid, "accept")
        other = self.student_client(STRANGER)
        _r, doc = self.view(f"/listings/{self.lid}", other)
        self.assertIsNone(doc.select_one("form[action$='/request']"))
        self.assertIn("not available to request", doc.get_text())


class InboxTests(RequestCase):
    def test_received_list_is_a_captioned_table_with_one_h1_and_a_stamp(self):
        rid = self.new_request()
        r, doc = self.view("/requests/received")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(doc.select("h1")), 1)
        self.assertEqual(doc.select_one("table caption").get_text(strip=True), "Requests I received")
        headers = [th.get_text(strip=True) for th in doc.select("thead th")]
        self.assertEqual(headers, ["Item", "From", "Offered in return", "Status", "Updated"])
        row = doc.select_one("tbody tr")
        self.assertEqual(row.select_one("a")["href"], f"/requests/{rid}")
        self.assertEqual(row.select_one(".stamp").get_text(strip=True), "Pending")
        self.assertIn("Ravi Singh", row.get_text())            # the asker's name (both test users share it)
        self.assertIn("No item offered", row.get_text())

    def test_sent_list_names_the_owner_and_the_offered_item(self):
        mine = helpers_listing(self, ASKER, "Sheet holder")
        self.new_request(offered=mine)
        r, doc = self.view("/requests/sent", self.asker)
        self.assertEqual([th.get_text(strip=True) for th in doc.select("thead th")][1], "Owner")
        self.assertIn("Sheet holder", doc.select_one("tbody tr").get_text())

    def test_rows_show_each_status_in_words(self):
        for action, word in (("accept", "Accepted"), ("decline", "Declined")):
            lid = self.seed(1, prefix=f"Item {action}")[0]
            rid = self.new_request(lid=lid)
            self.act(rid, action)
        _r, doc = self.view("/requests/received")
        words = sorted(s.get_text(strip=True) for s in doc.select("tbody .stamp"))
        self.assertEqual(words, ["Accepted", "Declined"])

    def test_empty_received_list_invites_an_action(self):
        r, doc = self.view("/requests/received")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(doc.select_one("table"))
        self.assertIn("Nobody has asked for your items yet", doc.get_text())
        self.assertIsNotNone(doc.find("a", string="See my listings"))

    def test_empty_sent_list_invites_an_action(self):
        _r, doc = self.view("/requests/sent")
        self.assertIsNone(doc.select_one("table"))
        self.assertIn("You have not sent any requests yet", doc.get_text())
        self.assertEqual(doc.find("a", string="Browse listings")["href"], "/listings/")

    def test_problem_state_when_the_list_cannot_load(self):
        for url, name in (("/requests/sent", "list_sent"), ("/requests/received", "list_received")):
            with self.assertLogs(self.app.logger, level="ERROR"):     # C15: the problem is now logged
                with mock.patch.object(exchange_service, name, side_effect=sqlite3.OperationalError("down")):
                    r, doc = self.view(url)
            self.assertEqual(r.status_code, 503, url)
            self.assertIn("could not be loaded", doc.get_text())
            self.assertIsNone(doc.select_one("table"))
            self.assertEqual(len(doc.select("h1")), 1)

    def test_guest_is_sent_to_log_in(self):
        for url in ("/requests/sent", "/requests/received", "/requests/1", "/requests/1/decline"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 303, url)
            self.assertIn("/login", r.headers["Location"])

    def test_user_text_is_escaped_in_the_lists(self):
        lid = self.seed(1, prefix=SCRIPT)[0]
        self.new_request(lid=lid)
        for url, who in (("/requests/received", self.owner), ("/requests/sent", self.asker)):
            html = who.get(url).get_data(as_text=True)
            self.assertNotIn(SCRIPT, html)
            self.assertIn("&lt;script&gt;", html)

    def test_link_between_the_two_lists(self):
        self.new_request()
        _r, doc = self.view("/requests/received")
        self.assertEqual(doc.find("a", string="Requests I sent")["href"], "/requests/sent")


def helpers_listing(case, email, title, **kw):
    conn = connect(case.app.config["DATABASE_PATH"])
    try:
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        lid = helpers.add_listing(conn, uid, title=title, **kw)
        conn.commit()
        return lid
    finally:
        conn.close()


class RequestPageTests(RequestCase):
    def test_page_has_one_h1_a_title_block_and_a_status_stamp(self):
        rid = self.new_request(message="Need it for the lab")
        r, doc = self.view(f"/requests/{rid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(doc.select("h1")), 1)
        block = doc.select_one(".title-block")
        self.assertEqual(block.select_one(".stamp").get_text(strip=True), "Pending")
        text = block.get_text(" ", strip=True)
        for expected in ("Drafter 0", "Swap", "Need it for the lab"):
            self.assertIn(expected, text)

    def test_owner_sees_accept_and_decline_requester_sees_cancel(self):
        rid = self.new_request()
        _r, doc = self.view(f"/requests/{rid}")
        forms = {f.find("button").get_text(strip=True) for f in doc.select("main form.inline-form")}
        self.assertEqual(forms, {"Accept", "Decline"})
        _r, doc = self.view(f"/requests/{rid}", self.asker)
        forms = {f.find("button").get_text(strip=True) for f in doc.select("main form.inline-form")}
        self.assertEqual(forms, {"Cancel request"})

    def test_decline_and_cancel_have_a_dialog_and_a_plain_link_but_accept_has_neither(self):
        rid = self.new_request()
        _r, doc = self.view(f"/requests/{rid}")
        self.assertIsNotNone(doc.select_one("dialog#confirm-decline"))
        self.assertEqual(doc.select_one("span[data-no-js] a")["href"], f"/requests/{rid}/decline")
        self.assertIsNotNone(doc.select_one('[data-dialog-open="confirm-decline"]'))
        self.assertIsNone(doc.select_one("dialog#confirm-accept"))
        _r, doc = self.view(f"/requests/{rid}", self.asker)
        self.assertIsNotNone(doc.select_one("dialog#confirm-cancel"))
        self.assertEqual(doc.select_one("span[data-no-js] a")["href"], f"/requests/{rid}/cancel")

    def test_the_dialog_posts_to_the_same_address_as_the_plain_page(self):
        rid = self.new_request()
        _r, doc = self.view(f"/requests/{rid}")
        form = doc.select_one("dialog#confirm-decline form")
        self.assertEqual(form["method"], "post")
        self.assertEqual(form["action"], f"/requests/{rid}/decline")
        self.assertTrue(form.find("input", attrs={"name": "csrf_token"})["value"])
        self.assertIn("Decline", form.get_text())

    def test_complete_button_only_while_accepted_and_wording_follows_the_mode(self):
        rid = self.new_request()
        self.assertNotIn("Mark as", self.owner.get(f"/requests/{rid}").get_data(as_text=True))
        self.act(rid, "accept")
        self.assertIn("Mark as completed", self.owner.get(f"/requests/{rid}").get_data(as_text=True))
        lend = self.seed(1, prefix="Lent", mode="lend")[0]
        rid2 = self.new_request(lid=lend)
        self.act(rid2, "accept")
        self.assertIn("Mark as returned", self.owner.get(f"/requests/{rid2}").get_data(as_text=True))

    def test_hint_text_follows_the_status_and_the_side(self):
        rid = self.new_request()
        self.assertIn("Accept to share contact details", self.owner.get(f"/requests/{rid}").get_data(as_text=True))
        self.assertIn("Waiting for", self.asker.get(f"/requests/{rid}").get_data(as_text=True))
        self.act(rid, "accept")
        self.assertIn("Arrange the handover", self.asker.get(f"/requests/{rid}").get_data(as_text=True))
        self.act(rid, "complete")
        self.assertIn("This exchange is complete", self.asker.get(f"/requests/{rid}").get_data(as_text=True))

    def test_closed_request_has_no_buttons_and_says_so(self):
        rid = self.new_request()
        self.act(rid, "decline")
        _r, doc = self.view(f"/requests/{rid}")
        self.assertEqual(doc.select("main form.inline-form"), [])
        self.assertEqual(doc.select("dialog"), [])
        self.assertIn("This request is closed", doc.get_text())
        self.assertEqual(doc.select_one(".title-block .stamp").get_text(strip=True), "Declined")

    def test_stranger_gets_404_and_admin_gets_404(self):
        rid = self.new_request()
        self.make_user(ADMIN, is_admin=True)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        for client in (self.student_client(STRANGER), admin):
            self.assertEqual(client.get(f"/requests/{rid}").status_code, 404)

    def test_message_and_offered_title_are_escaped(self):
        mine = helpers_listing(self, ASKER, SCRIPT)
        rid = self.new_request(message=SCRIPT, offered=mine)
        html = self.owner.get(f"/requests/{rid}").get_data(as_text=True)
        self.assertNotIn(SCRIPT, html)
        self.assertIn("&lt;script&gt;", html)


class ContactPrivacyTests(RequestCase):
    def pages_for_both(self, rid):
        for client in (self.owner, self.asker):
            for url in (f"/requests/{rid}", "/requests/sent", "/requests/received", f"/listings/{self.lid}"):
                yield client, url, client.get(url).get_data(as_text=True)

    def test_nothing_private_is_in_any_page_html_while_pending(self):
        rid = self.new_request()
        for _client, url, html in self.pages_for_both(rid):
            for secret in self.private_values():
                self.assertNotIn(secret, html, (url, secret))
        for client in (self.owner, self.asker):
            doc = page(client.get(f"/requests/{rid}").get_data(as_text=True))
            self.assertIsNone(doc.select_one("section.contact"))

    def test_phone_and_address_appear_only_after_accept_and_only_for_the_two_sides(self):
        rid = self.new_request()
        self.act(rid, "accept")
        owner_view = self.owner.get(f"/requests/{rid}").get_data(as_text=True)
        asker_view = self.asker.get(f"/requests/{rid}").get_data(as_text=True)
        for needle in (ASKER_PHONE, ASKER_ADDRESS, "@askergram"):
            self.assertIn(needle, owner_view)
            self.assertNotIn(needle, asker_view)
        for needle in (OWNER_PHONE, OWNER_ADDRESS, "u/ownerreddit"):
            self.assertIn(needle, asker_view)
            self.assertNotIn(needle, owner_view)
        stranger = self.student_client(STRANGER)
        for url in (f"/requests/{rid}", "/requests/sent", "/requests/received", f"/listings/{self.lid}"):
            html = stranger.get(url).get_data(as_text=True)
            for secret in self.private_values():
                self.assertNotIn(secret, html, (url, secret))

    def test_contact_block_is_a_title_block_with_a_safety_note(self):
        rid = self.new_request()
        self.act(rid, "accept")
        doc = page(self.asker.get(f"/requests/{rid}").get_data(as_text=True))
        block = doc.select_one("section.contact .title-block")
        self.assertIsNotNone(block)
        self.assertIn("public place", block.get_text())
        self.assertEqual(len(doc.select("h1")), 1)

    def test_contact_stays_after_complete_and_disappears_after_cancel_or_decline(self):
        rid = self.new_request()
        self.act(rid, "accept")
        self.act(rid, "complete")
        self.assertIn(OWNER_PHONE, self.asker.get(f"/requests/{rid}").get_data(as_text=True))
        lid2 = self.seed(1, prefix="Second")[0]
        rid2 = self.new_request(lid=lid2)
        self.act(rid2, "accept")
        self.act(rid2, "cancel", who=self.asker)
        for client in (self.owner, self.asker):
            html = client.get(f"/requests/{rid2}").get_data(as_text=True)
            for secret in self.private_values():
                self.assertNotIn(secret, html)
        lid3 = self.seed(1, prefix="Third")[0]
        rid3 = self.new_request(lid=lid3)
        self.act(rid3, "decline")
        html = self.owner.get(f"/requests/{rid3}").get_data(as_text=True)
        for secret in self.private_values():
            self.assertNotIn(secret, html)

    def test_missing_phone_is_said_politely_and_no_none_text_appears(self):
        self.sql("UPDATE users SET phone=NULL, address=NULL, social_link=NULL WHERE id=?", (self.owner_id,))
        rid = self.new_request()
        self.act(rid, "accept")
        html = self.asker.get(f"/requests/{rid}").get_data(as_text=True)
        self.assertIn("not provided", html)
        self.assertNotIn("None", html)


class ConfirmPageTests(RequestCase):
    def test_owner_gets_a_plain_confirm_page_for_decline_and_nothing_changes(self):
        rid = self.new_request()
        r, doc = self.view(f"/requests/{rid}/decline")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(doc.select("h1")), 1)
        form = doc.select_one("main form")
        self.assertEqual(form["action"], f"/requests/{rid}/decline")
        self.assertTrue(form.find("input", attrs={"name": "csrf_token"})["value"])
        self.assertEqual(doc.find("a", string="Keep request")["href"], f"/requests/{rid}")
        self.assertEqual(self.status(rid), "pending")

    def test_requester_gets_a_confirm_page_for_cancel_pending_and_accepted(self):
        rid = self.new_request()
        r, doc = self.view(f"/requests/{rid}/cancel", self.asker)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cancel your request", doc.get_text())
        self.act(rid, "accept")
        _r, doc = self.view(f"/requests/{rid}/cancel", self.asker)
        self.assertIn("Cancel the accepted request", doc.get_text())
        self.assertEqual(self.status(rid), "accepted")

    def test_confirm_page_posts_through_to_the_same_route(self):
        rid = self.new_request()
        r = self.post(f"/requests/{rid}/decline", {}, client=self.owner, page=f"/requests/{rid}/decline")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.status(rid), "declined")

    def test_wrong_side_stranger_and_closed_request_are_refused(self):
        rid = self.new_request()
        self.assertEqual(self.asker.get(f"/requests/{rid}/decline").status_code, 403)
        self.assertEqual(self.owner.get(f"/requests/{rid}/cancel").status_code, 403)
        self.assertEqual(self.student_client(STRANGER).get(f"/requests/{rid}/decline").status_code, 404)
        self.assertEqual(self.owner.get("/requests/9999/decline").status_code, 404)
        self.act(rid, "decline")
        self.assertEqual(self.owner.get(f"/requests/{rid}/decline").status_code, 403)
        self.assertEqual(self.asker.get(f"/requests/{rid}/cancel").status_code, 403)

    def test_get_never_runs_an_action(self):
        rid = self.new_request()
        for action in ("accept", "complete", "explode"):
            self.assertIn(self.owner.get(f"/requests/{rid}/{action}").status_code, (404, 405), action)
        self.owner.get(f"/requests/{rid}/decline")
        self.assertEqual(self.status(rid), "pending")


class DoubleSubmitTests(RequestCase):
    def test_sending_twice_makes_one_request(self):
        self.new_request()
        r = self.send()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(len(self.db_rows("SELECT id FROM exchange_requests")), 1)

    def test_accepting_twice_reserves_once_and_keeps_the_state(self):
        rid = self.new_request()
        self.assertEqual(self.act(rid, "accept").status_code, 303)
        self.assertEqual(self.act(rid, "accept").status_code, 303)
        self.assertEqual(self.status(rid), "accepted")
        self.assertEqual(self.db_rows("SELECT status FROM listings WHERE id=?", (self.lid,))[0][0], "reserved")

    def test_declining_or_cancelling_twice_keeps_the_final_state(self):
        rid = self.new_request()
        self.act(rid, "decline")
        self.assertEqual(self.act(rid, "decline").status_code, 303)
        self.assertEqual(self.status(rid), "declined")
        lid2 = self.seed(1, prefix="Other")[0]
        rid2 = self.new_request(lid=lid2)
        self.act(rid2, "cancel", who=self.asker)
        self.assertEqual(self.act(rid2, "cancel", who=self.asker).status_code, 303)
        self.assertEqual(self.status(rid2), "cancelled")

    def test_second_click_shows_a_clear_message_and_a_normal_page(self):
        rid = self.new_request()
        self.act(rid, "decline")
        self.act(rid, "decline")
        r, doc = self.view(f"/requests/{rid}")
        self.assertEqual(r.status_code, 200)
        message = doc.select_one(".alert-danger")
        self.assertIsNotNone(message)
        self.assertIn("cannot be declined", message.get_text())
        self.assertEqual(message["role"], "alert")

    def test_pages_load_the_submit_once_script_and_it_is_valid(self):
        rid = self.new_request()
        for client, url in ((self.owner, f"/requests/{rid}"), (self.owner, f"/requests/{rid}/decline"),
                            (self.asker, f"/listings/{self.lid}")):
            self.assertIn("js/submit-once.js", client.get(url).get_data(as_text=True), url)
        self.assertEqual(self.client.get("/static/js/submit-once.js").status_code, 200)
        done = subprocess.run(["node", "--check", SCRIPT_FILE], capture_output=True, text=True,
                              cwd=helpers.ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)


class HygieneTests(unittest.TestCase):
    def test_request_templates_have_no_inline_styles_or_handlers(self):
        for rel, text in all_templates():
            if rel.startswith("requests/") or rel == "components/request_confirm.html":
                self.assertIsNone(re.search(r"\sstyle\s*=|<style|\son[a-z]+\s*=", text), rel)

    def test_request_status_words_cover_every_status(self):
        self.assertEqual(set(REQUEST_STATUS_LABELS), set(REQUEST_STATUSES))
        self.assertEqual(set(REQUEST_STATUS_KINDS), set(REQUEST_STATUSES))


if __name__ == "__main__":
    unittest.main()
