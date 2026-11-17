# How to upgrade Flask (and the packages it brings)

Pins are in `requirements.txt` (app) and `requirements-dev.txt` (tests, audit, PDF). A test (tests/test_maintainability.py) checks that every line is an exact pin and that every outside package `app/` imports is pinned. It does NOT compare the pins with what is installed: do that with `pip freeze` after an upgrade.

1. Read the release notes of Flask and Werkzeug between your pin and the new version (breaking changes and deprecations).
2. In a NEW virtual environment: `python3 -m venv /tmp/up && . /tmp/up/bin/activate && pip install -r requirements-dev.txt`
   then `pip install Flask==<new>` and let pip choose matching Werkzeug, Jinja2, click, blinker, itsdangerous, MarkupSafe.
3. Run `bash scripts/check.sh`. Read warnings in the log too (deprecations show up before things break).
4. If a test breaks, find out whether the app or the new version changed. Fix the code; never edit a test to make it pass.
   Places that touch Flask/Werkzeug internals: `app/__init__.py` (config keys), `app/security.py` (CSRF, redirects, headers),
   `app/logs.py` (request id hooks), `app/problem_pages.py` (error handlers), `app/photo_service.py` (uploads, `MAX_CONTENT_LENGTH`),
   `app/db.py` (`g`, teardown), and the cookie settings in `create_app`.
5. Check by hand in a browser: log in, log out, create a listing with a photo, send a request (cookies and CSRF are the usual victims).
6. Write the new numbers with `pip freeze` into both requirements files (only packages that were already there), bump `VERSION`
   (minor), add a CHANGELOG.md entry, and keep the old pins in the entry so a rollback is easy.
7. Roll back = reinstall the old pins from git history; nothing in the database depends on the Flask version.
