"""C5: exchange requests - the state machine, permissions and the contact-reveal privacy rules.

Reminder: scripts/check.sh treats certain words in the test log as a gate, so do not use them
in test names or docstrings (see tests/helpers.py).
"""
import re
import unittest

import helpers
from app import accounts, exchange_service as svc
from app.db import connect
from app.permissions import can

PW = "river-stone-42"
DOMAIN = "college.example"


def token_in(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


class World(helpers.TempDirMixin):
    """A temp database with students and listings, plus a logged-in test client per student."""

    def setUp(self):
        self.tmp = self.make_tmp()
        self.app = helpers.make_app(self.tmp)
        self.conn = connect(self.app.config["DATABASE_PATH"])
        self.addCleanup(self.conn.close)
        self.clients = {}

    def student(self, name, phone=None, address=None, social=None, suspended=0):
        uid = accounts.create_user(
            self.conn, email=f"{name}@{DOMAIN}", password=PW, full_name=name.title(), branch="IT", year="SE",
            phone=phone, college_name=f"{name.title()} College", enrollment_no=f"EN-{name}",
            social_link=social, address=address)
        if suspended:
            self.conn.execute("UPDATE users SET is_suspended = 1 WHERE id = ?", (uid,))
            self.conn.commit()
        return uid

    def listing(self, owner_id, title="Item", **kw):
        lid = helpers.add_listing(self.conn, owner_id, title, **kw)
        self.conn.commit()
        return lid

    def user_row(self, uid):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    def lst(self, lid):
        return self.conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()

    def req(self, rid):
        return self.conn.execute("SELECT * FROM exchange_requests WHERE id = ?", (rid,)).fetchone()

    def client_for(self, name):
        if name not in self.clients:
            c = self.app.test_client()
            c.post("/login", data={"csrf_token": token_in(c.get("/login").get_data(as_text=True)),
                                   "email": f"{name}@{DOMAIN}", "password": PW})
            self.clients[name] = c
        return self.clients[name]

    def post(self, name, url, data=None, page=None):
        c = self.client_for(name)
        body = dict(data or {})
        body["csrf_token"] = token_in(c.get(page or "/requests/sent").get_data(as_text=True))
        return c.post(url, data=body)

    def get(self, name, url):
        return self.client_for(name).get(url)


class StateMachineTests(World, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.asker = self.student("owner"), self.student("asker")
        self.o, self.a = self.user_row(self.owner), self.user_row(self.asker)

    def send(self, lid, offered=None, message="hello"):
        return svc.send_request(self.conn, lid, self.a, offered, message)

    def test_every_allowed_transition_ends_in_the_documented_status(self):
        for action, (starts, end) in svc.TRANSITIONS.items():
            for start in starts:
                lid = self.listing(self.owner, mode="give")
                rid = self.send(lid)
                if start == "accepted":
                    svc.act(self.conn, rid, "accept", self.o)
                who = self.o if action in ("accept", "decline") else self.a
                self.assertEqual(svc.act(self.conn, rid, action, who), end, (action, start))
                self.assertEqual(self.req(rid)["status"], end)

    def test_every_forbidden_transition_is_refused_and_changes_nothing(self):
        for action, (starts, _end) in svc.TRANSITIONS.items():
            for bad in ("pending", "accepted", "completed", "declined", "cancelled"):
                if bad in starts:
                    continue
                lid = self.listing(self.owner, mode="give")
                rid = self.send(lid)
                self.conn.execute("UPDATE exchange_requests SET status = ? WHERE id = ?", (bad, rid))
                self.conn.commit()
                before = (self.req(rid)["status"], self.lst(lid)["status"])
                who = self.o if action in ("accept", "decline") else self.a
                with self.assertRaises(svc.ExchangeProblem):
                    svc.act(self.conn, rid, action, who)
                self.assertEqual((self.req(rid)["status"], self.lst(lid)["status"]), before)

    def test_new_request_is_pending_and_listing_stays_available(self):
        lid = self.listing(self.owner)
        rid = self.send(lid)
        self.assertEqual(self.req(rid)["status"], "pending")
        self.assertEqual(self.lst(lid)["status"], "available")

    def test_accept_reserves_listing_and_cancel_releases_it(self):
        lid = self.listing(self.owner)
        rid = self.send(lid)
        svc.act(self.conn, rid, "accept", self.o)
        self.assertEqual(self.lst(lid)["status"], "reserved")
        svc.act(self.conn, rid, "cancel", self.a)
        self.assertEqual(self.lst(lid)["status"], "available")

    def test_owner_may_cancel_an_accepted_request_but_not_a_pending_one(self):
        lid = self.listing(self.owner)
        rid = self.send(lid)
        with self.assertRaises(svc.ExchangeProblem):
            svc.act(self.conn, rid, "cancel", self.o)
        svc.act(self.conn, rid, "accept", self.o)
        svc.act(self.conn, rid, "cancel", self.o)
        self.assertEqual(self.req(rid)["status"], "cancelled")

    def test_completing_a_give_closes_the_listing_and_declines_other_pending_requests(self):
        third = self.student("third")
        lid = self.listing(self.owner, mode="give")
        rid = self.send(lid)
        other = svc.send_request(self.conn, lid, self.user_row(third), None, "")
        svc.act(self.conn, rid, "accept", self.o)
        svc.act(self.conn, rid, "complete", self.a)
        self.assertEqual(self.lst(lid)["status"], "closed")
        self.assertEqual(self.req(other)["status"], "declined")

    def test_completing_a_lend_means_returned_so_listing_is_available_again(self):
        lid = self.listing(self.owner, mode="lend")
        rid = self.send(lid)
        svc.act(self.conn, rid, "accept", self.o)
        self.assertEqual(self.lst(lid)["status"], "reserved")
        svc.act(self.conn, rid, "complete", self.o)
        self.assertEqual(self.lst(lid)["status"], "available")

    def test_swap_with_offer_reserves_then_closes_both_items(self):
        lid = self.listing(self.owner, mode="swap")
        mine = self.listing(self.asker, "Mine", mode="swap")
        rid = self.send(lid, offered=mine)
        svc.act(self.conn, rid, "accept", self.o)
        self.assertEqual((self.lst(lid)["status"], self.lst(mine)["status"]), ("reserved", "reserved"))
        svc.act(self.conn, rid, "complete", self.a)
        self.assertEqual((self.lst(lid)["status"], self.lst(mine)["status"]), ("closed", "closed"))

    def test_cancelling_an_accepted_swap_frees_the_offered_item_too(self):
        lid = self.listing(self.owner, mode="swap")
        mine = self.listing(self.asker, "Mine", mode="swap")
        rid = self.send(lid, offered=mine)
        svc.act(self.conn, rid, "accept", self.o)
        svc.act(self.conn, rid, "cancel", self.a)
        self.assertEqual((self.lst(lid)["status"], self.lst(mine)["status"]), ("available", "available"))

    def test_second_accept_on_the_same_listing_is_refused(self):
        third = self.student("third")
        lid = self.listing(self.owner)
        first = self.send(lid)
        second = svc.send_request(self.conn, lid, self.user_row(third), None, "")
        svc.act(self.conn, first, "accept", self.o)
        with self.assertRaises(svc.ExchangeProblem):
            svc.act(self.conn, second, "accept", self.o)
        self.assertEqual(self.req(second)["status"], "pending")

    def test_removing_a_listing_settles_its_requests(self):
        third = self.student("third")
        lid = self.listing(self.owner, mode="swap")
        mine = self.listing(self.asker, "Mine", mode="swap")
        accepted = self.send(lid, offered=mine)
        pending = svc.send_request(self.conn, lid, self.user_row(third), None, "")
        svc.act(self.conn, accepted, "accept", self.o)
        self.assertTrue(svc.remove_listing(self.conn, lid))
        self.assertEqual(self.req(accepted)["status"], "cancelled")
        self.assertEqual(self.req(pending)["status"], "declined")
        self.assertEqual(self.lst(mine)["status"], "available")
        self.assertEqual(self.lst(lid)["status"], "removed")
        self.assertFalse(svc.remove_listing(self.conn, lid))

    def test_accept_is_refused_when_the_offered_item_is_gone(self):
        lid = self.listing(self.owner, mode="swap")
        mine = self.listing(self.asker, "Mine", mode="swap")
        rid = self.send(lid, offered=mine)
        svc.remove_listing(self.conn, mine)
        with self.assertRaises(svc.ExchangeProblem):
            svc.act(self.conn, rid, "accept", self.o)
        self.assertEqual(self.lst(lid)["status"], "available")


class SendRuleTests(World, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.asker = self.student("owner"), self.student("asker")
        self.o, self.a = self.user_row(self.owner), self.user_row(self.asker)

    def refused(self, *args, status=None):
        with self.assertRaises(svc.ExchangeProblem) as cm:
            svc.send_request(self.conn, *args)
        if status:
            self.assertEqual(cm.exception.status, status)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchange_requests").fetchone()[0], 0)

    def test_own_listing_cannot_be_requested(self):
        self.refused(self.listing(self.owner), self.o, None, "", status=400)

    def test_only_available_listings_can_be_requested(self):
        for status in ("reserved", "closed"):
            self.refused(self.listing(self.owner, status=status), self.a, None, "")
        removed = self.listing(self.owner, status="removed", deleted_at="2026-01-01T00:00:00Z")
        self.refused(removed, self.a, None, "", status=404)
        self.refused(9999, self.a, None, "", status=404)

    def test_one_open_request_per_student_per_listing(self):
        lid = self.listing(self.owner)
        svc.send_request(self.conn, lid, self.a, None, "")
        with self.assertRaises(svc.ExchangeProblem):
            svc.send_request(self.conn, lid, self.a, None, "")
        rid = self.conn.execute("SELECT id FROM exchange_requests").fetchone()[0]
        svc.act(self.conn, rid, "cancel", self.a)
        svc.send_request(self.conn, lid, self.a, None, "")          # allowed again after a cancel

    def test_offer_rules(self):
        swap = self.listing(self.owner, mode="swap")
        give = self.listing(self.owner, mode="give", title="Free")
        theirs = self.listing(self.owner, "Theirs", mode="swap")
        mine = self.listing(self.asker, "Mine", mode="swap")
        reserved = self.listing(self.asker, "Taken", mode="swap", status="reserved")
        self.refused(give, self.a, mine, "", status=400)            # only swap listings take an offer
        self.refused(swap, self.a, theirs, "", status=400)          # must be your own item
        self.refused(swap, self.a, reserved, "", status=400)        # must be available
        self.refused(swap, self.a, 9999, "", status=400)
        self.assertIsNotNone(svc.send_request(self.conn, swap, self.a, mine, ""))

    def test_long_message_is_refused(self):
        self.refused(self.listing(self.owner), self.a, None, "x" * 501, status=400)


class PermissionTests(World, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.asker, self.other = self.student("owner"), self.student("asker"), self.student("other")
        self.lid = self.listing(self.owner)
        self.rid = svc.send_request(self.conn, self.lid, self.user_row(self.asker), None, "")

    def test_requester_cannot_accept_or_decline(self):
        for action in ("accept", "decline"):
            with self.assertRaises(svc.NotAllowed):
                svc.act(self.conn, self.rid, action, self.user_row(self.asker))

    def test_strangers_and_admins_cannot_act_or_even_find_the_request(self):
        admin = self.student("boss")
        self.conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin,))
        self.conn.commit()
        for uid in (self.other, admin):
            for action in svc.TRANSITIONS:
                with self.assertRaises(LookupError):
                    svc.act(self.conn, self.rid, action, self.user_row(uid))
            self.assertIsNone(svc.get_for_party(self.conn, self.rid, self.user_row(uid)))
        self.assertEqual(self.req(self.rid)["status"], "pending")

    def test_can_helper_rules(self):
        listing = self.lst(self.lid)
        o, a, x = (self.user_row(u) for u in (self.owner, self.asker, self.other))
        self.assertTrue(can("request_listing", listing, a))
        self.assertFalse(can("request_listing", listing, o))
        self.assertFalse(can("request_listing", listing, None))
        self.assertFalse(can("request_listing", dict(listing, status="reserved"), a))
        self.assertFalse(can("request_listing", listing, dict(a, is_suspended=1)))
        req = dict(self.req(self.rid), owner_id=self.owner)
        self.assertTrue(can("accept_request", req, o))
        self.assertFalse(can("accept_request", req, a))
        self.assertFalse(can("accept_request", req, x))
        self.assertTrue(can("view_request", req, a) and can("view_request", req, o))
        self.assertFalse(can("view_request", req, x))
        self.assertFalse(can("view_contact", req, o))               # pending: nothing revealed
        self.assertTrue(can("view_contact", dict(req, status="accepted"), a))
        self.assertFalse(can("view_contact", dict(req, status="accepted"), x))
        self.assertFalse(can("view_contact", dict(req, status="declined"), a))
        self.assertFalse(can("no_such_action", req, o))


class RouteAndPrivacyTests(World, unittest.TestCase):
    PHONE_O, PHONE_A = "9111111111", "9222222222"
    ADDR_O, ADDR_A = "12 Owner Street, Pune", "34 Asker Lane, Mumbai"

    def setUp(self):
        super().setUp()
        self.owner = self.student("owner", phone=self.PHONE_O, address=self.ADDR_O, social="u/ownerreddit")
        self.asker = self.student("asker", phone=self.PHONE_A, address=self.ADDR_A, social="@askergram")
        self.other = self.student("other", phone="9333333333", address="56 Other Road")
        self.lid = self.listing(self.owner, "Mini drafter", mode="give")

    def send(self, who="asker", message="please"):
        return self.post(who, f"/listings/{self.lid}/request", {"message": message}, page=f"/listings/{self.lid}")

    def new_request_id(self):
        r = self.send()
        self.assertEqual(r.status_code, 303)
        return int(r.headers["Location"].rstrip("/").rsplit("/", 1)[1])

    def all_private_values(self):
        return (self.PHONE_O, self.PHONE_A, self.ADDR_O, self.ADDR_A, "u/ownerreddit", "@askergram",
                f"owner@{DOMAIN}", f"asker@{DOMAIN}")

    def test_anonymous_visitors_are_sent_to_login(self):
        c = self.app.test_client()
        for url in ("/requests/sent", "/requests/received", "/requests/1"):
            r = c.get(url)
            self.assertEqual(r.status_code, 303, url)
            self.assertIn("/login", r.headers["Location"])
        r = c.post(f"/listings/{self.lid}/request", data={})
        self.assertIn(r.status_code, (303, 403))

    def test_post_without_csrf_token_is_forbidden(self):
        rid = self.new_request_id()
        c = self.client_for("owner")
        for url in (f"/listings/{self.lid}/request", f"/requests/{rid}/accept"):
            self.assertEqual(c.post(url, data={}).status_code, 403)
        self.assertEqual(self.req(rid)["status"], "pending")

    def test_no_private_detail_is_visible_while_pending(self):
        rid = self.new_request_id()
        for who in ("owner", "asker"):
            html = self.get(who, f"/requests/{rid}").get_data(as_text=True)
            for secret in self.all_private_values():
                self.assertNotIn(secret, html, (who, secret))
        for page in ("/requests/sent", "/requests/received", f"/listings/{self.lid}"):
            html = self.get("asker", page).get_data(as_text=True)
            for secret in self.all_private_values():
                self.assertNotIn(secret, html, (page, secret))

    def test_accept_reveals_phone_address_email_and_social_to_both_sides_only(self):
        rid = self.new_request_id()
        self.assertEqual(self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}").status_code, 303)
        owner_view = self.get("owner", f"/requests/{rid}").get_data(as_text=True)
        asker_view = self.get("asker", f"/requests/{rid}").get_data(as_text=True)
        for needle in (self.PHONE_A, self.ADDR_A, "@askergram", f"asker@{DOMAIN}"):
            self.assertIn(needle, owner_view)
        for needle in (self.PHONE_O, self.ADDR_O, "u/ownerreddit", f"owner@{DOMAIN}"):
            self.assertIn(needle, asker_view)
        self.assertNotIn(self.PHONE_O, owner_view)                  # you never see your own box as "theirs"
        self.assertIn("public place", owner_view)
        stranger = self.get("other", f"/requests/{rid}")
        self.assertEqual(stranger.status_code, 404)
        for secret in self.all_private_values():
            self.assertNotIn(secret, stranger.get_data(as_text=True))
        for page in ("/requests/sent", "/requests/received", f"/listings/{self.lid}"):
            html = self.get("other", page).get_data(as_text=True)
            for secret in self.all_private_values():
                self.assertNotIn(secret, html)

    def test_contact_disappears_again_after_decline_or_cancel(self):
        rid = self.new_request_id()
        self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}")
        self.post("asker", f"/requests/{rid}/cancel", page=f"/requests/{rid}")
        html = self.get("owner", f"/requests/{rid}").get_data(as_text=True)
        self.assertNotIn(self.PHONE_A, html)
        self.assertNotIn(self.ADDR_A, html)

    def test_missing_phone_and_address_are_shown_politely(self):
        bare = self.student("bare")
        self.post("bare", f"/listings/{self.lid}/request", {}, page=f"/listings/{self.lid}")
        rid = self.conn.execute("SELECT id FROM exchange_requests WHERE requester_id = ?", (bare,)).fetchone()[0]
        self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}")
        html = self.get("owner", f"/requests/{rid}").get_data(as_text=True)
        self.assertIn("not provided", html)
        self.assertNotIn("None", html)

    def test_wrong_side_and_stranger_get_the_right_status_codes(self):
        rid = self.new_request_id()
        self.assertEqual(self.post("asker", f"/requests/{rid}/accept", page=f"/requests/{rid}").status_code, 403)
        self.assertEqual(self.post("other", f"/requests/{rid}/accept", page="/requests/sent").status_code, 404)
        self.assertEqual(self.post("owner", f"/requests/{rid}/explode", page=f"/requests/{rid}").status_code, 404)
        self.assertEqual(self.post("owner", "/requests/9999/accept", page=f"/requests/{rid}").status_code, 404)
        self.assertEqual(self.req(rid)["status"], "pending")

    def test_state_conflict_shows_a_message_and_keeps_the_state(self):
        rid = self.new_request_id()
        self.post("owner", f"/requests/{rid}/decline", page=f"/requests/{rid}")
        r = self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}")
        self.assertEqual(r.status_code, 303)
        self.assertIn("declined", self.get("owner", f"/requests/{rid}").get_data(as_text=True))
        self.assertEqual(self.req(rid)["status"], "declined")

    def test_cannot_request_own_or_duplicate_through_the_route(self):
        self.post("owner", f"/listings/{self.lid}/request", {}, page=f"/listings/{self.lid}")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchange_requests").fetchone()[0], 0)
        self.send()
        self.send()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchange_requests").fetchone()[0], 1)

    def test_bad_offer_value_is_refused_and_removed_listing_is_404(self):
        r = self.post("asker", f"/listings/{self.lid}/request", {"offered_listing_id": "abc"},
                      page=f"/listings/{self.lid}")
        self.assertEqual(r.status_code, 303)
        gone = self.listing(self.owner, "Gone", status="removed", deleted_at="2026-01-01T00:00:00Z")
        r = self.post("asker", f"/listings/{gone}/request", {}, page=f"/listings/{self.lid}")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchange_requests").fetchone()[0], 0)

    def test_listing_page_shows_request_form_to_others_but_not_the_owner(self):
        self.assertIn("Request this item", self.get("asker", f"/listings/{self.lid}").get_data(as_text=True))
        self.assertNotIn("Request this item", self.get("owner", f"/listings/{self.lid}").get_data(as_text=True))
        self.send()
        rid = self.conn.execute("SELECT id FROM exchange_requests").fetchone()[0]
        self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}")
        self.assertIn("not available", self.get("other", f"/listings/{self.lid}").get_data(as_text=True))

    def test_sent_and_received_lists_show_only_your_own_requests(self):
        rid = self.new_request_id()
        sent = self.get("asker", "/requests/sent").get_data(as_text=True)
        got = self.get("owner", "/requests/received").get_data(as_text=True)
        self.assertIn(f"/requests/{rid}", sent)
        self.assertIn(f"/requests/{rid}", got)
        for who, page in (("other", "/requests/sent"), ("other", "/requests/received"),
                          ("owner", "/requests/sent"), ("asker", "/requests/received")):
            self.assertNotIn(f"/requests/{rid}", self.get(who, page).get_data(as_text=True), (who, page))

    def test_deleting_a_listing_through_the_route_settles_its_requests(self):
        rid = self.new_request_id()
        self.post("owner", f"/listings/{self.lid}/delete", page=f"/listings/{self.lid}/edit")
        self.assertEqual(self.req(rid)["status"], "declined")
        self.assertEqual(self.lst(self.lid)["status"], "removed")

    def test_swap_offer_through_the_form_and_full_happy_path(self):
        swap = self.listing(self.owner, "Calculator", mode="swap")
        mine = self.listing(self.asker, "Sheet holder", mode="swap")
        page = self.get("asker", f"/listings/{swap}").get_data(as_text=True)
        self.assertIn("Sheet holder", page)
        r = self.post("asker", f"/listings/{swap}/request", {"offered_listing_id": str(mine), "message": "deal?"},
                      page=f"/listings/{swap}")
        rid = int(r.headers["Location"].rsplit("/", 1)[1])
        self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}")
        self.post("owner", f"/requests/{rid}/complete", page=f"/requests/{rid}")
        self.assertEqual((self.lst(swap)["status"], self.lst(mine)["status"]), ("closed", "closed"))
        self.assertEqual(self.req(rid)["status"], "completed")


class MigrationGuardTests(World, unittest.TestCase):
    def test_database_rejects_two_accepted_requests_for_one_listing(self):
        import sqlite3
        owner, a, b = self.student("owner"), self.student("asker"), self.student("other")
        lid = self.listing(owner)
        for uid in (a, b):
            self.conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, status) "
                              "VALUES (?, ?, 'pending')", (lid, uid))
        self.conn.commit()
        ids = [r[0] for r in self.conn.execute("SELECT id FROM exchange_requests ORDER BY id")]
        self.conn.execute("UPDATE exchange_requests SET status = 'accepted' WHERE id = ?", (ids[0],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE exchange_requests SET status = 'accepted' WHERE id = ?", (ids[1],))
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
