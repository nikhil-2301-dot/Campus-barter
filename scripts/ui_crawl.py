#!/usr/bin/env python3
"""Link crawler for Campus Barter (chunk C17).

    python3 scripts/ui_crawl.py            # builds a scratch app, crawls as every role, prints a summary
    python3 scripts/ui_crawl.py --pages    # also lists every page each role reached

Starting from "/" and from each role's own start page, it follows every internal <a href> with GET and nothing
else: it never submits a form, never calls /logout. It reports:
  broken      a link ends on 404, a server problem (5xx), or any other answer that is not a normal page
              (a student who is shown a link must be able to open it)
  loop        a chain of redirects that comes back to a page it already passed through, or is too long
  not-in-plan a link leads to a route that has no row in scripts/ui_audit.py page_plan()
Links to other sites (the two font hosts), mailto:, tel:, javascript: and #fragments are ignored; the audit
already checks that #fragments point at an id on the same page.

Everything else is reused from scripts/ui_audit.py (build_audit_app, _login, page_plan, covered_endpoints):
there is no second page list.
"""
import os
import sys
import tempfile
from collections import deque, namedtuple
from urllib.parse import urljoin, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bs4 import BeautifulSoup  # noqa: E402

from scripts import ui_audit  # noqa: E402

Finding = namedtuple("Finding", "kind found_on link detail")
CrawlResult = namedtuple("CrawlResult", "pages findings links_followed")

# Never followed. /logout is POST-only (a GET answers 405 by design); /static is files; /_audit is the audit's
# own test-only routes; /design is the development style guide, not part of the product.
SKIP_PREFIXES = ("/logout", "/static/", "/_audit/", "/design")
ROLE_START = {"guest": [], "student": ["/listings/mine"], "admin": ["/admin/"]}
MAX_REDIRECTS = 5
MAX_DEPTH = 8
MAX_PAGES = 400
OK = (200,)


def _internal_path(href, current):
    """Turn an href into 'path?query' on this site, or None when it must be ignored."""
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    joined = urljoin(current, href)
    parts = urlsplit(joined)
    if parts.scheme not in ("", "http", "https") or (parts.netloc and parts.netloc != "localhost"):
        return None  # another site (the font hosts)
    path = parts.path or "/"
    if any(path == p.rstrip("/") or path.startswith(p) for p in SKIP_PREFIXES):
        return None
    return path + ("?" + parts.query if parts.query else "")


def _links(html, current):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        target = _internal_path(a["href"], current)
        if target is not None:
            out.append(target)
    return out


def _fetch(client, path):
    """GET `path`, following redirects by hand. Returns (final_path, response, problem) with problem None, 'loop'."""
    seen = [path]
    response = client.get(path)
    while response.status_code in (301, 302, 303, 307, 308):
        target = _internal_path(response.headers.get("Location", ""), seen[-1])
        if target is None:
            return seen[-1], response, None  # redirected away from the site: nothing more to follow
        if target in seen or len(seen) > MAX_REDIRECTS:
            return seen[-1], response, "loop"
        seen.append(target)
        response = client.get(target)
    return seen[-1], response, None


def crawl(client, starts, known_endpoints=None, adapter=None, max_depth=MAX_DEPTH, max_pages=MAX_PAGES):
    """Walk the site with `client` (only .get is ever called). Returns CrawlResult(pages, findings, links_followed).

    `pages` maps each page reached (path, after redirects) to its status. `known_endpoints` and `adapter` (from
    app.url_map.bind) are optional: with them, a link to a route without an audit row is reported.
    """
    findings, pages = [], {}
    queue = deque((start, 0, "(start)") for start in starts)
    queued = set(starts)
    followed = 0
    while queue and len(pages) < max_pages:
        path, depth, found_on = queue.popleft()
        followed += 1
        final, response, problem = _fetch(client, path)
        if problem == "loop":
            findings.append(Finding("loop", found_on, path, "the redirects come back to a page already passed"))
            continue
        pages[final] = response.status_code
        if response.status_code not in OK:
            findings.append(Finding("broken", found_on, path, f"answers {response.status_code}"))
            continue
        if known_endpoints is not None and adapter is not None:
            try:
                endpoint = adapter.match(final.split("?")[0], method="GET")[0]
            except Exception:
                endpoint = None
            if endpoint not in known_endpoints:
                findings.append(Finding("not-in-plan", found_on, final, "no row in page_plan() reaches this route"))
        if depth >= max_depth or "text/html" not in (response.content_type or ""):
            continue
        for link in _links(response.get_data(as_text=True), final):
            if link not in queued:
                queued.add(link)
                queue.append((link, depth + 1, final))
    return CrawlResult(pages, findings, followed)


def crawl_all(app, ids, actors=None):
    """Crawl once per actor (guest, the students, admin). Returns {actor: CrawlResult}."""
    plan = ui_audit.page_plan(ids)
    known = ui_audit.covered_endpoints(app, plan)
    adapter = app.url_map.bind("localhost")
    results = {}
    for actor in actors or ui_audit.ACTORS:
        client = app.test_client()
        ui_audit._login(app, client, actor, ids)
        starts = ["/"] + ROLE_START[ui_audit.ACTORS[actor][1]]
        results[actor] = crawl(client, starts, known, adapter)
    return results


def format_results(results, per_page=False):
    lines = ["Actor   role      pages  links followed  problems"]
    for actor, result in results.items():
        role = ui_audit.ACTORS[actor][1]
        lines.append(f"{actor:<7} {role:<9} {len(result.pages):>5} {result.links_followed:>15} {len(result.findings):>9}")
    if per_page:
        for actor, result in results.items():
            lines.append("")
            lines.append(f"-- {actor}")
            for path, status in sorted(result.pages.items()):
                lines.append(f"  {status} {path}")
    lines.append("")
    seen = set()
    for actor, result in results.items():
        for f in result.findings:
            key = (f.kind, f.found_on, f.link)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{f.kind.upper():<12} {actor}: on {f.found_on} the link {f.link} {f.detail}")
    total = sum(len(r.findings) for r in results.values())
    lines.append(f"TOTAL: {sum(len(r.pages) for r in results.values())} page views, {total} problems")
    return "\n".join(lines)


def exit_code(results):
    """0 only when every role was crawled, reached pages, and nothing was found."""
    roles = {ui_audit.ACTORS[a][1] for a, r in results.items() if r.pages}
    if roles != {"guest", "student", "admin"}:
        return 1
    return 1 if any(r.findings for r in results.values()) else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with tempfile.TemporaryDirectory() as tmp:
        app, ids = ui_audit.build_audit_app(tmp)
        results = crawl_all(app, ids)
        from app import logs
        logs._remove_ours(app.logger)
    print(format_results(results, per_page="--pages" in argv))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
