#!/usr/bin/env python3
"""C21: measure the app on a scratch database. Not part of check.sh (timings vary by machine).

  python3 scripts/perf.py load    [--threads 8] [--seconds 10] [--users 300] [--listings 6000]
  python3 scripts/perf.py explain [--users 300] [--listings 6000]

Both build a scratch, migrated database with synthetic rows, so no real data is touched.
`load` runs several client threads against the app through the Flask test client (no network, so the
numbers show the app + SQLite, not a web server). `explain` records the SELECT statements of each page
and prints SQLite's query plan for them; a line with SCAN on a big table is the thing to look at.
"""
import argparse
import os
import random
import re
import statistics
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app, db as app_db  # noqa: E402
from app.config import MIGRATIONS_DIR  # noqa: E402
from app.constants import CATEGORIES  # noqa: E402
from app.db import connect  # noqa: E402
from app.migrate import migrate  # noqa: E402

MODES = ("swap", "lend", "give")
CONDITIONS = ("new", "good", "fair")
STATUSES = ("available",) * 7 + ("reserved", "closed", "removed")


def build(tmp, users, listings, seed=7):
    """A migrated scratch app with synthetic rows. Returns (app, user_ids, listing_ids)."""
    rnd = random.Random(seed)
    path = os.path.join(tmp, "perf.db")
    app = create_app({"DATABASE_PATH": path, "TESTING": True},
                     environ={"APP_ENV": "development", "SECRET_KEY": "perf-only-key"})
    conn = connect(path)
    migrate(conn, MIGRATIONS_DIR)
    for i in range(users):
        conn.execute(
            "INSERT INTO users (email, password_hash, full_name, branch, year, college_name, enrollment_no) "
            "VALUES (?, 'x', ?, 'IT', 'SE', ?, ?)", (f"u{i}@perf.example", f"User {i}", f"College {i % 12}", f"EN{i}"))
    user_ids = [r[0] for r in conn.execute("SELECT id FROM users")]
    for i in range(listings):
        status = rnd.choice(STATUSES)
        conn.execute(
            "INSERT INTO listings (owner_id, title, category, condition, mode, status, photo_key, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rnd.choice(user_ids), f"Growth notes {i}" if i % 5 == 0 else f"Item {i}", rnd.choice(CATEGORIES),
             rnd.choice(CONDITIONS), rnd.choice(MODES), status,
             f"{i:032x}.jpg" if i % 4 == 0 else None, "2026-01-01" if status == "removed" else None))
    listing_ids = [r[0] for r in conn.execute("SELECT id FROM listings WHERE status = 'available'")]
    pairs = set()
    while len(pairs) < listings // 3:
        pairs.add((rnd.choice(listing_ids), rnd.choice(user_ids)))
    for lid, uid in pairs:
        conn.execute("INSERT INTO exchange_requests (listing_id, requester_id, message, status) VALUES (?, ?, 'hi', ?)",
                     (lid, uid, rnd.choice(("pending", "declined", "cancelled", "completed"))))
    conn.commit()
    conn.execute("ANALYZE")
    conn.close()
    return app, user_ids, listing_ids


def pages_for(listing_id):
    return ["/", "/listings/", "/listings/?page=3", "/listings/?q=Growth",
            "/listings/?category=Books&condition=good&mode=give", f"/listings/{listing_id}",
            "/listings/mine", "/requests/sent", "/requests/received"]


def client_for(app, user_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id
    return c


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def run_load(args):
    with tempfile.TemporaryDirectory() as tmp:
        app, users, listings = build(tmp, args.users, args.listings)
        stop = time.time() + args.seconds
        times, codes, lock = {}, {}, threading.Lock()

        def worker(n):
            rnd = random.Random(n)
            client = client_for(app, rnd.choice(users))
            while time.time() < stop:
                path = rnd.choice(pages_for(rnd.choice(listings)))
                t0 = time.perf_counter()
                code = client.get(path).status_code
                dt = (time.perf_counter() - t0) * 1000
                key = re.sub(r"/listings/\d+$", "/listings/<id>", path)
                with lock:
                    times.setdefault(key, []).append(dt)
                    codes[code] = codes.get(code, 0) + 1

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(args.threads)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = sum(len(v) for v in times.values())
        wall = time.time() - t0
        print(f"rows: {args.users} users, {args.listings} listings; {args.threads} threads, {wall:.1f} s")
        print(f"requests: {total}  ({total / wall:.0f} per second)  status codes: {codes}")
        print(f"{'page':58} {'n':>6} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}")
        for key in sorted(times):
            v = times[key]
            print(f"{key[:58]:58} {len(v):>6} {statistics.median(v):>8.1f} {pct(v, .95):>8.1f} {max(v):>8.1f}")
        return 0 if set(codes) <= {200, 303} else 1      # 303: '/' sends a logged-in student to browse


def run_explain(args):
    with tempfile.TemporaryDirectory() as tmp:
        app, users, listings = build(tmp, args.users, args.listings)
        seen = {}
        real = app_db.connect
        current = {"page": None}

        def tracing(path):
            conn = real(path)

            def trace(sql):
                s = sql.strip()
                if s.upper().startswith(("SELECT", "WITH")) and current["page"]:
                    seen.setdefault(current["page"], {})[re.sub(r"\s+", " ", s)] = 1
            conn.set_trace_callback(trace)
            return conn

        app_db.connect = tracing
        try:
            client = client_for(app, users[0])
            for path in pages_for(listings[0]) + ["/photos/" + f"{0:032x}.jpg"]:
                current["page"] = path
                client.get(path)
        finally:
            app_db.connect = real
        conn = connect(os.path.join(tmp, "perf.db"))
        extra = {"landing counts": ["SELECT category, COUNT(*) AS n FROM listings WHERE status = 'available' GROUP BY category",
                                    "SELECT COUNT(*) FROM exchange_requests WHERE status = 'completed'"]}
        for page, stmts in list(seen.items()) + list(extra.items()):
            print(f"\n== {page}")
            for sql in (stmts if isinstance(stmts, list) else stmts):
                print("  SQL :", sql[:150])
                for row in conn.execute("EXPLAIN QUERY PLAN " + sql):
                    print("   plan:", row[-1])
        conn.close()
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("load", "explain"))
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--seconds", type=int, default=10)
    p.add_argument("--users", type=int, default=300)
    p.add_argument("--listings", type=int, default=6000)
    args = p.parse_args()
    sys.exit(run_load(args) if args.mode == "load" else run_explain(args))


if __name__ == "__main__":
    main()
