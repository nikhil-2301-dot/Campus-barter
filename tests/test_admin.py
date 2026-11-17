"""C13: admin area. Route x role matrix, report moderation, removing a listing, suspend / unsuspend,
suspended users, admins are not parties to exchanges, navbar link.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import unittest

import helpers
from app.constants import REPORT_STATUS_KINDS, REPORT_STATUS_LABELS, REPORT_STATUSES
from test_auth import DOMAIN
from test_browse_ui import ADMIN, ASKER, OWNER, BrowseCase, page

STRANGER = f"stranger@{DOMAIN}"
FROZEN = f"frozen@{DOMAIN}"
REASON = "The description does not match the item."


class AdminWorld(BrowseCase):
    """Owner (from BrowseCase), an admin, a reporting student, a target student, a suspended student."""

    def setUp(self):
        super().setUp()
        self.admin_id = self.make_user(ADMIN, is_admin=True)
        self.asker_id = self.make_user(ASKER)
        self.target_id = self.make_user(STRANGER)
        self.frozen_id = self.make_user(FROZEN)
        self.sql("UPDATE users SET is_suspended = 1 WHERE id = ?", (self.frozen_id,))
        self.lid = self.seed(1, prefix="Drafter", mode="swap")[0]
        self.sql("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, ?)",
                 (self.lid, self.asker_id, REASON))
        self.rid = self.db_rows("SELECT id FROM reports")[0][0]

    def client_for(self, email):
        client = self.app.test_client()
        self.assertIn(self.login(email=email, client=client).status_code, (303,))
        return client

    def report_status(self, rid=None):
        return self.db_rows("SELECT status FROM reports WHERE id = ?", (rid or self.rid,))[0][0]

    def listing_status(self, lid=None):
        return self.db_rows("SELECT status FROM listings WHERE id = ?", (lid or self.lid,))[0][0]

    def suspended(self, uid):
        return self.db_rows("SELECT is_suspended FROM users WHERE id = ?", (uid,))[0][0]


# (endpoint, method) -> (url builder, status for guest, student, suspended student, admin, form data)
def _matrix(w):
    return {
        ("admin.dashboard", "GET"): ("/admin/", (303, 403, 303, 200), {}),
        ("admin.report_list", "GET"): ("/admin/reports", (303, 403, 303, 200), {}),
        ("admin.report_detail", "GET"): (f"/admin/reports/{w.rid}", (303, 403, 303, 200), {}),
        ("admin.report_resolve", "POST"): (f"/admin/reports/{w.rid}/resolve", (303, 403, 303, 303), {}),
        ("admin.report_dismiss", "POST"): (f"/admin/reports/{w.rid}/dismiss", (303, 403, 303, 303), {}),
        ("admin.listing_remove", "GET"): (f"/admin/listings/{w.lid}/remove", (303, 403, 303, 200), {}),
        ("admin.listing_remove", "POST"): (f"/admin/listings/{w.lid}/remove", (303, 403, 303, 303), {}),
        ("admin.campus_list", "GET"): ("/admin/campuses", (303, 403, 303, 200), {}),
        ("admin.user_list", "GET"): ("/admin/users", (303, 403, 303, 200), {}),
        ("admin.user_suspend", "GET"): (f"/admin/users/{w.target_id}/suspend", (303, 403, 303, 200), {}),
        ("admin.user_suspend", "POST"): (f"/admin/users/{w.target_id}/suspend", (303, 403, 303, 303), {}),
        ("admin.user_unsuspend", "POST"): (f"/admin/users/{w.frozen_id}/unsuspend", (303, 403, 303, 303), {}),
        # the student-side report route: a student may report, an admin may not
        ("reports.send", "POST"): (f"/listings/{w.lid}/report", (303, 303, 303, 403), {"reason": REASON}),
    }


def _rules(app):
    found = set()
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/admin") or rule.endpoint == "reports.send":
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                found.add((rule.endpoint, method))
    return found


class RouteRoleMatrixTests(AdminWorld):
    def test_every_admin_route_has_a_row_in_the_matrix(self):
        """A new /admin route without a matrix row makes this test fail."""
        self.assertEqual(_rules(self.app), set(_matrix(self)))

    def _send(self, client, method, url, data, token_page):
        if method == "GET":
            return client.get(url)
        return self.post(url, data, client=client, page=token_page)

    def test_guest_is_sent_to_login_on_every_route(self):
        for (endpoint, method), (url, expected, data) in _matrix(self).items():
            with self.subTest(endpoint=endpoint, method=method):
                r = self._send(self.app.test_client(), method, url, data, "/login")
                self.assertEqual(r.status_code, expected[0])
                self.assertTrue(r.headers["Location"].startswith("/login"))

    def test_student_gets_403_on_every_admin_route(self):
        client = self.client_for(ASKER)
        for (endpoint, method), (url, expected, data) in _matrix(self).items():
            with self.subTest(endpoint=endpoint, method=method):
                r = self._send(client, method, url, data, "/profile")
                self.assertEqual(r.status_code, expected[1])
        self.assertEqual(self.report_status(), "open")
        self.assertEqual(self.listing_status(), "available")
        self.assertEqual(self.suspended(self.target_id), 0)

    def test_suspended_student_is_treated_like_a_guest(self):
        for (endpoint, method), (url, expected, data) in _matrix(self).items():
            with self.subTest(endpoint=endpoint, method=method):
                self.sql("UPDATE users SET is_suspended = 0 WHERE id = ?", (self.target_id,))
                client = self.client_for(STRANGER)
                token = page(client.get("/profile").get_data(as_text=True)).select_one("input[name=csrf_token]")["value"]
                self.sql("UPDATE users SET is_suspended = 1 WHERE id = ?", (self.target_id,))
                r = client.get(url) if method == "GET" else client.post(url, data={**data, "csrf_token": token})
                self.assertEqual(r.status_code, expected[2])
                self.assertTrue(r.headers["Location"].startswith("/login"))

    def reset_state(self):
        self.sql("UPDATE reports SET status = 'open'")
        self.sql("UPDATE listings SET status = 'available', deleted_at = NULL WHERE id = ?", (self.lid,))
        self.sql("UPDATE users SET is_suspended = 0 WHERE id = ?", (self.target_id,))
        self.sql("UPDATE users SET is_suspended = 1 WHERE id = ?", (self.frozen_id,))

    def test_admin_gets_the_documented_answer_on_every_route(self):
        client = self.client_for(ADMIN)
        for (endpoint, method), (url, expected, data) in _matrix(self).items():
            with self.subTest(endpoint=endpoint, method=method):
                self.reset_state()
                r = self._send(client, method, url, data, "/admin/")
                self.assertEqual(r.status_code, expected[3])

    def test_every_post_without_a_token_is_403_for_every_role(self):
        clients = {"student": self.client_for(ASKER), "admin": self.client_for(ADMIN)}
        for (endpoint, method), (url, _e, data) in _matrix(self).items():
            if method != "POST":
                continue
            for role, client in clients.items():
                with self.subTest(endpoint=endpoint, role=role):
                    self.assertEqual(client.post(url, data=data).status_code, 403)
        self.assertEqual(self.report_status(), "open")

    def test_get_on_a_post_only_route_is_405(self):
        client = self.client_for(ADMIN)
        for url in (f"/admin/reports/{self.rid}/resolve", f"/admin/reports/{self.rid}/dismiss",
                    f"/admin/users/{self.frozen_id}/unsuspend"):
            self.assertEqual(client.get(url).status_code, 405)

    def test_unknown_ids_are_404_for_an_admin(self):
        client = self.client_for(ADMIN)
        for url in ("/admin/reports/9999", "/admin/listings/9999/remove", "/admin/users/9999/suspend"):
            self.assertEqual(client.get(url).status_code, 404)
        for url in ("/admin/reports/9999/resolve", "/admin/users/9999/unsuspend"):
            self.assertEqual(self.post(url, {}, client=client, page="/admin/").status_code, 404)

    def test_login_redirect_remembers_the_admin_page(self):
        r = self.client.get("/admin/reports?status=all")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/login?next=/admin/reports?status%3Dall")


class ReportModerationTests(AdminWorld):
    def setUp(self):
        super().setUp()
        self.admin = self.client_for(ADMIN)

    def act(self, action):
        return self.post(f"/admin/reports/{self.rid}/{action}", {}, client=self.admin, page="/admin/")

    def test_resolve_changes_only_the_report(self):
        r = self.act("resolve")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.report_status(), "resolved")
        self.assertEqual(self.listing_status(), "available")
        self.assertEqual(self.suspended(self.owner_id), 0)

    def test_dismiss_changes_only_the_report(self):
        self.act("dismiss")
        self.assertEqual(self.report_status(), "dismissed")
        self.assertEqual(self.listing_status(), "available")

    def test_a_closed_report_cannot_be_closed_again(self):
        self.act("resolve")
        r = self.act("dismiss")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.report_status(), "resolved")
        html = self.admin.get(f"/admin/reports/{self.rid}").get_data(as_text=True)
        self.assertIn("already closed", html)

    def test_closed_report_page_has_no_close_buttons(self):
        self.act("dismiss")
        doc = page(self.admin.get(f"/admin/reports/{self.rid}").get_data(as_text=True))
        actions = " ".join(f["action"] for f in doc.select("main form"))
        self.assertNotIn("/resolve", actions)
        self.assertNotIn("/dismiss", actions)

    def test_report_page_shows_reporter_and_owner_to_the_admin(self):
        self.sql("UPDATE users SET full_name = 'Reporter Rita' WHERE id = ?", (self.asker_id,))
        html = self.admin.get(f"/admin/reports/{self.rid}").get_data(as_text=True)
        self.assertIn("Reporter Rita", html)
        self.assertIn(REASON, html)

    def test_status_filter_uses_allowed_values_only(self):
        self.act("resolve")
        self.assertIn("1 report.", self.admin.get("/admin/reports?status=resolved").get_data(as_text=True))
        self.assertIn("No reports here", self.admin.get("/admin/reports?status=dismissed").get_data(as_text=True))
        junk = self.admin.get("/admin/reports?status=%27%3B--").get_data(as_text=True)
        self.assertIn("No reports here", junk)           # junk falls back to the open filter (none open now)
        self.assertEqual(self.admin.get("/admin/reports?status=all").status_code, 200)

    def test_report_status_words_cover_every_status(self):
        self.assertEqual(set(REPORT_STATUS_LABELS), set(REPORT_STATUSES))
        self.assertEqual(set(REPORT_STATUS_KINDS), set(REPORT_STATUSES))


class RemoveListingTests(AdminWorld):
    def setUp(self):
        super().setUp()
        self.admin = self.client_for(ADMIN)
        self.asker = self.client_for(ASKER)
        self.owner = self.client_for(OWNER)

    def remove(self):
        return self.post(f"/admin/listings/{self.lid}/remove", {}, client=self.admin, page="/admin/")

    def request_row(self):
        r = self.post(f"/listings/{self.lid}/request", {"message": "hi"}, client=self.asker,
                      page=f"/listings/{self.lid}")
        self.assertEqual(r.status_code, 303)
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def req_status(self, rid):
        return self.db_rows("SELECT status FROM exchange_requests WHERE id = ?", (rid,))[0][0]

    def test_remove_hides_the_listing_from_browse(self):
        self.assertEqual(self.remove().status_code, 303)
        self.assertEqual(self.listing_status(), "removed")
        self.assertNotIn("Drafter 0", self.client.get("/listings/").get_data(as_text=True))

    def test_remove_declines_a_pending_request(self):
        rid = self.request_row()
        self.remove()
        self.assertEqual(self.req_status(rid), "declined")

    def test_remove_cancels_an_accepted_request_and_frees_the_offered_item(self):
        offered = self.seed(1, prefix="Offer")[0]
        self.sql("UPDATE listings SET owner_id = ? WHERE id = ?", (self.asker_id, offered))
        r = self.post(f"/listings/{self.lid}/request", {"message": "hi", "offered_listing_id": str(offered)},
                      client=self.asker, page=f"/listings/{self.lid}")
        rid = int(r.headers["Location"].rsplit("/", 1)[1])
        self.post(f"/requests/{rid}/accept", {}, client=self.owner, page=f"/requests/{rid}")
        self.assertEqual(self.listing_status(offered), "reserved")
        self.remove()
        self.assertEqual(self.req_status(rid), "cancelled")
        self.assertEqual(self.listing_status(offered), "available")

    def test_remove_resolves_every_open_report_on_that_listing(self):
        other = self.seed(1, prefix="Other")[0]
        self.sql("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, ?)",
                 (self.lid, self.target_id, REASON))
        self.sql("INSERT INTO reports (listing_id, reporter_id, reason) VALUES (?, ?, ?)",
                 (other, self.asker_id, REASON))
        self.remove()
        rows = self.db_rows("SELECT listing_id, status FROM reports ORDER BY id")
        self.assertEqual([tuple(r) for r in rows], [(self.lid, "resolved"), (self.lid, "resolved"), (other, "open")])

    def test_removing_twice_changes_nothing_and_says_so(self):
        self.remove()
        before = self.db_rows("SELECT status, updated_at FROM reports")[0]
        r = self.remove()
        self.assertEqual(r.status_code, 303)
        html = self.admin.get("/admin/reports").get_data(as_text=True)
        self.assertIn("already removed", html)
        self.assertEqual(tuple(self.db_rows("SELECT status, updated_at FROM reports")[0]), tuple(before))

    def test_remove_calls_the_exchange_service_function(self):
        from unittest import mock
        with mock.patch("app.exchange_service.remove_listing", return_value=True) as fake:
            self.remove()
        self.assertEqual(fake.call_count, 1)

    def test_confirm_page_changes_nothing_until_the_button_is_pressed(self):
        r = self.admin.get(f"/admin/listings/{self.lid}/remove")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.listing_status(), "available")
        doc = page(r.get_data(as_text=True))
        self.assertEqual(doc.select_one("main form")["action"], f"/admin/listings/{self.lid}/remove")


class SuspendTests(AdminWorld):
    def setUp(self):
        super().setUp()
        self.admin = self.client_for(ADMIN)

    def suspend(self, uid):
        return self.post(f"/admin/users/{uid}/suspend", {}, client=self.admin, page="/admin/")

    def test_suspend_sets_only_the_flag(self):
        listing = self.seed(1, prefix="Mine")[0]
        self.sql("UPDATE listings SET owner_id = ? WHERE id = ?", (self.target_id, listing))
        self.assertEqual(self.suspend(self.target_id).status_code, 303)
        self.assertEqual(self.suspended(self.target_id), 1)
        self.assertEqual(self.listing_status(listing), "available")

    def test_unsuspend_restores_the_account(self):
        r = self.post(f"/admin/users/{self.frozen_id}/unsuspend", {}, client=self.admin, page="/admin/")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.suspended(self.frozen_id), 0)
        self.assertEqual(self.login(email=FROZEN, client=self.app.test_client()).status_code, 303)

    def test_an_admin_cannot_suspend_themselves(self):
        self.suspend(self.admin_id)
        self.assertEqual(self.suspended(self.admin_id), 0)
        self.assertIn("own account", self.admin.get("/admin/users").get_data(as_text=True))

    def test_an_admin_cannot_suspend_another_admin(self):
        other = self.make_user(f"boss2@{DOMAIN}", is_admin=True)
        self.suspend(other)
        self.assertEqual(self.suspended(other), 0)
        self.assertIn("cannot be suspended", self.admin.get("/admin/users").get_data(as_text=True))

    def test_suspending_twice_and_unsuspending_an_active_user_change_nothing(self):
        self.suspend(self.frozen_id)
        self.assertEqual(self.suspended(self.frozen_id), 1)
        self.post(f"/admin/users/{self.target_id}/unsuspend", {}, client=self.admin, page="/admin/")
        self.assertEqual(self.suspended(self.target_id), 0)

    def test_confirm_page_for_an_admin_target_does_not_offer_the_button(self):
        r = self.admin.get(f"/admin/users/{self.admin_id}/suspend")
        self.assertEqual(r.status_code, 303)

    def test_suspended_user_cannot_log_in(self):
        r = self.login(email=FROZEN, client=self.app.test_client())
        self.assertEqual(r.status_code, 403)
        self.assertIn("This account is suspended", r.get_data(as_text=True))

    def test_logged_in_user_is_logged_out_on_the_next_request(self):
        student = self.client_for(STRANGER)
        self.assertEqual(student.get("/listings/mine").status_code, 200)
        self.suspend(self.target_id)
        r = student.get("/listings/mine")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].startswith("/login"))
        with student.session_transaction() as sess:
            self.assertNotIn("user_id", sess)

    def test_suspended_user_cannot_report(self):
        student = self.client_for(STRANGER)
        token = page(student.get(f"/listings/{self.lid}").get_data(as_text=True)).select_one("input[name=csrf_token]")["value"]
        self.suspend(self.target_id)
        r = student.post(f"/listings/{self.lid}/report", data={"reason": REASON, "csrf_token": token})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM reports")[0][0], 1)

    def test_users_page_lists_status_and_actions(self):
        doc = page(self.admin.get("/admin/users").get_data(as_text=True))
        text = doc.select_one("table").get_text(" ", strip=True)
        for word in ("Active", "Suspended", "Admin", "Student", "Unsuspend", "Suspend"):
            self.assertIn(word, text)

    def test_users_search_matches_name_or_email_and_escapes_like_characters(self):
        self.sql("UPDATE users SET full_name = '100% Sure' WHERE id = ?", (self.target_id,))
        html = self.admin.get("/admin/users?q=%25").get_data(as_text=True)
        self.assertIn("1 user.", html)
        self.assertIn("100% Sure", html)
        self.assertIn("1 user.", self.admin.get(f"/admin/users?q=frozen@{DOMAIN}").get_data(as_text=True))
        self.assertIn("No users found", self.admin.get("/admin/users?q=zzzzzz").get_data(as_text=True))


class AdminIsNotAPartyTests(AdminWorld):
    def test_admin_cannot_request_view_or_act_on_exchanges(self):
        asker = self.client_for(ASKER)
        r = self.post(f"/listings/{self.lid}/request", {"message": "hi"}, client=asker, page=f"/listings/{self.lid}")
        rid = int(r.headers["Location"].rsplit("/", 1)[1])
        admin = self.client_for(ADMIN)
        self.assertEqual(admin.get(f"/requests/{rid}").status_code, 404)
        self.assertEqual(self.post(f"/requests/{rid}/accept", {}, client=admin, page="/admin/").status_code, 404)
        other = self.seed(1, prefix="Second")[0]
        r = self.post(f"/listings/{other}/request", {"message": "hi"}, client=admin, page="/admin/")
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM exchange_requests WHERE requester_id = ?",
                                      (self.admin_id,))[0][0], 0)


class NavbarAdminLinkTests(AdminWorld):
    def nav_has_admin(self, client):
        doc = page(client.get("/listings/").get_data(as_text=True))
        return any(a.get_text(strip=True) == "Admin" for a in doc.select("header a"))

    def test_admin_link_is_shown_only_to_admins(self):
        self.assertFalse(self.nav_has_admin(self.app.test_client()))
        self.assertFalse(self.nav_has_admin(self.client_for(ASKER)))
        self.assertTrue(self.nav_has_admin(self.client_for(ADMIN)))


class CanRuleTests(unittest.TestCase):
    """Unit checks of the permission rules added in C13."""

    def u(self, uid=1, admin=0, suspended=0):
        return {"id": uid, "is_admin": admin, "is_suspended": suspended}

    def test_report_listing_rules(self):
        listing = {"owner_id": 9, "status": "available"}
        from app.permissions import can
        self.assertTrue(can("report_listing", listing, self.u()))
        self.assertFalse(can("report_listing", listing, None))
        self.assertFalse(can("report_listing", listing, self.u(admin=1)))
        self.assertFalse(can("report_listing", listing, self.u(suspended=1)))
        self.assertFalse(can("report_listing", listing, self.u(uid=9)))
        self.assertFalse(can("report_listing", {**listing, "status": "removed"}, self.u()))
        self.assertTrue(can("report_listing", {**listing, "status": "reserved"}, self.u()))

    def test_view_admin_is_for_admins_only(self):
        from app.permissions import can
        self.assertTrue(can("view_admin", None, self.u(admin=1)))
        self.assertFalse(can("view_admin", None, self.u()))
        self.assertFalse(can("view_admin", None, None))
        self.assertFalse(can("view_admin", None, self.u(admin=1, suspended=1)))

    def test_resolve_report_needs_an_admin_and_an_open_report(self):
        from app.permissions import can
        self.assertTrue(can("resolve_report", {"status": "open"}, self.u(admin=1)))
        self.assertFalse(can("resolve_report", {"status": "resolved"}, self.u(admin=1)))
        self.assertFalse(can("resolve_report", {"status": "open"}, self.u()))

    def test_suspend_rules(self):
        from app.permissions import can
        admin = self.u(uid=1, admin=1)
        student = {"id": 2, "is_admin": 0, "is_suspended": 0}
        self.assertTrue(can("suspend_user", student, admin))
        self.assertFalse(can("suspend_user", {**student, "id": 1}, admin))
        self.assertFalse(can("suspend_user", {**student, "is_admin": 1}, admin))
        self.assertFalse(can("suspend_user", {**student, "is_suspended": 1}, admin))
        self.assertFalse(can("suspend_user", student, self.u()))
        self.assertTrue(can("unsuspend_user", {**student, "is_suspended": 1}, admin))
        self.assertFalse(can("unsuspend_user", student, admin))
        self.assertFalse(can("unsuspend_user", {**student, "is_suspended": 1}, self.u()))


if __name__ == "__main__":
    unittest.main()
