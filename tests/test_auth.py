import zlib
import re
import unittest

import helpers
from app import accounts, create_app
from app.db import connect

DOMAIN = "college.example"
GOOD_PASSWORD = "river-stone-42"


def token_from(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf_token field in the page"
    return m.group(1)


def signup_form(**over):
    data = {"email": f"asha@{DOMAIN}", "full_name": "Asha Verma", "branch": "Information Technology",
            "year": "SE", "phone": "98765 43210", "password": GOOD_PASSWORD,
            "password_confirm": GOOD_PASSWORD,
            "college_name": "Test Engineering College", "enrollment_no": "TEC2024001"}
    data.update(over)
    return data


class AuthCase(helpers.TempDirMixin, unittest.TestCase):
    """Base: a fresh migrated app and client per test, plus small helpers."""

    def setUp(self):
        self.tmp = self.make_tmp()
        self.app = helpers.make_app(self.tmp)
        self.client = self.app.test_client()

    def db_rows(self, sql, params=()):
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def user_row(self, email):
        rows = self.db_rows("SELECT * FROM users WHERE email = ?", (email,))
        return rows[0] if rows else None

    def post(self, url, data, client=None, token=True, **kw):
        client = client or self.client
        data = dict(data)
        if token is True:
            data["csrf_token"] = token_from(client.get(kw.pop("page", url)).get_data(as_text=True))
        elif token:
            data["csrf_token"] = token
        return client.post(url, data=data, **kw)

    def signup(self, client=None, **over):
        return self.post("/signup", signup_form(**over), client=client)

    def make_user(self, email=f"ravi@{DOMAIN}", password=GOOD_PASSWORD, **kw):
        conn = connect(self.app.config["DATABASE_PATH"])
        try:
            kw.setdefault("college_name", "Test Engineering College")
            kw.setdefault("enrollment_no", "TEC" + str(zlib.crc32(email.encode())))
            uid = accounts.create_user(conn, email=email, password=password, full_name="Ravi Singh",
                                       branch="Computer Engineering", year="TE", **kw)
            return uid
        finally:
            conn.close()

    def login(self, email=f"ravi@{DOMAIN}", password=GOOD_PASSWORD, client=None, **extra):
        return self.post("/login", {"email": email, "password": password, **extra}, client=client, page="/login")


class EmailRuleTests(unittest.TestCase):
    def test_good_email_is_accepted(self):
        self.assertIsNone(accounts.check_email("asha.v+x@college.example"))

    def test_any_provider_is_accepted(self):
        for good in ("asha@gmail.com", "asha.v+x@googlemail.com", "asha@outlook.com", "a@sub.college.ac.in", "a@college.example"):
            self.assertIsNone(accounts.check_email(good), good)

    def test_malformed_addresses_are_refused(self):
        for bad in ("a@college.example@evil.com", "a@@college.example", "college.example", "@college.example",
                    "a b@college.example", ".a@college.example", "a@college.example\n", "a@localhost",
                    "a@-bad.com", "a@bad..com", "a@gmail", "a@gmail.c", ""):
            self.assertIsNotNone(accounts.check_email(bad), bad)

    def test_normalize_lowercases_and_strips(self):
        self.assertEqual(accounts.normalize_email("  Asha@College.EXAMPLE "), "asha@college.example")

    def test_very_long_email_is_refused(self):
        self.assertIsNotNone(accounts.check_email("a" * 300 + "@college.example"))


class PasswordAndProfileRuleTests(unittest.TestCase):
    def test_password_rules(self):
        self.assertIsNone(accounts.check_password("river-stone-42", "river-stone-42", "a@college.example"))
        self.assertIsNotNone(accounts.check_password("short1", "short1"))
        self.assertIsNotNone(accounts.check_password("x" * 129, "x" * 129))
        self.assertIsNotNone(accounts.check_password("Password123", "Password123"))
        self.assertIsNotNone(accounts.check_password("asha.verma", "asha.verma", "asha.verma@college.example"))
        self.assertIsNotNone(accounts.check_password("river-stone-42", "river-stone-43"))

    _PROFILE_BASE = {
        "full_name": "Asha Verma", "branch": "IT", "year": "FE",
        "college_name": "Test Engineering College", "enrollment_no": "TEC2024001",
    }

    def test_phone_rules(self):
        base = dict(self._PROFILE_BASE, phone="+91 98765-43210")
        ok, problems = accounts.validate_profile(base)
        self.assertEqual(problems, {})
        self.assertEqual(ok["phone"], "+919876543210")
        for bad in ("12345", "abcdefghij", "98765 4321x", "1" * 16):
            _, problems = accounts.validate_profile({**self._PROFILE_BASE, "phone": bad})
            self.assertIn("phone", problems, bad)
        ok, problems = accounts.validate_profile(dict(self._PROFILE_BASE))
        self.assertIsNone(ok["phone"])
        self.assertEqual(problems, {})

    def test_name_and_year_rules(self):
        base = dict(self._PROFILE_BASE)
        for field, bad in (("full_name", "A"), ("full_name", "x" * 81), ("full_name", "Bad\x00Name"),
                           ("full_name", "evil\u202ename"), ("branch", ""), ("year", "XX"), ("year", "")):
            _, problems = accounts.validate_profile({**base, field: bad})
            self.assertIn(field, problems, (field, bad))

    def test_zero_width_joiner_is_allowed_in_names(self):
        _, problems = accounts.validate_profile(
            {**self._PROFILE_BASE, "full_name": "\u0915\u094d\u200d\u0937 Kumar"})
        self.assertEqual(problems, {})

    def test_whitespace_in_names_is_collapsed(self):
        clean, _ = accounts.validate_profile({**self._PROFILE_BASE, "full_name": "  Asha \n  Verma "})
        self.assertEqual(clean["full_name"], "Asha Verma")

    def test_enrollment_no_required(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "enrollment_no": ""})
        self.assertIn("enrollment_no", problems)

    def test_enrollment_no_invalid_chars(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "enrollment_no": "TC 2024!"})
        self.assertIn("enrollment_no", problems)

    def test_enrollment_no_valid(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "enrollment_no": "PVPIT/2024/001"})
        self.assertNotIn("enrollment_no", problems)

    def test_college_name_required(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "college_name": ""})
        self.assertIn("college_name", problems)

    def test_social_link_valid_reddit(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "social_link": "u/myuser123"})
        self.assertNotIn("social_link", problems)

    def test_social_link_valid_instagram(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "social_link": "@myinsta"})
        self.assertNotIn("social_link", problems)

    def test_social_link_valid_url(self):
        _, problems = accounts.validate_profile(
            {**self._PROFILE_BASE, "social_link": "https://reddit.com/u/me"})
        self.assertNotIn("social_link", problems)

    def test_social_link_invalid(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "social_link": "not-a-link"})
        self.assertIn("social_link", problems)

    def test_social_link_optional(self):
        _, problems = accounts.validate_profile({**self._PROFILE_BASE, "social_link": ""})
        self.assertNotIn("social_link", problems)


class SignupTests(AuthCase):
    def test_signup_page_has_labels_and_a_csrf_field(self):
        r = self.client.get("/signup")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="csrf_token"', html)
        for field in ("email", "full_name", "college_name", "branch", "year", "enrollment_no",
                      "phone", "password", "password_confirm"):
            self.assertIn(f'<label for="f-{field}"', html)
        self.assertIn('autocomplete="new-password"', html)

    def test_good_signup_creates_a_hashed_user_and_logs_in(self):
        r = self.signup(email="Asha@College.Example ")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/profile"))
        row = self.user_row(f"asha@{DOMAIN}")
        self.assertIsNotNone(row)
        self.assertEqual((row["is_admin"], row["is_suspended"], row["year"]), (0, 0, "SE"))
        self.assertEqual(row["phone"], "9876543210")
        self.assertEqual(row["college_name"], "Test Engineering College")
        self.assertEqual(row["enrollment_no"], "TEC2024001")
        self.assertNotIn(GOOD_PASSWORD, tuple(row))
        self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_signup_cannot_make_an_admin_or_suspend_flags(self):
        self.signup(is_admin="1", is_suspended="1", id="99")
        row = self.user_row(f"asha@{DOMAIN}")
        self.assertEqual((row["is_admin"], row["is_suspended"]), (0, 0))
        self.assertNotEqual(row["id"], 99)

    def test_gmail_signup_is_accepted_and_stored(self):
        r = self.signup(email="Asha.V@Gmail.com")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(len(self.db_rows("SELECT * FROM users WHERE email = ?", ("asha.v@gmail.com",))), 1)

    def test_malformed_email_is_refused_and_nothing_is_stored(self):
        r = self.signup(email="asha@gmail")
        self.assertEqual(r.status_code, 400)
        self.assertIn("valid email", r.get_data(as_text=True))
        self.assertEqual(self.db_rows("SELECT * FROM users"), [])

    def test_signup_page_does_not_ask_for_a_college_address(self):
        text = self.client.get("/signup").get_data(as_text=True)
        self.assertNotIn("college email", text.lower())
        self.assertIn("any email", text.lower())

    def test_duplicate_email_is_refused_even_with_different_case(self):
        self.signup()
        other = self.app.test_client()
        r = self.signup(client=other, email=f"ASHA@{DOMAIN}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already exists", r.get_data(as_text=True))
        self.assertEqual(len(self.db_rows("SELECT * FROM users")), 1)

    def test_invalid_input_keeps_typed_values_but_never_echoes_the_password(self):
        r = self.signup(full_name="A", password="UNIQUE-secret-marker-1", password_confirm="different")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn('value="Information Technology"', html)
        self.assertNotIn("UNIQUE-secret-marker-1", html)
        self.assertIn('aria-invalid="true"', html)
        self.assertEqual(self.db_rows("SELECT * FROM users"), [])

    def test_signup_without_a_csrf_token_is_forbidden(self):
        r = self.client.post("/signup", data=signup_form())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.db_rows("SELECT * FROM users"), [])

    def test_signup_with_a_wrong_csrf_token_is_forbidden(self):
        self.client.get("/signup")
        r = self.post("/signup", signup_form(), token="not-the-token")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.db_rows("SELECT * FROM users"), [])

    def test_csrf_token_from_another_session_is_forbidden(self):
        other = self.app.test_client()
        stolen = token_from(other.get("/signup").get_data(as_text=True))
        self.client.get("/signup")
        r = self.post("/signup", signup_form(), token=stolen)
        self.assertEqual(r.status_code, 403)

    def test_html_in_a_name_is_escaped_on_the_profile_page(self):
        self.signup(full_name="<script>alert(1)</script> Asha")
        html = self.client.get("/profile").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_logged_in_user_is_sent_away_from_signup(self):
        self.signup()
        r = self.client.get("/signup")
        self.assertEqual(r.status_code, 303)


class LoginTests(AuthCase):
    def test_good_login_redirects_to_profile(self):
        self.make_user()
        r = self.login()
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/profile"))
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_email_login_is_case_insensitive(self):
        self.make_user()
        self.assertEqual(self.login(email=f"  RAVI@{DOMAIN.upper()}").status_code, 303)

    def test_wrong_password_and_unknown_email_look_identical(self):
        self.make_user()
        wrong = self.login(password="not-the-password")
        unknown = self.login(email=f"nobody@{DOMAIN}", client=self.app.test_client())
        self.assertEqual((wrong.status_code, unknown.status_code), (401, 401))
        msg = lambda r: re.search(r'role="alert">([^<]+)<', r.get_data(as_text=True)).group(1)
        self.assertEqual(msg(wrong), msg(unknown))
        self.assertEqual(msg(wrong), "Wrong email or password.")
        self.assertEqual(self.client.get("/profile").status_code, 303)

    def test_empty_and_oversized_logins_are_refused(self):
        self.make_user()
        self.assertEqual(self.login(email="", password="").status_code, 401)
        self.assertEqual(self.login(password="x" * 20000).status_code, 401)

    def test_login_page_never_prefills_the_password(self):
        self.make_user()
        html = self.login(password="wrong-one-9").get_data(as_text=True)
        self.assertNotIn("wrong-one-9", html)
        self.assertIn(f'value="ravi@{DOMAIN}"', html)

    def test_suspended_user_cannot_log_in(self):
        self.make_user(email=f"sus@{DOMAIN}")
        conn = connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE users SET is_suspended = 1")
        conn.commit()
        conn.close()
        r = self.login(email=f"sus@{DOMAIN}")
        self.assertEqual(r.status_code, 403)
        self.assertIn("suspended", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/profile").status_code, 303)
        # a wrong password must not reveal that the account is suspended
        r = self.login(email=f"sus@{DOMAIN}", password="wrong-one-9")
        self.assertEqual(r.status_code, 401)

    def test_deleted_account_cannot_log_in(self):
        self.make_user()
        conn = connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE users SET deleted_at = '2026-01-01T00:00:00Z'")
        conn.commit()
        conn.close()
        self.assertEqual(self.login().status_code, 401)

    def test_login_without_a_csrf_token_is_forbidden(self):
        self.make_user()
        r = self.client.post("/login", data={"email": f"ravi@{DOMAIN}", "password": GOOD_PASSWORD})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/profile").status_code, 303)

    def test_login_rotates_the_csrf_token(self):
        self.make_user()
        before = token_from(self.client.get("/login").get_data(as_text=True))
        self.login()
        with self.client.session_transaction() as s:
            self.assertNotEqual(s.get("csrf_token"), before)
            self.assertIn("user_id", s)

    def test_safe_next_is_followed_and_unsafe_next_is_ignored(self):
        self.make_user()
        r = self.login(next="/profile?x=1")
        self.assertTrue(r.headers["Location"].endswith("/profile?x=1"))
        for bad in ("//evil.example", "https://evil.example/x", "/\\evil.example", "javascript:alert(1)",
                    "evil", "/ok\r\nSet-Cookie: a=b", "/" + "a" * 400):
            client = self.app.test_client()
            r = self.login(next=bad, client=client)
            self.assertEqual(r.status_code, 303, bad)
            self.assertTrue(r.headers["Location"].endswith("/profile"), (bad, r.headers["Location"]))

    def test_protected_page_sends_anonymous_user_to_login_with_next(self):
        r = self.client.get("/profile")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login?next=/profile", r.headers["Location"])

    def test_login_page_does_not_echo_an_unsafe_next(self):
        html = self.client.get("/login?next=//evil.example").get_data(as_text=True)
        self.assertNotIn("evil.example", html)

    def test_logged_in_user_is_sent_away_from_login(self):
        self.make_user()
        self.login()
        self.assertEqual(self.client.get("/login").status_code, 303)

    def test_home_redirects_by_login_state(self):
        self.assertEqual(self.client.get("/").status_code, 200)   # C12: guests see the landing page
        self.make_user()
        self.login()
        self.assertTrue(self.client.get("/").headers["Location"].endswith("/listings/"))   # C12: students go to browse


class CsrfRoutingTests(AuthCase):
    def test_unknown_url_and_wrong_method_get_404_and_405_not_403(self):
        self.assertEqual(self.client.post("/no-such-page").status_code, 404)
        self.assertEqual(self.client.post("/health").status_code, 405)

    def test_every_unsafe_method_needs_the_token_on_real_routes(self):
        self.signup()
        for method in ("post", "put", "patch", "delete"):
            r = getattr(self.client, method)("/profile", data={"full_name": "Hacked", "branch": "IT", "year": "BE"})
            self.assertIn(r.status_code, (403, 405), method)
        self.assertEqual(self.user_row(f"asha@{DOMAIN}")["full_name"], "Asha Verma")

    def test_token_in_the_header_is_accepted(self):
        self.signup()
        self.client.get("/profile")   # rendering a form is what creates the new token
        with self.client.session_transaction() as s:
            token = s["csrf_token"]
        r = self.client.post("/profile", data={"full_name": "Asha V", "branch": "IT", "year": "BE",
                                               "college_name": "Test Engineering College",
                                               "enrollment_no": "TEC2024001"},
                             headers={"X-CSRF-Token": token})
        self.assertEqual(r.status_code, 303)


class LogoutAndSessionTests(AuthCase):
    def test_logout_ends_the_session(self):
        self.make_user()
        self.login()
        r = self.post("/logout", {}, page="/profile")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.client.get("/profile").status_code, 303)

    def test_logout_by_link_is_not_allowed(self):
        self.make_user()
        self.login()
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_logout_without_csrf_token_is_forbidden_and_keeps_the_session(self):
        self.make_user()
        self.login()
        self.assertEqual(self.client.post("/logout").status_code, 403)
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_logout_when_not_logged_in_is_harmless(self):
        self.client.get("/login")
        r = self.post("/logout", {}, page="/login")
        self.assertEqual(r.status_code, 303)

    def test_session_stops_working_when_the_user_is_suspended(self):
        uid = self.make_user()
        self.login()
        conn = connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE users SET is_suspended = 1 WHERE id = ?", (uid,))
        conn.commit()
        conn.close()
        self.assertEqual(self.client.get("/profile").status_code, 303)
        with self.client.session_transaction() as s:
            self.assertNotIn("user_id", s)

    def test_session_for_a_missing_user_is_dropped(self):
        with self.client.session_transaction() as s:
            s["user_id"] = 424242
        self.assertEqual(self.client.get("/profile").status_code, 303)

    def test_session_cookie_flags(self):
        self.make_user()
        cookie = self.login().headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)   # development runs over plain HTTP
        self.assertIn("Expires=", cookie)    # permanent session: it has an end date

    def test_production_cookie_is_secure_only(self):
        env = {"APP_ENV": "production", "SECRET_KEY": "k" * 40}
        app = create_app({"DATABASE_PATH": self.tmp + "/p.db"}, environ=env)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(self.app.config["PERMANENT_SESSION_LIFETIME"].days >= 1)

    def test_tampered_session_cookie_is_ignored(self):
        self.make_user()
        self.login()
        jar_cookie = self.client.get_cookie("session")
        self.client.set_cookie("session", jar_cookie.value[:-3] + "AAA")
        self.assertEqual(self.client.get("/profile").status_code, 303)


class ProfileTests(AuthCase):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user()
        self.login()

    def _pf(self, **over):
        """Return a valid profile form dict, optionally overriding fields."""
        base = {"full_name": "Ravi Singh", "branch": "Computer Engineering", "year": "TE",
                "college_name": "Test Engineering College", "enrollment_no": "TEC2024002"}
        base.update(over)
        return base

    def test_profile_page_shows_the_current_values(self):
        html = self.client.get("/profile").get_data(as_text=True)
        self.assertIn('value="Ravi Singh"', html)
        self.assertIn(f"ravi@{DOMAIN}", html)
        self.assertIn('<option value="TE" selected>', html)

    def test_profile_update_saves_changes(self):
        r = self.post("/profile", self._pf(full_name="Ravi K Singh", branch="IT", year="BE",
                                           phone="9000011111"))
        self.assertEqual(r.status_code, 303)
        row = self.user_row(f"ravi@{DOMAIN}")
        self.assertEqual((row["full_name"], row["branch"], row["year"], row["phone"]),
                         ("Ravi K Singh", "IT", "BE", "9000011111"))

    def test_profile_update_saves_new_fields(self):
        r = self.post("/profile", self._pf(college_name="My College", enrollment_no="MC2025999",
                                           social_link="u/ravitest", address="Room 42, College Hostel"))
        self.assertEqual(r.status_code, 303)
        row = self.user_row(f"ravi@{DOMAIN}")
        self.assertEqual(row["college_name"], "My College")
        self.assertEqual(row["enrollment_no"], "MC2025999")
        self.assertEqual(row["social_link"], "u/ravitest")

    def test_profile_update_cannot_change_email_admin_or_password(self):
        before = self.user_row(f"ravi@{DOMAIN}")
        self.post("/profile", self._pf(email=f"x@{DOMAIN}", is_admin="1", is_suspended="1",
                                       password_hash="x", id="5"))
        after = self.user_row(f"ravi@{DOMAIN}")
        self.assertIsNotNone(after)
        self.assertEqual((after["is_admin"], after["is_suspended"], after["id"], after["password_hash"]),
                         (0, 0, before["id"], before["password_hash"]))

    def test_invalid_profile_is_refused_and_not_saved(self):
        r = self.post("/profile", self._pf(full_name="R", year="ZZ", phone="12"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.user_row(f"ravi@{DOMAIN}")["full_name"], "Ravi Singh")

    def test_profile_post_without_csrf_token_is_forbidden(self):
        r = self.client.post("/profile", data=self._pf(full_name="Hacked"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.user_row(f"ravi@{DOMAIN}")["full_name"], "Ravi Singh")

    def test_a_user_only_ever_edits_their_own_row(self):
        other = self.make_user(email=f"meera@{DOMAIN}", enrollment_no="TEC2024099")
        self.post("/profile", self._pf(full_name="Changed Name", id=str(other), user_id=str(other)))
        self.assertEqual(self.user_row(f"meera@{DOMAIN}")["full_name"], "Ravi Singh")
        self.assertEqual(self.user_row(f"ravi@{DOMAIN}")["full_name"], "Changed Name")

    def test_clearing_the_phone_stores_null(self):
        self.post("/profile", self._pf(phone=""))
        self.assertIsNone(self.user_row(f"ravi@{DOMAIN}")["phone"])


class CreateAdminCommandTests(AuthCase):
    def run_cli(self, args, text=""):
        return self.app.test_cli_runner().invoke(args=["create-admin", *args], input=text)

    def test_creates_a_new_admin_with_a_hashed_password(self):
        r = self.run_cli(["--email", f"Boss@{DOMAIN}", "--name", "Site Admin"], "long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertEqual(r.exit_code, 0, r.output)
        row = self.user_row(f"boss@{DOMAIN}")
        self.assertEqual(row["is_admin"], 1)
        self.assertNotIn("long-enough-pw-1", tuple(row))
        self.assertNotIn("long-enough-pw-1", r.output)
        self.login(email=f"boss@{DOMAIN}", password="long-enough-pw-1")
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_promotes_an_existing_user_without_touching_the_password(self):
        self.make_user()
        before = self.user_row(f"ravi@{DOMAIN}")["password_hash"]
        r = self.run_cli(["--email", f"ravi@{DOMAIN}", "--name", "Ravi Singh"])
        self.assertEqual(r.exit_code, 0, r.output)
        row = self.user_row(f"ravi@{DOMAIN}")
        self.assertEqual((row["is_admin"], row["password_hash"]), (1, before))

    def test_accepts_a_gmail_address(self):
        r = self.run_cli(["--email", "boss@gmail.com", "--name", "Site Admin"], "long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertEqual(self.user_row("boss@gmail.com")["is_admin"], 1)

    def test_refuses_bad_addresses_and_weak_or_mismatched_passwords(self):
        r = self.run_cli(["--email", "boss@gmail", "--name", "Site Admin"], "long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertNotEqual(r.exit_code, 0)
        r = self.run_cli(["--email", f"boss@{DOMAIN}", "--name", "Site Admin"], "short\nshort\n")
        self.assertNotEqual(r.exit_code, 0)
        r = self.run_cli(["--email", f"boss@{DOMAIN}", "--name", "Site Admin"], "long-enough-pw-1\nother-pw-12345\n")
        self.assertNotEqual(r.exit_code, 0)
        self.assertEqual(self.db_rows("SELECT * FROM users"), [])


class EnrollmentUniquenessTests(AuthCase):
    """An enrollment number is unique across the whole university (many colleges)."""
    TAKEN = "enrollment number already exists"

    def test_signup_with_a_taken_number_is_refused_even_in_other_case_or_college(self):
        self.make_user(enrollment_no="TEC2024777")
        r = self.signup(email=f"asha@{DOMAIN}", enrollment_no=" tec2024777 ",
                        college_name="Some Other College")
        self.assertEqual(r.status_code, 400)
        self.assertIn(self.TAKEN, r.get_data(as_text=True))
        self.assertIsNone(self.user_row(f"asha@{DOMAIN}"))

    def test_profile_cannot_take_another_students_number(self):
        self.make_user(email=f"meera@{DOMAIN}", enrollment_no="TEC2024099")
        self.signup()
        r = self.post("/profile", {"full_name": "Asha Verma", "branch": "IT", "year": "SE",
                                   "college_name": "Test Engineering College",
                                   "enrollment_no": "TEC2024099"})
        self.assertEqual(r.status_code, 400)
        self.assertIn(self.TAKEN, r.get_data(as_text=True))
        self.assertEqual(self.user_row(f"asha@{DOMAIN}")["enrollment_no"], "TEC2024001")

    def test_profile_can_be_saved_again_with_own_number(self):
        self.signup()
        r = self.post("/profile", {"full_name": "Asha V", "branch": "IT", "year": "SE",
                                   "college_name": "Test Engineering College",
                                   "enrollment_no": "TEC2024001"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.user_row(f"asha@{DOMAIN}")["full_name"], "Asha V")

    def test_database_itself_rejects_a_duplicate_number(self):
        self.make_user(enrollment_no="TEC2024555")
        with self.assertRaises(accounts.EnrollmentTaken):
            self.make_user(email=f"other@{DOMAIN}", enrollment_no="TEC2024555")
        self.assertEqual(len(self.db_rows("SELECT id FROM users")), 1)

    def test_second_admin_with_the_default_number_is_told_to_choose_another(self):
        runner = self.app.test_cli_runner()
        first = runner.invoke(args=["create-admin", "--email", f"boss1@{DOMAIN}", "--name", "Admin One"],
                              input="long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertEqual(first.exit_code, 0, first.output)
        second = runner.invoke(args=["create-admin", "--email", f"boss2@{DOMAIN}", "--name", "Admin Two"],
                               input="long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertNotEqual(second.exit_code, 0)
        self.assertIn("--enrollment", second.output)
        third = runner.invoke(args=["create-admin", "--email", f"boss2@{DOMAIN}", "--name", "Admin Two",
                                    "--enrollment", "ADMIN002"], input="long-enough-pw-1\nlong-enough-pw-1\n")
        self.assertEqual(third.exit_code, 0, third.output)


if __name__ == "__main__":
    unittest.main()
