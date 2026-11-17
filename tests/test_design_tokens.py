"""Design tokens: contrast maths, CSS hygiene, docs in sync, stylesheet served.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import os
import re
import unittest

from helpers import ROOT, TempDirMixin, make_app

CSS_DIR = os.path.join(ROOT, "app", "static", "css")
DOC = os.path.join(ROOT, "docs", "design-direction.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def colour_tokens():
    """name -> '#RRGGBB' for every colour token in tokens.css."""
    css = strip_comments(read(os.path.join(CSS_DIR, "tokens.css")))
    return {m.group(1): m.group(2).upper()
            for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", css)}


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ContrastMathTests(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(contrast("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(contrast("#777777", "#777777"), 1.0, places=2)
        self.assertAlmostEqual(contrast("#767676", "#FFFFFF"), 4.54, places=1)


class PaletteContrastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = colour_tokens()

    def check(self, fg, bg, minimum):
        r = contrast(self.c[fg], self.c[bg])
        self.assertGreaterEqual(r, minimum, f"{fg} on {bg} is {r:.2f}, needs {minimum}")

    def test_body_text_meets_aaa_on_both_grounds(self):
        for bg in ("paper", "sheet"):
            self.check("ink", bg, 7.0)

    def test_secondary_text_and_links_meet_aa(self):
        for bg in ("paper", "sheet"):
            self.check("ink-muted", bg, 4.5)
            self.check("link", bg, 4.5)

    def test_primary_button_text_meets_aa_in_both_states(self):
        self.check("on-action", "action", 4.5)
        self.check("on-action", "action-hover", 4.5)

    def test_text_on_pencil_highlight(self):
        self.check("ink", "pencil", 4.5)

    def test_status_text_on_its_tint_and_on_sheet(self):
        for name in ("ok", "danger", "info", "warning"):
            self.check(name, name + "-bg", 4.5)
        for name in ("ok", "danger", "info"):
            self.check(name, "sheet", 4.5)

    def test_control_border_and_focus_ring_meet_non_text_contrast(self):
        for bg in ("paper", "sheet"):
            self.check("control-border", bg, 3.0)
            self.check("focus", bg, 3.0)

    def test_every_colour_token_is_a_valid_six_digit_hex(self):
        self.assertGreaterEqual(len(self.c), 20)


class CssHygieneTests(unittest.TestCase):
    def css_files(self):
        return sorted(f for f in os.listdir(CSS_DIR) if f.endswith(".css"))

    def test_no_colour_literals_outside_tokens(self):
        pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
        for name in self.css_files():
            if name == "tokens.css":
                continue
            found = pattern.findall(strip_comments(read(os.path.join(CSS_DIR, name))))
            self.assertEqual(found, [], f"{name} has hard-coded colours: {found}")

    def test_every_var_used_is_defined_in_tokens(self):
        defined = set(re.findall(r"--([a-z0-9-]+)\s*:", strip_comments(read(os.path.join(CSS_DIR, "tokens.css")))))
        for name in self.css_files():
            used = set(re.findall(r"var\(--([a-z0-9-]+)", strip_comments(read(os.path.join(CSS_DIR, name)))))
            self.assertEqual(used - defined, set(), f"{name} uses undefined tokens")

    def test_base_css_has_focus_visible_and_reduced_motion_rules(self):
        base = strip_comments(read(os.path.join(CSS_DIR, "base.css")))
        self.assertIn(":focus-visible", base)
        self.assertIn("prefers-reduced-motion: reduce", base)

    def test_focus_ring_is_not_removed_anywhere(self):
        for name in self.css_files():
            css = strip_comments(read(os.path.join(CSS_DIR, name)))
            self.assertIsNone(re.search(r"outline\s*:\s*(none|0)\b", css), f"{name} removes the focus outline")


class DocInSyncTests(unittest.TestCase):
    def test_every_colour_token_is_listed_in_design_direction(self):
        doc = read(DOC).upper()
        for name, hex_value in colour_tokens().items():
            self.assertIn(hex_value, doc, f"{name} {hex_value} missing from docs/design-direction.md")

    def test_doc_names_both_font_families(self):
        doc = read(DOC)
        self.assertIn("Barlow Condensed", doc)
        self.assertIn("IBM Plex Sans", doc)


class ServedStylesheetTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.client = make_app(self.make_tmp()).test_client()

    def test_stylesheets_are_served_as_css(self):
        for name in ("tokens.css", "base.css"):
            r = self.client.get(f"/static/css/{name}")
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/css", r.headers["Content-Type"])
            r.close()

    def test_pages_link_both_stylesheets(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn("/static/css/tokens.css", html)
        self.assertIn("/static/css/base.css", html)
        self.assertLess(html.index("tokens.css"), html.index("base.css"), "tokens must load first")

    def test_font_link_uses_swap_so_text_shows_while_fonts_load(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn("display=swap", html)

    def test_unknown_static_file_is_404(self):
        self.assertEqual(self.client.get("/static/css/nope.css").status_code, 404)

    def test_static_path_traversal_is_refused(self):
        for path in ("/static/../config.py", "/static/%2e%2e/config.py", "/static/css/../../config.py"):
            self.assertIn(self.client.get(path).status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
