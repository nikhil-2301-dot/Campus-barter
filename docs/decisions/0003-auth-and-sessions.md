# ADR 0003: Authentication, sessions and CSRF (chunk C3)
Context: students sign up with any email address (no email service, so no verification, see
docs/system-design.md section 9). Auth must be simple enough to test fully in the sandbox.
Decision:
- Signup allows ANY well-formed email address (changed after C18 by user decision; before that only domains in ALLOWED_EMAIL_DOMAINS were allowed, the setting is gone). The domain part must be a dotted host name; no email is verified
  (no suffix match: `a@college.example.evil.com` is refused). Emails are stored lower case.
- Passwords: werkzeug `generate_password_hash` (scrypt). 8 to 128 characters, a tiny deny-list of
  common passwords, not equal to the email. The 128 cap also stops huge inputs being hashed.
- Login gives ONE message for unknown email and wrong password, and hashes a dummy value for unknown
  emails so timing does not reveal which emails exist. "Suspended" is only revealed after a correct
  password. Signup does say "email already exists" (accepted trade-off: needed for usability).
- Sessions: Flask signed cookie holding only user_id + csrf_token. HttpOnly, SameSite=Lax, Secure in
  production, 7 days sliding. The user row is reloaded on EVERY request, so suspending or deleting an
  account takes effect immediately. Login and logout call session.clear() (no session fixation).
- CSRF: one per-session token, required on every POST/PUT/PATCH/DELETE (form field or X-CSRF-Token
  header), no exemptions. When no route matches, Flask's own 404/405 is returned instead of 403.
- Logout is POST only. Redirects after login follow `next` only if it is a local path.
- Profile and signup read only whitelisted form fields, so posting is_admin, id or email changes nothing.
- First admin: `flask --app app create-admin` (password asked for hidden, never on the command line).
Not in this chunk (planned): login throttling and logging of failed logins (C8), password change and
account deletion (not in the v1 requirements list), security headers (C8).
Consequences: no password reset exists: an admin cannot reset a password either (out of scope for v1).
