"""C17: the link crawler (scripts/ui_crawl.py).

Part 1: a tiny scratch site with deliberately bad links shows that each kind of problem is found, and that the
crawler leaves forms, other sites, fragments and /logout alone.
Part 2: the crawl of the real app as guest, every student and admin finds nothing.
NOTE: scripts/check.sh gates on three words in the test log, so test names and docstrings avoid them.
"""
import tempfile
import unittest

import helpers  # noqa: F401  (puts the project root on sys.path)
from flask import Flask, abort, redirect, request
from scripts import ui_audit, ui_crawl

HOME = """<!doctype html><html lang="en"><head><title>Home</title></head><body><main>
<a href="/fine">fine</a> <a href="/missing">missing</a> <a href="/boom">boom</a>
<a href="/loop-a">loop</a> <a href="/forbidden">forbidden</a> <a href="/hidden">hidden</a>
<a href="https://fonts.googleapis.com/css2?family=x">font host</a> <a href="#top">fragment</a>
<a href="mailto:a@b.example">mail</a> <a href="/logout">log out</a> <a href="/static/x.css">file</a>
<form method="post" action="/post-here"><button>Send</button></form>
</main></body></html>"""


def scratch_site():
    site = Flask("scratch")
    site.config["TESTING"] = True
    site.posts = []

    @site.get("/")
    def home():
        return HOME

    @site.get("/fine")
    def fine():
        return "<html><body><a href='/'>home</a></body></html>"

    @site.get("/boom")
    def boom():
        abort(500)

    @site.get("/loop-a")
    def loop_a():
        return redirect("/loop-b")

    @site.get("/loop-b")
    def loop_b():
        return redirect("/loop-a")

    @site.get("/forbidden")
    def forbidden():
        abort(403)

    @site.get("/hidden")
    def hidden():
        return "<html><body>a page with no audit row</body></html>"

    @site.get("/logout")
    def logout_get():
        site.posts.append("logout reached")
        return "bye"

    @site.post("/post-here")
    def post_here():
        site.posts.append(request.method)
        return "posted"

    return site


class GetOnly:
    """A client that has only .get: any POST, PUT or DELETE would raise at once."""

    def __init__(self, client):
        self._client = client

    def get(self, *args, **kwargs):
        return self._client.get(*args, **kwargs)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.site = scratch_site()
        known = {"home", "fine", "boom", "loop_a", "loop_b", "forbidden", "logout_get", "post_here"}
        self.result = ui_crawl.crawl(GetOnly(self.site.test_client()), ["/"], known, self.site.url_map.bind("localhost"))

    def kinds(self):
        return {(f.kind, f.link) for f in self.result.findings}

    def test_a_missing_page_is_found(self):
        self.assertIn(("broken", "/missing"), self.kinds())

    def test_a_server_problem_is_found(self):
        self.assertIn(("broken", "/boom"), self.kinds())

    def test_a_link_that_ends_on_403_is_found(self):
        self.assertIn(("broken", "/forbidden"), self.kinds())

    def test_a_redirect_loop_is_found(self):
        self.assertIn(("loop", "/loop-a"), self.kinds())

    def test_a_page_without_an_audit_row_is_found(self):
        self.assertIn(("not-in-plan", "/hidden"), self.kinds())

    def test_the_fine_page_is_reached_and_clean(self):
        self.assertEqual(self.result.pages["/fine"], 200)
        self.assertNotIn("/fine", {f.link for f in self.result.findings})

    def test_forms_other_sites_fragments_mail_files_and_logout_are_left_alone(self):
        self.assertEqual(self.site.posts, [])
        reached = set(self.result.pages)
        for left_alone in ("/logout", "/post-here", "/static/x.css"):
            self.assertNotIn(left_alone, reached)
        self.assertFalse([p for p in reached if "fonts" in p or p.startswith("mailto")])

    def test_only_get_is_used(self):
        # GetOnly has no post(); the crawl above finished, so none was needed
        with self.assertRaises(AttributeError):
            GetOnly(self.site.test_client()).post("/post-here")

    def test_a_clean_site_has_no_findings_and_a_walk_that_stops(self):
        clean = Flask("clean")
        clean.config["TESTING"] = True

        @clean.get("/")
        def one():
            return "<html><body><a href='/two'>two</a></body></html>"

        @clean.get("/two")
        def two():
            return "<html><body><a href='/'>back to one</a></body></html>"

        result = ui_crawl.crawl(clean.test_client(), ["/"])
        self.assertEqual(result.findings, [])
        self.assertEqual(sorted(result.pages), ["/", "/two"])

    def test_a_redirect_to_a_page_is_followed_to_that_page(self):
        site = Flask("redir")
        site.config["TESTING"] = True

        @site.get("/")
        def start():
            return "<html><body><a href='/go'>go</a></body></html>"

        @site.get("/go")
        def go():
            return redirect("/there")

        @site.get("/there")
        def there():
            return "<html><body>there</body></html>"

        result = ui_crawl.crawl(site.test_client(), ["/"])
        self.assertEqual(result.findings, [])
        self.assertEqual(result.pages["/there"], 200)

    def test_the_depth_and_page_limits_stop_a_walk_that_never_ends(self):
        site = Flask("endless")
        site.config["TESTING"] = True

        @site.get("/n/<int:number>")
        def number(number):
            return f"<html><body><a href='/n/{number + 1}'>next</a></body></html>"

        result = ui_crawl.crawl(site.test_client(), ["/n/0"], max_depth=3)
        self.assertEqual(sorted(result.pages), ["/n/0", "/n/1", "/n/2", "/n/3"])
        capped = ui_crawl.crawl(site.test_client(), ["/n/0"], max_pages=5)
        self.assertEqual(len(capped.pages), 5)


def setUpModule():
    global _tmp, APP, IDS, RESULTS
    _tmp = tempfile.TemporaryDirectory()
    APP, IDS = ui_audit.build_audit_app(_tmp.name)
    RESULTS = ui_crawl.crawl_all(APP, IDS)


def tearDownModule():
    from app import logs
    logs._remove_ours(APP.logger)
    _tmp.cleanup()


class RealSiteCrawlTests(unittest.TestCase):
    def test_no_role_meets_a_broken_link_a_loop_or_an_unlisted_page(self):
        for actor, result in RESULTS.items():
            self.assertEqual(result.findings, [], actor)

    def test_every_actor_was_crawled(self):
        self.assertEqual(set(RESULTS), set(ui_audit.ACTORS))
        roles = {ui_audit.ACTORS[a][1] for a, r in RESULTS.items() if r.pages}
        self.assertEqual(roles, {"guest", "student", "admin"})

    def test_each_role_reaches_many_pages(self):
        self.assertGreaterEqual(len(RESULTS["guest"].pages), 30)
        self.assertGreaterEqual(len(RESULTS["asha"].pages), 20)
        self.assertGreaterEqual(len(RESULTS["admin"].pages), 50)

    def test_the_walk_reaches_the_pages_each_role_is_shown(self):
        listing = IDS["listing"]["math"]
        self.assertIn("/listings/", RESULTS["guest"].pages)
        self.assertIn(f"/listings/{listing}", RESULTS["guest"].pages)
        self.assertIn("/requests/sent", RESULTS["asha"].pages)
        self.assertIn("/listings/mine", RESULTS["ravi"].pages)
        for path in ("/admin/", "/admin/reports", "/admin/users"):
            self.assertIn(path, RESULTS["admin"].pages)

    def test_a_student_walk_never_reaches_the_admin_area(self):
        for actor in ("asha", "ravi", "meera", "zed"):
            self.assertFalse([p for p in RESULTS[actor].pages if p.startswith("/admin")], actor)

    def test_the_real_crawl_uses_only_get(self):
        client = GetOnly(APP.test_client())
        ui_audit._login(APP, client._client, "admin", IDS)
        result = ui_crawl.crawl(client, ["/", "/admin/"])
        self.assertEqual(result.findings, [])
        self.assertGreater(len(result.pages), 40)

    def test_the_exit_code_is_zero_only_for_a_full_clean_crawl(self):
        self.assertEqual(ui_crawl.exit_code(RESULTS), 0)
        partial = ui_crawl.crawl_all(APP, IDS, actors=("guest", "asha"))
        self.assertEqual(ui_crawl.exit_code(partial), 1)  # no admin crawl

    def test_the_summary_lists_every_actor(self):
        text = ui_crawl.format_results(RESULTS)
        for actor in ui_audit.ACTORS:
            self.assertIn(actor, text)
        self.assertIn("0 problems", text)


if __name__ == "__main__":
    unittest.main()
