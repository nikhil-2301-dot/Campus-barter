"""C15: JSON log lines, request id on every response, redaction, event lines (review finding R10),
no silent except block, no-store on logged-in pages (R6), styled 400 and 405 pages.
Each test app writes its log into a StringIO, so nothing reaches the test log.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import ast
import io
import json
import logging
import os
import unittest
from unittest import mock

from bs4 import BeautifulSoup
from flask import abort

import helpers
from app import config, logs, ratelimit
from test_admin import AdminWorld
from test_auth import DOMAIN, GOOD_PASSWORD, AuthCase, signup_form, token_from
from test_browse_ui import ADMIN, ASKER

RAVI = f"ravi@{DOMAIN}"
WRONG = "not-the-password-77"
CRASH_SECRET = "hunter2xyz"
BASE_KEYS = {"time", "level", "logger", "message"}
LISTING_FORM = {"title": "Mini drafter", "category": "Books", "condition": "good", "mode": "give",
                "description": "", "wanted_in_return": ""}


def capture(case, **config_over):
    """Give the case's app a JSON handler that writes into a StringIO (returned)."""
    stream = io.StringIO()
    case.app.config["LOG_STREAM"] = stream
    case.app.config.update(config_over)
    logs.configure(case.app)
    case.addCleanup(logs._remove_ours, case.app.logger)
    return stream


class LogCase(AuthCase):
    def setUp(self):
        super().setUp()
        self.stream = capture(self)

        @self.app.get("/test-boom")
        def boom():
            raise RuntimeError(f"password={CRASH_SECRET} while drawing")

        @self.app.get("/test-bad")
        def bad():
            abort(400)

    def _static(self):
        with self.client.get("/static/css/tokens.css") as r:
            return r

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    def events(self, name):
        return [row for row in self.lines() if row.get("event") == name]

    def raw(self):
        return self.stream.getvalue()


class LogFormatTests(LogCase):
    def test_every_line_is_json_with_the_agreed_keys(self):
        self.client.get("/")
        self.client.get("/no-such-page")
        rows = self.lines()
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(BASE_KEYS <= set(row), row)
            self.assertTrue(row["time"].endswith("Z"))
        access = self.events("request")
        self.assertEqual([r["status"] for r in access], [200, 404])
        for row in access:
            self.assertEqual(set(row) & {"request_id", "method", "path", "status", "duration_ms"},
                             {"request_id", "method", "path", "status", "duration_ms"})
            self.assertGreaterEqual(row["duration_ms"], 0)

    def test_one_access_line_per_request_with_the_user_id_when_known(self):
        uid = self.make_user(RAVI)
        self.login()
        self.stream.truncate(0)
        self.stream.seek(0)
        self.client.get("/profile")
        access = self.events("request")
        self.assertEqual(len(access), 1)
        self.assertEqual(access[0]["user_id"], uid)
        self.assertEqual(access[0]["path"], "/profile")

    def test_the_level_setting_hides_lower_levels(self):
        stream = capture(self, LOG_LEVEL="WARNING")
        self.client.get("/")
        self.assertEqual(stream.getvalue(), "")

    def test_the_default_level_shows_info_lines(self):
        self.client.get("/")
        self.assertTrue(self.events("request"))

    def test_the_level_setting_is_read_and_checked_at_start(self):
        env = dict(helpers.DEV_ENV)
        self.assertEqual(config.load_settings(env).log_level, "INFO")
        self.assertEqual(config.load_settings({**env, "LOG_LEVEL": " debug "}).log_level, "DEBUG")
        with self.assertRaises(config.ConfigError):
            config.load_settings({**env, "LOG_LEVEL": "LOUD"})

    def test_path_is_logged_without_its_query_string(self):
        self.client.get("/listings/?q=needle-text&category=Books")
        row = self.events("request")[0]
        self.assertEqual(row["path"], "/listings/")
        self.assertNotIn("needle-text", self.raw())
        self.assertNotIn("category=Books", self.raw())


class RequestIdTests(LogCase):
    def test_every_kind_of_response_carries_the_header(self):
        self.make_user(RAVI)
        responses = {
            "page": self.client.get("/"),
            "redirect": self.client.get("/profile"),
            "not found": self.client.get("/no-such-page"),
            "static": self._static(),
            "wrong method": self.client.post("/health"),
            "bad request": self.client.get("/test-bad"),
            "crash": self.client.get("/test-boom"),
        }
        student = self.app.test_client()
        self.login(client=student)
        responses["forbidden"] = student.get("/admin/")
        for name, r in responses.items():
            self.assertRegex(r.headers.get("X-Request-ID", ""), r"^[A-Za-z0-9._-]{1,64}$", name)

    def test_too_many_response_carries_the_header(self):
        self.make_user(RAVI)
        for _ in range(ratelimit.LOGIN_EMAIL_MAX):
            self.login(password=WRONG)
        r = self.login(password=WRONG)
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r.headers.get("X-Request-ID"))
        self.make_user(f"asha@{DOMAIN}")
        with mock.patch.dict(ratelimit.ACTIONS, {"create_listing": (1, 3600)}):
            client = self.app.test_client()
            self.login(f"asha@{DOMAIN}", client=client)
            self.post("/listings/new", LISTING_FORM, client=client, page="/listings/new")
            r = self.post("/listings/new", LISTING_FORM, client=client, page="/listings/new")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r.headers.get("X-Request-ID"))

    def test_a_good_incoming_id_is_echoed_and_used_in_the_log(self):
        r = self.client.get("/", headers={"X-Request-ID": "abc-123_x.y"})
        self.assertEqual(r.headers["X-Request-ID"], "abc-123_x.y")
        self.assertEqual({row["request_id"] for row in self.lines()}, {"abc-123_x.y"})

    def test_a_bad_incoming_id_is_replaced(self):
        for bad in ("x" * 65, "has space", "semi;colon", "a/b", "tab\there", "ü", ""):
            r = self.client.get("/", headers={"X-Request-ID": bad})
            sent = r.headers["X-Request-ID"]
            self.assertNotEqual(sent, bad, repr(bad))
            self.assertRegex(sent, r"^[A-Za-z0-9._-]{1,64}$")

    def test_a_64_character_id_is_accepted_and_new_ids_differ(self):
        long_id = "k" * 64
        self.assertEqual(self.client.get("/", headers={"X-Request-ID": long_id}).headers["X-Request-ID"], long_id)
        first = self.client.get("/").headers["X-Request-ID"]
        second = self.client.get("/").headers["X-Request-ID"]
        self.assertNotEqual(first, second)

    def test_the_cleaning_function_rejects_a_trailing_newline(self):
        self.assertNotEqual(logs.clean_request_id("abc\n"), "abc\n")
        self.assertEqual(logs.clean_request_id("abc"), "abc")
        self.assertNotEqual(logs.clean_request_id(None), None)


class CrashPageTests(LogCase):
    def test_the_500_page_shows_only_the_request_id_as_a_reference(self):
        r = self.client.get("/test-boom", headers={"X-Request-ID": "ref-777"})
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 500)
        self.assertIn("ref-777", html)
        self.assertIn("Reference", html)
        for hidden in (CRASH_SECRET, "Traceback", "RuntimeError", "while drawing", os.path.dirname(logs.__file__)):
            self.assertNotIn(hidden, html)

    def test_the_crash_is_logged_once_with_the_id_and_the_trace_but_not_the_secret(self):
        self.client.get("/test-boom", headers={"X-Request-ID": "ref-778"})
        problems = self.events("problem")
        self.assertEqual(len(problems), 1)
        row = problems[0]
        self.assertEqual(row["level"], "ERROR")
        self.assertEqual(row["request_id"], "ref-778")
        self.assertIn("RuntimeError", row["exception"])
        self.assertNotIn(CRASH_SECRET, self.raw())

    def test_other_pages_do_not_show_a_reference(self):
        self.assertNotIn("Reference", self.client.get("/no-such-page").get_data(as_text=True))


class StyledPageTests(LogCase):
    def test_wrong_method_gets_the_styled_page_and_keeps_the_allow_header(self):
        r = self.client.post("/health")
        self.assertEqual(r.status_code, 405)
        self.assertIn("GET", r.headers["Allow"])
        doc = BeautifulSoup(r.get_data(as_text=True), "html.parser")
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertIsNotNone(doc.select_one("main a.btn"))

    def test_bad_request_gets_the_styled_page_without_werkzeug_text(self):
        r = self.client.get("/test-bad")
        self.assertEqual(r.status_code, 400)
        doc = BeautifulSoup(r.get_data(as_text=True), "html.parser")
        self.assertEqual(len(doc.find_all("h1")), 1)
        self.assertNotIn("could not understand", r.get_data(as_text=True))


class RedactionUnitTests(unittest.TestCase):
    def test_sensitive_keys_are_masked_in_nested_data(self):
        data = {"password": "a", "csrf_token": "b", "Cookie": "c", "session": "d", "Authorization": "e",
                "api_key": "f", "SECRET_KEY": "g", "ok": {"new_password": "h", "name": "visible"}, "list": [{"token": "i"}]}
        out = logs.redact_value(data)
        flat = json.dumps(out)
        for secret in "abcdefghi":
            self.assertNotIn(f'"{secret}"', flat)
        self.assertEqual(out["ok"]["name"], "visible")

    def test_key_value_text_is_masked(self):
        for text in ("password=abc123", "Authorization: Bearer abc123", 'csrf_token": "abc123"',
                     "Cookie: session=abc123", "token: abc123", "SECRET_KEY = abc123"):
            self.assertNotIn("abc123", logs.redact_text(f"before {text} after"), text)
        self.assertEqual(logs.redact_text("plain words only"), "plain words only")

    def test_a_logged_record_hides_every_secret(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logs.JsonFormatter())
        logger = logging.getLogger("redaction-unit")
        logger.propagate = False
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.warning("login password=pw-111 token: tk-222",
                       extra={"fields": {"password": "pw-333", "headers": {"Authorization": "Bearer au-444"}}})
        try:
            raise ValueError("csrf=cs-555")
        except ValueError:
            logger.exception("trace")
        text = stream.getvalue()
        for secret in ("pw-111", "tk-222", "pw-333", "au-444", "cs-555"):
            self.assertNotIn(secret, text)

    def test_email_hash_is_short_stable_and_hides_the_address(self):
        a = logs.email_hash("Asha@College.example ")
        self.assertEqual(a, logs.email_hash("asha@college.example"))
        self.assertEqual(len(a), 8)
        self.assertNotIn("asha", a)


class RedactionFlowTests(LogCase):
    def test_secrets_never_reach_the_log_across_signup_login_lock_and_a_crash(self):
        csrf = token_from(self.client.get("/signup").get_data(as_text=True))
        self.client.post("/signup", data=signup_form(csrf_token=csrf, email=f"asha@{DOMAIN}"),
                         headers={"Authorization": "Bearer bearer-secret-9"})
        self.client.get("/logout")
        other = self.app.test_client()
        self.make_user(RAVI)
        other_csrf = token_from(other.get("/login?next=/profile").get_data(as_text=True))
        for _ in range(ratelimit.LOGIN_EMAIL_MAX + 1):          # wrong passwords, then the lock
            other.post("/login", data={"email": RAVI, "password": WRONG, "csrf_token": other_csrf})
        self.assertTrue(self.events("login_locked"))
        good = self.app.test_client()
        good_csrf = token_from(good.get("/login").get_data(as_text=True))
        good.post("/login", data={"email": f"asha@{DOMAIN}", "password": GOOD_PASSWORD, "csrf_token": good_csrf})
        session_cookie = good.get_cookie("session").value
        good.get("/test-boom?token=query-secret&q=needle", headers={"Cookie": f"session={session_cookie}"})
        text = self.raw()
        for secret in (GOOD_PASSWORD, WRONG, csrf, other_csrf, good_csrf, session_cookie, "bearer-secret-9",
                       CRASH_SECRET, "query-secret", "needle"):
            self.assertNotIn(secret, text, secret)

    def test_no_raw_email_is_logged(self):
        self.make_user(RAVI)
        self.login(password=WRONG)
        self.login()
        self.assertNotIn("ravi", self.raw())
        self.assertNotIn(DOMAIN, self.raw())


class LoginEventTests(LogCase):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(RAVI)

    def test_a_wrong_password_writes_one_line_with_ip_and_hash(self):
        self.login(password=WRONG)
        rows = self.events("login_failure")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], "WARNING")
        self.assertEqual(rows[0]["email_hash"], logs.email_hash(RAVI))
        self.assertIn("ip", rows[0])
        self.assertNotIn("password", rows[0])

    def test_a_good_login_writes_one_success_line_with_the_user_id(self):
        self.login()
        rows = self.events("login_success")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], self.uid)
        self.assertEqual(self.events("login_failure"), [])

    def test_the_lock_writes_one_line_per_refused_try(self):
        for _ in range(ratelimit.LOGIN_EMAIL_MAX):
            self.login(password=WRONG)
        self.assertEqual(self.events("login_locked"), [])
        self.login(password=WRONG)
        rows = self.events("login_locked")
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["wait_seconds"], 0)
        self.assertEqual(len(self.events("login_failure")), ratelimit.LOGIN_EMAIL_MAX)

    def test_a_suspended_account_is_logged_without_its_address(self):
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        conn.execute("UPDATE users SET is_suspended = 1 WHERE id = ?", (self.uid,))
        conn.commit()
        conn.close()
        self.login()
        rows = self.events("login_suspended")
        self.assertEqual(len(rows), 1)
        self.assertNotIn(RAVI, self.raw())

    def test_the_signup_cap_writes_one_line(self):
        with mock.patch("app.ratelimit.SIGNUP_MAX", 1):
            self.assertEqual(self.signup().status_code, 303)
            r = self.signup(self.app.test_client(), email=f"two@{DOMAIN}", enrollment_no="TEC2024002")
        self.assertEqual(r.status_code, 429)
        rows = self.events("signup_capped")
        self.assertEqual(len(rows), 1)
        self.assertIn("ip", rows[0])

    def test_a_per_hour_limit_hit_writes_one_line_with_action_and_user(self):
        client = self.app.test_client()
        self.login(client=client)
        with mock.patch.dict(ratelimit.ACTIONS, {"create_listing": (1, 3600)}):
            self.post("/listings/new", LISTING_FORM, client=client, page="/listings/new")
            self.assertEqual(self.events("limit_hit"), [])
            r = self.post("/listings/new", LISTING_FORM, client=client, page="/listings/new")
        self.assertEqual(r.status_code, 429)
        rows = self.events("limit_hit")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["action"], rows[0]["user_id"]), ("create_listing", self.uid))


class AdminEventTests(AdminWorld):
    def setUp(self):
        super().setUp()
        self.stream = capture(self)
        self.admin = self.client_for(ADMIN)

    def events(self, name="admin_action"):
        return [row for row in (json.loads(x) for x in self.stream.getvalue().splitlines() if x.strip())
                if row.get("event") == name]

    def only_line(self, action, target_kind, target_id):
        rows = [r for r in self.events() if r["action"] == action]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["admin_id"], row["target_kind"], row["target_id"]), (self.admin_id, target_kind, target_id))
        self.assertNotIn(ADMIN, self.stream.getvalue())
        return row

    def test_dismiss_writes_one_line(self):
        self.post(f"/admin/reports/{self.rid}/dismiss", {}, client=self.admin, page="/admin/")
        self.only_line("dismiss_report", "report", self.rid)

    def test_resolve_writes_one_line(self):
        self.post(f"/admin/reports/{self.rid}/resolve", {}, client=self.admin, page="/admin/")
        self.only_line("resolve_report", "report", self.rid)

    def test_remove_listing_writes_one_line(self):
        self.post(f"/admin/listings/{self.lid}/remove", {}, client=self.admin, page="/admin/")
        row = self.only_line("remove_listing", "listing", self.lid)
        self.assertIs(row["changed"], True)

    def test_suspend_and_unsuspend_write_one_line_each(self):
        self.post(f"/admin/users/{self.target_id}/suspend", {}, client=self.admin, page="/admin/")
        self.only_line("suspend_user", "user", self.target_id)
        self.post(f"/admin/users/{self.target_id}/unsuspend", {}, client=self.admin, page="/admin/")
        self.only_line("unsuspend_user", "user", self.target_id)

    def test_a_refused_admin_action_writes_no_admin_line(self):
        self.post(f"/admin/users/{self.admin_id}/suspend", {}, client=self.admin, page="/admin/")
        self.assertEqual(self.events(), [])


class CacheHeaderTests(LogCase):
    def test_logged_in_pages_are_not_stored_and_guest_pages_are_unchanged(self):
        self.make_user(RAVI)
        guest = self.client.get("/")
        self.assertNotIn("no-store", guest.headers.get("Cache-Control", ""))
        self.login()
        for url in ("/profile", "/listings/", "/listings/mine", "/requests/sent", "/no-such-page"):
            self.assertEqual(self.client.get(url).headers.get("Cache-Control"), "no-store", url)
        with self.client.get("/static/css/tokens.css") as static:
            self.assertNotIn("no-store", static.headers.get("Cache-Control", ""))

    def test_the_login_page_after_logout_is_not_a_stored_copy_of_a_private_page(self):
        self.make_user(RAVI)
        self.login()
        csrf = token_from(self.client.get("/profile").get_data(as_text=True))
        r = self.client.post("/logout", data={"csrf_token": csrf})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.client.get("/profile").status_code, 303)


APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")


def _modules():
    for name in sorted(os.listdir(APP_DIR)):
        if name.endswith(".py"):
            path = os.path.join(APP_DIR, name)
            with open(path, encoding="utf-8") as handle:
                yield name, ast.parse(handle.read(), filename=path)


def _is_silent(handler):
    return all(isinstance(node, ast.Pass) or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                                              and node.value.value is Ellipsis) for node in handler.body)


def _calls_log_problem(handler):
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "log_problem"
               for stmt in handler.body for node in ast.walk(stmt))


def _names_sqlite_error(handler):
    return handler.type is not None and "sqlite3.Error" in ast.unparse(handler.type)


class SilentExceptTests(unittest.TestCase):
    def test_no_except_block_in_app_swallows_silently(self):
        silent = [f"{name}:{node.lineno}" for name, tree in _modules() for node in ast.walk(tree)
                  if isinstance(node, ast.ExceptHandler) and _is_silent(node)]
        self.assertEqual(silent, [])

    def test_the_check_itself_catches_a_silent_handler(self):
        tree = ast.parse("try:\n    x = 1\nexcept ValueError:\n    pass\n")
        handler = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)][0]
        self.assertTrue(_is_silent(handler))
        tree = ast.parse("try:\n    x = 1\nexcept ValueError:\n    ...\n")
        self.assertTrue(_is_silent([n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)][0]))

    def test_every_page_level_database_problem_goes_through_the_shared_helper(self):
        pages = ("admin.py", "exchange.py", "listings.py", "main.py", "health.py")
        for name, tree in _modules():
            if name not in pages:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and _names_sqlite_error(node):
                    self.assertTrue(_calls_log_problem(node), f"{name}:{node.lineno}")

    def test_the_problem_pages_use_the_shared_helper_and_no_one_logs_by_hand(self):
        with open(os.path.join(APP_DIR, "problem_pages.py"), encoding="utf-8") as handle:
            self.assertIn("log_problem", handle.read())
        for name in sorted(os.listdir(APP_DIR)):
            if name.endswith(".py") and name != "logs.py":
                with open(os.path.join(APP_DIR, name), encoding="utf-8") as handle:
                    text = handle.read()
                self.assertNotIn("logger.exception(", text, name)


if __name__ == "__main__":
    unittest.main()
