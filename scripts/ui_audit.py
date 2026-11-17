#!/usr/bin/env python3
"""Accessibility audit for every Campus Barter page and role (chunk C16).

Standard library + bs4 (already installed, already used by the tests). No new dependency.

    python3 scripts/ui_audit.py            # builds a scratch app, audits every page, prints counts
    python3 scripts/ui_audit.py --pages    # also prints one line per page and role

Three parts, each usable on its own (tests/test_ui_audit.py uses all of them):
  audit_html(html, page)   one HTML string -> list of Finding (rule, severity, selector, message, page)
  audit_css(files)         the shipped CSS -> list of Finding (focus ring, reduced motion)
  build_audit_app / run_audit   a scratch migrated + seeded app, and the walk over every page and role

Severity: "error" breaks the gate (must be 0 on every page); "warning" is listed but does not fail.
The rules, why they exist and how to fix them: docs/ui-audit-checklist.md.
"""
import io
import os
import re
import sys
import tempfile
from collections import Counter, namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bs4 import BeautifulSoup, NavigableString  # noqa: E402

Finding = namedtuple("Finding", "rule severity selector message page")

# rule id -> (severity, one-line description). docs/ui-audit-checklist.md explains each one.
RULES = {
    "lang": ("error", "<html> has a lang attribute"),
    "title": ("error", "the page has a non-empty <title>"),
    "viewport-zoom": ("error", "the viewport meta tag does not block zooming"),
    "h1-count": ("error", "exactly one h1"),
    "heading-order": ("error", "headings start at h1 and never skip a level going down"),
    "heading-empty": ("error", "no empty heading"),
    "label": ("error", "every input, select and textarea has a label"),
    "alt": ("error", "every image has an alt attribute"),
    "button-name": ("error", "every button has a name"),
    "link-name": ("error", "every link has a name"),
    "link-text": ("warning", "no link that says only 'click here' or 'more'"),
    "landmark-main": ("error", "exactly one main landmark"),
    "landmark-banner": ("error", "exactly one site header (banner)"),
    "landmark-footer": ("error", "exactly one site footer (contentinfo)"),
    "landmark-nav": ("warning", "at least one nav landmark"),
    "nav-label": ("warning", "several nav landmarks each have their own aria-label"),
    "duplicate-id": ("error", "no id is used twice"),
    "aria-ref": ("error", "aria-labelledby, aria-describedby and aria-controls point to ids that exist"),
    "fragment-target": ("error", "a link to #something points to an id that exists (skip link)"),
    "table-caption": ("error", "every table has a caption"),
    "table-header": ("warning", "every table has header cells (th) with a scope"),
    "dialog-name": ("error", "every dialog has an accessible name"),
    "tabindex": ("error", "no positive tabindex"),
    "focus-visible": ("error", "the CSS draws a visible keyboard focus ring"),
    "outline-removed": ("error", "no CSS rule removes the outline without a replacement"),
    "reduced-motion": ("error", "the CSS switches animation and transition off for reduced motion"),
    # C17: forms and cheap structure checks (see docs/ui-audit-checklist.md, part A2)
    "title-text": ("error", "the <title> holds plain text only, no tags inside it"),
    "autocomplete": ("error", "email, password, name and phone fields carry the right autocomplete value"),
    "input-type": ("error", "fields named email, phone, password or search use the matching input type"),
    "submit-once": ("error", "a page with a POST form loads submit-once.js (buttons switch off after a send)"),
    "problem-summary": ("error", "a long form with field problems has a summary of them above the form"),
    "html-balance": ("error", "tags are closed in order and there is no stray closing tag"),
    "html-nesting": ("error", "no block inside <p>, no link inside link, no button inside link or button, no form inside form"),
}

LONG_FORM_CONTROLS = 5   # a form with MORE controls than this needs a problem summary when it refuses input
NO_LABEL_INPUT_TYPES = {"hidden", "submit", "reset", "button", "image"}
VAGUE_LINK_TEXT = {"click here", "here", "more", "read more", "link", "this link", "click"}
LANDMARK_BLOCKS = {"main", "article", "section", "aside", "nav"}


# --------------------------------------------------------------------------- helpers

def _selector(el):
    s = el.name
    if el.get("id"):
        s += "#" + el["id"]
    classes = el.get("class") or []
    if classes:
        s += "." + ".".join(classes[:2])
    if el.get("name"):
        s += f"[name={el['name']}]"
    return s


def _visible_text(el):
    """Text a screen reader would read inside `el`: skips aria-hidden parts and scripts, adds image alt."""
    parts = []

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                if child.parent.name not in ("script", "style"):
                    parts.append(str(child))
                continue
            if child.get("aria-hidden") == "true" or child.name in ("script", "style"):
                continue
            if child.name == "img":
                parts.append(child.get("alt") or "")
                continue
            walk(child)

    walk(el)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _text_of_ids(soup, id_list):
    out = []
    for token in (id_list or "").split():
        target = soup.find(id=token)
        if target is not None:
            out.append(_visible_text(target))
    return " ".join(t for t in out if t).strip()


def _name(el, soup):
    """Accessible name of a button, link, dialog or similar element (empty string = no name)."""
    labelled = _text_of_ids(soup, el.get("aria-labelledby"))
    if labelled:
        return labelled
    if (el.get("aria-label") or "").strip():
        return el["aria-label"].strip()
    return _visible_text(el)


def _is_banner(el):
    if el.get("role") == "banner":
        return True
    if el.name != "header":
        return False
    return not any(p.name in LANDMARK_BLOCKS for p in el.parents)


def _is_contentinfo(el):
    if el.get("role") == "contentinfo":
        return True
    if el.name != "footer":
        return False
    return not any(p.name in LANDMARK_BLOCKS for p in el.parents)


# --------------------------------------------------------------------------- HTML rules

def _real_controls(form):
    """The controls a person fills in: not hidden inputs, not buttons."""
    out = []
    for c in form.find_all(["input", "select", "textarea"]):
        kind = (c.get("type") or "text").lower() if c.name == "input" else c.name
        if c.name == "input" and kind in NO_LABEL_INPUT_TYPES:
            continue
        out.append(c)
    return out


def _wanted_autocomplete(control, form):
    """The autocomplete values a field must carry, or None when the field needs none."""
    kind = (control.get("type") or "text").lower() if control.name == "input" else control.name
    name = (control.get("name") or "").lower()
    if kind == "password":
        passwords = [c for c in form.find_all("input", attrs={"type": "password"})]
        return {"new-password"} if len(passwords) > 1 else {"current-password", "new-password"}
    if kind == "email" or "email" in name:
        return {"email", "username"}
    if kind == "tel" or "phone" in name:
        return {"tel"}
    if name in ("name", "full_name"):
        return {"name"}
    return None


def _expected_type(control):
    """The input type a field must use, judged from its name, or None."""
    name = (control.get("name") or "").lower()
    if "csrf" in name:
        return None
    if "email" in name:
        return "email"
    if "phone" in name:
        return "tel"
    if "password" in name:
        return "password"
    if name == "q" or "search" in name:
        return "search"
    return None


BLOCK_TAGS = {"address", "article", "aside", "blockquote", "div", "dl", "fieldset", "figure", "footer", "form",
              "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "p", "pre", "section",
              "table", "ul"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
END_TAG_OPTIONAL = {"li", "p", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option", "optgroup"}
INTERACTIVE = {"a", "button"}


def structure_findings(html):
    """Cheap structure check with the standard-library parser. Returns a list of (rule, tag, message).

    NOT the W3C validator: it only knows balanced tags and a few nesting rules that browsers silently repair.
    """
    from html.parser import HTMLParser

    out = []
    stack = []

    class Walker(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in VOID_TAGS:
                return
            if tag in BLOCK_TAGS and "p" in stack:
                out.append(("html-nesting", tag, f"<{tag}> inside <p> (a browser closes the <p> early)"))
            if tag == "a" and "a" in stack:
                out.append(("html-nesting", tag, "a link inside a link"))
            if tag == "button" and ("a" in stack or "button" in stack):
                out.append(("html-nesting", tag, "a button inside a link or a button"))
            if tag == "form" and "form" in stack:
                out.append(("html-nesting", tag, "a form inside a form"))
            stack.append(tag)

        def handle_startendtag(self, tag, attrs):
            pass  # <br/>, <img ... />: nothing to balance

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            if tag not in stack:
                out.append(("html-balance", tag, f"</{tag}> closes a tag that is not open"))
                return
            while stack:
                top = stack.pop()
                if top == tag:
                    break
                if top not in END_TAG_OPTIONAL:
                    out.append(("html-balance", top, f"<{top}> is not closed before </{tag}>"))

    walker = Walker(convert_charrefs=True)
    walker.feed(html)
    walker.close()
    for tag in stack:
        if tag not in END_TAG_OPTIONAL and tag not in ("html", "body", "head"):
            out.append(("html-balance", tag, f"<{tag}> is never closed"))
    return out


def audit_html(html, page="page"):
    """Audit one HTML document. Returns a list of Finding (empty list = clean)."""
    soup = BeautifulSoup(html, "html.parser")
    found = []

    def add(rule, el, message):
        sel = _selector(el) if hasattr(el, "name") and el.name else str(el)
        found.append(Finding(rule, RULES[rule][0], sel, message, page))

    root = soup.find("html")
    if root is None or not (root.get("lang") or "").strip():
        add("lang", root if root is not None else "html", "the <html> tag has no lang attribute")

    title = soup.find("title")
    if title is None or not title.get_text(strip=True):
        add("title", title if title is not None else "head", "the page has no title text")

    for meta in soup.find_all("meta", attrs={"name": "viewport"}):
        content = (meta.get("content") or "").replace(" ", "").lower()
        scale = re.search(r"maximum-scale=([0-9.]+)", content)
        if "user-scalable=no" in content or "user-scalable=0" in content or (scale and float(scale.group(1)) < 2):
            add("viewport-zoom", meta, "the viewport tag stops people from zooming")

    # headings
    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    h1s = [h for h in headings if h.name == "h1"]
    if len(h1s) != 1:
        add("h1-count", h1s[1] if len(h1s) > 1 else "body",
            f"the page has {len(h1s)} h1 headings, it needs exactly one")
    previous = 0
    for h in headings:
        level = int(h.name[1])
        if level > previous + 1:
            add("heading-order", h, f"{h.name} follows " + (f"h{previous}" if previous else "no heading at all")
                + ", a level was skipped")
        previous = level
        if not _name(h, soup):
            add("heading-empty", h, "the heading has no text")

    # forms
    labels_for = {}
    for label in soup.find_all("label"):
        if label.get("for") and _visible_text(label):
            labels_for.setdefault(label["for"], True)
    for control in soup.find_all(["input", "select", "textarea"]):
        kind = (control.get("type") or "text").lower() if control.name == "input" else control.name
        if control.name == "input" and kind in NO_LABEL_INPUT_TYPES:
            if kind == "image" and not (control.get("alt") or "").strip():
                add("alt", control, "an image button has no alt text")
            if kind == "button" and not (control.get("value") or control.get("aria-label") or "").strip():
                add("button-name", control, "an input button has no value")
            continue
        named = (
            (control.get("id") and control["id"] in labels_for)
            or (control.get("aria-label") or "").strip()
            or _text_of_ids(soup, control.get("aria-labelledby"))
            or (control.find_parent("label") is not None and _visible_text(control.find_parent("label")))
        )
        if not named:
            add("label", control, f"the {control.name} has no label")

    for img in soup.find_all("img"):
        if img.get("alt") is None:
            add("alt", img, "the image has no alt attribute (use alt=\"\" when it is only decoration)")

    for button in soup.find_all("button"):
        if not _name(button, soup):
            add("button-name", button, "the button has no text and no aria-label")

    for link in soup.find_all("a", href=True):
        name = _name(link, soup)
        if not name:
            add("link-name", link, "the link has no text and no aria-label")
        elif name.lower().strip(" .!") in VAGUE_LINK_TEXT:
            add("link-text", link, f"the link text '{name}' says nothing about where it goes")

    # landmarks
    mains = soup.find_all(lambda t: t.name == "main" or t.get("role") == "main")
    if len(mains) != 1:
        add("landmark-main", mains[1] if len(mains) > 1 else "body", f"the page has {len(mains)} main landmarks")
    banners = [t for t in soup.find_all(True) if _is_banner(t)]
    if len(banners) != 1:
        add("landmark-banner", "body", f"the page has {len(banners)} site headers, it needs exactly one")
    footers = [t for t in soup.find_all(True) if _is_contentinfo(t)]
    if len(footers) != 1:
        add("landmark-footer", "body", f"the page has {len(footers)} site footers, it needs exactly one")
    navs = soup.find_all(lambda t: t.name == "nav" or t.get("role") == "navigation")
    if not navs:
        add("landmark-nav", "body", "the page has no navigation landmark")
    if len(navs) > 1:
        names = [(n.get("aria-label") or "").strip() for n in navs]
        if not all(names) or len(set(names)) != len(names):
            add("nav-label", navs[0], "several nav landmarks need different aria-label values")

    # ids and references
    ids = Counter(t["id"] for t in soup.find_all(id=True))
    for value, count in ids.items():
        if count > 1:
            add("duplicate-id", soup.find(id=value), f"the id '{value}' is used {count} times")
    for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
        for el in soup.find_all(attrs={attr: True}):
            for token in el[attr].split():
                if token not in ids:
                    add("aria-ref", el, f"{attr} points to '{token}', which does not exist on the page")
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
            add("fragment-target", link, f"the link goes to {href}, which does not exist on the page")

    # tables, dialogs, focus order
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption is None or not caption.get_text(strip=True):
            add("table-caption", table, "the table has no caption")
        heads = table.find_all("th")
        if not heads or any(not th.get("scope") for th in heads):
            add("table-header", table, "the table needs th cells that each carry a scope")
    for dialog in soup.find_all("dialog"):
        # a dialog is named only by aria-labelledby or aria-label, never by the text it happens to contain
        if not (_text_of_ids(soup, dialog.get("aria-labelledby")) or (dialog.get("aria-label") or "").strip()):
            add("dialog-name", dialog, "the dialog has no aria-label or aria-labelledby")
    for el in soup.find_all(attrs={"tabindex": True}):
        try:
            positive = int(el["tabindex"]) > 0
        except ValueError:
            positive = False
        if positive:
            add("tabindex", el, f"tabindex={el['tabindex']} breaks the natural focus order")

    # C17: title is text only
    # Python 3.12's parser builds a child tag; 3.13+ keeps <title> as plain text, so the tag shows up as "<" in it.
    # Either way a browser prints that markup in the tab, so both shapes are findings.
    if title is not None and title.find(True) is not None:
        add("title-text", title, "the title has a tag inside it: " + title.find(True).name)
    elif title is not None and re.search(r"<\s*/?\s*[a-zA-Z!]", title.get_text()):
        add("title-text", title, "the title text contains markup: " + title.get_text().strip()[:60])

    # C17: forms
    post_forms = [f for f in soup.find_all("form") if (f.get("method") or "get").lower() == "post"]
    if post_forms and not soup.find("script", src=lambda v: v and "submit-once.js" in v):
        add("submit-once", post_forms[0], "the page has a POST form but does not load submit-once.js")
    for form in soup.find_all("form"):
        for control in _real_controls(form):
            wanted = _wanted_autocomplete(control, form)
            if wanted is not None and (control.get("autocomplete") or "").strip().lower() not in wanted:
                add("autocomplete", control, "needs autocomplete=" + " or ".join(sorted(wanted))
                    + ", it has " + repr(control.get("autocomplete")))
            if control.name == "input":
                expected = _expected_type(control)
                have = (control.get("type") or "text").lower()
                if expected is not None and have != expected:
                    add("input-type", control, f"the field '{control.get('name')}' needs type={expected}, it has type={have}")
        controls = _real_controls(form)
        problems = form.select(".field-problem")
        if len(controls) > LONG_FORM_CONTROLS and problems:
            summary = form.find_previous(class_="problem-summary")
            if summary is None or summary.get("role") != "alert":
                add("problem-summary", form, f"a form with {len(controls)} fields refused input but has no "
                    "problem summary (role=alert) above it")
            else:
                linked = {a.get("href") for a in summary.find_all("a", href=True)}
                for p in problems:
                    field_name = (p.get("id") or "")[2:]
                    if "#f-" + field_name not in linked:
                        add("problem-summary", p, f"the summary has no link to the field '{field_name}'")

    # C17: structure
    for rule, tag, message in structure_findings(html):
        found.append(Finding(rule, RULES[rule][0], f"<{tag}>", message, page))
    return found


# --------------------------------------------------------------------------- CSS rules

_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")


def audit_css(files):
    """Audit shipped CSS. `files` is {file name: css text}. Checked once, not per page."""
    found = []
    text = "\n".join(files.values())
    blocks = [(sel.strip(), body) for sel, body in _BLOCK.findall(re.sub(r"/\*.*?\*/", "", text, flags=re.S))]

    ring = False
    for sel, body in blocks:
        if ":focus-visible" in sel and ":not(:focus-visible)" not in sel:
            for decl in body.split(";"):
                name, _, value = decl.partition(":")
                value = value.strip().lower()
                if name.strip() in ("outline", "box-shadow") and value not in ("none", "0", "0px"):
                    ring = True
    if not ring:
        found.append(Finding("focus-visible", "error", "css", "no :focus-visible rule draws an outline", "css"))

    for sel, body in blocks:
        if ":not(:focus-visible)" in sel:
            continue
        if re.search(r"outline(-style)?\s*:\s*(none|0|0px)\b", body) and ":focus" in sel:
            found.append(Finding("outline-removed", "error", sel, "this rule removes the focus outline", "css"))

    has_motion = bool(re.search(r"(^|[;{\s])(animation|transition)\s*:", text))
    media = re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{(.*?)\}\s*\}", text, flags=re.S)
    if media is None:
        found.append(Finding("reduced-motion", "error", "css", "no prefers-reduced-motion: reduce block", "css"))
    elif has_motion:
        inside = media.group(1) + "}"
        if "animation" not in inside or "transition" not in inside:
            found.append(Finding("reduced-motion", "error", "css",
                                 "the reduced-motion block must switch off both animation and transition", "css"))
    return found


def read_css(root=ROOT):
    css_dir = os.path.join(root, "app", "static", "css")
    out = {}
    for name in sorted(os.listdir(css_dir)):
        if name.endswith(".css"):
            with open(os.path.join(css_dir, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


# --------------------------------------------------------------------------- the scratch app

AUDIT_PASSWORD = "Audit-only-pass-1"
ACTORS = {  # actor -> (local part of the email, role)
    "guest": (None, "guest"),
    "asha": ("asha.fe", "student"), "ravi": ("ravi.te", "student"), "meera": ("meera.be", "student"),
    "zed": ("zed.nolistings", "student"),
    "admin": ("admin.demo", "admin"),
}
DEFAULT_ACTORS = ("guest", "asha", "admin")
DOMAIN = "college.example"


def build_audit_app(tmp_dir):
    """A migrated + seeded scratch app. Returns (app, ids). Needs no network and touches no real database."""
    from app import create_app
    from app.config import MIGRATIONS_DIR
    from app.db import connect
    from app.migrate import migrate
    from app.seed import seed
    from flask import abort
    from werkzeug.security import generate_password_hash

    path = os.path.join(tmp_dir, "audit.db")
    app = create_app({"DATABASE_PATH": path, "TESTING": True, "LOG_STREAM": io.StringIO()},
                     environ={"APP_ENV": "development", "SECRET_KEY": "audit-only-key",
                              })
    from app import logs
    logs.configure(app)  # the JSON handler writes into LOG_STREAM, so a deliberate 500 stays out of the output
    conn = connect(path)
    try:
        migrate(conn, MIGRATIONS_DIR)
        seed(conn, env="development", domain=DOMAIN, password=AUDIT_PASSWORD)
        pw = generate_password_hash(AUDIT_PASSWORD)
        for local, enrol, suspended in (("zed.nolistings", "AUD0001", 0), ("sam.suspended", "AUD0002", 1)):
            conn.execute(
                "INSERT INTO users (email, password_hash, full_name, branch, year, college_name, enrollment_no, "
                "is_suspended) VALUES (?, ?, ?, 'Civil', 'SE', 'Demo Engineering College', ?, ?)",
                (local + "@" + DOMAIN, pw, local.split(".")[0].title() + " Audit", enrol, suspended))
        meera = conn.execute("SELECT id FROM users WHERE email = ?", ("meera.be@" + DOMAIN,)).fetchone()[0]
        for i in range(12):  # enough rows for a second browse page (10 per page)
            conn.execute("INSERT INTO listings (owner_id, title, category, condition, mode) "
                         "VALUES (?, ?, 'Books', 'good', 'give')", (meera, f"Audit filler book {i + 1}"))
        conn.commit()
        ids = {"user": {}, "listing": {}}
        for local, _role in ACTORS.values():
            if local:
                row = conn.execute("SELECT id FROM users WHERE email = ?", (local + "@" + DOMAIN,)).fetchone()
                ids["user"][local] = row[0]
        sam = conn.execute("SELECT id FROM users WHERE email = ?", ("sam.suspended@" + DOMAIN,)).fetchone()
        ids["user"]["sam.suspended"] = sam[0]
        for key, title in (("math", "Engineering Mathematics textbook"), ("drafter", "Mini drafter (Omega)"),
                           ("notes", "Handwritten notes, Semester 3"), ("holder", "Sheet holder"),
                           ("calc", "Scientific calculator (fx-991)")):
            ids["listing"][key] = conn.execute("SELECT id FROM listings WHERE title = ?", (title,)).fetchone()[0]
        ids["request"] = {
            "pending": conn.execute("SELECT id FROM exchange_requests WHERE status = 'pending'").fetchone()[0],
            "accepted": conn.execute("SELECT id FROM exchange_requests WHERE status = 'accepted'").fetchone()[0]}
        ids["report"] = conn.execute("SELECT id FROM reports").fetchone()[0]
        # C19: one listing has a photo, so the alt rule is proven on real pages (browse and the detail page)
        from app import photo_service
        with app.app_context():
            ids["photo_key"] = photo_service.save_file((b"\xff\xd8\xff\xe0" + bytes(32), "jpg"))
        conn.execute("UPDATE listings SET photo_key = ? WHERE id = ?", (ids["photo_key"], ids["listing"]["math"]))
        conn.commit()
    finally:
        conn.close()

    # pages that only exist for the audit: the ones nothing in the real app can trigger on demand
    @app.get("/_audit/400")
    def audit_400():
        abort(400)

    @app.get("/_audit/429")
    def audit_429():
        abort(429)

    @app.get("/_audit/500")
    def audit_500():
        abort(500)

    return app, ids


Row = namedtuple("Row", "path actors")
PostRow = namedtuple("PostRow", "name actor form_page path data follow")


def page_plan(ids):
    """Every page the audit visits: (path, actors). Built from the seed, so ids are looked up, not guessed."""
    L, R, U = ids["listing"], ids["request"], ids["user"]
    everyone = ("guest", "asha", "ravi", "admin")
    plan = [
        Row("/", DEFAULT_ACTORS),
        Row("/login", DEFAULT_ACTORS), Row("/signup", DEFAULT_ACTORS), Row("/profile", DEFAULT_ACTORS),
        Row("/campuses/new", DEFAULT_ACTORS + ("ravi",)),
        Row("/listings/", DEFAULT_ACTORS),
        Row("/listings/?page=2", DEFAULT_ACTORS),
        Row("/listings/?q=nothing-matches-this", DEFAULT_ACTORS),
        Row("/listings/?category=Books&condition=good&mode=give", DEFAULT_ACTORS),
        Row("/listings/?category=not-a-type", DEFAULT_ACTORS),
        Row(f"/listings/{L['math']}", everyone + ("meera",)),
        Row(f"/listings/{L['drafter']}", everyone),
        Row(f"/listings/{L['calc']}", ("guest", "ravi", "meera", "admin")),
        Row(f"/listings/{L['notes']}", everyone),
        Row(f"/listings/{L['holder']}", ("meera", "ravi", "guest")),
        Row("/listings/new", DEFAULT_ACTORS + ("ravi",)),
        Row("/listings/mine", DEFAULT_ACTORS + ("zed",)),
        Row(f"/listings/{L['drafter']}/edit", everyone),
        Row(f"/listings/{L['drafter']}/delete", everyone),
        Row("/requests/sent", DEFAULT_ACTORS + ("zed",)),
        Row("/requests/received", DEFAULT_ACTORS + ("ravi", "zed")),
        Row(f"/requests/{R['pending']}", everyone),
        Row(f"/requests/{R['accepted']}", ("guest", "meera", "ravi", "asha", "admin")),
        Row(f"/requests/{R['pending']}/decline", everyone),
        Row(f"/requests/{R['pending']}/cancel", everyone),
        Row(f"/requests/{R['accepted']}/cancel", ("meera", "ravi", "guest")),
        Row(f"/requests/{R['pending']}/accept", DEFAULT_ACTORS),  # GET is not allowed: the 405 page
        Row("/admin/", DEFAULT_ACTORS),
        Row("/admin/reports", DEFAULT_ACTORS),
        Row("/admin/reports?status=resolved", ("admin",)),
        Row("/admin/reports?status=dismissed", ("admin",)),
        Row("/admin/reports?status=all", ("admin",)),
        Row(f"/admin/reports/{ids['report']}", DEFAULT_ACTORS),
        Row(f"/admin/listings/{L['notes']}/remove", DEFAULT_ACTORS),
        Row("/admin/campuses", DEFAULT_ACTORS),
        Row("/admin/users", DEFAULT_ACTORS),
        Row("/admin/users?q=a", ("admin",)),
        Row(f"/admin/users/{U['ravi.te']}/suspend", DEFAULT_ACTORS),
        Row("/design", DEFAULT_ACTORS),
        Row(f"/photos/{ids['photo_key']}", DEFAULT_ACTORS),   # an image, not a page: nothing to audit, but it must be reachable
        Row("/nothing-lives-at-this-address", DEFAULT_ACTORS),   # 404
        Row("/logout", DEFAULT_ACTORS),                          # 405 (POST only)
        Row("/_audit/400", DEFAULT_ACTORS), Row("/_audit/429", DEFAULT_ACTORS), Row("/_audit/500", DEFAULT_ACTORS),
    ]
    return plan


def post_plan(ids):
    """Pages that only appear after a POST (a refused form, a locked login, a flash message)."""
    L = ids["listing"]
    return [
        PostRow("POST /login wrong password", "guest", "/login", "/login",
                {"email": "ravi.te@" + DOMAIN, "password": "not-the-password"}, False),
        PostRow("POST /signup empty form", "guest", "/signup", "/signup", {}, False),
        PostRow("POST add campus empty form", "guest", "/campuses/new", "/campuses/new", {}, False),
        PostRow("POST /profile empty name", "asha", "/profile", "/profile", {"full_name": ""}, False),
        PostRow("POST /listings/new empty form", "asha", "/listings/new", "/listings/new", {}, False),
        PostRow("POST edit listing empty title", "ravi", f"/listings/{L['drafter']}/edit",
                f"/listings/{L['drafter']}/edit", {"title": ""}, False),
        PostRow("POST report too short", "asha", f"/listings/{L['math']}", f"/listings/{L['math']}/report",
                {"reason": "short"}, False),
        PostRow("POST request twice (message after redirect)", "asha", f"/listings/{L['drafter']}",
                f"/listings/{L['drafter']}/request", {}, True),
        PostRow("POST /logout (message after redirect)", "zed", "/listings/", "/logout", {}, True),
    ]


class Report:
    """What the audit found. `pages` has one entry per (page, actor); `findings` is every finding."""

    def __init__(self):
        self.pages = []      # dicts: name, actor, role, status, audited, findings
        self.css = []
        self.unmatched = []  # url_map rules no page row covers

    @property
    def findings(self):
        out = list(self.css)
        for page in self.pages:
            out.extend(page["findings"])
        return out

    def count(self, severity="error", rule=None, role=None):
        n = 0
        for page in self.pages:
            if role and page["role"] != role:
                continue
            n += sum(1 for f in page["findings"] if f.severity == severity and (rule is None or f.rule == rule))
        if role is None:
            n += sum(1 for f in self.css if f.severity == severity and (rule is None or f.rule == rule))
        return n

    def audited(self, role):
        return sum(1 for p in self.pages if p["role"] == role and p["audited"])


def _login(app, client, actor, ids):
    local = ACTORS[actor][0]
    if local is None:
        return
    with client.session_transaction() as s:
        s["user_id"] = ids["user"][local]


def _token(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


def _record(report, name, actor, response, html):
    audited = response.status_code not in (301, 302, 303, 307, 308) and "text/html" in (response.content_type or "")
    findings = audit_html(html, f"{name} as {actor}") if audited else []
    report.pages.append({"name": name, "actor": actor, "role": ACTORS[actor][1], "status": response.status_code,
                         "audited": audited, "findings": findings})


def covered_endpoints(app, plan):
    """Endpoints reached by a page row, found by matching each path against the real url_map."""
    adapter = app.url_map.bind("localhost")
    out = set()
    for row in plan:
        path = row.path.split("?")[0]
        try:
            out.add(adapter.match(path, method="GET")[0])
        except Exception:  # a path nobody serves (the 404 row) matches no endpoint
            continue
    return out


NOT_PAGES = {"static", "health.health"}  # files and JSON, nothing to read on screen


def uncovered_rules(app, plan):
    """url_map rules that can show a page (they accept GET) but no audit row reaches."""
    covered = covered_endpoints(app, plan)
    missing = []
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and rule.endpoint not in covered and rule.endpoint not in NOT_PAGES:
            missing.append(rule.rule)
    return sorted(missing)


def run_audit(app, ids, roles=None):
    """Visit every page as every listed actor, plus the POST pages. `roles` limits actors (tests use it)."""
    report = Report()
    report.css = audit_css(read_css())
    plan = page_plan(ids)
    report.unmatched = uncovered_rules(app, plan)
    clients = {}
    for row in plan:
        for actor in row.actors:
            if roles is not None and ACTORS[actor][1] not in roles:
                continue
            if actor not in clients:
                clients[actor] = app.test_client()
                _login(app, clients[actor], actor, ids)
            response = clients[actor].get(row.path)
            is_page = "text/html" in (response.content_type or "")   # C19: an image is not decoded as text
            _record(report, "GET " + row.path, actor, response, response.get_data(as_text=True) if is_page else "")
    for row in post_plan(ids):
        if roles is not None and ACTORS[row.actor][1] not in roles:
            continue
        client = app.test_client()
        _login(app, client, row.actor, ids)
        token = _token(client.get(row.form_page).get_data(as_text=True))
        data = dict(row.data, csrf_token=token)
        response = client.post(row.path, data=data, follow_redirects=row.follow)
        _record(report, row.name, row.actor, response, response.get_data(as_text=True))
    if roles is None or "guest" in roles:  # five wrong passwords lock the email: the locked login page
        client = app.test_client()
        token = _token(client.get("/login").get_data(as_text=True))
        for _ in range(6):
            response = client.post("/login", data={"email": "lock.me@" + DOMAIN, "password": "wrong-password-1",
                                                   "csrf_token": token})
        _record(report, "POST /login locked", "guest", response, response.get_data(as_text=True))
    return report


# --------------------------------------------------------------------------- printing

def format_report(report, per_page=False):
    lines = []
    lines.append("Rule                 severity   errors  warnings")
    for rule, (severity, _desc) in RULES.items():
        e = report.count("error", rule)
        w = report.count("warning", rule)
        lines.append(f"{rule:<20} {severity:<9} {e:>7} {w:>9}")
    lines.append("")
    lines.append("Role      pages audited   errors  warnings")
    for role in ("guest", "student", "admin"):
        lines.append(f"{role:<9} {report.audited(role):>13} {report.count('error', role=role):>8} "
                     f"{report.count('warning', role=role):>9}")
    lines.append(f"CSS (once)            {sum(1 for f in report.css if f.severity == 'error'):>11} errors")
    if per_page:
        lines.append("")
        for page in report.pages:
            e = sum(1 for f in page["findings"] if f.severity == "error")
            w = len(page["findings"]) - e
            lines.append(f"{page['status']} {page['actor']:<6} {page['name']}  errors={e} warnings={w}")
    lines.append("")
    seen = set()
    for f in report.findings:
        key = (f.rule, f.selector, f.message, f.page.split(" as ")[0])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{f.severity.upper():<7} {f.rule:<16} {f.page} | {f.selector} | {f.message}")
    for rule in report.unmatched:
        lines.append(f"ERROR   no-audit-row      the route {rule} has no row in page_plan()")
    total_e, total_w = report.count("error"), report.count("warning")
    lines.append("")
    lines.append(f"TOTAL: {len(report.pages)} page views, {total_e} errors, {total_w} warnings")
    return "\n".join(lines)


def unaudited_roles(report):
    return [r for r in ("guest", "student", "admin") if report.audited(r) == 0]


def exit_code(report):
    """0 only when there are no errors, every route has an audit row and every role was audited."""
    return 1 if report.count("error") or report.unmatched or unaudited_roles(report) else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with tempfile.TemporaryDirectory() as tmp:
        app, ids = build_audit_app(tmp)
        report = run_audit(app, ids)
    print(format_report(report, per_page="--pages" in argv))
    if unaudited_roles(report):
        print("a role was never audited: " + ", ".join(unaudited_roles(report)))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
