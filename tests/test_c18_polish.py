"""C18 motion and polish: reduced-motion proof, motion tokens, JS syntax, favicon, meta tags, SVG rules.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import os
import re
import shutil
import subprocess
import unittest

from bs4 import BeautifulSoup

from helpers import ROOT, TempDirMixin, make_app

STATIC = os.path.join(ROOT, "app", "static")
CSS_DIR = os.path.join(STATIC, "css")
JS_DIR = os.path.join(STATIC, "js")
TEMPLATES = os.path.join(ROOT, "app", "templates")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def block_after(css, opener):
    """The text between the braces of the first block that starts with `opener` (braces counted)."""
    start = css.index(opener)
    depth, i = 0, css.index("{", start)
    begin = i + 1
    while True:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[begin:i]
        i += 1


def token_colours():
    css = strip_comments(read(os.path.join(CSS_DIR, "tokens.css")))
    return {m.group(1): m.group(2).upper() for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", css)}


NO_PREF = "@media (prefers-reduced-motion: no-preference)"
OLD_ANIMATIONS = {"skeleton-sweep", "dialog-in"}


class ReducedMotionTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.css = strip_comments(read(os.path.join(CSS_DIR, "components.css")))

    def test_the_reveal_exists_only_for_people_who_have_not_asked_for_reduced_motion(self):
        inside = block_after(self.css, NO_PREF)
        self.assertIn("animation: reveal-in", inside)
        outside = self.css.replace(inside, "")
        self.assertNotIn("reveal-in var(", outside, "the reveal animation is used outside the no-preference block")

    def test_the_card_fade_and_the_flash_slide_exist_only_inside_the_no_preference_block(self):
        inside = block_after(self.css, NO_PREF)
        self.assertIn(".listing-grid > li { animation: reveal-in", inside)
        self.assertIn(".page > .alert { animation: flash-in", inside)
        outside = self.css.replace(inside, "")
        self.assertNotRegex(outside, r"animation:\s*(reveal-in|flash-in)\b")
        self.assertEqual(len(re.findall(r"\.listing-grid > li:nth-child\((\d)\) \{ animation-delay", inside)), 5)

    def test_every_other_animation_is_switched_off_by_the_base_reduced_motion_rule(self):
        outside = self.css.replace(block_after(self.css, NO_PREF), "")
        names = set(re.findall(r"animation:\s*([a-z-]+)", outside))
        self.assertEqual(names, OLD_ANIMATIONS)
        base = strip_comments(read(os.path.join(CSS_DIR, "base.css")))
        rule = block_after(base, "@media (prefers-reduced-motion: reduce)")
        for needle in ("animation-duration", "animation-iteration-count", "transition-duration"):
            self.assertIn(needle, rule)

    def test_no_animation_starts_hidden_outside_the_no_preference_block(self):
        outside = self.css.replace(block_after(self.css, NO_PREF), "")
        self.assertNotIn("animation-fill-mode", outside)
        self.assertNotRegex(outside, r"animation:[^;]*\b(both|backwards)\b")

    def test_motion_times_come_from_tokens(self):
        tokens = strip_comments(read(os.path.join(CSS_DIR, "tokens.css")))
        for name in ("motion-fast", "motion-base", "motion-loop", "motion-reveal", "reveal-step", "reveal-distance"):
            self.assertRegex(tokens, rf"--{name}:")
        for fname in ("components.css", "base.css"):
            css = strip_comments(read(os.path.join(CSS_DIR, fname)))
            css = css.replace(block_after(css, "@media (prefers-reduced-motion: reduce)"), "") if fname == "base.css" else css
            for decl in re.findall(r"(?:animation|transition)(?:-delay|-duration)?:[^;}]*", css):
                self.assertNotRegex(decl, r"(?<![\w-])\d+(\.\d+)?m?s\b", f"{fname}: literal time in '{decl}'")

    def test_the_landing_page_still_renders_every_piece_the_reveal_touches(self):
        client = make_app(self.make_tmp()).test_client()
        html = client.get("/").get_data(as_text=True)
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNotNone(soup.select_one(".lp-hero h1"))
        self.assertIsNotNone(soup.select_one(".lp-hero .lp-actions"))


class JavaScriptSyntaxTests(unittest.TestCase):
    def files(self):
        return sorted(os.path.join(JS_DIR, f) for f in os.listdir(JS_DIR) if f.endswith(".js"))

    def test_there_are_script_files(self):
        self.assertGreaterEqual(len(self.files()), 3)

    def test_every_script_passes_node_check(self):
        node = shutil.which("node")
        if node is None:
            # node is not installed here: fall back to a crude brace/paren balance so the gate still bites
            for path in self.files():
                code = read(path)
                for a, b in ("{}", "()", "[]"):
                    self.assertEqual(code.count(a), code.count(b), f"{os.path.basename(path)}: unbalanced {a}{b}")
            return
        for path in self.files():
            result = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{os.path.basename(path)}: {result.stderr}")

    def test_scripts_have_no_inline_style_or_eval(self):
        for path in self.files():
            code = read(path)
            self.assertNotIn("eval(", code)
            self.assertNotIn(".style.", code, os.path.basename(path))


class PageHeadTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.client = make_app(self.make_tmp()).test_client()

    def head(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        return BeautifulSoup(r.get_data(as_text=True), "html.parser")

    def meta(self, soup, **attrs):
        tag = soup.find("meta", attrs=attrs)
        self.assertIsNotNone(tag, attrs)
        return tag["content"]

    def test_favicon_files_are_served_and_linked(self):
        soup = self.head("/login")
        hrefs = {l["href"] for l in soup.find_all("link", rel="icon")}
        self.assertEqual(hrefs, {"/static/favicon.svg", "/static/favicon.ico"})
        svg = self.client.get("/static/favicon.svg")
        self.assertEqual(svg.status_code, 200)
        self.assertIn("image/svg+xml", svg.headers["Content-Type"])
        svg.close()
        ico = self.client.get("/static/favicon.ico")
        self.assertEqual(ico.status_code, 200)
        self.assertEqual(ico.get_data()[:4], b"\x00\x00\x01\x00")
        ico.close()

    def test_favicon_colours_are_token_colours(self):
        allowed = set(token_colours().values())
        used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", read(os.path.join(STATIC, "favicon.svg")))}
        self.assertTrue(used)
        self.assertTrue(used <= allowed, used - allowed)

    def test_every_page_has_description_theme_colour_and_open_graph_tags(self):
        for path in ("/", "/login", "/signup", "/listings/"):
            soup = self.head(path)
            self.assertTrue(self.meta(soup, name="description").strip(), path)
            self.assertEqual(self.meta(soup, property="og:title"), soup.title.get_text().strip(), path)
            self.assertEqual(self.meta(soup, property="og:description"), self.meta(soup, name="description"), path)
            self.assertEqual(self.meta(soup, property="og:type"), "website")

    def test_theme_colour_is_the_paper_token(self):
        soup = self.head("/")
        self.assertEqual(self.meta(soup, name="theme-color").upper(), token_colours()["paper"])

    def test_no_invented_domain_in_the_meta_tags(self):
        for path in ("/", "/login"):
            soup = self.head(path)
            self.assertIsNone(soup.find("meta", attrs={"property": "og:url"}))
            self.assertIsNone(soup.find("meta", attrs={"property": "og:image"}))
            for tag in soup.find_all("meta"):
                self.assertNotRegex(tag.get("content", ""), r"https?://", str(tag))

    def test_landing_has_its_own_description(self):
        self.assertIn("trade skills", self.meta(self.head("/"), name="description"))


class IllustrationTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.client = make_app(self.make_tmp()).test_client()

    def test_landing_hero_has_one_hidden_drawing(self):
        soup = BeautifulSoup(self.client.get("/").get_data(as_text=True), "html.parser")
        art = soup.select(".lp-hero svg.art")
        self.assertEqual(len(art), 1)

    def test_every_svg_in_every_page_is_hidden_from_assistive_tech_and_has_no_inline_style(self):
        for path in ("/", "/design", "/listings/"):
            soup = BeautifulSoup(self.client.get(path, follow_redirects=True).get_data(as_text=True), "html.parser")
            for svg in soup.find_all("svg"):
                self.assertEqual(svg.get("aria-hidden"), "true", path)
                self.assertEqual(svg.get("focusable"), "false", path)
                self.assertFalse(svg.find_all(style=True) or svg.has_attr("style"), path)

    def test_empty_states_carry_the_box_drawing(self):
        soup = BeautifulSoup(self.client.get("/listings/?q=zzzznothing").get_data(as_text=True), "html.parser")
        self.assertTrue(soup.select("section.state svg.state-art"))

    def test_drawing_template_has_no_colour_values_or_style_attributes(self):
        text = read(os.path.join(TEMPLATES, "components", "illustrations.html"))
        body = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
        self.assertNotRegex(body, r"#[0-9A-Fa-f]{3,8}\b|rgb|hsl|style=")

    def test_illustration_classes_have_css_rules(self):
        css = read(os.path.join(CSS_DIR, "components.css"))
        for cls in ("ill", "ill-fill", "ill-paper", "ill-soft", "hero-art", "state-art"):
            self.assertRegex(css, rf"\.{cls}\b", cls)


if __name__ == "__main__":
    unittest.main()
