# Performance notes (C21)

How to repeat: `python3 scripts/perf.py load` and `python3 scripts/perf.py explain` (scratch database, synthetic rows,
nothing real is touched; not part of check.sh because timings change from machine to machine).

## What was measured
- Data: 300 users, 6000 listings (70% available, some with a photo key), 2000 requests, `ANALYZE` run.
- Load: Flask test client in threads (no web server, no network), each thread logged in as a random student, random GET of
  the pages below for 10 s. This shows the app + SQLite only. Python threads share one interpreter lock, so more threads
  do not add speed; the test shows the app stays correct and answers when many people ask at once.
- Sandbox machine, Python 3.13; your laptop will differ. Compare runs with each other, not with a promise.

## Results
| | 1 thread | 8 threads |
|---|---|---|
| Requests per second | about 435 | about 480 |
| Bad answers (500 / locked database) | 0 | 0 |

p50 / p95 in ms with ONE thread (the cost of the page itself): `/` redirect 0.9 / 1.4; browse 2.0 / 2.9; browse page 3 2.0 / 2.9;
browse with filters 2.4 / 3.2; browse search `Growth` 3.3 / 4.6; listing page 1.6 / 2.3; my listings 2.8 / 3.9;
requests sent 1.6 / 2.2; requests received 2.7 / 3.7. With 8 threads the same pages show p50 0.8 to 26 ms and p95 33 to 70 ms
because the threads wait for each other; the slowest single answer was 193 ms.

## SQLite settings (proved by tests/test_stories.py)
Every connection opened by `app.db.connect` has `journal_mode=WAL` (database file), `foreign_keys=ON`, `busy_timeout=5000`,
and `synchronous=NORMAL`.

## Two requests accepting at once (tests/test_stories.py)
Two threads with their own connections accept (a) the same request, (b) two different requests for one item.
Exactly one wins each time; the other gets the normal "cannot do that now" answer. Mutation: changing `BEGIN IMMEDIATE`
to `BEGIN` in `Tx` in `app/tx.py` (it moved there in C22) makes the same-request test fail ("database is locked", two answers that are not 1 win + 1 refusal).

## EXPLAIN QUERY PLAN (top queries)
| Query | Plan | Verdict |
|---|---|---|
| browse count | covering index `ix_listings_status_cat` | fine |
| browse page (no filter) | walks `ix_listings_created` newest first, stops at the page size | fine |
| browse with category filter | `ix_listings_status_cat`, then a temp sort of the matches | fine at this size |
| browse search (LIKE '%x%') | cannot use an index (leading wildcard): reads the `available` rows | the slowest page (3.3 ms of 1 thread at 6000 rows); grows with rows |
| listing page (4 queries) | primary key or unique index each | fine |
| my listings | `ix_listings_owner` + temp sort | fine |
| requests sent | `ix_requests_requester` | fine |
| requests received | planner scans the requester users table first, then `ix_requests_requester` | works (2.7 ms); look again if the user table grows large |
| photo lookup `photo_key = ?` | SCAN of listings (no index) | 0.22 ms at 6000 listings, 2.9 ms at 60000; with an index 0.003 ms |
| landing counts | covering index for listings; SCAN of exchange_requests for completed | the guest landing no longer reads these counts (`stats_service` is kept and tested), so no cost today |

## Decisions and open items (no schema change in C21 by rule)
- Photo index: not needed for a college-size site. If listings pass about 50,000, add `CREATE INDEX ix_listings_photo_key ON listings (photo_key)` in a new migration (C22/C23 can decide).
- Search: a full-text index would be a new feature; leave unless search passes about 10 ms.
- Landing counts: no cache added, nothing slow was measured (and the landing page does not read them).
- Not measured: a real web server (gunicorn), real network, a real browser, a database on a slow disk.
