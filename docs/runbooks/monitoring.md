# Monitoring runbook (C24)

One check is enough for this app: an uptime monitor that calls `/health` every few minutes and tells you when it stops answering.

## What /health says
`GET /health` needs no login and returns JSON with `Cache-Control: no-store`.

| Answer | Meaning |
|---|---|
| 200, `"status":"ok"`, `"db":"ok"` | Database opens, schema table present, no migration waiting |
| 503, `"db":"unavailable"` | The database file cannot be opened (details only in the server log) |
| 503, `"db":"not migrated"` | New database: run `flask --app app migrate` |
| 503, `"db":"migrations pending"` | A migration file has not been applied: run `flask --app app migrate` |

A 200 body also carries `schema_version`, `latest_schema_version`, `pending_migrations` (0) and `app_version`.

## Set up a monitor (any service: UptimeRobot, Better Stack, a cron job on another machine)
1. Create an HTTP(S) monitor for `https://<your domain>/health`.
2. Interval 5 minutes (1 minute if the service allows it for free).
3. Add a keyword check: the page must contain `"status":"ok"`. A plain "200" check is not enough when a proxy serves its own page with status 200.
4. Alert after 2 checks in a row that get no good answer (avoids alerts for one slow answer).
5. Send alerts to a mail address and one phone channel (push or SMS) that you read.
6. Test it once: stop the app, wait for the alert, start the app, wait for the recovery message.

No monitoring service? Run from another machine: `*/5 * * * * curl -fsS --max-time 10 https://<your domain>/health | grep -q '"status":"ok"' || echo "campus-barter health check problem" | mail -s "campus-barter" you@example.com`

## What to watch besides /health
- Free disk space (data disk and backup disk): alert at 80% used.
- Age of the newest backup folder: alert when older than 2 days ([backups.md](backups.md)).
- Certificate expiry (most monitors check this for you).

## When an alert arrives
Follow [incident.md](incident.md).

## First real deploy
Also run `pip freeze | grep -i gunicorn` once: the pin `gunicorn==23.0.0` was never installed in the build sandbox (see [rollback.md](rollback.md) section 3).
