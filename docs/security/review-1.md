# Security review 1 (chunk C14)

Fresh-eyes pass over the whole app as it stands after C13: every route, the session and CSRF code,
the permission rules, the templates and the new rate limits. Method: read each route and the
code it calls, then try to break it (tests, live curl run, mutation checks; see HANDOFF.md).
Not covered: a real browser (user checks in Chrome), load testing (C21), deployment (C23).

Severity: HIGH = fix in this chunk. MEDIUM / LOW = listed in HANDOFF "Known gaps".
Status: FIXED, OPEN (with the chunk that should take it) or NOTE.

## Findings

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| R1 | HIGH | Signup | Signup had no limit, so anyone could create unlimited accounts. That floods the database and defeats every per-user limit (a new account gets a new allowance). | FIXED: 30 new accounts per IP per hour, counted only when the account is created (`ratelimit.SIGNUP_MAX`); 429 page above that. |
| R2 | MEDIUM | Rate limit | The IP key is `request.remote_addr`. Behind a reverse proxy that is the proxy's address, so 20 wrong passwords from anyone would lock login for everybody. `X-Forwarded-For` is deliberately not trusted. | FIXED in C23: setting `TRUSTED_PROXIES` (default 0) wraps the app in werkzeug `ProxyFix`, so `request.remote_addr` is the real visitor behind that many proxies; tests/test_deploy.py. Set it only to the real number of proxies. |
| R3 | MEDIUM | Rate limit | Locking by email lets someone deliberately lock a known email (5 wrong passwords, 15 minutes). The IP limit (20 per 15 minutes) caps how many emails one address can lock. | OPEN (accepted trade-off for v1). |
| R4 | MEDIUM | Signup | No email verification: since the domain rule was removed (user decision, C19 session), anyone can register with any address, so a stranger can also register a classmate's enrollment number first. Remedies: admin suspend, signup rate limit; a verification email is the real fix. | OPEN (higher exposure after the domain rule was removed; not in v1 scope). |
| R5 | MEDIUM | Signup | Signup tells you that an email or enrollment number already exists (account enumeration). Login does not: same message and same lock for known and unknown emails. | OPEN (same cause as R4; the signup limit slows scraping). |
| R6 | MEDIUM | Privacy | Logged-in pages (including contact details on accepted requests) have no `Cache-Control: no-store`, so on a shared computer the Back button after logout may show the last page. | FIXED in C15: `Cache-Control: no-store` on every non-static response built for a logged-in user (`security._no_store_for_logged_in`). |
| R7 | LOW | Browse | The search text `q` has no length cap. A huge value makes SQLite refuse the pattern; the page then shows the 503 problem state (no crash, no leak). | FIXED in C17 part 1: `q` is cut to 100 characters (`MAX_SEARCH_LEN`), tests/test_c17_search_cap.py. |
| R8 | LOW | Listings | An owner can edit title, offered-as and type while a request on the listing is accepted (for example swap to lend). | OPEN (C20 rule review). |
| R9 | LOW | Session | Sessions slide for 7 days with no absolute maximum and no "log out everywhere". | OPEN (known since C3). |
| R10 | LOW | Audit | Failed logins, throttle hits and admin actions are not logged. | FIXED in C15: one JSON line each for login success/failure/lock/suspended, signup cap, per-hour limit hit and every admin action (admin id + target id); no passwords, no raw emails (`logs.log_event`). |
| R11 | LOW | Privacy | The CSP must allow Google Fonts, so each visitor's browser contacts Google. Hosting the two fonts ourselves would let the policy drop both hosts. | OPEN (C18/C23). |
| R12 | LOW | Listings | Only 10 new listings per hour per student, but no total cap per student. | OPEN. |
| R13 | NOTE | Headers | HSTS is `max-age=31536000` without `includeSubDomains` or preload, and only when `APP_ENV=production`. Revisit once the real HTTPS domain is known. | NOTE (C23). |
| R14 | NOTE | Dev only | `flask run --debug` shows Werkzeug's debugger and `/design` exists outside production. Production start-up (gunicorn, no debug) is C23. | NOTE (C23). |

## Route walk

"Guard" = how the route decides who may use it. Every POST goes through the global CSRF check
(`security._check_csrf`), which has no exemptions.

| Route | Methods | Guard | Notes |
|-------|---------|-------|-------|
| `/` | GET | public | landing for guests, 303 to listings otherwise |
| `/health` | GET | public | status and schema number only; no paths or SQL |
| `/signup` | GET, POST | guests (logged-in: 303) | IP signup limit (R1); input checked by `accounts.validate_signup`; only whitelisted fields reach `create_user`, so `is_admin` cannot be posted |
| `/login` | GET, POST | guests | throttle per email and IP checked before any password work; same message for unknown email and wrong password; `next` passes `safe_next`; session cleared on login |
| `/logout` | POST | anyone | CSRF only |
| `/profile` | GET, POST | `login_required` | whitelisted fields only |
| `/listings/` | GET | public | `?` parameters; filters allow-listed; LIKE escaped (R7) |
| `/listings/<id>` | GET | public | removed listings are 404; owner name only; report and request panels depend on `can()` |
| `/listings/new` | GET, POST | `login_required`, `can("create_listing")`, 10 per hour | GET not counted |
| `/listings/<id>/edit` | GET, POST | `login_required`, `can("edit_listing")` | owner or admin (R8) |
| `/listings/<id>/delete` | GET, POST | `login_required`, `can("delete_listing")` | GET is only a confirm page |
| `/listings/mine` | GET | `login_required` | own rows only |
| `/listings/<id>/request` | POST | `login_required`, `can("request_listing")` in the service, 20 per hour | offered item must be the requester's own available listing |
| `/listings/<id>/report` | POST | `login_required`, `can("report_listing")` in the service, 5 per hour | one open report per student per listing |
| `/requests/sent`, `/requests/received` | GET | `login_required` | own rows only |
| `/requests/<id>` | GET | `login_required`, `can("view_request")` | strangers and admins get 404; contact only while accepted or completed (`view_contact`) |
| `/requests/<id>/<action>` | GET, POST | `login_required`, `can(action + "_request")` | unknown action 404; GET only for decline and cancel confirm pages |
| `/admin/` and `/admin/reports`, `/admin/reports/<id>`, `/admin/users` | GET | `admin_required` | admin only; every route has a row in the route x role matrix test |
| `/admin/reports/<id>/resolve`, `/dismiss` | POST | `admin_required`, `can("resolve_report")` | not rate limited by design |
| `/admin/listings/<id>/remove` | GET, POST | `admin_required`, `can("delete_listing")` | GET is a confirm page |
| `/admin/users/<id>/suspend`, `/unsuspend` | GET, POST / POST | `admin_required`, `can("suspend_user")` / `can("unsuspend_user")` | cannot suspend yourself or another admin |
| `/design` | GET, POST | development only | not registered in production |
| `/static/...` | GET | public | carries the same headers |

## Checked and fine

- **CSRF**: one global check before every unsafe request; login, logout and signup included; no GET changes state.
- **SQL**: raw `?` parameters only; a test scans `app/` for f-string SQL; the browse query joins fixed fragments.
- **XSS**: autoescape on, no `|safe`, `Markup` or `render_template_string` anywhere; tests render hostile text on many pages; no inline script, style or handler in any template (test added in C14), so the strict CSP costs nothing.
- **Open redirects**: `safe_next` for `next`; the 429 page's Retry link accepts only a same-site path (tests with foreign referers).
- **Authorization**: every route above has its guard; request pages and contact data go through `get_for_party` / `view_contact`; suspended users are logged out on their next request.
- **Passwords and sessions**: werkzeug hashing, a dummy hash for unknown emails, input length cap before hashing, session cleared at login, cookies HttpOnly + SameSite=Lax (+ Secure in production), production refuses to start without a strong `SECRET_KEY`.
- **Error pages**: 403, 404, 429 and 500 show no trace, path or SQL.
- **Throttles**: a refused attempt is never recorded (the lock cannot grow); old rows are ignored by time and deleted after a day; the password is never stored or logged.

## Headers (all responses, tests in tests/test_security_headers.py)

Content-Security-Policy: `default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com;
font-src https://fonts.gstatic.com; img-src 'self' data:; object-src 'none'; frame-ancestors 'none';
form-action 'self'; base-uri 'self'`. Also X-Frame-Options DENY, X-Content-Type-Options nosniff,
Referrer-Policy strict-origin-when-cross-origin, Permissions-Policy (camera, microphone, geolocation off),
and Strict-Transport-Security only in production.
