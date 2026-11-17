"""C16: the accessibility audit (scripts/ui_audit.py) and its results on every page and role.

Part 1: each rule fires on a small bad snippet and stays quiet on a good one (so a rule cannot die silently).
Part 2: the shipped CSS has the focus ring and the reduced-motion rule.
Part 3: the audit over every page as guest, student and admin finds 0 problems, one test per category.
NOTE: scripts/check.sh gates on three words in the test log, so test names and docstrings avoid them.
"""
import os
import tempfile
import unittest

import helpers  # noqa: F401  (puts the project root on sys.path)
from scripts import ui_audit
from scripts.ui_audit import audit_css, audit_html, RULES

GOOD = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Page</title></head><body>'
    '<a class="skip-link" href="#main">Skip to main content</a>'
    '<header><a href="/">Home</a><nav aria-label="Main"><ul><li><a href="/x">Listings</a></li></ul></nav></header>'
    '<main id="main"><h1>Title</h1>{body}</main><footer><p>Footer</p></footer></body></html>'
)


def page(body=""):
    return GOOD.replace("{body}", body)


def rules_in(html):
    return {f.rule for f in audit_html(html, "snippet")}


class GoodPageTests(unittest.TestCase):
    def test_the_good_page_has_no_findings(self):
        self.assertEqual(audit_html(page(), "good"), [])

    def test_every_rule_has_a_severity_and_a_description(self):
        for rule, (severity, text) in RULES.items():
            self.assertIn(severity, ("error", "warning"), rule)
            self.assertTrue(text, rule)


class DocumentRuleTests(unittest.TestCase):
    def test_lang_rule(self):
        self.assertIn("lang", rules_in(GOOD.replace(' lang="en"', "").replace("{body}", "")))
        self.assertIn("lang", rules_in(GOOD.replace('lang="en"', 'lang=""').replace("{body}", "")))
        self.assertNotIn("lang", rules_in(page()))

    def test_title_rule(self):
        self.assertIn("title", rules_in(GOOD.replace("<title>Page</title>", "").replace("{body}", "")))
        self.assertIn("title", rules_in(GOOD.replace("<title>Page</title>", "<title> </title>").replace("{body}", "")))
        self.assertNotIn("title", rules_in(page()))

    def test_zoom_rule(self):
        blocked = GOOD.replace("initial-scale=1", "initial-scale=1, user-scalable=no").replace("{body}", "")
        self.assertIn("viewport-zoom", rules_in(blocked))
        capped = GOOD.replace("initial-scale=1", "maximum-scale=1").replace("{body}", "")
        self.assertIn("viewport-zoom", rules_in(capped))
        self.assertNotIn("viewport-zoom", rules_in(page()))


class HeadingRuleTests(unittest.TestCase):
    def test_h1_count_rule(self):
        self.assertIn("h1-count", rules_in(page("<h1>Second</h1>")))
        self.assertIn("h1-count", rules_in(page().replace("<h1>Title</h1>", "")))
        self.assertNotIn("h1-count", rules_in(page("<h2>Part</h2>")))

    def test_heading_order_rule(self):
        self.assertIn("heading-order", rules_in(page("<h3>Skipped a level</h3>")))
        self.assertIn("heading-order", rules_in(page().replace("<h1>Title</h1>", "<h2>Title</h2><h1>Late</h1>")))
        self.assertNotIn("heading-order", rules_in(page("<h2>A</h2><h3>B</h3><h2>C</h2>")))
        self.assertNotIn("heading-order", rules_in(page("<h2>A</h2><h3>B</h3><h4>C</h4><h2>D</h2>")))

    def test_empty_heading_rule(self):
        self.assertIn("heading-empty", rules_in(page("<h2> </h2>")))
        self.assertNotIn("heading-empty", rules_in(page("<h2>Text</h2>")))


class FormRuleTests(unittest.TestCase):
    def test_label_rule_fires_for_each_kind_of_control(self):
        for control in ('<input id="f-a" name="a">', '<select id="f-a" name="a"><option>x</option></select>',
                        '<textarea id="f-a" name="a"></textarea>'):
            self.assertIn("label", rules_in(page(control)), control)

    def test_label_rule_needs_a_matching_for_and_some_text(self):
        self.assertIn("label", rules_in(page('<label for="other">Name</label><input id="f-a">')))
        self.assertIn("label", rules_in(page('<label for="f-a"> </label><input id="f-a">')))
        self.assertIn("label", rules_in(page('<input id="f-a" placeholder="Name">')))

    def test_label_rule_accepts_every_proper_label(self):
        for good in ('<label for="f-a">Name</label><input id="f-a">',
                     '<input aria-label="Search">',
                     '<span id="s">Search</span><input aria-labelledby="s">',
                     '<label>Name <input></label>',
                     '<input type="hidden" name="csrf_token" value="x">',
                     '<input type="submit" value="Go">'):
            self.assertNotIn("label", rules_in(page(good)), good)

    def test_alt_rule(self):
        self.assertIn("alt", rules_in(page('<img src="a.png">')))
        self.assertIn("alt", rules_in(page('<input type="image" src="a.png">')))
        self.assertNotIn("alt", rules_in(page('<img src="a.png" alt="A drafter">')))
        self.assertNotIn("alt", rules_in(page('<img src="a.png" alt="">')))

    def test_button_name_rule(self):
        self.assertIn("button-name", rules_in(page("<button></button>")))
        self.assertIn("button-name", rules_in(page('<button><span aria-hidden="true">x</span></button>')))
        self.assertIn("button-name", rules_in(page('<input type="button">')))
        self.assertNotIn("button-name", rules_in(page("<button>Send</button>")))
        self.assertNotIn("button-name", rules_in(page('<button aria-label="Close"></button>')))

    def test_link_name_rule(self):
        self.assertIn("link-name", rules_in(page('<a href="/y"></a>')))
        self.assertIn("link-name", rules_in(page('<a href="/y"><img src="a.png" alt=""></a>')))
        self.assertNotIn("link-name", rules_in(page('<a href="/y">Listings</a>')))
        self.assertNotIn("link-name", rules_in(page('<a href="/y"><img src="a.png" alt="Home"></a>')))

    def test_vague_link_text_is_a_warning_only(self):
        findings = [f for f in audit_html(page('<a href="/y">Click here</a>'), "x") if f.rule == "link-text"]
        self.assertEqual([f.severity for f in findings], ["warning"])
        self.assertNotIn("link-text", rules_in(page('<a href="/y">Read the safety advice</a>')))


class LandmarkRuleTests(unittest.TestCase):
    def test_main_rule(self):
        self.assertIn("landmark-main", rules_in(page().replace("<main", "<div").replace("</main>", "</div>")))
        self.assertIn("landmark-main", rules_in(page("</main><main>")))

    def test_banner_and_footer_rules(self):
        self.assertIn("landmark-banner", rules_in(page().replace("<header>", "<div>").replace("</header>", "</div>")))
        self.assertIn("landmark-footer", rules_in(page().replace("<footer>", "<div>").replace("</footer>", "</div>")))
        self.assertIn("landmark-footer", rules_in(page().replace("</body>", "<footer>Second</footer></body>")))

    def test_a_header_inside_main_is_not_the_site_banner(self):
        inner = page("<section><header><h2>Part</h2></header></section>")
        self.assertNotIn("landmark-banner", rules_in(inner))

    def test_nav_rules_are_warnings(self):
        no_nav = page().replace('<nav aria-label="Main">', "<div>").replace("</nav>", "</div>")
        two_nav = page('<nav><a href="/z">More pages</a></nav>')
        for html, rule in ((no_nav, "landmark-nav"), (two_nav, "nav-label")):
            findings = [f for f in audit_html(html, "x") if f.rule == rule]
            self.assertTrue(findings, rule)
            self.assertEqual({f.severity for f in findings}, {"warning"})
        labelled = page('<nav aria-label="Pagination"><a href="/z">Next page</a></nav>')
        self.assertNotIn("nav-label", rules_in(labelled))


class IdRuleTests(unittest.TestCase):
    def test_duplicate_id_rule(self):
        self.assertIn("duplicate-id", rules_in(page('<p id="a">1</p><p id="a">2</p>')))
        self.assertNotIn("duplicate-id", rules_in(page('<p id="a">1</p><p id="b">2</p>')))

    def test_aria_reference_rule(self):
        self.assertIn("aria-ref", rules_in(page('<input aria-label="x" aria-describedby="missing">')))
        self.assertIn("aria-ref", rules_in(page('<button aria-controls="missing">Open</button>')))
        self.assertNotIn("aria-ref", rules_in(page('<p id="h">Hint</p><input aria-label="x" aria-describedby="h">')))

    def test_fragment_target_rule_covers_the_skip_link(self):
        self.assertIn("fragment-target", rules_in(page().replace('id="main"', 'id="other"')))
        self.assertNotIn("fragment-target", rules_in(page()))


class TableAndDialogRuleTests(unittest.TestCase):
    TABLE = '<table>{cap}<thead><tr><th scope="col">A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>'

    def test_table_caption_rule(self):
        self.assertIn("table-caption", rules_in(page(self.TABLE.replace("{cap}", ""))))
        self.assertIn("table-caption", rules_in(page(self.TABLE.replace("{cap}", "<caption> </caption>"))))
        self.assertNotIn("table-caption", rules_in(page(self.TABLE.replace("{cap}", "<caption>Items</caption>"))))

    def test_table_header_rule_is_a_warning(self):
        without_scope = self.TABLE.replace("{cap}", "<caption>Items</caption>").replace(' scope="col"', "")
        findings = [f for f in audit_html(page(without_scope), "x") if f.rule == "table-header"]
        self.assertEqual([f.severity for f in findings], ["warning"])

    def test_dialog_name_rule(self):
        self.assertIn("dialog-name", rules_in(page("<dialog><p>Sure?</p></dialog>")))
        self.assertNotIn("dialog-name", rules_in(page('<dialog aria-label="Confirm"><p>Sure?</p></dialog>')))
        self.assertNotIn("dialog-name", rules_in(
            page('<dialog aria-labelledby="t"><h2 id="t">Confirm</h2></dialog>')))

    def test_positive_tabindex_rule(self):
        self.assertIn("tabindex", rules_in(page('<a href="/y" tabindex="3">Go</a>')))
        self.assertNotIn("tabindex", rules_in(page('<a href="/y" tabindex="0">Go</a>')))
        self.assertNotIn("tabindex", rules_in(page().replace('id="main"', 'id="main" tabindex="-1"')))


class CssRuleTests(unittest.TestCase):
    FOCUS = ":focus-visible { outline: 2px solid #000; }"
    MOTION = "@media (prefers-reduced-motion: reduce) { * { animation-duration: 0s; transition-duration: 0s; } }"

    def rules(self, css):
        return {f.rule for f in audit_css({"x.css": css})}

    def test_good_css_has_no_findings(self):
        self.assertEqual(self.rules(self.FOCUS + self.MOTION), set())

    def test_focus_visible_rule(self):
        self.assertIn("focus-visible", self.rules(self.MOTION))
        self.assertIn("focus-visible", self.rules(":focus-visible { outline: none; }" + self.MOTION))
        self.assertNotIn("focus-visible", self.rules(":focus-visible { box-shadow: 0 0 0 2px #000; }" + self.MOTION))

    def test_outline_removed_rule(self):
        self.assertIn("outline-removed", self.rules(self.FOCUS + self.MOTION + "input:focus { outline: none; }"))
        self.assertNotIn("outline-removed", self.rules(
            self.FOCUS + self.MOTION + "input:focus:not(:focus-visible) { outline: none; }"))

    def test_reduced_motion_rule(self):
        self.assertIn("reduced-motion", self.rules(self.FOCUS))
        only_animation = "@media (prefers-reduced-motion: reduce) { * { animation-duration: 0s; } }"
        moving = ".a { transition: color 1s; }" + self.FOCUS
        self.assertIn("reduced-motion", self.rules(moving + only_animation))
        self.assertNotIn("reduced-motion", self.rules(moving + self.MOTION))

    def test_the_shipped_css_has_both_rules(self):
        files = ui_audit.read_css()
        self.assertEqual(sorted(files), ["base.css", "components.css", "landing.css", "tokens.css"])
        self.assertEqual(audit_css(files), [])


# ---------------------------------------------------------------- the full walk

_tmp = None
APP = IDS = REPORT = None


def setUpModule():
    global _tmp, APP, IDS, REPORT
    _tmp = tempfile.TemporaryDirectory()
    APP, IDS = ui_audit.build_audit_app(_tmp.name)
    REPORT = ui_audit.run_audit(APP, IDS)


def tearDownModule():
    from app import logs
    logs._remove_ours(APP.logger)
    _tmp.cleanup()


class FullAuditTests(unittest.TestCase):
    def count(self, *rules):
        return sum(REPORT.count("error", r) for r in rules)

    def show(self, *rules):
        return [f"{f.rule} {f.page} {f.selector}" for f in REPORT.findings if f.rule in rules][:5]

    def test_no_page_has_any_blocking_finding(self):
        self.assertEqual(REPORT.count("error"), 0, [f"{f.rule} {f.page}" for f in REPORT.findings][:8])

    def test_document_basics_on_every_page(self):
        names = ("lang", "title", "viewport-zoom")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_headings_on_every_page(self):
        names = ("h1-count", "heading-order", "heading-empty")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_labels_on_every_page(self):
        self.assertEqual(self.count("label"), 0, self.show("label"))

    def test_images_buttons_and_links_are_named(self):
        names = ("alt", "button-name", "link-name")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_landmarks_on_every_page(self):
        names = ("landmark-main", "landmark-banner", "landmark-footer")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_ids_and_references_on_every_page(self):
        names = ("duplicate-id", "aria-ref", "fragment-target")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_tables_dialogs_and_focus_order_on_every_page(self):
        names = ("table-caption", "dialog-name", "tabindex")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_css_rules_hold(self):
        names = ("focus-visible", "outline-removed", "reduced-motion")
        self.assertEqual(self.count(*names), 0, self.show(*names))

    def test_warnings_stay_at_zero_today(self):
        # warnings do not break the gate, but today there are none; a new one should be looked at on purpose
        self.assertEqual(REPORT.count("warning"), 0, [f"{f.rule} {f.page}" for f in REPORT.findings][:8])


class CoverageTests(unittest.TestCase):
    def test_every_route_that_shows_a_page_has_an_audit_row(self):
        self.assertEqual(REPORT.unmatched, [])

    def test_a_route_without_a_row_is_noticed(self):
        plan = [row for row in ui_audit.page_plan(IDS) if not row.path.startswith("/admin/users")]
        missing = ui_audit.uncovered_rules(APP, plan)
        self.assertIn("/admin/users", missing)
        self.assertTrue(any(m.endswith("/suspend") for m in missing), missing)
        self.assertEqual(ui_audit.uncovered_rules(APP, ui_audit.page_plan(IDS)), [])

    def test_every_role_was_audited_on_many_pages(self):
        self.assertGreaterEqual(REPORT.audited("guest"), 15)
        self.assertGreaterEqual(REPORT.audited("student"), 40)
        self.assertGreaterEqual(REPORT.audited("admin"), 25)
        self.assertEqual(ui_audit.unaudited_roles(REPORT), [])
        self.assertEqual(ui_audit.exit_code(REPORT), 0)

    def test_a_missing_role_is_noticed(self):
        partial = ui_audit.run_audit(APP, IDS, roles={"guest", "student"})
        self.assertEqual(partial.audited("admin"), 0)
        self.assertEqual(ui_audit.unaudited_roles(partial), ["admin"])
        self.assertEqual(ui_audit.exit_code(partial), 1)

    def test_all_six_problem_pages_were_audited(self):
        seen = {p["status"] for p in REPORT.pages if p["audited"]}
        self.assertTrue({400, 403, 404, 405, 429, 500} <= seen, seen)

    def test_the_audit_sees_real_pages_not_empty_answers(self):
        ok = [p for p in REPORT.pages if p["status"] == 200]
        self.assertGreater(len(ok), 60)
        by_role = {p["role"] for p in ok}
        self.assertEqual(by_role, {"guest", "student", "admin"})

    def test_refused_forms_and_messages_were_audited(self):
        names = {p["name"] for p in REPORT.pages if p["audited"]}
        for expected in ("POST /login wrong password", "POST /signup empty form", "POST report too short",
                         "POST /login locked", "POST /logout (message after redirect)"):
            self.assertIn(expected, names)

    def test_a_bad_page_would_be_caught_in_the_walk(self):
        bad = audit_html(page('<input id="f-x">'), "walk")
        self.assertEqual([f.rule for f in bad], ["label"])
        self.assertEqual(bad[0].severity, "error")


if __name__ == "__main__":
    unittest.main()
