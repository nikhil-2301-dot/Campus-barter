# How to add a page

1. **Service first.** Put rules and SQL in `app/<thing>_service.py` (functions take the connection). Routes never contain SQL.
2. **Route.** In the right blueprint (or a new one: create `app/<thing>.py` with `bp = Blueprint(...)` and register it in
   `app/__init__.py`). Order of checks: `@login_required` (or `@admin_required`), `can(...)`, validate input, call the service.
   Every POST has the CSRF token field (`<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` in the form; `security` rejects a missing one). A refused form answers 400; a successful POST answers 303.
3. **Permission.** If the page needs a new rule, add an action to `can()` in `app/permissions.py` (see add-an-admin-action.md).
4. **Template.** Extend `base.html`, one `h1` (use `page_header`), build from `components/` macros, four states where data is loaded
   (loading, empty, problem, success; server-rendered pages need no loading state). No inline style or handler, colours only from `tokens.css`.
5. **Guards that will fail until you add rows:**
   - `tests/test_permission_matrix.py`: one row in `ROUTES` per (endpoint, method) with the expected status for each role.
   - `scripts/ui_audit.py` `page_plan`: one `Row(path, actors)` for each GET page (feeds the accessibility audit, the link crawler and the query budget).
   - `tests/test_query_budget.py` `CEILINGS`: the most SQL statements one request may run (measure, then set the ceiling).
   - A rate limit for a POST that creates rows: the `ACTIONS` table in `app/ratelimit.py` (tests/test_ratelimit.py pins it).
6. Add your own tests for the happy path, a refused input, and each role that must be turned away.
7. `bash scripts/check.sh`; update CHANGELOG.md.
