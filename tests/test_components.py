"""Component library: macros render accessible HTML, escape user text, /design works.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import os
import re
import shutil
import subprocess
import unittest

from bs4 import BeautifulSoup
from flask import g

from helpers import ROOT, TempDirMixin, make_app
from app import create_app

CSS = os.path.join(ROOT, "app", "static", "css", "components.css")
JS = os.path.join(ROOT, "app", "static", "js", "dialog.js")
COMPONENT_DIR = os.path.join(ROOT, "app", "templates", "components")
SCRIPT = "<script>alert(1)</script>"

IMPORTS = ('{% from "components/forms.html" import field, select, textarea, checkbox %}'
           '{% from "components/actions.html" import button, link_button, post_button, dialog_open, dialog with context %}'
           '{% from "components/display.html" import status, panel, title_block, alert, table, pagination, '
           'empty_state, problem_state, loading_state %}'
           '{% from "components/nav.html" import navbar with context %}')


def soup(html):
    return BeautifulSoup(html, "html.parser")


class MacroCase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.app = make_app(self.make_tmp())
        self.client = self.app.test_client()

    def render(self, template, user=None, **ctx):
        """Render a template snippet that may use every component macro."""
        with self.app.test_request_context("/design"):
            g.user = user
            return self.app.jinja_env.from_string(IMPORTS + template).render(**ctx)

    def design(self):
        r = self.client.get("/design")
        self.assertEqual(r.status_code, 200)
        return soup(r.get_data(as_text=True))


class DesignPageTests(MacroCase):
    def test_design_page_is_served_with_one_h1(self):
        page = self.design()
        self.assertEqual(len(page.find_all("h1")), 1)

    def test_every_form_control_has_a_label(self):
        page = self.design()
        controls = [c for c in page.find_all(["input", "select", "textarea"]) if c.get("type") != "hidden"]
        self.assertGreaterEqual(len(controls), 10)
        for c in controls:
            label = page.find("label", attrs={"for": c.get("id")})
            self.assertIsNotNone(label, f"control {c.get('name')} has no label")
            self.assertTrue(label.get_text(strip=True))

    def test_ids_are_unique(self):
        ids = [t["id"] for t in self.design().find_all(id=True)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_aria_reference_points_at_an_existing_id(self):
        page = self.design()
        ids = {t["id"] for t in page.find_all(id=True)}
        refs = 0
        for t in page.find_all(attrs={"aria-describedby": True}):
            for ref in t["aria-describedby"].split():
                refs += 1
                self.assertIn(ref, ids)
        for t in page.find_all(attrs={"aria-labelledby": True}):
            self.assertIn(t["aria-labelledby"], ids)
        self.assertGreater(refs, 4)

    def test_every_button_and_link_has_text(self):
        page = self.design()
        for tag in page.find_all(["button", "a"]):
            self.assertTrue(tag.get_text(strip=True), f"empty {tag.name}: {tag}")

    def test_no_inline_styles_or_scripts_in_page(self):
        html = str(self.design())
        self.assertNotIn("style=", html)
        self.assertNotRegex(html, r"\son[a-z]+=")

    def test_every_class_used_has_a_css_rule(self):
        page = self.design()
        used = {c for t in page.find_all(class_=True) for c in t["class"]}
        with open(CSS, encoding="utf-8") as f:
            defined = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)))
        base = os.path.join(ROOT, "app", "static", "css", "base.css")
        with open(base, encoding="utf-8") as f:
            defined |= set(re.findall(r"\.([a-z][a-z0-9-]*)", f.read()))
        self.assertEqual(sorted(used - defined), [], "classes with no CSS rule")

    def test_bad_page_numbers_fall_back_to_a_valid_page(self):
        for raw, expected in (("abc", 1), ("0", 1), ("-4", 1), ("99", 5), ("3", 3)):
            html = self.client.get("/design?page=" + raw).get_data(as_text=True)
            self.assertIn(f"Page {expected} of 5", html, raw)

    def test_post_needs_csrf_token_then_redirects(self):
        self.assertEqual(self.client.post("/design").status_code, 403)
        html = self.client.get("/design").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        r = self.client.post("/design", data={"csrf_token": token})
        self.assertEqual(r.status_code, 303)

    def test_page_is_absent_in_production(self):
        env = {"APP_ENV": "production", "SECRET_KEY": "k" * 40}
        app = create_app({"DATABASE_PATH": self.make_tmp() + "/p.db"}, environ=env)
        self.assertEqual(app.test_client().get("/design").status_code, 404)


class FieldTests(MacroCase):
    def test_field_with_problem_is_linked_and_announced(self):
        page = soup(self.render('{{ field("email", "College email", problem="Enter a valid email.") }}'))
        inp = page.find("input")
        self.assertEqual(inp["aria-invalid"], "true")
        self.assertEqual(inp["aria-describedby"], "p-email")
        msg = page.find(id="p-email")
        self.assertEqual(msg["role"], "alert")
        self.assertEqual(msg.get_text(strip=True), "Enter a valid email.")

    def test_field_with_hint_and_problem_links_both(self):
        page = soup(self.render('{{ field("x", "X", hint="Help", problem="Bad") }}'))
        self.assertEqual(page.find("input")["aria-describedby"], "h-x p-x")

    def test_field_without_problem_is_not_marked_invalid(self):
        inp = soup(self.render('{{ field("x", "X") }}')).find("input")
        self.assertIsNone(inp.get("aria-invalid"))
        self.assertIsNone(inp.get("aria-describedby"))

    def test_optional_field_says_so_and_is_not_required(self):
        page = soup(self.render('{{ field("x", "Phone", required=False) }}'))
        self.assertIn("(optional)", page.find("label").get_text())
        self.assertIsNone(page.find("input").get("required"))

    def test_password_value_is_never_written_back(self):
        html = self.render('{{ field("pw", "Password", type="password", value="hunter2") }}')
        self.assertNotIn("hunter2", html)

    def test_user_text_is_escaped_in_every_field_component(self):
        html = self.render(
            '{{ field("a", s, value=s, hint=s, problem=s) }}'
            '{{ textarea("b", s, value=s, hint=s, problem=s) }}'
            '{{ select("c", s, [(s, s)], value=s, problem=s, hint=s) }}'
            '{{ checkbox("d", s, hint=s, problem=s) }}', s=SCRIPT)
        self.assertNotIn(SCRIPT, html)
        self.assertIn("&lt;script&gt;", html)

    def test_select_marks_the_chosen_option_and_adds_a_placeholder(self):
        page = soup(self.render('{{ select("t", "Type", [("a", "A"), ("b", "B")], value="b") }}'))
        self.assertEqual(page.find("option", selected=True)["value"], "b")
        self.assertEqual(page.find("option")["value"], "")

    def test_textarea_is_labelled_and_keeps_its_text(self):
        page = soup(self.render('{{ textarea("m", "Message", value="Hello") }}'))
        self.assertEqual(page.find("textarea").get_text(), "Hello")
        self.assertIsNotNone(page.find("label", attrs={"for": "f-m"}))

    def test_checkbox_label_is_attached(self):
        page = soup(self.render('{{ checkbox("t", "Agree", checked=True, problem="Tick it") }}'))
        box = page.find("input")
        self.assertEqual(box["type"], "checkbox")
        self.assertTrue(box.has_attr("checked"))
        self.assertEqual(box["aria-describedby"], "p-t")
        self.assertEqual(page.find("label")["for"], "f-t")


class ActionTests(MacroCase):
    def test_button_has_its_label_and_defaults_to_submit(self):
        b = soup(self.render('{{ button("Save changes") }}')).find("button")
        self.assertEqual(b.get_text(strip=True), "Save changes")
        self.assertEqual(b["type"], "submit")

    def test_disabled_button_is_really_disabled(self):
        b = soup(self.render('{{ button("Wait", disabled=True) }}')).find("button")
        self.assertTrue(b.has_attr("disabled"))

    def test_button_label_is_escaped(self):
        self.assertNotIn(SCRIPT, self.render('{{ button(s) }}{{ link_button(s, "/x") }}', s=SCRIPT))

    def test_post_button_carries_a_csrf_token_and_posts(self):
        form = soup(self.render('{{ post_button("Log out", "/logout") }}')).find("form")
        self.assertEqual(form["method"], "post")
        self.assertEqual(form["action"], "/logout")
        self.assertTrue(form.find("input", attrs={"name": "csrf_token"})["value"])
        self.assertIsNone(form.get("style"))

    def test_dialog_is_named_and_can_be_closed(self):
        page = soup(self.render('{% call dialog("d1", "Remove it?") %}<p>Sure?</p>{% endcall %}'
                                '{{ dialog_open("d1", "Remove") }}'))
        d = page.find("dialog")
        self.assertEqual(page.find(id=d["aria-labelledby"]).get_text(strip=True), "Remove it?")
        self.assertIsNotNone(d.find("button", attrs={"data-dialog-close": True}))
        self.assertEqual(page.find("button", attrs={"data-dialog-open": True})["data-dialog-open"], "d1")


class DisplayTests(MacroCase):
    def test_status_stamp_shows_its_text_for_every_kind(self):
        for kind in ("ok", "danger", "info", "warning", "neutral"):
            s = soup(self.render('{{ status("Label", kind) }}', kind=kind)).find(class_="stamp")
            self.assertEqual(s.get_text(strip=True), "Label")
            self.assertIn("stamp-" + kind, s["class"])

    def test_alert_roles_match_severity(self):
        self.assertEqual(soup(self.render('{{ alert("m", "danger") }}')).find("p")["role"], "alert")
        self.assertEqual(soup(self.render('{{ alert("m", "ok") }}')).find("p")["role"], "status")

    def test_title_block_lists_every_fact_in_a_definition_list(self):
        page = soup(self.render('{{ title_block("Drafter", [("Condition", "Good"), ("Mode", "Lend")], '
                                'status_label="Available", status_kind="ok", wide_rows=[("Wanted", "Book")]) }}'))
        self.assertEqual(page.find("h2").get_text(strip=True), "Drafter")
        self.assertEqual([t.get_text() for t in page.find_all("dt")], ["Condition", "Mode", "Wanted"])
        self.assertEqual([t.get_text() for t in page.find_all("dd")], ["Good", "Lend", "Book"])
        self.assertEqual(page.find(class_="stamp").get_text(), "Available")

    def test_title_block_escapes_titles_and_values(self):
        html = self.render('{{ title_block(s, [(s, s)], status_label=s, wide_rows=[(s, s)]) }}'
                           '{% call panel(s) %}{{ s }}{% endcall %}', s=SCRIPT)
        self.assertNotIn(SCRIPT, html)

    def test_title_block_without_status_has_no_stamp(self):
        page = soup(self.render('{{ title_block("T", [("a", "b")]) }}'))
        self.assertIsNone(page.find(class_="stamp"))

    def test_table_has_caption_scoped_headers_and_numeric_alignment(self):
        page = soup(self.render('{{ table("People", [("Name", False), ("Count", True)], [("Ann", 3)]) }}'))
        self.assertEqual(page.find("caption").get_text(), "People")
        self.assertTrue(all(th["scope"] == "col" for th in page.find_all("th")))
        self.assertIn("num", page.find_all("td")[1]["class"])
        self.assertNotIn("class", page.find_all("td")[0].attrs)

    def test_table_cells_are_escaped(self):
        self.assertNotIn(SCRIPT, self.render('{{ table(s, [(s, False)], [(s,)]) }}', s=SCRIPT))

    def test_pagination_is_hidden_for_a_single_page(self):
        self.assertEqual(self.render('{{ pagination(1, 1, "design.index") }}').strip(), "")

    def test_pagination_first_page_has_no_previous_link(self):
        page = soup(self.render('{{ pagination(1, 3, "design.index") }}'))
        links = {a.get_text(): a["href"] for a in page.find_all("a")}
        self.assertEqual(list(links), ["Next"])
        self.assertEqual(links["Next"], "/design?page=2")
        self.assertEqual(page.find(class_="page-link-off")["aria-disabled"], "true")

    def test_pagination_keeps_filters_and_marks_page_status(self):
        page = soup(self.render('{{ pagination(2, 3, "design.index", {"q": "drafter"}) }}'))
        hrefs = sorted(a["href"] for a in page.find_all("a"))
        self.assertEqual(hrefs, ["/design?page=1&q=drafter", "/design?page=3&q=drafter"])
        self.assertIn("Page 2 of 3", page.get_text())

    def test_last_page_has_no_next_link(self):
        page = soup(self.render('{{ pagination(3, 3, "design.index") }}'))
        self.assertEqual([a.get_text() for a in page.find_all("a")], ["Previous"])

    def test_empty_state_invites_an_action_only_when_given_one(self):
        with_action = soup(self.render('{{ empty_state("None", "Nothing here.", "Post an item", "/new") }}'))
        self.assertEqual(with_action.find("a")["href"], "/new")
        self.assertIsNone(soup(self.render('{{ empty_state("None", "Nothing here.") }}')).find("a"))

    def test_problem_and_loading_states_are_announced(self):
        self.assertEqual(soup(self.render('{{ problem_state("Broke", "Reload it.") }}')).find("section")["role"], "alert")
        loading = soup(self.render('{{ loading_state("Loading listings", 4) }}')).find("section")
        self.assertEqual(loading["role"], "status")
        self.assertEqual(len(loading.find_all(class_="skeleton")), 4)
        self.assertTrue(all(s["aria-hidden"] == "true" for s in loading.find_all(class_="skeleton")))
        self.assertIn("Loading listings", loading.get_text())

    def test_state_text_is_escaped(self):
        html = self.render('{{ empty_state(s, s, s, "/x") }}{{ problem_state(s, s) }}{{ loading_state(s) }}', s=SCRIPT)
        self.assertNotIn(SCRIPT, html)


class NavbarTests(MacroCase):
    def test_guest_sees_login_and_signup_but_no_logout(self):
        nav = soup(self.render('{{ navbar("auth.login") }}'))
        texts = [a.get_text() for a in nav.find_all("a")]
        self.assertIn("Log in", texts)
        self.assertIn("Sign up", texts)
        self.assertNotIn("Post an item", texts)
        self.assertIsNone(nav.find("form"))

    def test_student_sees_member_links_and_a_logout_form(self):
        nav = soup(self.render('{{ navbar("listings.browse") }}', user={"full_name": "Asha Rao"}))
        texts = [a.get_text() for a in nav.find_all("a")]
        for expected in ("Post an item", "My listings", "Requests sent", "Requests received", "Asha Rao"):
            self.assertIn(expected, texts)
        self.assertNotIn("Log in", texts)
        self.assertEqual(nav.find("form")["action"], "/logout")

    def test_current_page_link_is_marked(self):
        nav = soup(self.render('{{ navbar("listings.browse") }}'))
        current = nav.find_all(attrs={"aria-current": "page"})
        self.assertEqual([a.get_text() for a in current], ["Listings"])

    def test_user_name_is_escaped(self):
        self.assertNotIn(SCRIPT, self.render('{{ navbar() }}', user={"full_name": SCRIPT}))

    def test_navigation_has_a_label(self):
        self.assertEqual(soup(self.render('{{ navbar() }}')).find("nav")["aria-label"], "Main")


class AssetTests(MacroCase):
    def test_base_loads_components_css_after_tokens_and_base(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertLess(html.index("tokens.css"), html.index("base.css"))
        self.assertLess(html.index("base.css"), html.index("components.css"))
        self.assertRegex(html, r'<script src="/static/js/dialog.js" defer>')

    def test_assets_are_served(self):
        for path, kind in (("/static/css/components.css", "text/css"), ("/static/js/dialog.js", "javascript")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertIn(kind, r.headers["Content-Type"])
            r.close()

    def test_component_templates_have_no_inline_styles(self):
        names = os.listdir(COMPONENT_DIR)
        self.assertGreaterEqual(len(names), 4)
        for name in names:
            with open(os.path.join(COMPONENT_DIR, name), encoding="utf-8") as f:
                self.assertNotIn("style=", f.read(), name)

    def test_dialog_script_uses_native_dialog_and_has_valid_syntax(self):
        with open(JS, encoding="utf-8") as f:
            code = f.read()
        for needle in ("showModal", "data-dialog-open", "data-dialog-close"):
            self.assertIn(needle, code)
        node = shutil.which("node")
        if node:
            result = subprocess.run([node, "--check", JS], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_motion_in_components_is_covered_by_the_reduced_motion_rule(self):
        with open(CSS, encoding="utf-8") as f:
            css = f.read()
        self.assertIn("animation:", css)
        base = os.path.join(ROOT, "app", "static", "css", "base.css")
        with open(base, encoding="utf-8") as f:
            rule = f.read()
        self.assertRegex(rule, r"prefers-reduced-motion: reduce\)\s*\{\s*\*")
        self.assertIn("animation-duration", rule)


if __name__ == "__main__":
    unittest.main()
