# Changelog

Newest first. Format: `## version (chunk)`; versions follow VERSION. Add a line here whenever behaviour, schema or setup changes.

## Unreleased

## 1.0.0 (C28: final review)
- Added: docs/security/review-2.md (second review: photos, campus page, open signup, proxies; no HIGH finding open), docs/release-checklist.md (what is done, what the user must do before real users).
- Tests: tests/test_release_docs.py (4). No app code, schema or dependency change.
- Not done: C27 bonus diagrams; deploy-time checks (docs/release-checklist.md section B).

## 0.26.0 (C26: Practical PDF final)
- Added: cover page (practical/cover.txt), index with page numbers, footer numbers that match the real page; build_practical.py takes an optional cover file (no new package).
- Changed: Exp 4 use cases UC21 (suggest a campus) and UC22 (view campus suggestions) and a redrawn use case diagram; Exp 2 FR11, photo and owner-cancel wording; Exp 5 note on the two helper tables.
- Tests: tests/test_practical_cover.py (9). No app code, schema or dependency change.

## 0.25.0 (C25 part 1: browser verification guide)
- Added: docs/browser-verification.md (console, network and headers, fonts, Lighthouse, keyboard-only walk-through, dialogs, focus rings and motion, wrong addresses, a table linking the older C7-C24 lists, how to report findings).
- Tests: tests/test_browser_guide.py (5): the guide exists, has a section per topic, has a row linking HANDOFF.md for each of C7 to C24, the labels it names still exist in HANDOFF.md, relative links resolve.
- No code, page, schema or dependency change.
- Part 2 (fix round): the user looked at the pages and the login in their own Chrome and reported no findings, so no fix was needed. Guide section 1 now lists the two other expected red console lines (400 on the empty new-listing form, 405 on a typed /logout). The long checks (Lighthouse, console, keyboard-only, older lists) were not run by the user; they stay open in docs/LEFTOVERS.md.

## 0.24.0 (C24 CI + runbooks + monitoring)
- Changed: `/health` now also runs `SELECT 1`, reports `latest_schema_version`, `pending_migrations` and `app_version`, and answers 503 (`"db":"migrations pending"`) while a migration file is not applied. Every answer is `no-store`. Existing keys and statuses are unchanged.
- Added: `migrate.available_versions` and `migrate.pending_versions` (public helpers, used by /health).
- Added: `.github/workflows/check.yml` (runs `bash scripts/check.sh` on Python 3.12 for pushes to main and pull requests).
- Added: docs/runbooks/backups.md, rollback.md, incident.md, monitoring.md; deploy.md links them (section 6).
- Tests: tests/test_ops.py (health detail, workflow file structure, runbooks). The workflow is checked by structure, not by a YAML parser (no new package).
- No schema change (migrations 0001-0004). No new dependency. Still NOT verified: `gunicorn==23.0.0` pin (never installed in the sandbox).

## 0.23.0 (C23 Deploy prep + backups)
- Added: `wsgi.py` and `start.sh` (gunicorn), `scripts/backup.py` (backup + restore of the database and photos), docs/runbooks/deploy.md, tests/test_deploy.py.
- Added setting `TRUSTED_PROXIES` (0-5, default 0): behind a reverse proxy the client address comes from X-Forwarded-For (security review R2). Documented in `.env.example`, together with `WEB_WORKERS` and `BIND` (start.sh only).
- Changed: production now also refuses to start when `FLASK_DEBUG` is on.
- Dependency: `gunicorn==23.0.0` added to requirements.txt (the one new package; pin chosen by the assistant, NOT installed in the build sandbox: verify once with `pip install -r requirements.txt`).
- No schema change (migrations 0001-0004).

## 0.22.0 (C22 Maintainability)
- Added: docs/ARCHITECTURE.md, docs/how-to/ (five recipes), CHANGELOG.md, VERSION, requirements.txt and requirements-dev.txt (pins of the installed versions; no new packages).
- Changed (no behaviour change): all listing SQL moved from `app/listings.py` and one line in `app/admin.py` into `app/listing_service.py`;
  `_Tx` and `NOW` moved to `app/tx.py` (now `Tx`); `accounts._clean_text` is public (`clean_text`); `can("restore_listing")` returns True/False instead of 1/0.
- Tests: tests/test_maintainability.py guards the layering (no SQL in routes, no private imports across modules), the pins, the version and the recipes.

## 0.21.0 (C21 End-to-end + performance)
- Story tests, a two-accepts concurrency test, `scripts/perf.py`, docs/performance.md. No schema change.

## 0.20.0 and earlier (C0-C20)
- See the status board in HANDOFF.md. Migrations 0001-0004 define the schema.
