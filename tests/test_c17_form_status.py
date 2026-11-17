"""C17: every form that is refused for bad input answers 400 and keeps the page; the login keeps its own codes.
Runs on the audit's scratch app (one migrated + seeded database for the whole module).
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import tempfile
import unittest

import helpers  # noqa: F401  (puts the project root on sys.path)
from scripts import ui_audit

REFUSED = 400


def setUpModule():
    global _tmp, APP, IDS
    _tmp = tempfile.TemporaryDirectory()
    APP, IDS = ui_audit.build_audit_app(_tmp.name)


def tearDownModule():
    from app import logs
    logs._remove_ours(APP.logger)
    _tmp.cleanup()


def post(actor, form_page, path, data):
    client = APP.test_client()
    ui_audit._login(APP, client, actor, IDS)
    token = ui_audit._token(client.get(form_page).get_data(as_text=True))
    return client.post(path, data=dict(data, csrf_token=token))


class RefusedFormStatusTests(unittest.TestCase):
    def test_signup_with_nothing_filled_in_is_400(self):
        self.assertEqual(post("guest", "/signup", "/signup", {}).status_code, REFUSED)

    def test_profile_with_an_empty_name_is_400(self):
        self.assertEqual(post("asha", "/profile", "/profile", {"full_name": ""}).status_code, REFUSED)

    def test_new_listing_with_nothing_filled_in_is_400(self):
        self.assertEqual(post("asha", "/listings/new", "/listings/new", {}).status_code, REFUSED)

    def test_edit_listing_with_an_empty_title_is_400(self):
        url = f"/listings/{IDS['listing']['drafter']}/edit"
        self.assertEqual(post("ravi", url, url, {"title": ""}).status_code, REFUSED)

    def test_report_that_is_too_short_is_400(self):
        listing = IDS["listing"]["math"]
        r = post("asha", f"/listings/{listing}", f"/listings/{listing}/report", {"reason": "short"})
        self.assertEqual(r.status_code, REFUSED)

    def test_login_with_a_wrong_password_keeps_its_own_code_401(self):
        r = post("guest", "/login", "/login", {"email": "ravi.te@" + ui_audit.DOMAIN, "password": "not-the-password"})
        self.assertEqual(r.status_code, 401)

    def test_a_good_listing_edit_still_redirects(self):
        url = f"/listings/{IDS['listing']['drafter']}/edit"
        data = {"title": "Mini drafter (Omega)", "category": "Mini drafter", "condition": "good", "mode": "lend"}
        self.assertEqual(post("ravi", url, url, data).status_code, 303)


if __name__ == "__main__":
    unittest.main()
