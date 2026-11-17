"""C14: rate limits. The login throttle (per email and per IP, kept in the database) and the per-hour
limits on sending requests, creating listings and sending reports. The clock is moved by replacing
`app.ratelimit.now`, and old events are made with back-dated rows, so no test waits.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import unittest
from datetime import timedelta
from unittest import mock

from bs4 import BeautifulSoup

import helpers
from app import ratelimit
from app.db import connect
from test_auth import DOMAIN, GOOD_PASSWORD, AuthCase

RAVI = f"ravi@{DOMAIN}"
ASHA = f"asha@{DOMAIN}"
WRONG = "not-the-password-77"
LISTING_FORM = {"title": "Mini drafter", "category": "Books", "condition": "good", "mode": "give",
                "description": "", "wanted_in_return": ""}


class RateCase(AuthCase):
    """AuthCase plus helpers to read and write login_attempts directly."""

    def rows(self, key_prefix=""):
        return self.db_rows('SELECT "key", attempted_at FROM login_attempts WHERE "key" LIKE ?',
                            (key_prefix + "%",))

    def put_rows(self, key, count, minutes_ago=0):
        """Insert `count` events for `key`, dated `minutes_ago` minutes in the past."""
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            moment = ratelimit._stamp(ratelimit.now() - timedelta(minutes=minutes_ago))
            for _ in range(count):
                conn.execute('INSERT INTO login_attempts ("key", attempted_at) VALUES (?, ?)', (key, moment))
            conn.commit()
        finally:
            conn.close()

    def later(self, minutes):
        """Context manager: the app's clock runs `minutes` ahead."""
        moved = ratelimit.now() + timedelta(minutes=minutes)
        return mock.patch("app.ratelimit.now", return_value=moved)

    def wrong(self, email=RAVI, client=None):
        return self.login(email, WRONG, client=client)

    def logged_in(self, client):
        return client.get("/profile").status_code == 200


class LoginThrottleTests(RateCase):
    def setUp(self):
        super().setUp()
        self.make_user(RAVI)
        self.make_user(ASHA)

    def lock_ravi(self):
        for _ in range(ratelimit.LOGIN_EMAIL_MAX):
            self.assertEqual(self.wrong().status_code, 401)

    def test_the_attempt_after_the_limit_is_answered_429_with_a_clear_message(self):
        self.lock_ravi()
        r = self.wrong()
        self.assertEqual(r.status_code, 429)
        self.assertIn("Too many attempts. Try again in 15 minutes.", r.get_data(as_text=True))
        self.assertEqual(r.headers["Retry-After"], str(15 * 60))

    def test_failures_before_the_limit_still_say_wrong_password(self):
        for _ in range(ratelimit.LOGIN_EMAIL_MAX):
            r = self.wrong()
            self.assertEqual(r.status_code, 401)
            self.assertIn("Wrong email or password.", r.get_data(as_text=True))

    def test_the_correct_password_is_also_refused_while_locked(self):
        self.lock_ravi()
        client = self.app.test_client()
        r = self.login(RAVI, GOOD_PASSWORD, client=client)
        self.assertEqual(r.status_code, 429)
        self.assertFalse(self.logged_in(client))

    def test_another_email_is_not_affected(self):
        self.lock_ravi()
        client = self.app.test_client()
        self.assertEqual(self.login(ASHA, GOOD_PASSWORD, client=client).status_code, 303)
        self.assertTrue(self.logged_in(client))

    def test_the_lock_ends_after_the_window(self):
        self.lock_ravi()
        with self.later(16):
            client = self.app.test_client()
            self.assertEqual(self.login(RAVI, GOOD_PASSWORD, client=client).status_code, 303)

    def test_the_lock_still_holds_just_before_the_window_ends(self):
        self.lock_ravi()
        with self.later(14):
            r = self.wrong()
            self.assertEqual(r.status_code, 429)
            self.assertIn("Try again in 1 minute.", r.get_data(as_text=True))

    def test_old_failures_do_not_count(self):
        self.put_rows(f"email:{RAVI}", ratelimit.LOGIN_EMAIL_MAX, minutes_ago=16)
        r = self.wrong()
        self.assertEqual(r.status_code, 401)

    def test_a_successful_login_clears_the_count_for_that_email(self):
        for _ in range(ratelimit.LOGIN_EMAIL_MAX - 1):
            self.wrong()
        client = self.app.test_client()
        self.assertEqual(self.login(RAVI, GOOD_PASSWORD, client=client).status_code, 303)
        self.assertEqual(self.rows(f"email:{RAVI}"), [])
        for _ in range(ratelimit.LOGIN_EMAIL_MAX - 1):       # a fresh allowance, not the old one plus these
            self.assertEqual(self.wrong().status_code, 401)

    def test_a_success_does_not_clear_another_emails_count(self):
        self.wrong(ASHA)
        self.assertEqual(self.login(RAVI, GOOD_PASSWORD, client=self.app.test_client()).status_code, 303)
        self.assertEqual(len(self.rows(f"email:{ASHA}")), 1)

    def test_the_ip_limit_locks_every_email_from_that_address(self):
        self.put_rows("ip:127.0.0.1", ratelimit.LOGIN_IP_MAX)
        client = self.app.test_client()
        r = self.login(ASHA, GOOD_PASSWORD, client=client)
        self.assertEqual(r.status_code, 429)
        self.assertFalse(self.logged_in(client))

    def test_one_address_below_the_ip_limit_is_still_allowed(self):
        self.put_rows("ip:127.0.0.1", ratelimit.LOGIN_IP_MAX - 1)
        self.assertEqual(self.login(ASHA, GOOD_PASSWORD, client=self.app.test_client()).status_code, 303)

    def test_each_wrong_attempt_counts_for_the_address_and_for_the_email(self):
        self.wrong()
        self.wrong()
        self.assertEqual(len(self.rows(f"email:{RAVI}")), 2)
        self.assertEqual(len(self.rows("ip:127.0.0.1")), 2)

    def test_a_refused_attempt_is_not_recorded(self):
        self.lock_ravi()
        for _ in range(3):
            self.assertEqual(self.wrong().status_code, 429)
        self.assertEqual(len(self.rows(f"email:{RAVI}")), ratelimit.LOGIN_EMAIL_MAX)

    def test_the_lock_survives_a_restart(self):
        self.lock_ravi()
        restarted = helpers.make_app(self.tmp)
        client = restarted.test_client()
        self.assertEqual(self.login(RAVI, GOOD_PASSWORD, client=client).status_code, 429)

    def test_an_unknown_email_is_locked_in_the_same_way(self):
        ghost = f"nobody@{DOMAIN}"
        for _ in range(ratelimit.LOGIN_EMAIL_MAX):
            self.assertEqual(self.wrong(ghost).status_code, 401)
        r = self.wrong(ghost)
        self.assertEqual(r.status_code, 429)
        self.assertIn("Too many attempts", r.get_data(as_text=True))

    def test_capital_letters_and_spaces_in_the_email_share_one_count(self):
        for variant in (RAVI.upper(), f"  {RAVI}  ", RAVI, RAVI.title(), f" {RAVI.upper()}"):
            self.wrong(variant)
        self.assertEqual(self.wrong().status_code, 429)

    def test_the_form_is_shown_again_with_the_email_when_locked(self):
        self.lock_ravi()
        doc = BeautifulSoup(self.wrong().get_data(as_text=True), "html.parser")
        self.assertEqual(doc.select_one("input[name=email]")["value"], RAVI)
        self.assertEqual(len(doc.find_all("h1")), 1)

    def test_the_password_is_never_stored_or_logged(self):
        with self.assertLogs(self.app.logger, level="DEBUG") as logged:    # C15: events are logged, secrets are not
            self.lock_ravi()
            self.wrong()
        for secret in (WRONG, GOOD_PASSWORD):
            self.assertNotIn(secret, "\n".join(logged.output))
        everything = " ".join(f"{row['key']} {row['attempted_at']}" for row in self.rows())
        self.assertNotIn(WRONG, everything)
        self.assertNotIn(GOOD_PASSWORD, everything)

    def test_events_older_than_a_day_are_deleted_when_a_new_one_is_written(self):
        self.put_rows("email:old@college.example", 3, minutes_ago=60 * 25)
        self.wrong()
        self.assertEqual(self.rows("email:old@"), [])
        self.assertEqual(len(self.rows(f"email:{RAVI}")), 1)

    def test_a_suspended_account_still_needs_the_right_password_to_hear_it_is_suspended(self):
        conn = connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE users SET is_suspended = 1 WHERE email = ?", (RAVI,))
        conn.commit()
        conn.close()
        self.assertEqual(self.wrong().status_code, 401)
        self.assertEqual(self.login(RAVI, GOOD_PASSWORD).status_code, 403)

    def test_a_blank_email_is_counted_for_the_address_only(self):
        self.login("", WRONG)
        self.assertEqual(self.rows("email:"), [])
        self.assertEqual(len(self.rows("ip:127.0.0.1")), 1)


class ActionThrottleCase(RateCase):
    """Two students and an owner with listings to act on."""

    def setUp(self):
        super().setUp()
        self.owner_id = self.make_user(f"owner@{DOMAIN}")
        self.asha_id = self.make_user(ASHA)
        self.ravi_id = self.make_user(RAVI)
        self.asha = self.app.test_client()
        self.ravi = self.app.test_client()
        self.assertEqual(self.login(ASHA, GOOD_PASSWORD, client=self.asha).status_code, 303)
        self.assertEqual(self.login(RAVI, GOOD_PASSWORD, client=self.ravi).status_code, 303)

    def listings(self, count):
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            ids = [helpers.add_listing(conn, self.owner_id, f"Item {n}") for n in range(count)]
            conn.commit()
            return ids
        finally:
            conn.close()

    def count(self, table):
        return self.db_rows(f"SELECT COUNT(*) FROM {table}")[0][0] if table in ALLOWED_TABLES else None

    def send_request(self, lid, client):
        return self.post(f"/listings/{lid}/request", {"message": "Could I borrow this?"}, client=client,
                         page=f"/listings/{lid}")

    def create_listing(self, client, title="Mini drafter"):
        return self.post("/listings/new", {**LISTING_FORM, "title": title}, client=client)

    def send_report(self, lid, client):
        return self.post(f"/listings/{lid}/report", {"reason": "This listing looks like a scam."},
                         client=client, page=f"/listings/{lid}")


ALLOWED_TABLES = {"exchange_requests", "listings", "reports"}


class RequestThrottleTests(ActionThrottleCase):
    def test_the_request_after_the_limit_is_answered_429_and_nothing_is_stored(self):
        limit = ratelimit.ACTIONS["send_request"][0]
        ids = self.listings(limit + 1)
        for lid in ids[:limit]:
            self.assertEqual(self.send_request(lid, self.asha).status_code, 303)
        self.assertEqual(self.count("exchange_requests"), limit)
        r = self.send_request(ids[limit], self.asha)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.count("exchange_requests"), limit)

    def test_another_student_is_not_affected(self):
        limit = ratelimit.ACTIONS["send_request"][0]
        ids = self.listings(1)
        self.put_rows(f"send_request:{self.asha_id}", limit)
        self.assertEqual(self.send_request(ids[0], self.asha).status_code, 429)
        self.assertEqual(self.send_request(ids[0], self.ravi).status_code, 303)

    def test_the_count_is_per_hour(self):
        limit = ratelimit.ACTIONS["send_request"][0]
        ids = self.listings(1)
        self.put_rows(f"send_request:{self.asha_id}", limit)
        self.assertEqual(self.send_request(ids[0], self.asha).status_code, 429)
        with self.later(61):
            self.assertEqual(self.send_request(ids[0], self.asha).status_code, 303)

    def test_events_older_than_an_hour_do_not_count(self):
        limit = ratelimit.ACTIONS["send_request"][0]
        ids = self.listings(1)
        self.put_rows(f"send_request:{self.asha_id}", limit, minutes_ago=61)
        self.assertEqual(self.send_request(ids[0], self.asha).status_code, 303)


class ListingThrottleTests(ActionThrottleCase):
    def test_the_listing_after_the_limit_is_answered_429_and_nothing_is_stored(self):
        limit = ratelimit.ACTIONS["create_listing"][0]
        for n in range(limit):
            self.assertEqual(self.create_listing(self.asha, f"Book {n}").status_code, 303)
        self.assertEqual(self.count("listings"), limit)
        r = self.create_listing(self.asha, "One too many")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.count("listings"), limit)

    def test_another_student_is_not_affected(self):
        self.put_rows(f"create_listing:{self.asha_id}", ratelimit.ACTIONS["create_listing"][0])
        self.assertEqual(self.create_listing(self.asha).status_code, 429)
        self.assertEqual(self.create_listing(self.ravi).status_code, 303)

    def test_the_count_is_per_hour(self):
        self.put_rows(f"create_listing:{self.asha_id}", ratelimit.ACTIONS["create_listing"][0])
        with self.later(61):
            self.assertEqual(self.create_listing(self.asha).status_code, 303)

    def test_opening_the_form_is_never_counted(self):
        self.put_rows(f"create_listing:{self.asha_id}", ratelimit.ACTIONS["create_listing"][0])
        for _ in range(3):
            self.assertEqual(self.asha.get("/listings/new").status_code, 200)

    def test_the_other_actions_do_not_share_this_count(self):
        self.put_rows(f"send_report:{self.asha_id}", ratelimit.ACTIONS["send_report"][0])
        self.put_rows(f"send_request:{self.asha_id}", ratelimit.ACTIONS["send_request"][0])
        self.assertEqual(self.create_listing(self.asha).status_code, 303)


class ReportThrottleTests(ActionThrottleCase):
    def test_the_report_after_the_limit_is_answered_429_and_nothing_is_stored(self):
        limit = ratelimit.ACTIONS["send_report"][0]
        ids = self.listings(limit + 1)
        for lid in ids[:limit]:
            self.assertEqual(self.send_report(lid, self.asha).status_code, 303)
        self.assertEqual(self.count("reports"), limit)
        r = self.send_report(ids[limit], self.asha)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.count("reports"), limit)

    def test_another_student_is_not_affected(self):
        ids = self.listings(1)
        self.put_rows(f"send_report:{self.asha_id}", ratelimit.ACTIONS["send_report"][0])
        self.assertEqual(self.send_report(ids[0], self.asha).status_code, 429)
        self.assertEqual(self.send_report(ids[0], self.ravi).status_code, 303)

    def test_the_count_is_per_hour(self):
        ids = self.listings(1)
        self.put_rows(f"send_report:{self.asha_id}", ratelimit.ACTIONS["send_report"][0])
        with self.later(61):
            self.assertEqual(self.send_report(ids[0], self.asha).status_code, 303)

    def test_a_refused_report_still_uses_one_slot(self):
        ids = self.listings(1)
        self.assertEqual(self.post(f"/listings/{ids[0]}/report", {"reason": "short"}, client=self.asha,
                                   page=f"/listings/{ids[0]}").status_code, 400)
        self.assertEqual(len(self.rows(f"send_report:{self.asha_id}")), 1)


class TooManyPageTests(ActionThrottleCase):
    def limited_response(self):
        ids = self.listings(1)
        self.put_rows(f"send_report:{self.asha_id}", ratelimit.ACTIONS["send_report"][0])
        token = self.asha.get(f"/listings/{ids[0]}").get_data(as_text=True)
        return ids[0], self.post(f"/listings/{ids[0]}/report", {"reason": "This listing looks like a scam."},
                                 client=self.asha, token=helpers_token(token),
                                 headers={"Referer": f"http://localhost/listings/{ids[0]}"})

    def test_the_page_has_one_h1_a_retry_link_and_a_retry_after_header(self):
        lid, r = self.limited_response()
        self.assertEqual(r.status_code, 429)
        doc = BeautifulSoup(r.get_data(as_text=True), "html.parser")
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertEqual(doc.find("h1").get_text(strip=True), "Too many tries")
        retry = [a for a in doc.find_all("a") if a.get_text(strip=True) == "Try again"]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]["href"], f"/listings/{lid}")
        self.assertIn("Try again in", doc.get_text())
        self.assertTrue(0 < int(r.headers["Retry-After"]) <= 3600)

    def test_the_retry_link_never_leaves_the_site(self):
        ids = self.listings(1)
        self.put_rows(f"send_report:{self.asha_id}", ratelimit.ACTIONS["send_report"][0])
        token = helpers_token(self.asha.get(f"/listings/{ids[0]}").get_data(as_text=True))
        for referer in ("https://evil.example/steal", "//evil.example", "javascript:alert(1)"):
            r = self.post(f"/listings/{ids[0]}/report", {"reason": "This listing looks like a scam."},
                          client=self.asha, token=token, headers={"Referer": referer})
            doc = BeautifulSoup(r.get_data(as_text=True), "html.parser")
            href = [a["href"] for a in doc.find_all("a") if a.get_text(strip=True) == "Try again"][0]
            self.assertEqual(href, "/listings/", referer)

    def test_the_page_never_shows_another_students_details(self):
        _lid, r = self.limited_response()
        self.assertNotIn(RAVI, r.get_data(as_text=True))
        self.assertNotIn("send_report:", r.get_data(as_text=True))


class AdminAndSafeRouteTests(ActionThrottleCase):
    def test_admin_routes_are_not_throttled(self):
        admin_id = self.make_user(f"admin@{DOMAIN}", is_admin=True)
        admin = self.app.test_client()
        self.assertEqual(self.login(f"admin@{DOMAIN}", GOOD_PASSWORD, client=admin).status_code, 303)
        for _ in range(30):
            r = self.post(f"/admin/users/{self.asha_id}/unsuspend", {}, client=admin, page="/admin/users")
            self.assertNotEqual(r.status_code, 429)
        self.assertEqual(self.rows(f"send_report:{admin_id}"), [])

    def test_reading_pages_is_never_counted(self):
        ids = self.listings(1)
        for _ in range(30):
            self.assertEqual(self.asha.get(f"/listings/{ids[0]}").status_code, 200)
            self.assertEqual(self.asha.get("/listings/").status_code, 200)
        self.assertEqual(self.rows(f"send_"), [])

    def test_a_post_without_the_form_token_is_not_counted(self):
        ids = self.listings(1)
        r = self.asha.post(f"/listings/{ids[0]}/report", data={"reason": "This listing looks like a scam."})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rows("send_report:"), [])

    def test_a_guest_post_is_sent_to_the_login_page_and_not_counted(self):
        ids = self.listings(1)
        guest = self.app.test_client()
        token = helpers_token(guest.get("/login").get_data(as_text=True))
        r = guest.post(f"/listings/{ids[0]}/report", data={"csrf_token": token, "reason": "This one is a scam."})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.rows("send_report:"), [])

    def test_the_limits_are_the_numbers_agreed_for_v1(self):
        self.assertEqual((ratelimit.LOGIN_EMAIL_MAX, ratelimit.LOGIN_IP_MAX, ratelimit.LOGIN_WINDOW),
                         (5, 20, 900))
        self.assertEqual(ratelimit.ACTIONS, {"send_request": (20, 3600), "create_listing": (10, 3600),
                                             "send_report": (5, 3600)})


class SignupThrottleTests(RateCase):
    def signup_as(self, n, client=None):
        return self.signup(client=client or self.app.test_client(), email=f"new{n}@{DOMAIN}",
                           enrollment_no=f"ENR{n:04d}")

    def test_the_signup_after_the_limit_is_answered_429_and_no_account_is_made(self):
        self.put_rows("signup:127.0.0.1", ratelimit.SIGNUP_MAX)
        r = self.signup_as(1)
        self.assertEqual(r.status_code, 429)
        self.assertIsNone(self.user_row(f"new1@{DOMAIN}"))
        self.assertEqual(r.headers["Retry-After"], str(3600))

    def test_a_good_signup_is_counted_once(self):
        self.assertEqual(self.signup_as(1).status_code, 303)
        self.assertEqual(len(self.rows("signup:")), 1)

    def test_a_refused_signup_form_is_not_counted(self):
        r = self.signup(email="not-an-email")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.rows("signup:"), [])

    def test_a_taken_email_is_not_counted(self):
        self.make_user(f"new1@{DOMAIN}")
        self.assertEqual(self.signup_as(1).status_code, 400)
        self.assertEqual(self.rows("signup:"), [])

    def test_another_address_is_not_affected(self):
        self.put_rows("signup:127.0.0.1", ratelimit.SIGNUP_MAX)
        other = self.app.test_client()
        other.environ_base["REMOTE_ADDR"] = "10.9.8.7"
        self.assertEqual(self.signup_as(2, client=other).status_code, 303)

    def test_the_count_is_per_hour(self):
        self.put_rows("signup:127.0.0.1", ratelimit.SIGNUP_MAX)
        with self.later(61):
            self.assertEqual(self.signup_as(3).status_code, 303)

    def test_the_signup_page_itself_is_never_limited(self):
        self.put_rows("signup:127.0.0.1", ratelimit.SIGNUP_MAX)
        self.assertEqual(self.client.get("/signup").status_code, 200)


def helpers_token(html):
    from test_auth import token_from
    return token_from(html)


if __name__ == "__main__":
    unittest.main()
