"""C14: security headers on every response, the exact Content-Security-Policy, and a scan that keeps the
templates free of inline script, style and event handlers (which is what lets the policy stay strict).
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import os
import re
import unittest

import helpers
from app import ratelimit, security
from test_auth import DOMAIN, GOOD_PASSWORD, AuthCase, token_from

EXPECTED_CSP = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'", "https://fonts.googleapis.com"],
    "font-src": ["https://fonts.gstatic.com"],
    "img-src": ["'self'", "data:"],
    "object-src": ["'none'"],
    "frame-ancestors": ["'none'"],
    "form-action": ["'self'"],
    "base-uri": ["'self'"],
}
EXPECTED = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def parse_csp(text):
    policy = {}
    for part in text.split(";"):
        words = part.split()
        if words:
            policy[words[0]] = words[1:]
    return policy


class HeaderCase(AuthCase):
    def assert_headers(self, response, label=""):
        for name, value in EXPECTED.items():
            self.assertEqual(response.headers.get(name), value, f"{label}: {name}")
        self.assertEqual(parse_csp(response.headers.get("Content-Security-Policy", "")), EXPECTED_CSP, label)
        self.assertNotIn("Strict-Transport-Security", response.headers, label)   # development: no HSTS


class EveryResponseTests(HeaderCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user(f"owner@{DOMAIN}")
        self.make_user(f"ravi@{DOMAIN}")
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        self.lid = helpers.add_listing(conn, self.owner, "Mini drafter")
        conn.commit()
        conn.close()

    def test_pages_for_a_guest(self):
        for url in ("/", "/login", "/signup", "/listings/", f"/listings/{self.lid}", "/health", "/design"):
            self.assert_headers(self.client.get(url), url)

    def test_a_redirect_carries_the_headers(self):
        r = self.client.get("/profile")
        self.assertEqual(r.status_code, 303)
        self.assert_headers(r, "redirect")

    def test_a_404_page_carries_the_headers(self):
        r = self.client.get("/no-such-page")
        self.assertEqual(r.status_code, 404)
        self.assert_headers(r, "404")

    def test_a_403_page_carries_the_headers(self):
        r = self.client.post("/login", data={"email": "a@b.c"})          # no form token
        self.assertEqual(r.status_code, 403)
        self.assert_headers(r, "403")

    def test_a_405_answer_carries_the_headers(self):
        r = self.client.get("/logout")
        self.assertEqual(r.status_code, 405)
        self.assert_headers(r, "405")

    def test_a_429_page_carries_the_headers(self):
        client = self.app.test_client()
        self.login(f"ravi@{DOMAIN}", GOOD_PASSWORD, client=client)
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        uid = conn.execute("SELECT id FROM users WHERE email = ?", (f"ravi@{DOMAIN}",)).fetchone()["id"]
        for _ in range(ratelimit.ACTIONS["send_report"][0]):
            conn.execute('INSERT INTO login_attempts ("key") VALUES (?)', (f"send_report:{uid}",))
        conn.commit()
        conn.close()
        token = token_from(client.get(f"/listings/{self.lid}").get_data(as_text=True))
        r = client.post(f"/listings/{self.lid}/report", data={"csrf_token": token, "reason": "Looks like a scam."})
        self.assertEqual(r.status_code, 429)
        self.assert_headers(r, "429")

    def test_a_locked_login_page_carries_the_headers(self):
        conn = helpers.connect(self.app.config["DATABASE_PATH"])
        for _ in range(ratelimit.LOGIN_IP_MAX):
            conn.execute('INSERT INTO login_attempts ("key") VALUES (?)', ("ip:127.0.0.1",))
        conn.commit()
        conn.close()
        r = self.login(f"ravi@{DOMAIN}", GOOD_PASSWORD)
        self.assertEqual(r.status_code, 429)
        self.assert_headers(r, "login 429")

    def test_static_files_carry_the_headers(self):
        for url in ("/static/css/tokens.css", "/static/css/base.css", "/static/css/components.css",
                    "/static/js/dialog.js"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assert_headers(r, url)
            r.close()

    def test_pages_for_a_logged_in_student_and_a_post_answer(self):
        client = self.app.test_client()
        self.assertEqual(self.login(f"ravi@{DOMAIN}", GOOD_PASSWORD, client=client).status_code, 303)
        for url in ("/profile", "/listings/mine", "/requests/sent", "/requests/received", "/listings/new"):
            self.assert_headers(client.get(url), url)
        r = self.post("/listings/new", {"title": ""}, client=client)
        self.assert_headers(r, "POST answer")

    def test_an_admin_page_refused_to_a_student_carries_the_headers(self):
        client = self.app.test_client()
        self.login(f"ravi@{DOMAIN}", GOOD_PASSWORD, client=client)
        r = client.get("/admin/")
        self.assertEqual(r.status_code, 403)
        self.assert_headers(r, "admin 403")


class PolicyTests(unittest.TestCase):
    def test_the_policy_allows_exactly_the_agreed_sources(self):
        self.assertEqual(parse_csp(security.CSP), EXPECTED_CSP)

    def test_the_policy_names_no_other_host_and_no_unsafe_keyword(self):
        hosts = set(re.findall(r"https?://[^\s;]+", security.CSP))
        self.assertEqual(hosts, {"https://fonts.googleapis.com", "https://fonts.gstatic.com"})
        self.assertNotIn("unsafe", security.CSP)
        self.assertNotIn("*", security.CSP)

    def test_hsts_is_sent_only_in_production(self):
        dev = helpers.make_app(tempdir())
        prod = helpers.make_app(tempdir(), APP_ENV="production")
        self.assertNotIn("Strict-Transport-Security", dev.test_client().get("/health").headers)
        sts = prod.test_client().get("/health").headers.get("Strict-Transport-Security", "")
        self.assertRegex(sts, r"^max-age=\d{7,}")


class NoInlineCodeTests(unittest.TestCase):
    """The policy has no 'unsafe-inline', so any inline script, style or handler would simply not run."""

    def templates(self):
        root = os.path.join(helpers.ROOT, "app", "templates")
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".html"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as fh:
                        yield os.path.relpath(path, root), fh.read()

    def test_no_template_has_an_inline_script_style_or_handler(self):
        offenders = []
        for path, text in self.templates():
            for match in re.finditer(r"<script\b[^>]*>", text, re.I):
                if "src=" not in match.group(0):
                    offenders.append(f"{path}: inline script")
            if re.search(r"<style\b", text, re.I):
                offenders.append(f"{path}: style element")
            if re.search(r"""\sstyle\s*=""", text, re.I):
                offenders.append(f"{path}: style attribute")
            if re.search(r"""<[a-z][^>]*\s(on[a-z]+)\s*=""", text, re.I):
                offenders.append(f"{path}: event handler attribute")
            if re.search(r"""(href|src|action|formaction)\s*=\s*["']\s*javascript:""", text, re.I):
                offenders.append(f"{path}: javascript link")
        self.assertEqual(offenders, [])

    def test_every_script_tag_loads_a_file_from_this_site(self):
        for path, text in self.templates():
            for src in re.findall(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", text, re.I):
                self.assertTrue(src.startswith("/static/") or "url_for" in src or src.startswith("{{"),
                                f"{path}: {src}")

    def test_external_hosts_in_templates_are_only_the_two_font_hosts(self):
        allowed = {"fonts.googleapis.com", "fonts.gstatic.com"}
        for path, text in self.templates():
            for host in re.findall(r"""(?:href|src)\s*=\s*["']https?://([^/"']+)""", text, re.I):
                self.assertIn(host, allowed, f"{path}: {host}")


class ReviewDocumentTests(unittest.TestCase):
    PATH = os.path.join(helpers.ROOT, "docs", "security", "review-1.md")

    def rows(self):
        with open(self.PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        return [[cell.strip() for cell in line.strip("|").split("|")]
                for line in lines if re.match(r"\|\s*R\d+\s*\|", line)]

    def test_the_review_exists_and_lists_findings(self):
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual(len({row[0] for row in rows}), len(rows))        # ids are unique

    def test_no_high_finding_is_open(self):
        for row in self.rows():
            if row[1] == "HIGH":
                self.assertTrue(row[4].startswith("FIXED"), f"{row[0]} is still open")

    def test_every_finding_has_a_known_severity_and_status(self):
        for row in self.rows():
            self.assertIn(row[1], {"HIGH", "MEDIUM", "LOW", "NOTE"}, row[0])
            self.assertRegex(row[4], r"^(FIXED|OPEN|NOTE)", row[0])


def tempdir():
    import tempfile
    folder = tempfile.mkdtemp()
    unittest.addModuleCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
    return folder


if __name__ == "__main__":
    unittest.main()
