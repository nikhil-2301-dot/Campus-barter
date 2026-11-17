# ADR 0001: Flask + SQLite instead of Next.js + Supabase
Context: The playbook suggests Next.js/Postgres/Supabase-style stacks. The build
sandbox has no internet, so `npm install` cannot run and nothing from that
stack could be executed or tested. Flask 3.1, Jinja2, Werkzeug, sqlite3 and
ReportLab are preinstalled.
Decision: Python/Flask modular monolith, SQLite, server-rendered Jinja, plain CSS.
Alternatives rejected: Next.js+Supabase (cannot be tested here; more moving parts
for a college project); FastAPI (not installed).
Consequences: every chunk can be run and tested for real. Trade-offs: no
database RLS (authorization lives in can()), single-writer SQLite, hand-written
CSRF helper (small, tested). Hosting target: a free Python host (e.g.
PythonAnywhere or Render) with a persistent disk for the SQLite file.
