# UI audit checklist (accessibility, chunk C16)

Our own checklist. Part A is checked by a program on every page, for every role, on every test run.
Part A2 (C17) adds the form and structure rules. The link crawler (`scripts/ui_crawl.py`) and the query budget are described under Part C.
Part B needs a person in a real browser.

Run it: `python3 scripts/ui_audit.py` (add `--pages` for one line per page and role).
It builds a scratch database, signs in as guest, student and admin, visits every page, and exits with 1 on any error.
The same walk runs inside the test suite (`tests/test_ui_audit.py`).

## A. Checked by `scripts/ui_audit.py`

Severity: **error** breaks the gate (must be 0 on every page). **warning** is listed but does not fail.

| Rule | Severity | What is checked | Why | How to fix |
|------|----------|-----------------|-----|------------|
| `lang` | error | `<html lang="...">` is set | screen readers pick the voice and pronunciation from it | keep `lang="en"` in `base.html` |
| `title` | error | non-empty `<title>` | it is the first thing a screen reader reads and the name of the browser tab | fill `{% block title %}` |
| `viewport-zoom` | error | no `user-scalable=no`, no `maximum-scale` below 2 | people must be able to zoom | remove the restriction |
| `h1-count` | error | exactly one `h1` | the page's one title | `page_header()` or a level-1 `title_block` only |
| `heading-order` | error | first heading is h1; going down never skips a level | headings are a table of contents | change the `level` argument of the macro |
| `heading-empty` | error | no heading without text | an empty heading is announced as nothing | remove it or give it text |
| `label` | error | every input, select, textarea has a `<label for>`, a wrapping label, `aria-label` or `aria-labelledby` with text (hidden and submit inputs exempt; a placeholder is not a label) | people using screen readers or voice control need the name | use the `field()`, `select()`, `textarea()`, `checkbox()` macros |
| `alt` | error | every `<img>` has `alt` (empty `alt=""` = decoration); image buttons have alt | images need a text version | add `alt` |
| `button-name` | error | every `<button>` and `input type=button` has text or `aria-label` | a button must say what it does | give the macro a visible label |
| `link-name` | error | every link with an `href` has text, image alt or `aria-label` | same | same |
| `link-text` | warning | link text is not just "click here", "here", "more" | links are read out of context in a links list | say where the link goes |
| `landmark-main` | error | exactly one `<main>` | lets people jump to the content | only `base.html` draws it |
| `landmark-banner` | error | exactly one site `<header>` (a header inside main/article/section does not count) | page-level region | only `navbar()` draws it |
| `landmark-footer` | error | exactly one site `<footer>` | page-level region | only `base.html` draws it |
| `landmark-nav` | warning | at least one `<nav>` | navigation region | use `navbar()` |
| `nav-label` | warning | when there are several `<nav>`, each has a different `aria-label` | they are listed by name | label each (`Main`, `Pagination`) |
| `duplicate-id` | error | no id appears twice on a page | labels and aria links would point to the wrong element | ids come from the macros (`f-NAME`, `h-NAME`, `p-NAME`, dialog ids): keep names unique per page |
| `aria-ref` | error | `aria-labelledby`, `aria-describedby`, `aria-controls` point to ids that exist | a broken pointer silently drops the name or hint | fix the id |
| `fragment-target` | error | a link to `#something` points to an id that exists (this is the skip link check) | a skip link to nowhere does nothing | keep `id="main"` |
| `table-caption` | error | every table has a non-empty `<caption>` | names the table | `table()` already requires it |
| `table-header` | warning | every table has `th` cells and each has a `scope` | cells are announced with their header | use the `table()` macro |
| `dialog-name` | error | every `<dialog>` has `aria-label` or `aria-labelledby` (its own text does not count) | a dialog must be announced by name | use the `dialog()` macro |
| `tabindex` | error | no `tabindex` above 0 (`0` and `-1` are fine) | a positive value breaks the natural focus order | remove it |
| `focus-visible` | error | the CSS has a `:focus-visible` rule with an outline or shadow | keyboard users must see where they are | `base.css` line with `:focus-visible` |
| `outline-removed` | error | no rule removes an outline on `:focus` (unless it is `:focus:not(:focus-visible)`) | an invisible focus is the most common failure | use the focus token instead |
| `reduced-motion` | error | `@media (prefers-reduced-motion: reduce)` exists and, when the CSS animates or transitions, switches off both | motion can make people ill | `base.css` bottom block |

The two CSS rules are checked once (on the files), not per page.

### A2. Form and structure rules (C17)

Same severity words as above. All of these are checked on every page and role, in the same walk.

| Rule | Severity | What is checked | Why | How to fix |
|------|----------|-----------------|-----|------------|
| `title-text` | error | the `<title>` holds plain text, no tag inside it | a script or tag inside the title shows up as text in the browser tab and is never run | keep `{% block title %}` to words; scripts belong in `base.html` |
| `autocomplete` | error | email fields carry `autocomplete="email"` (or `username`), phone fields `tel`, a full name `name`; a password field is `current-password` when it is alone (login) and `new-password` when the form has a second password field (signup) | browsers and password managers fill the right thing, and never offer an old password for a new one | pass `autocomplete=` to `field()` |
| `input-type` | error | a field named `*email*` has `type=email`, `*phone*` has `tel`, `*password*` has `password`, and the search box (`q`, `*search*`) has `search` | the right keyboard, the right autofill, and screen readers announce the kind of field | pass `type=` to `field()` |
| `submit-once` | error | a page with a POST form loads `submit-once.js` (it switches the buttons off after a send, so a second click cannot send twice) | double posting; the server stays the real guard | the script is loaded once in `base.html`; do not remove it |
| `problem-summary` | error | a form with more than 5 controls that shows field problems has a `problem_summary()` box (`role="alert"`) above it with a link to every field that has a problem | on a long form the one broken field may be off screen; the box tells you and the links jump there | call `problem_summary(problems, fields)` above the form |
| `html-balance` | error | every opened tag is closed in order; no closing tag without an opening one | browsers repair broken markup in different ways | close the tag |
| `html-nesting` | error | no block element inside `<p>`, no link inside a link, no button inside a link or button, no form inside a form | the browser quietly moves the content, so the page differs from the template | restructure the template |

**Not the W3C validator.** `html-balance` and `html-nesting` use Python's `html.parser` and know only the checks above. They do not check
attribute names or values, allowed parents (a `li` outside a list), or the content model of every element. Run the real validator
(https://validator.w3.org/nu/) on the pages you care about before a hand-in.

### What the audit visits
Every route that accepts GET has a row in `page_plan()`; a test fails if a new route has none (like the C13 admin matrix).
The plan covers guest, three students (the owner of an item, the requester, an uninvolved one, one with nothing at all) and the admin,
the empty and filtered browse pages, the second browse page, the confirm pages, the admin lists with each filter,
`/design` (development only) and the pages 400, 403, 404, 405, 429 and 500.
`POST` pages that show something are in `post_plan()`: a refused login, a refused signup, a refused profile, a refused listing form (create and edit),
a refused report, a locked login, and the messages shown after a redirect (already requested, logged out).
Not covered: `/health` (JSON) and static files. Redirect responses (303) have no body and are not audited; the page they lead to is.

## B. Needs a person in Chrome (the program cannot judge these)

1. **Colour contrast as seen.** The tokens are tested for contrast (`tests/test_design_tokens.py`); look for text that is hard to read on the real page.
2. **Focus order.** Press Tab through each page: the order follows the reading order, nothing is skipped, nothing is trapped; in a dialog, Tab stays inside and Esc closes it.
3. **Focus ring visible everywhere**, including on the skip link ("Skip to main content" appears with the first Tab and Enter jumps to the sheet).
4. **Screen reader wording.** With a screen reader (NVDA, VoiceOver) or Chrome's accessibility tree (DevTools > Elements > Accessibility): button and link names make sense out of context ("Remove listing" is clear, "Remove" alone is not); stamps are read with their text.
5. **Zoom to 200%.** Nothing is cut off or overlaps (the layout is desktop-only, but zoom must work).
6. **Reduced motion.** Turn on "reduce motion" in the operating system: the skeleton bars and dialog opening stop animating.
7. **No JavaScript.** Delete and decline links still lead to a confirm page.

## C. Other gates that run in the test suite (C17)

- **Link crawler** (`scripts/ui_crawl.py`, `tests/test_ui_crawl.py`): follows every internal `<a href>` as guest, the four student actors and admin. GET only: forms, `/logout`, `/static`, `/design`, `/_audit` and other sites are skipped. A link whose final answer is not 200, a redirect loop, or a page that has no row in `page_plan` is a problem. Run: `python3 scripts/ui_crawl.py [--pages]`.
- **Query budget** (`tests/test_query_budget.py`): every page in `page_plan` is requested as every actor listed for it; the SQL statements (SELECT, INSERT, UPDATE, DELETE, WITH; not PRAGMA/BEGIN/COMMIT) are counted through a trace callback on `app.db.connect`. The highest count per endpoint must be at or under its entry in the `CEILINGS` table: measured max + 1 for pages needing 3 or fewer, + 2 above. A new endpoint in `page_plan` needs a ceiling (a test says so); never raise a ceiling to hide a repeated query, fix the page.
- **N+1 guard** (same file): browse (search `Growth`), requests received, my listings, admin reports and admin users are measured, many rows are added with plain sqlite, and the count must be the same.
- The HTML checks (rules `html-balance`, `html-nesting`, `title-text`) are cheap structure checks, not the W3C validator.

## How to add a rule
Add it to `RULES` in `scripts/ui_audit.py`, put the check in `audit_html` (or `audit_css`), add a bad snippet that fires it and a good
snippet that stays quiet to `tests/test_ui_audit.py` (C16 rules) or `tests/test_ui_audit_forms.py` (C17 rules), add a row to the table above. If it makes an existing page fail, fix the page in the
same change (rule by rule, one category at a time) and report the before and after counts.
