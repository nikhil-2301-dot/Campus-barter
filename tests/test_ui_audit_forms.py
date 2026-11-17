"""C17: the form and structure rules of the audit (scripts/ui_audit.py).

Part 1: each new rule fires on a small bad snippet and stays quiet on a good one, so a rule cannot die silently.
Part 2: the audit over every page as guest, student and admin finds none of them, one test per category.
NOTE: scripts/check.sh gates on three words in the test log, so test names and docstrings avoid them.
"""
import tempfile
import unittest

import helpers  # noqa: F401  (puts the project root on sys.path)
from scripts import ui_audit
from scripts.ui_audit import RULES, audit_html, structure_findings
from test_ui_audit import page, rules_in

SUBMIT = '<script src="/static/js/submit-once.js" defer></script>'


def form(body, method="post"):
    return page(f'<form method="{method}" novalidate>{body}</form>{SUBMIT}')


def field(name, kind="text", extra=""):
    return f'<label for="f-{name}">{name}</label><input id="f-{name}" name="{name}" type="{kind}" {extra}>'


class TitleTextTests(unittest.TestCase):
    def test_a_tag_inside_the_title_is_found(self):
        bad = page().replace("<title>Page</title>", '<title>Page<script src="/x.js"></script></title>')
        self.assertIn("title-text", rules_in(bad))

    def test_a_plain_title_is_fine(self):
        self.assertNotIn("title-text", rules_in(page()))


class AutocompleteTests(unittest.TestCase):
    def test_email_without_autocomplete_is_found(self):
        self.assertIn("autocomplete", rules_in(form(field("email", "email"))))

    def test_email_with_autocomplete_off_is_found(self):
        self.assertIn("autocomplete", rules_in(form(field("email", "email", 'autocomplete="off"'))))

    def test_email_with_the_right_value_is_fine(self):
        self.assertNotIn("autocomplete", rules_in(form(field("email", "email", 'autocomplete="email"'))))

    def test_a_signup_pair_of_passwords_needs_new_password(self):
        pair = field("password", "password", 'autocomplete="current-password"') + field(
            "password_confirm", "password", 'autocomplete="new-password"')
        self.assertIn("autocomplete", rules_in(form(pair)))
        good = field("password", "password", 'autocomplete="new-password"') + field(
            "password_confirm", "password", 'autocomplete="new-password"')
        self.assertNotIn("autocomplete", rules_in(form(good)))

    def test_a_single_password_needs_current_password(self):
        self.assertIn("autocomplete", rules_in(form(field("password", "password"))))
        self.assertNotIn("autocomplete", rules_in(form(field("password", "password", 'autocomplete="current-password"'))))

    def test_name_and_phone_need_their_values(self):
        self.assertIn("autocomplete", rules_in(form(field("full_name"))))
        self.assertNotIn("autocomplete", rules_in(form(field("full_name", "text", 'autocomplete="name"'))))
        self.assertIn("autocomplete", rules_in(form(field("phone", "tel"))))
        self.assertNotIn("autocomplete", rules_in(form(field("phone", "tel", 'autocomplete="tel"'))))

    def test_other_fields_need_nothing(self):
        self.assertNotIn("autocomplete", rules_in(form(field("branch"))))


class InputTypeTests(unittest.TestCase):
    def test_email_phone_password_and_search_must_use_their_type(self):
        for name, wrong, right in (("email", "text", "email"), ("phone", "text", "tel"),
                                   ("password", "text", "password"), ("q", "text", "search")):
            self.assertIn("input-type", rules_in(form(field(name, wrong, 'autocomplete="email"'))), name)
            self.assertNotIn("input-type", rules_in(form(field(name, right, 'autocomplete="off"'))), name)

    def test_the_hidden_csrf_field_is_left_alone(self):
        self.assertNotIn("input-type", rules_in(form('<input type="hidden" name="csrf_token" value="x">')))


class SubmitOnceTests(unittest.TestCase):
    def test_a_post_form_without_the_script_is_found(self):
        self.assertIn("submit-once", rules_in(page('<form method="post"><button>Go</button></form>')))

    def test_a_post_form_with_the_script_is_fine(self):
        self.assertNotIn("submit-once", rules_in(page('<form method="post"><button>Go</button></form>' + SUBMIT)))

    def test_a_search_form_that_uses_get_needs_no_script(self):
        self.assertNotIn("submit-once", rules_in(page('<form method="get"><button>Go</button></form>')))


class ProblemSummaryTests(unittest.TestCase):
    def long_form(self, problem=True, summary=True):
        fields = "".join(field(f"f{i}") for i in range(6))
        if problem:
            fields += '<p class="field-problem" id="p-f3" role="alert">Wrong.</p>'
        box = ('<div class="problem-summary" role="alert"><h2>One problem</h2><ul>'
               '<li><a href="#f-f3">f3: Wrong.</a></li></ul></div>') if summary else ""
        return page(box + f'<form method="post" novalidate>{fields}</form>{SUBMIT}')

    def test_long_form_with_a_problem_and_no_summary_is_found(self):
        self.assertIn("problem-summary", rules_in(self.long_form(summary=False)))

    def test_long_form_with_a_summary_is_fine(self):
        self.assertNotIn("problem-summary", rules_in(self.long_form()))

    def test_a_summary_that_misses_a_field_is_found(self):
        bad = self.long_form().replace('href="#f-f3"', 'href="#f-f0"')
        self.assertIn("problem-summary", rules_in(bad))

    def test_a_summary_that_is_not_an_alert_is_found(self):
        bad = self.long_form().replace('class="problem-summary" role="alert"', 'class="problem-summary"')
        self.assertIn("problem-summary", rules_in(bad))

    def test_long_form_without_problems_needs_no_summary(self):
        self.assertNotIn("problem-summary", rules_in(self.long_form(problem=False, summary=False)))

    def test_short_form_with_a_problem_needs_no_summary(self):
        short = page('<form method="post" novalidate>' + field("a") + '<p class="field-problem" id="p-a">No.</p></form>' + SUBMIT)
        self.assertNotIn("problem-summary", rules_in(short))


class StructureTests(unittest.TestCase):
    def rules(self, body):
        return {rule for rule, _tag, _msg in structure_findings(page(body))}

    def test_a_tag_that_is_never_closed_is_found(self):
        self.assertIn("html-balance", self.rules("<div><span>text</div>"))
        self.assertIn("html-balance", self.rules("<div>text"))

    def test_a_stray_closing_tag_is_found(self):
        self.assertIn("html-balance", self.rules("<p>text</p></div>"))

    def test_balanced_tags_are_fine(self):
        self.assertEqual(self.rules("<div><p>one</p><ul><li>a<li>b</ul><img src='x' alt=''><br></div>"), set())

    def test_block_inside_p_is_found(self):
        self.assertIn("html-nesting", self.rules("<p>text <div>box</div></p>"))

    def test_link_in_link_and_button_in_link_are_found(self):
        self.assertIn("html-nesting", self.rules('<a href="/a">x<a href="/b">y</a></a>'))
        self.assertIn("html-nesting", self.rules('<a href="/a"><button>y</button></a>'))
        self.assertIn("html-nesting", self.rules("<button><button>y</button></button>"))

    def test_form_in_form_is_found(self):
        self.assertIn("html-nesting", self.rules("<form><form></form></form>"))

    def test_the_findings_reach_audit_html_with_their_rule_names(self):
        self.assertIn("html-nesting", rules_in(page("<p><div>x</div></p>")))
        self.assertEqual(RULES["html-balance"][0], "error")


def setUpModule():
    global _tmp, APP, IDS, REPORT
    _tmp = tempfile.TemporaryDirectory()
    APP, IDS = ui_audit.build_audit_app(_tmp.name)
    REPORT = ui_audit.run_audit(APP, IDS)


def tearDownModule():
    from app import logs
    logs._remove_ours(APP.logger)
    _tmp.cleanup()


class FormAuditOnEveryPageTests(unittest.TestCase):
    def count(self, *rules):
        return sum(REPORT.count("error", r) for r in rules)

    def show(self, *rules):
        return [f"{f.rule} {f.page} {f.selector}" for f in REPORT.findings if f.rule in rules][:5]

    def test_titles_are_plain_text_on_every_page(self):
        self.assertEqual(self.count("title-text"), 0, self.show("title-text"))

    def test_autocomplete_on_every_form(self):
        self.assertEqual(self.count("autocomplete"), 0, self.show("autocomplete"))

    def test_input_types_on_every_form(self):
        self.assertEqual(self.count("input-type"), 0, self.show("input-type"))

    def test_every_post_form_page_loads_submit_once(self):
        self.assertEqual(self.count("submit-once"), 0, self.show("submit-once"))

    def test_long_refused_forms_show_a_problem_summary(self):
        self.assertEqual(self.count("problem-summary"), 0, self.show("problem-summary"))

    def test_html_structure_on_every_page(self):
        self.assertEqual(self.count("html-balance", "html-nesting"), 0, self.show("html-balance", "html-nesting"))

    def test_the_refused_long_forms_were_in_the_walk(self):
        names = {p["name"] for p in REPORT.pages if p["audited"]}
        for expected in ("POST /signup empty form", "POST /profile empty name", "POST /listings/new empty form"):
            self.assertIn(expected, names)

    def test_the_edit_form_with_a_problem_is_in_the_walk(self):
        names = {p["name"] for p in REPORT.pages if p["audited"]}
        self.assertIn("POST edit listing empty title", names)


if __name__ == "__main__":
    unittest.main()
