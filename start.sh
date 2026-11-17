#!/usr/bin/env bash
# Start Campus Barter with gunicorn. Export the variables from .env.example first (APP_ENV, SECRET_KEY, DATABASE_PATH).
# A new database needs `flask --app app migrate` once before the first start.
set -e
cd "$(dirname "$0")"
export APP_ENV="${APP_ENV:-production}"
exec gunicorn --workers "${WEB_WORKERS:-2}" --bind "${BIND:-127.0.0.1:8000}" --timeout 30 wsgi:app
