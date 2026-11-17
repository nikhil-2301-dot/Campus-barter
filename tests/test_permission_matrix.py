"""C20: permission tests that are generated from tables, not written one case at a time.

Part 1: `can()` for every action x user kind x resource state, checked against `expected()`,
        a plain restatement of the rules in app/permissions.py.
Part 2: every route (taken from `app.url_map`) as guest, owner, asker, bystander, suspended student
        and admin: the HTTP status AND whether the database (or the photo file) changed.
Part 3: private details (phone, address, social link, email) never reach another user's page
        before a request is accepted, and reach only the two parties afterwards.
A route with no row in ROUTES makes a test fail (add a row, do not add an exemption lightly).
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)
"""
import itertools
import os
import unittest

import helpers
from app import accounts
from app.db import connect
from app.permissions import can

DOMAIN = "college.example"
PW = "Correct-Horse-9"
KEY = "0123456789abcdef0123456789abcdef.jpg"


# --------------------------------------------------------------------------- part 1: can()
def _u(uid, admin=0, suspended=0):
    return {"id": uid, "is_admin": admin, "is_suspended": suspended}


USERS = {                                   # owner = id 1, requester = id 2
    "anonymous": None, "owner": _u(1), "owner_suspended": _u(1, suspended=1),
    "requester": _u(2), "requester_suspended": _u(2, suspended=1), "bystander": _u(3),
    "admin": _u(4, admin=1), "admin_suspended": _u(4, admin=1, suspended=1),
}
LISTINGS = [{"owner_id": 1, "status": s} for s in ("available", "reserved", "closed", "removed")]
REQUESTS = [{"owner_id": 1, "requester_id": 2, "status": s}
            for s in ("pending", "accepted", "completed", "declined", "cancelled")]
REPORTS = [{"status": s} for s in ("open", "resolved", "dismissed")]
TARGETS = [{"id": 3, "is_admin": 0, "is_suspended": 0}, {"id": 3, "is_admin": 0, "is_suspended": 1},
           {"id": 9, "is_admin": 1, "is_suspended": 0}, {"id": 4, "is_admin": 1, "is_suspended": 0}]
ACTIONS = {
    "create_listing": [None], "restore_listing": LISTINGS,
    "edit_listing": LISTINGS, "delete_listing": LISTINGS,
    "request_listing": LISTINGS, "report_listing": LISTINGS,
    "accept_request": REQUESTS, "decline_request": REQUESTS, "cancel_request": REQUESTS,
    "complete_request": REQUESTS, "view_request": REQUESTS, "view_contact": REQUESTS,
    "view_admin": [None], "resolve_report": REPORTS,
    "suspend_user": TARGETS, "unsuspend_user": TARGETS,
}


def expected(action, user, res):
    """The documented rule for one case, written out directly (not copied from can())."""
    if user is None:
        return False
    live = not user["is_suspended"]
    admin = bool(user["is_admin"])
    if action == "create_listing":
        return live
    if action in ("edit_listing", "delete_listing"):
        return admin or (res["owner_id"] == user["id"] and res["status"] != "removed")
    if action == "restore_listing":
        return admin
    if action == "request_listing":
        return live and not admin and res["status"] == "available" and res["owner_id"] != user["id"]
    if action == "report_listing":
        return live and not admin and res["owner_id"] != user["id"] and res["status"] != "removed"
    if action in ("view_admin", "resolve_report", "suspend_user", "unsuspend_user"):
        if not (admin and live):
            return False
        if action == "resolve_report":
            return res["status"] == "open"
        if action == "suspend_user":
            return res["id"] != user["id"] and not res["is_admin"] and not res["is_suspended"]
        if action == "unsuspend_user":
            return bool(res["is_suspended"])
        return True
    side = "owner" if res["owner_id"] == user["id"] else "requester" if res["requester_id"] == user["id"] else None
    status = res["status"]
    if action == "view_request":
        return side is not None
    if action == "view_contact":
        return side is not None and status in ("accepted", "completed")
    if side is None or not live:
        return False
    if action in ("accept_request", "decline_request"):
        return side == "owner" and status == "pending"
    if action == "cancel_request":
        return (side == "owner" and status == "accepted") or (side == "requester" and status in ("pending", "accepted"))
    if action == "complete_request":
        return status == "accepted"
    raise LookupError(f"no rule written for {action}")


class CanTableTests(unittest.TestCase):
    def test_every_action_for_every_user_kind_and_resource_state(self):
        count = 0
        for action, resources in ACTIONS.items():
            for (kind, user), res in itertools.product(USERS.items(), resources):
                with self.subTest(action=action, user=kind, resource=res):
                    self.assertEqual(bool(can(action, res, user)), expected(action, user, res))
                    count += 1
        self.assertGreater(count, 500)      # the table is not empty by accident

    def test_every_action_in_the_module_text_has_a_row(self):
        """A new can() action without a row in ACTIONS makes this test fail."""
        import inspect
        import re
        from app import permissions
        source = inspect.getsource(permissions.can)
        named = set(re.findall(r'action == "(\w+)"', source)) | set(re.findall(r'"(\w+_listing)"', source))
        named |= {f"{v}_request" for v in permissions.REQUEST_ACTION_ROLES} | {"view_request", "view_contact"}
        named |= {"view_admin", "resolve_report", "suspend_user", "unsuspend_user", "report_listing"}
        self.assertEqual(named - set(ACTIONS), set())

    def test_an_unknown_action_is_denied_for_everyone(self):
        for kind, user in USERS.items():
            with self.subTest(user=kind):
                self.assertFalse(can("no_such_action", REQUESTS[0], user))


# --------------------------------------------------------------------------- part 2: routes
ROLES = ("guest", "owner", "asker", "bystander", "suspended", "admin")
PUBLIC = (200,) * 6
STUDENTS = (303, 200, 200, 200, 303, 200)       # login needed; a suspended student is sent to log in
OWNER_ONLY = (303, 200, 403, 403, 303, 200)     # owner or admin
OWNER_POST = (303, 303, 403, 403, 303, 303)
ADMIN_GET = (303, 403, 403, 403, 303, 200)
ADMIN_POST = (303, 403, 403, 403, 303, 303)
EVERYONE = set(ROLES)

LISTING_FORM = {"title": "Changed title", "category": "Books", "condition": "good", "mode": "give",
                "description": "d"}

# label -> (endpoint, method, url, form data, request status before, statuses by role, roles that change the data)
ROUTES = [
    ("home", "main.index", "GET", "/", {}, "pending", (200, 303, 303, 303, 200, 303), set()),
    ("health", "health.health", "GET", "/health", {}, "pending", PUBLIC, set()),
    ("login page", "auth.login", "GET", "/login", {}, "pending", (200, 303, 303, 303, 200, 303), set()),
    ("login post", "auth.login", "POST", "/login", {}, "pending", (401, 303, 303, 303, 401, 303), set()),
    ("signup page", "auth.signup", "GET", "/signup", {}, "pending", (200, 303, 303, 303, 200, 303), set()),
    ("signup post", "auth.signup", "POST", "/signup", {}, "pending", (400, 303, 303, 303, 400, 303), set()),
    ("logout", "auth.logout", "POST", "/logout", {}, "pending", (303, 303, 303, 303, 303, 303), set()),
    ("profile page", "auth.profile", "GET", "/profile", {}, "pending", STUDENTS, set()),
    ("profile post", "auth.profile", "POST", "/profile", {}, "pending", (303, 400, 400, 400, 303, 400), set()),
    ("browse", "listings.browse", "GET", "/listings/", {}, "pending", PUBLIC, set()),
    ("listing page", "listings.view", "GET", "/listings/{L}", {}, "pending", PUBLIC, set()),
    ("new page", "listings.create", "GET", "/listings/new", {}, "pending", STUDENTS, set()),
    ("new post", "listings.create", "POST", "/listings/new", {}, "pending", (303, 400, 400, 400, 303, 400), set()),
    ("my listings", "listings.my_listings", "GET", "/listings/mine", {}, "pending", STUDENTS, set()),
    ("edit page", "listings.edit", "GET", "/listings/{L}/edit", {}, "pending", OWNER_ONLY, set()),
    ("edit post", "listings.edit", "POST", "/listings/{L}/edit", LISTING_FORM, "pending", OWNER_POST,
     {"owner", "admin"}),
    ("delete page", "listings.delete", "GET", "/listings/{L}/delete", {}, "pending", OWNER_ONLY, set()),
    ("delete post", "listings.delete", "POST", "/listings/{L}/delete", {}, "pending", OWNER_POST,
     {"owner", "admin"}),
    ("remove photo", "listings.remove_photo", "POST", "/listings/{L}/photo/remove", {}, "pending", OWNER_POST,
     {"owner", "admin"}),
    ("report", "reports.send", "POST", "/listings/{L}/report", {"reason": "It does not match the photo."},
     "pending", (303, 403, 303, 303, 303, 403), {"asker", "bystander"}),
    ("send request", "exchange.send", "POST", "/listings/{L}/request", {"message": "please"}, "pending",
     (303, 303, 303, 303, 303, 303), {"bystander"}),
    ("requests received", "exchange.received", "GET", "/requests/received", {}, "pending", STUDENTS, set()),
    ("requests sent", "exchange.sent", "GET", "/requests/sent", {}, "pending", STUDENTS, set()),
    ("request page", "exchange.detail", "GET", "/requests/{R}", {}, "pending", (303, 200, 200, 404, 303, 404), set()),
    ("decline page", "exchange.change", "GET", "/requests/{R}/decline", {}, "pending",
     (303, 200, 403, 404, 303, 404), set()),
    ("cancel page", "exchange.change", "GET", "/requests/{R}/cancel", {}, "pending",
     (303, 403, 200, 404, 303, 404), set()),
    ("accept", "exchange.change", "POST", "/requests/{R}/accept", {}, "pending",
     (303, 303, 403, 404, 303, 404), {"owner"}),
    ("decline", "exchange.change", "POST", "/requests/{R}/decline", {}, "pending",
     (303, 303, 403, 404, 303, 404), {"owner"}),
    ("cancel", "exchange.change", "POST", "/requests/{R}/cancel", {}, "pending",
     (303, 303, 303, 404, 303, 404), {"asker"}),
    ("complete", "exchange.change", "POST", "/requests/{R}/complete", {}, "accepted",
     (303, 303, 303, 404, 303, 404), {"owner", "asker"}),
    ("photo", "photos.serve", "GET", "/photos/" + KEY, {}, "pending", PUBLIC, set()),
    ("campus page", "campuses.new", "GET", "/campuses/new", {}, "pending", PUBLIC, set()),
    ("campus post", "campuses.new", "POST", "/campuses/new",
     {"college_name": "Delhi Test Institute", "area": "Dwarka", "kind": "engineering"}, "pending",
     (303, 303, 303, 303, 303, 303), EVERYONE),
    ("admin home", "admin.dashboard", "GET", "/admin/", {}, "pending", ADMIN_GET, set()),
    ("admin reports", "admin.report_list", "GET", "/admin/reports", {}, "pending", ADMIN_GET, set()),
    ("admin report", "admin.report_detail", "GET", "/admin/reports/{P}", {}, "pending", ADMIN_GET, set()),
    ("admin resolve", "admin.report_resolve", "POST", "/admin/reports/{P}/resolve", {}, "pending", ADMIN_POST,
     {"admin"}),
    ("admin dismiss", "admin.report_dismiss", "POST", "/admin/reports/{P}/dismiss", {}, "pending", ADMIN_POST,
     {"admin"}),
    ("admin remove page", "admin.listing_remove", "GET", "/admin/listings/{L}/remove", {}, "pending", ADMIN_GET,
     set()),
    ("admin remove", "admin.listing_remove", "POST", "/admin/listings/{L}/remove", {}, "pending", ADMIN_POST,
     {"admin"}),
    ("admin campuses", "admin.campus_list", "GET", "/admin/campuses", {}, "pending", ADMIN_GET, set()),
    ("admin users", "admin.user_list", "GET", "/admin/users", {}, "pending", ADMIN_GET, set()),
    ("admin suspend page", "admin.user_suspend", "GET", "/admin/users/{B}/suspend", {}, "pending", ADMIN_GET,
     set()),
    ("admin suspend", "admin.user_suspend", "POST", "/admin/users/{B}/suspend", {}, "pending", ADMIN_POST,
     {"admin"}),
    ("admin unsuspend", "admin.user_unsuspend", "POST", "/admin/users/{S}/unsuspend", {}, "pending", ADMIN_POST,
     {"admin"}),
]
# Routes that need no row, each with the reason.
EXEMPT = {("static", "GET"): "framework route for CSS/JS files, serves no user data",
          ("design.index", "GET"): "developer gallery, not registered in production",
          ("design.index", "POST"): "developer gallery, not registered in production"}

SECRETS = {   # name -> (phone, address, social link): one set per student, all different
    "owner": ("9111111111", "12 Owner Street Pune", "u/ownerreddit"),
    "asker": ("9222222222", "34 Asker Lane Mumbai", "@askergram"),
    "bystander": ("9333333333", "56 Bystander Road Nashik", "@bystandergram"),
    "suspended": ("9444444444", "78 Frozen Close Thane", "@frozengram"),
    "admin": ("9555555555", "90 Admin Avenue Delhi", "@admingram"),
}


class MatrixWorld(helpers.TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.photo_dir = os.path.join(self.tmp, "photos")
        os.makedirs(self.photo_dir)
        self.app = helpers.make_app(self.tmp, PHOTO_DIR=self.photo_dir)
        self.conn = connect(self.app.config["DATABASE_PATH"])
        self.addCleanup(self.conn.close)
        self.ids = {}
        for name, (phone, address, social) in SECRETS.items():
            self.ids[name] = accounts.create_user(
                self.conn, email=f"{name}@{DOMAIN}", password=PW, full_name=name.title(), branch="IT", year="SE",
                phone=phone, address=address, social_link=social, college_name=f"{name.title()} College",
                enrollment_no=f"EN-{name}")
        self.conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (self.ids["admin"],))
        helpers.add_listing(self.conn, self.ids["owner"], "Mini drafter", mode="give")
        self.conn.commit()
        self.fill = {"L": 1, "R": 1, "P": 1, "B": self.ids["bystander"], "S": self.ids["suspended"]}

    def reset(self, request_status):
        c = self.conn
        for table in ("reports", "exchange_requests", "campus_requests", "login_attempts"):
            c.execute(f"DELETE FROM {table}")          # fixed table names, no user input
        c.execute("DELETE FROM listings WHERE id <> 1")
        c.execute("UPDATE listings SET status = ?, deleted_at = NULL, photo_key = ?, title = 'Mini drafter' "
                  "WHERE id = 1", ("reserved" if request_status == "accepted" else "available", KEY))
        c.execute("UPDATE users SET is_suspended = 0")
        c.execute("UPDATE users SET is_suspended = 1 WHERE id = ?", (self.ids["suspended"],))
        c.execute("INSERT INTO exchange_requests (id, listing_id, requester_id, status) VALUES (1, 1, ?, ?)",
                  (self.ids["asker"], request_status))
        c.execute("INSERT INTO reports (id, listing_id, reporter_id, reason) VALUES (1, 1, ?, 'Old report text.')",
                  (self.ids["suspended"],))
        c.commit()
        with open(os.path.join(self.photo_dir, KEY), "wb") as handle:
            handle.write(b"\xff\xd8\xff\xe0 photo bytes")

    def snapshot(self):
        c = self.conn
        rows = (c.execute("SELECT id, status, deleted_at, photo_key, title FROM listings").fetchall(),
                c.execute("SELECT id, status FROM exchange_requests").fetchall(),
                c.execute("SELECT id, status FROM reports").fetchall(),
                c.execute("SELECT id, is_suspended FROM users").fetchall(),
                c.execute("SELECT count(*) FROM campus_requests").fetchall(),
                os.path.isfile(os.path.join(self.photo_dir, KEY)))
        return [[tuple(r) for r in part] if isinstance(part, list) else part for part in rows]

    def client_as(self, role):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "matrix-token"
            if role != "guest":
                session["user_id"] = self.ids[role]
        return client

    def call(self, role, method, url, data):
        client = self.client_as(role)
        if method == "GET":
            return client.get(url, buffered=True)      # buffered: a served photo file is closed at once
        return client.post(url, data={**data, "csrf_token": "matrix-token"})


class RouteRoleMatrixTests(MatrixWorld):
    def test_every_route_in_the_app_has_a_row(self):
        """A new route without a row in ROUTES (or a reason in EXEMPT) makes this test fail."""
        found = {(rule.endpoint, method) for rule in self.app.url_map.iter_rules()
                 for method in rule.methods - {"HEAD", "OPTIONS"}}
        rows = {(endpoint, method) for _l, endpoint, method, *_ in ROUTES}
        self.assertEqual(found - rows - set(EXEMPT), set())
        self.assertEqual(rows - found, set())

    def test_every_row_has_one_answer_per_role(self):
        for label, _e, _m, _u, _d, _s, statuses, changers in ROUTES:
            with self.subTest(route=label):
                self.assertEqual(len(statuses), len(ROLES))
                self.assertLessEqual(changers, set(ROLES))

    def test_each_role_gets_the_documented_status_and_changes_data_only_when_allowed(self):
        for label, _endpoint, method, url, data, before, statuses, changers in ROUTES:
            for role, status in zip(ROLES, statuses):
                with self.subTest(route=label, role=role):
                    self.reset(before)
                    snap = self.snapshot()
                    response = self.call(role, method, url.format(**self.fill), data)
                    self.assertEqual(response.status_code, status)
                    changed = self.snapshot() != snap
                    self.assertEqual(changed, role in changers,
                                     f"{role} {method} {url}: data changed={changed}")

    def test_a_post_without_the_form_token_changes_nothing_for_every_role(self):
        for label, _endpoint, method, url, data, before, _s, _c in ROUTES:
            if method != "POST" or label in ("login post", "signup post"):
                continue
            for role in ROLES:
                with self.subTest(route=label, role=role):
                    self.reset(before)
                    snap = self.snapshot()
                    client = self.client_as(role)
                    response = client.post(url.format(**self.fill), data=data)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.snapshot(), snap)


# --------------------------------------------------------------------------- part 3: privacy
class PrivacyBeforeAcceptTests(MatrixWorld):
    def pages_for(self, role):
        """Yield (label, html) for every GET page this role can open."""
        for label, _e, method, url, data, _b, _s, _c in ROUTES:
            if method == "GET":
                response = self.call(role, "GET", url.format(**self.fill), data)
                if response.status_code == 200 and response.mimetype == "text/html":
                    yield label, response.get_data(as_text=True)

    def foreign_secrets(self, role, parties=()):
        """Phones, addresses and social links of everybody except the role itself and its exchange partner."""
        hidden = []
        for name, values in SECRETS.items():
            if name == role or name in parties:
                continue
            hidden.extend(values)
        return hidden

    def test_no_page_shows_anyone_elses_contact_details_while_the_request_is_not_accepted(self):
        for status in ("pending", "declined", "cancelled"):
            self.reset(status)
            for role in ROLES:
                partner = {"owner": ("asker",), "asker": ("owner",)}.get(role, ())
                partner_values = [v for name in partner for v in SECRETS[name]]
                forbidden = self.foreign_secrets(role) + partner_values
                for label, html in self.pages_for(role):
                    for value in forbidden:
                        with self.subTest(status=status, role=role, page=label, value=value):
                            self.assertNotIn(value, html)

    def test_no_student_page_shows_another_students_email_before_accept(self):
        self.reset("pending")
        for role in ("guest", "owner", "asker", "bystander"):
            others = [f"{name}@{DOMAIN}" for name in SECRETS if name != role]
            for label, html in self.pages_for(role):
                for address in others:
                    with self.subTest(role=role, page=label, address=address):
                        self.assertNotIn(address, html)

    def test_after_accept_only_the_two_parties_see_the_contact_details(self):
        self.reset("accepted")
        for role in ROLES:
            partner = {"owner": "asker", "asker": "owner"}.get(role)
            shown = "".join(html for _l, html in self.pages_for(role))
            for name, values in SECRETS.items():
                if name == role:
                    continue
                for value in values:
                    with self.subTest(role=role, secret_of=name, value=value):
                        if name == partner:
                            self.assertIn(value, shown)
                        else:
                            self.assertNotIn(value, shown)


if __name__ == "__main__":
    unittest.main()
