# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private reporting instead: open the **Security** tab of this repository and click **Report a vulnerability**.

Please include:
- What the problem is and where it is
- Steps to reproduce it
- What an attacker could do with it

We will read every report and reply as soon as we can.

## What is already in place
- CSRF protection on every form
- Rate limits on login, sign-up and campus suggestions
- Secure, HttpOnly session cookies in production
- Security headers and a strict Content Security Policy
- Contact details hidden until a request is accepted
- Logs that never contain passwords, tokens, cookies or request bodies

Past reviews and their open items are in [docs/security/](docs/security/).

## Supported versions
Only the latest version on the `main` branch gets fixes.
