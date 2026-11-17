"""C8: page frame (one header, skip link, footer), flash alerts, template hygiene, the three auth pages
built from components, request action buttons, and 303 redirects.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import os
import re
import unittest

from bs4 import BeautifulSoup
from flask import flash, g, render_template_string, url_for

import helpers
from app import accounts
from app.auth import BAD_LOGIN
from test_auth import AuthCase, DOMAIN, signup_form
from test_exchange import World

TEMPLATES = os.path.join(helpers.ROOT, "app", "templates")
GUEST_PAGES = ("/login", "/signup", "/listings/")
ONE_OF_EACH = {"header": 1, "site_header": 1, "main": 1, "footer": 1, "nav": 1}


def page(html):
    return BeautifulSoup(html, "html.parser")


def counts(doc):
    """Top-level page header, site-header bars anywhere, main, footer and main navigation.
    (Title blocks have their own <header> inside an <article>; those are not page headers.)"""
    return {"header": len(doc.select("body > header")), "site_header": len(doc.select("header.site-header")),
            "main": len(doc.find_all("main")),
            "footer": len(doc.find_all("footer")),
            "nav": len(doc.find_all("nav", attrs={"aria-label": "Main"}))}


def all_templates():
    for folder, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith(".html"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    yield os.path.relpath(path, TEMPLATES).replace(os.sep, "/"), f.read()


class PageFrameTests(AuthCase):
    def test_pages_start_with_doctype_and_declare_a_language(self):
        for url in GUEST_PAGES:
            html = self.client.get(url).get_data(as_text=True)
            self.assertTrue(html.lower().startswith("<!doctype html>"), url)
            self.assertIn('<html lang="en">', html)

    def test_guest_pages_have_one_header_one_main_one_footer(self):
        for url in GUEST_PAGES:
            self.assertEqual(counts(page(self.client.get(url).get_data(as_text=True))), ONE_OF_EACH, url)

    def test_student_pages_have_one_header_one_main_one_footer(self):
        self.make_user()
        self.login()
        with self.app.test_request_context():
            urls = [url_for(n) for n in ("auth.profile", "listings.browse", "listings.create",
                                         "listings.my_listings", "exchange.sent", "exchange.received")]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(counts(page(r.get_data(as_text=True))), ONE_OF_EACH, url)

    def test_design_gallery_shows_the_navigation_once(self):
        """Regression: the gallery used to print its own navbar under the page header."""
        self.assertEqual(counts(page(self.client.get("/design").get_data(as_text=True))), ONE_OF_EACH)

    def test_skip_link_is_first_and_points_at_main(self):
        doc = page(self.client.get("/login").get_data(as_text=True))
        first = doc.body.find("a")
        self.assertIn("skip-link", first["class"])
        self.assertEqual(first["href"], "#main")
        self.assertEqual(doc.find("main")["id"], "main")
        self.assertTrue(first.get_text(strip=True))

    def test_current_page_is_marked_in_the_navigation(self):
        doc = page(self.client.get("/signup").get_data(as_text=True))
        current = doc.select('nav a[aria-current="page"]')
        self.assertEqual([a.get_text(strip=True) for a in current], ["Sign up"])

    def test_logout_is_a_post_button_with_a_csrf_token(self):
        self.make_user()
        self.login()
        doc = page(self.client.get("/profile").get_data(as_text=True))
        with self.app.test_request_context():
            action = url_for("auth.logout")
        form = doc.select_one(f'nav form.inline-form[action="{action}"]')
        self.assertIsNotNone(form)
        self.assertEqual(form["method"], "post")
        self.assertTrue(form.find("input", attrs={"name": "csrf_token"})["value"])
        self.assertEqual(form.find("button").get_text(strip=True), "Log out")

    def test_flash_categories_map_to_alert_kinds_and_roles(self):
        with self.app.test_request_context("/login"):
            g.user = None
            for text, category in (("Bad thing", "error"), ("Good thing", "success"), ("Heads up", "warning"),
                                   ("FYI", "info"), ("Odd one", "weird")):
                flash(text, category)
            html = render_template_string('{% extends "base.html" %}{% block content %}x{% endblock %}')
        got = {p.get_text(strip=True): (sorted(set(p["class"]) - {"alert"})[0], p["role"])
               for p in page(html).select("main p.alert")}
        self.assertEqual(got, {"Bad thing": ("alert-danger", "alert"), "Good thing": ("alert-ok", "status"),
                               "Heads up": ("alert-warning", "status"), "FYI": ("alert-info", "status"),
                               "Odd one": ("alert-info", "status")})


class TemplateHygieneTests(unittest.TestCase):
    KNOWN_CONFIRM_HANDLERS = set()   # C10: no template keeps an inline handler any more

    def test_no_template_has_inline_styles(self):
        for rel, text in all_templates():
            self.assertIsNone(re.search(r"\sstyle\s*=|<style", text), rel)

    def test_inline_event_handlers_do_not_spread(self):
        found = {rel for rel, text in all_templates() if re.search(r"\son[a-z]+\s*=", text)}
        self.assertLessEqual(found, self.KNOWN_CONFIRM_HANDLERS)

    def test_old_forms_template_is_gone_and_not_imported(self):
        self.assertFalse(os.path.exists(os.path.join(TEMPLATES, "_forms.html")))
        for rel, text in all_templates():
            self.assertNotIn("_forms.html", text, rel)

    def test_every_page_template_extends_the_base(self):
        for rel, text in all_templates():
            if rel == "base.html" or rel.startswith("components/"):
                continue
            self.assertIn('{% extends "base.html" %}', text, rel)

    def test_auth_pages_use_components_not_raw_controls(self):
        for rel, text in all_templates():
            if not rel.startswith("auth/"):
                continue
            self.assertNotRegex(text, r"<(button|select|textarea)\b", rel)
            for tag in re.findall(r"<input\b[^>]*>", text):
                self.assertIn('type="hidden"', tag, rel)


class LoginPageTests(AuthCase):
    def test_login_page_structure(self):
        doc = page(self.client.get("/login").get_data(as_text=True))
        self.assertEqual([h.get_text(strip=True) for h in doc.find_all("h1")], ["Log in"])
        email, password = doc.select_one("#f-email"), doc.select_one("#f-password")
        self.assertEqual((email["type"], email["autocomplete"], int(email["maxlength"])),
                         ("email", "email", accounts.EMAIL_MAX))
        self.assertEqual((password["type"], password["autocomplete"], int(password["maxlength"])),
                         ("password", "current-password", accounts.MAX_PASSWORD))
        self.assertEqual(doc.select_one("form button.btn-primary").get_text(strip=True), "Log in")
        self.assertIsNotNone(doc.find("input", attrs={"name": "csrf_token"}))

    def test_bad_password_shows_one_alert_and_keeps_the_email(self):
        self.make_user()
        r = self.login(password="not-the-password-1")
        self.assertEqual(r.status_code, 401)
        doc = page(r.get_data(as_text=True))
        alert = doc.select_one("main p.alert-danger")
        self.assertEqual((alert.get_text(strip=True), alert["role"]), (BAD_LOGIN, "alert"))
        self.assertEqual(doc.select_one("#f-email")["value"], f"ravi@{DOMAIN}")
        self.assertIsNone(doc.select_one("#f-password").get("value"))


class SignupPageTests(AuthCase):
    AUTOCOMPLETE = {"email": "email", "password": "new-password", "password_confirm": "new-password",
                    "full_name": "name", "college_name": "organization", "branch": "off",
                    "enrollment_no": "off", "phone": "tel", "social_link": "off", "address": "off"}
    LIMITS = {"email": accounts.EMAIL_MAX, "password": accounts.MAX_PASSWORD,
              "password_confirm": accounts.MAX_PASSWORD, "full_name": accounts.NAME_MAX,
              "college_name": accounts.COLLEGE_MAX, "branch": accounts.BRANCH_MAX,
              "enrollment_no": accounts.ENROLLMENT_MAX, "phone": accounts.PHONE_INPUT_MAX,
              "social_link": accounts.SOCIAL_MAX, "address": accounts.ADDRESS_MAX}

    def doc(self):
        return page(self.client.get("/signup").get_data(as_text=True))

    def test_one_h1_and_three_sections_in_order(self):
        doc = self.doc()
        self.assertEqual([h.get_text(strip=True) for h in doc.find_all("h1")], ["Create your account"])
        legends = [l.get_text(strip=True) for l in doc.select("fieldset.form-section > legend")]
        self.assertEqual(legends, ["Your account", "About you", "Contact details"])

    def test_every_control_has_a_label_and_the_right_autocomplete(self):
        doc = self.doc()
        names = set()
        for c in doc.select("input.control, select.control"):
            names.add(c["name"])
            self.assertTrue(doc.find("label", attrs={"for": c["id"]}).get_text(strip=True), c["name"])
            if c.name == "input":
                self.assertEqual(c["autocomplete"], self.AUTOCOMPLETE[c["name"]], c["name"])
        self.assertEqual(names, set(self.AUTOCOMPLETE) | {"year"})

    def test_maxlength_comes_from_the_account_rules(self):
        doc = self.doc()
        for name, limit in self.LIMITS.items():
            self.assertEqual(int(doc.select_one(f"#f-{name}")["maxlength"]), limit, name)

    def test_every_hint_is_linked_to_its_own_control(self):
        doc = self.doc()
        hints = doc.select("p.field-hint[id]")
        self.assertGreaterEqual(len(hints), 6)
        for hint in hints:
            name = hint["id"][2:]
            self.assertIn(hint["id"], doc.select_one(f"#f-{name}")["aria-describedby"].split(), name)
        self.assertTrue(doc.select_one("#h-college_name").get_text().startswith("Your college"))

    def test_optional_fields_say_so_and_are_not_required(self):
        doc = self.doc()
        for name in ("phone", "social_link", "address"):
            self.assertFalse(doc.select_one(f"#f-{name}").has_attr("required"), name)
            self.assertIn("(optional)", doc.find("label", attrs={"for": f"f-{name}"}).get_text())
        for name in ("email", "password", "full_name", "college_name", "branch", "year", "enrollment_no"):
            self.assertTrue(doc.select_one(f"#f-{name}").has_attr("required"), name)

    def test_year_options(self):
        values = [o["value"] for o in self.doc().select("#f-year option")]
        self.assertEqual(values, ["", "FE", "SE", "TE", "BE"])

    def test_problem_state_marks_the_field_and_keeps_what_was_typed(self):
        r = self.post("/signup", signup_form(email="asha@gmail", year="TE"), page="/signup")
        self.assertEqual(r.status_code, 400)
        doc = page(r.get_data(as_text=True))
        email = doc.select_one("#f-email")
        self.assertEqual(email["aria-invalid"], "true")
        self.assertIn("p-email", email["aria-describedby"].split())
        problem = doc.select_one("#p-email")
        self.assertEqual(problem["role"], "alert")
        self.assertTrue(problem.get_text(strip=True))
        self.assertEqual(doc.select_one("#f-full_name")["value"], "Asha Verma")
        self.assertEqual(doc.select_one("#f-year option[selected]")["value"], "TE")
        self.assertIsNone(doc.select_one("#f-password").get("value"))

    def test_good_signup_flashes_a_success_alert(self):
        r = self.post("/signup", signup_form(), page="/signup", follow_redirects=True)
        alert = page(r.get_data(as_text=True)).select_one("main p.alert-ok")
        self.assertIn("Welcome to Campus Barter", alert.get_text())


class ProfilePageTests(AuthCase):
    def setUp(self):
        super().setUp()
        self.make_user()
        self.login()

    def doc(self):
        return page(self.client.get("/profile").get_data(as_text=True))

    def test_profile_is_prefilled_and_names_the_email(self):
        doc = self.doc()
        self.assertEqual([h.get_text(strip=True) for h in doc.find_all("h1")], ["Your profile"])
        self.assertIn(f"ravi@{DOMAIN}", doc.select_one(".page-lead").get_text())
        self.assertEqual(doc.select_one("#f-full_name")["value"], "Ravi Singh")
        self.assertEqual(doc.select_one("#f-branch")["value"], "Computer Engineering")
        self.assertEqual(doc.select_one("#f-year option[selected]")["value"], "TE")
        self.assertEqual(doc.select_one("form button.btn-primary").get_text(strip=True), "Save profile")

    def test_profile_maxlength_matches_the_rules(self):
        doc = self.doc()
        for name, limit in accounts.FIELD_LIMITS.items():
            if name in ("email", "password"):
                continue
            self.assertEqual(int(doc.select_one(f"#f-{name}")["maxlength"]), limit, name)

    def test_saving_shows_a_success_alert(self):
        row = self.user_row(f"ravi@{DOMAIN}")
        data = {"full_name": "Ravi S", "college_name": "Test Engineering College", "branch": "Computer Engineering",
                "year": "TE", "enrollment_no": row["enrollment_no"], "phone": "", "social_link": "", "address": ""}
        r = self.post("/profile", data, page="/profile", follow_redirects=True)
        doc = page(r.get_data(as_text=True))
        self.assertEqual(doc.select_one("main p.alert-ok").get_text(strip=True), "Profile saved.")
        self.assertEqual(doc.select_one("#f-full_name")["value"], "Ravi S")

    def test_bad_value_marks_the_field(self):
        row = self.user_row(f"ravi@{DOMAIN}")
        data = {"full_name": "R", "college_name": "Test Engineering College", "branch": "Computer Engineering",
                "year": "TE", "enrollment_no": row["enrollment_no"]}
        r = self.post("/profile", data, page="/profile")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(page(r.get_data(as_text=True)).select_one("#f-full_name")["aria-invalid"], "true")


class ListingRedirectTests(AuthCase):
    def setUp(self):
        super().setUp()
        self.make_user()
        self.login()

    def form(self, **over):
        data = {"title": "Mini drafter", "category": "Books", "condition": "good", "mode": "give",
                "description": "Works well"}
        data.update(over)
        return data

    def test_create_edit_and_delete_all_answer_with_303(self):
        r = self.post("/listings/new", self.form(), page="/listings/new")
        self.assertEqual(r.status_code, 303)
        lid = int(r.headers["Location"].rstrip("/").rsplit("/", 1)[1])
        with self.app.test_request_context():
            edit, delete = url_for("listings.edit", listing_id=lid), url_for("listings.delete", listing_id=lid)
        r = self.post(edit, self.form(title="Mini drafter v2"), page=edit)
        self.assertEqual(r.status_code, 303)
        r = self.post(delete, {}, page=f"/listings/{lid}")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/listings/mine"))


class RequestActionButtonTests(World, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.student("owner")
        self.asker = self.student("asker")
        self.give = self.listing(self.owner, "Calculator", mode="give")
        self.lend = self.listing(self.owner, "Drafter", mode="lend")

    def new_request(self, lid):
        r = self.post("asker", f"/listings/{lid}/request", {"message": "please"}, page=f"/listings/{lid}")
        self.assertEqual(r.status_code, 303)
        return int(r.headers["Location"].rstrip("/").rsplit("/", 1)[1])

    def buttons(self, who, rid):
        doc = page(self.get(who, f"/requests/{rid}").get_data(as_text=True))
        return {f.find("button").get_text(strip=True): f for f in doc.select("main form.inline-form")}

    def test_owner_sees_accept_and_decline_as_post_buttons(self):
        rid = self.new_request(self.give)
        forms = self.buttons("owner", rid)
        self.assertEqual(set(forms), {"Accept", "Decline"})
        for label, action in (("Accept", "accept"), ("Decline", "decline")):
            form = forms[label]
            self.assertEqual(form["method"], "post")
            self.assertTrue(form["action"].endswith(f"/requests/{rid}/{action}"))
            self.assertTrue(form.find("input", attrs={"name": "csrf_token"})["value"])
        self.assertIn("btn-primary", forms["Accept"].find("button")["class"])
        # C11: Decline now asks first. This form is the one inside the confirm dialog, drawn as a danger button.
        self.assertIn("btn-danger", forms["Decline"].find("button")["class"])

    def test_asker_can_only_cancel_while_pending(self):
        rid = self.new_request(self.give)
        self.assertEqual(set(self.buttons("asker", rid)), {"Cancel request"})

    def test_complete_button_wording_depends_on_the_mode(self):
        for lid, label in ((self.give, "Mark as completed"), (self.lend, "Mark as returned")):
            rid = self.new_request(lid)
            self.assertEqual(self.post("owner", f"/requests/{rid}/accept", page=f"/requests/{rid}").status_code, 303)
            self.assertIn(label, self.buttons("owner", rid))

    def test_buttons_still_work_when_posted(self):
        rid = self.new_request(self.give)
        r = self.post("owner", f"/requests/{rid}/decline", page=f"/requests/{rid}")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.req(rid)["status"], "declined")


if __name__ == "__main__":
    unittest.main()
