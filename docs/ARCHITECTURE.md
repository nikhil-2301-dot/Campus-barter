# Architecture (written in C22)

One page for a new developer. The reasons for the stack are in docs/decisions/0001-stack.md.
Recipes for common changes are in docs/how-to/.

## The three layers
```
browser -> routes (app/*.py blueprints) -> services (app/*_service.py, rules + SQL) -> SQLite (app/db.py)
                 |                                  |
          templates/components             permissions.can()   tx.Tx (BEGIN IMMEDIATE)
```
- **Routes** are thin: read the request, check login and `can()`, call a service, flash a message, render or redirect.
  They contain NO SQL (a test, tests/test_maintainability.py, keeps it that way).
- **Services** hold the business rules and every SQL statement (`?` parameters only, never f-string SQL).
  A change that touches several rows runs inside `tx.Tx(conn)` (one `BEGIN IMMEDIATE` transaction).
- **Templates** only show data. Pages are built from the Jinja macros in `app/templates/components/`.

## Module map (app/)
| Module | What it does |
|---|---|
| `__init__.py` | `create_app()`: config, hooks, registers every blueprint (`design` only outside production) |
| `config.py`, `constants.py` | Environment settings; fixed vocabularies (categories, conditions, modes, statuses, labels) |
| `db.py`, `migrate.py`, `tx.py` | Connection (WAL, foreign keys, busy timeout), numbered-migration runner, transaction wrapper + `NOW` |
| `accounts.py`, `auth.py`, `security.py` | Rules and password hashing; /signup /login /logout /profile; CSRF, `login_required`, `admin_required` |
| `permissions.py` | `can(action, resource, user)`: the only place that says who may do what |
| `listings.py` + `listing_service.py` | Browse, view, create, edit, delete, my listings; all listing SQL |
| `photo_service.py`, `photos.py` | One photo per listing: type/size checks, random file names, safe serving route |
| `exchange.py` + `exchange_service.py` | Requests (accept, decline, cancel, complete): the state machine |
| `reports.py` + `report_service.py` | A student reports a listing; admin resolves or dismisses |
| `admin.py` + `admin_service.py` | Dashboard, reports, remove listing, suspend users, campus suggestions |
| `campuses.py` + `campus_service.py` | "Add your campus" page |
| `ratelimit.py`, `logs.py`, `problem_pages.py` | Throttles (login and per-user actions), JSON logs with request id, 403/404/429/500 pages |
| `stats_service.py`, `seed.py`, `cli.py`, `health.py`, `design.py`, `main.py` | Counts, demo data, `flask` commands, /health, dev-only /design gallery, `/` |

## A request, step by step
1. `logs` gives it a request id; `security` adds headers (CSP, frame, nosniff...) and checks the CSRF token on every POST.
2. `auth.load_user` puts the signed-in user (or None) in `g.user`.
3. The route checks login and `can(...)`; `ratelimit.limited(...)` may answer 429.
4. The route calls a service with `get_db()`; the service validates, changes rows (in a `Tx` when more than one row changes) and returns data or raises its own problem class (`ExchangeProblem`, `AdminProblem`, `ReportProblem`).
5. The route turns a problem into a flash message or a status page; otherwise it renders a template or redirects with 303.

## Data
Tables: `users`, `listings`, `exchange_requests`, `reports`, `login_attempts`, `campus_requests`, `schema_migrations`
(migrations/0001-0004). Rules enforced by the database itself: one open request per student per listing, one accepted request
per listing, unique enrollment number. Categories are NOT a table: the list lives in `constants.CATEGORIES`.
A schema change is always a new numbered file `migrations/000N_name.sql`; an applied file is never edited
(the runner stores a SHA-256 and refuses a changed file). Apply with `flask --app app migrate`.

## Tests and gates
- `bash scripts/check.sh` = compile, all tests (stdlib unittest, Flask test client). About 4-5 minutes.
- Guards that fail when something is forgotten: a route without a row in `ROUTES` (tests/test_permission_matrix.py), a page without
  a `page_plan` row or a ceiling in `CEILINGS` (tests/test_query_budget.py, scripts/ui_audit.py), a `can()` action without a row in `ACTIONS`,
  SQL inside a route file, a private name imported from another module, a requirements line that is not an exact pin or an outside package the app imports that is not pinned, a VERSION that differs from the newest CHANGELOG heading.
- More in docs/testing.md (what each test file protects) and docs/performance.md (numbers, EXPLAIN plans).

## Deploy and backups
- `wsgi.py` (`app = create_app()`) and `start.sh` (gunicorn) start production; settings come from environment variables (`.env.example`). `TRUSTED_PROXIES` makes `request.remote_addr` the real client behind a proxy.
- `scripts/backup.py` backs up and restores the database file and the `photos/` folder next to it. Steps: docs/runbooks/deploy.md. Day-to-day runbooks (C24): docs/runbooks/backups.md, rollback.md, incident.md, monitoring.md. The CI workflow is .github/workflows/check.yml (runs scripts/check.sh).
- `/health` (app/health.py) answers 200 only when the database opens and no migration file is pending; otherwise 503 with a short reason.

## Where to change what
| I want to... | Recipe |
|---|---|
| add an item type (a new category) | docs/how-to/add-an-item-type.md |
| add a field to a listing | docs/how-to/add-a-listing-field.md |
| add a page | docs/how-to/add-a-page.md |
| add an admin action | docs/how-to/add-an-admin-action.md |
| move to a newer Flask | docs/how-to/upgrade-flask.md |

The history of changes is in CHANGELOG.md.
