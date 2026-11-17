# Testing guide (written in C20)

## Run it
- Everything: `bash scripts/check.sh` (compile, all tests, PDF build). About 250-290 s, so start it in the
  background: `(nohup setsid bash scripts/check.sh > /tmp/check.log 2>&1 &)` and poll the log.
- One file: `cd tests && python3 -m unittest test_permission_matrix`.
- The gate in check.sh fails on the words "error", "failed" and "skipped" anywhere in the test log, so never use
  them in a test name, class name or docstring. A test may not be skipped: 0 skips is part of "green".
- Stack rules: stdlib `unittest` + the Flask test client (pytest is not installed). Shared helpers are in
  `tests/helpers.py` (`make_app`, `add_user`, `add_listing`); `AuthCase` (test_auth.py) and `BrowseCase`
  (test_browse_ui.py) add login, post-with-token and SQL helpers.

## What each group of test files protects
| Area | Files |
|------|-------|
| Accounts, email rules, config | test_auth, test_email_rules, test_config |
| Listings, forms, browse | test_listings, test_listing_forms, test_browse_ui, test_c17_* |
| Exchange state machine and privacy | test_exchange, test_requests_ui |
| Admin, reports, campuses | test_admin, test_reports, test_campuses |
| Photos | test_photos |
| Abuse limits, headers, logs, error pages | test_ratelimit, test_security_headers, test_logging, test_problem_pages |
| UI rules (audit, crawler, query budget, polish) | test_ui_audit*, test_ui_crawl, test_query_budget, test_c18_polish, test_layout, test_components |
| **Permissions from tables (C20)** | **test_permission_matrix** |
| Layering, pins, version, docs (C22) | test_maintainability |

## The C20 permission tests (tests/test_permission_matrix.py)
1. **`can()` table.** 16 actions x 8 kinds of user (anonymous, owner, requester, bystander, admin, each suspended
   or not) x every resource state = 504 cases, compared with `expected()`, a plain restatement of the rules.
   A new action in `can()` without a row in `ACTIONS` fails `test_every_action_in_the_module_text_has_a_row`.
2. **Route x role matrix.** Every `(endpoint, method)` in `app.url_map` has one row in `ROUTES` (45 rows; only
   `static` and the dev-only `/design` are exempt, with reasons). Each row is run as guest, owner, asker,
   bystander, suspended student and admin = 270 cells. A cell checks the HTTP status AND whether the database
   or the photo file changed, so a refused action that still answers 303 is caught. A route without a row fails
   `test_every_route_in_the_app_has_a_row`. Every POST is also sent without the form token (403, nothing changes).
3. **Privacy sweep.** With the request pending, declined or cancelled, every GET page of every role is searched
   for the other students' phone, address, social link and (for students) email. After accept, the partner's
   details must appear and nobody else's. The positive check keeps the sweep from passing on empty pages.
To add a route: add a row to `ROUTES` (url, form data, request status before, status per role, roles that change
data). If the status you expect differs from what the app does, find out which one is wrong before editing the row.

## Mutation spot-checks (done by hand in C20)
Method: copy the file, change one expression, run the tests, restore the file, compare md5 (all restored). One
mutation per call. "Caught" = at least one test fails for the right reason (a mutation that only broke the import
does not count: one such run was thrown away and redone).
| # | File | Mutation | Caught by |
|---|------|----------|-----------|
| 1 | permissions.py | edit_listing: owner check inverted | matrix (46) |
| 2 | permissions.py | request_listing: admins allowed | matrix (2) |
| 3 | permissions.py | view_contact: also while pending | matrix (19) |
| 4 | permissions.py | accept/decline: also on accepted requests | matrix (2) |
| 5 | exchange_service.py | accept no longer reserves the listing | test_exchange (4) |
| 6 | exchange_service.py | completing a lend closes the item | test_exchange (1) |
| 7 | exchange_service.py | act() skips the can() state check | matrix (1) + test_exchange (3) |
| 8 | exchange_service.py | contact returned in every state | matrix (38) + test_exchange (2) |
| 9 | exchange_service.py | wrong party's contact shown after accept | matrix (6) + test_exchange (2) |
| 10 | listings.py | listing page shows the owner's phone | matrix (22) |
| 11 | listings.py | remove-photo route skips can() | matrix (2) |
| 12 | photo_service.py | size limit off by one | test_photos (1) |
| 13 | photo_service.py | any RIFF file accepted as WebP | test_photos (2) |
| 14 | photo_service.py | PNG signature checked on 4 bytes only | test_photos (1) |
| 15 | security.py | admin_required no longer checks view_admin | matrix (31) |
| 16 | auth.py | suspended students keep their session | matrix (37) |

Equivalent mutant (documented, not a gap): removing the `KEY_RE` check in `photos.serve` (found in C19). The
database lookup `photo_key = ?` refuses odd keys too, so no visible behaviour changes; `KEY_RE` has its own unit
test and `send_from_directory` blocks `..` as well.

## Review notes (permission and privacy view of the suite)
- Before C20 `can()` had one test with about 15 assertions and the route matrix covered only `/admin`. Now every
  action and every route is in a table; nothing is left to the author remembering a case.
- The service-level state machine (listing statuses after accept, cancel, complete, remove) is NOT in the matrix;
  test_exchange guards it (mutations 5-7). The matrix proves who may act, test_exchange proves what the action does.
- `can("restore_listing")` returns the raw `is_admin` value (0 or 1), not `False`/`True` like the other actions.
  Callers only use it as a condition and no route uses it yet, so nothing is wrong today; the table compares with
  `bool()`. Worth a one-line fix in C22 (code change, so not done in C20).
- Suspended students: `load_user` drops them, so on every route they behave like a guest. The matrix pins this.
- Logged-in users who open `/login` or `/signup` are redirected (303), also pinned.
- Not covered by anyone: real-browser behaviour (C25), concurrency (C21), and the Windows file-delete case.
