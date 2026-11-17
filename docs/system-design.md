# Campus Barter: System Design (Layer 1)

## 1. Problem statement
Students of partner engineering colleges need items such as books, notes, PYQ books,
mini drafters, lab coats and calculators. New ones are
costly; seniors have unused ones; juniors and classmates cannot find them.
Today this happens through scattered WhatsApp groups with no search, no record
and no trust. Campus Barter gives one searchable place to list items and agree
on a swap, loan or giveaway inside the community of partner colleges.

## 2. Users and roles
- Student (member): lists items, browses, requests items, manages own requests.
  Profile carries the college name and enrollment number (identity anchor).
- Admin (moderator): removes listings, suspends users, reviews reports.
  Admin is flagged in DB; first admin set via a CLI script, never via signup.

## 3. Core flows (the only 5 that matter for v1)
1. Sign up / log in with ANY valid email address (Gmail or any provider; no college
   domain is required or checked). Profile: name, college name, branch, year
   (FE/SE/TE/BE), enrollment number (required), phone/WhatsApp (private),
   optional social link (Reddit u/name or Instagram @handle, or https link),
   optional address (private).
2. List an item: title, category, condition, description, mode
   (swap | lend | give), optional "wanted in return", optional photo.
3. Browse/search: text search, filter by category, mode, owner year, available only.
4. Request an item: requester optionally offers one of own listings + message.
   Owner accepts or declines. On accept, BOTH see each other's contact. After
   the meet-up either side marks completed.
5. Report a listing; admin removes it.

## 4. Status model
Listing: available -> reserved (request accepted) -> closed (swap/give completed)
         reserved -> available (request cancelled, or lend returned)
         any -> removed (owner or admin; soft delete)
Request: pending -> accepted -> completed
         pending -> declined | cancelled ; accepted -> cancelled
Rule: one accepted request per listing at a time.

## 5. Data model (SQLite)
Partner colleges share one database; the college is a text field on the user
(`college_name`), NOT a tenant column, so there is no per-college data
separation in v1 (see ADR 0001; add a colleges table and college_id if full
multi-tenancy is ever needed). Every table: id, created_at, updated_at.
- users(id, email UNIQUE, password_hash, full_name, college_name, branch, year,
        enrollment_no UNIQUE, phone, social_link, address,
        is_admin, is_suspended, deleted_at)
- listings(id, owner_id FK, title, category, condition, description, mode,
           wanted_in_return, photo_key, status, deleted_at)
- exchange_requests(id, listing_id FK, requester_id FK, offered_listing_id FK NULL,
           message, status, UNIQUE(listing_id, requester_id) while open)
- reports(id, listing_id FK, reporter_id FK, reason, status)
- login_attempts(id, key, attempted_at)   -- for rate limiting
- campus_requests(id, college_name, area, kind, created_at)   -- the Add your campus page (0004); no FK, UNIQUE(lower(college_name), lower(area))
Indexes: FKs, listings(status, category), listings(created_at), users(email).
Categories (config list, not a table; they are also the browse filter): Books, Notes,
PYQs book, Mini drafter, Lab coats, Calculator, Other.
Condition values (DB CHECK, chosen in C2): new, good, fair, worn. DB rules: docs/decisions/0002-database-rules.md.

## 6. Functional requirements
FR1 Register with any valid email address (no domain allow-list); reject malformed addresses; enrollment number required and unique
    (one account per student, university-wide).
FR2 Login/logout; session expires; throttled after repeated failures.
FR3 Edit own profile (name, college name, branch, year, enrollment number, phone, social link, address);
    phone and address visible only to a counterpart on an accepted request.
FR4 Create/edit/delete (soft) own listings; cannot touch others' listings.
FR5 Browse with search, filters, pagination.
FR6 Send request (optionally offering own item); one open request per user per listing;
    cannot request own listing.
FR7 Owner accepts/declines; requester cancels; either completes.
FR8 Contact details revealed only after accept.
FR9 Report listing; admin removes listing / suspends user.
FR10 "My listings", "Requests I sent", "Requests I received" pages.

## 7. Non-functional requirements
- Security: hashed passwords, CSRF on all POST, parameterised SQL, output
  escaping, server-side authorization, security headers, no secrets in repo.
- Privacy: phone shared only after accept; account deletion anonymises user.
- Performance: pages < 500 ms for ~3,000 users / ~50 concurrent (SQLite WAL is enough).
- Usability: desktop-browser web page (fluid layout, no dedicated mobile work); keyboard usable; loading/empty/error states.
- Reliability: nightly copy of the SQLite file; restore documented and tested once.

## 8. Out of scope for v1 (do not add)
In-app chat, payments/deposits, ratings/reviews, push notifications, email
verification, full multi-tenant support (per-college data separation, college admins;
signup already accepts any email domain), native mobile app, multiple photos.

## 9. Risks / open questions
- No email verification => anyone with a typo-squatted college address could sign up.
  Mitigation: unique enrollment number + rate limits + admin suspend. Revisit if an email service is available (verification link).
- Enrollment numbers are unique university-wide, so the database refuses a second account
  with the same number. The number is still self-declared and not verified (no university
  roster or email check): someone could register first with another student's number, and
  the college name is free text. Mitigation: unique enrollment number + admin suspend. Revisit if
  the university can supply a roster.
- With students of several colleges in one database, all students see all listings; there is
  no per-college filter or isolation in v1. Meet-ups across colleges need extra care.
- Safety of meet-ups: show a "meet on campus" notice on accepted requests.
- Loan items may not come back: v1 relies on the community + admin; no deposits.

## 10. Capacity back-of-envelope
~3,000 students, maybe 600 listings, 5-10 requests/day/department. Peak ~2-5
req/s. SQLite (WAL) on one small server handles this with large headroom.
First bottleneck: photo storage disk, then single-writer SQLite. Both are fine for year one.
