# Security review 2 (chunk C28)

Second fresh-eyes pass, done after the last feature chunk. It follows review 1 (docs/security/review-1.md)
and adds what was built after it: listing photos (C19), the "Add your campus" page and its admin list,
open signup, trusted proxies, /health.
Method: read the code (photo_service, photos, campus_service, campuses, security, config, ratelimit,
permissions, auth, seed), compare with review 1, and run small live probes on a scratch database
(wrong photo addresses, guest access to protected pages, POST without a CSRF token, a 3 MB body,
the 404 page). The 1074 tests of the project also ran green.
Not covered: a real browser, a real server behind HTTPS, a scan of the pinned packages for known
problems (the build sandbox has no internet), Python 3.14 (the project was built and tested on 3.12).

Severity: HIGH = must be fixed before a public launch. MEDIUM / LOW = listed, the user decides.
Status: FIXED, OPEN, NOTE, ACCEPTED.

## New findings

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| R15 | MEDIUM | Privacy, photos | A photo is saved exactly as uploaded (photo_service.save_file). A phone photo normally carries hidden EXIF data (where and when it was taken, the phone model, sometimes GPS). Anyone, even a guest, can open the photo of a visible listing, so the hidden data is public. | OPEN. Fix needs the photo to be re-saved without metadata: either a small image package (a new dependency: needs the user's yes) or a hand-written JPEG/PNG/WebP metadata stripper (more code to test). Until then the upload page could say "remove the location from the photo first". |
| R16 | LOW | Listings | Still true after C20: the owner can edit a listing while a request on it is accepted (can("edit_listing") only checks the owner and that the listing is not removed). Same as R8. | OPEN (accepted for v1). |
| R17 | LOW | Campus page | The page is open to everyone and capped per IP address only. Many addresses can still fill the `campus_requests` table; the admin list shows the newest 200. | OPEN (accepted for v1; no personal data is stored there). |
| R18 | NOTE | Packages | The pinned packages in requirements.txt were not scanned for published problems (no internet in the build sandbox). | OPEN for the user: run `pip install pip-audit` then `pip-audit -r requirements.txt` on a machine with internet, before going public. |
| R19 | NOTE | Python version | The user's computer runs Python 3.14.7; the tests ran on 3.12. Sign-up, login, pages and the demo data worked there, but the test suite was never run on 3.14. | OPEN for the user (CI on GitHub uses 3.12). |

## Review 1 items: status now

| ID | Status now |
|----|-----------|
| R1 signup flood | FIXED (C14) |
| R2 client IP behind a proxy | FIXED (C23: `TRUSTED_PROXIES`) |
| R3 a known email can be locked on purpose | ACCEPTED (the IP limit caps the damage) |
| R4 no email verification | OPEN: needs an email service; the real remedy before a public launch |
| R5 signup shows "already exists" | OPEN (same cause as R4) |
| R6 Back button after logout | FIXED (C15, `no-store`) |
| R7 huge search text | FIXED (C17) |
| R8 edit while accepted | OPEN (same as R16) |
| R9 sessions 7 days sliding, no "log out everywhere" | OPEN, low |
| R10 no log lines | FIXED (C15) |
| R11 Google Fonts in the policy | OPEN (self-hosting the two fonts would remove it) |
| R12 no total cap of listings per student | OPEN, low |
| R13 HSTS without includeSubDomains | NOTE: revisit with the real domain |
| R14 debug and /design | FIXED (C23: production refuses debug, /design is not registered) |

No HIGH finding is open.

## Checked again and fine

- **Photo address**: the key must match 32 hex characters plus jpg, png or webp before anything touches the disk; `../`, `%2e%2e%2f` and `.svg` all give 404 (probe run). Files get random names; the type comes from the first bytes, not from the file name; SVG is not accepted; the response is `nosniff`, `Content-Disposition: inline`, and the CSP stays strict.
- **Photo of a removed listing**: not served; the file is deleted when the listing is removed or the photo is replaced or removed.
- **Upload size**: `MAX_CONTENT_LENGTH` is 2 MB plus 64 KB; a 3 MB body gets 413 before any form code runs.
- **Guest access**: `/admin/`, `/admin/users`, `/requests/1`, `/listings/new`, `/profile` all send a guest to the login page (303).
- **CSRF**: POST to `/logout`, `/login` and `/campuses/new` without a token gives 403.
- **Problem pages**: the 404 page shows no trace, path or SQL.
- **Campus suggestions**: validated (length, kind from a fixed list), raw `?` SQL, shown with autoescape, no personal data, repeats stored once.
- **Production settings**: secret key length and placeholder check, debug refused, secure cookie, HSTS only in production, demo seed refuses to run in production.
- **Code scan**: no `|safe`, `Markup`, `render_template_string`, `eval`, `exec`, `pickle`, `subprocess` or `os.system` in `app/`.

## What the user decides

1. R15: warn users about photo location data, or add metadata stripping (needs a yes for a new package, or more code).
2. R4/R5: add email verification before real strangers can register (needs an email service).
3. R18: run the package scan once with internet.
