# Deploy runbook (C23)

For one small server (a VPS or a host that runs a Python process) with a persistent disk. Commands run from the project folder.

## 1. First deploy
1. Python 3.12. `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
   (this installs gunicorn too; the tests and PDF build need `requirements-dev.txt`, a server does not).
2. Set the variables (see `.env.example`; Flask does not read the file itself, so `export` them or load them in your service manager):
   - `APP_ENV=production`
   - `SECRET_KEY` = 32+ random characters: `python3 -c "import secrets; print(secrets.token_hex(32))"`. Keep it; changing it logs everyone out.
   - `DATABASE_PATH` = a file on the persistent disk, e.g. `/var/lib/campus-barter/campus_barter.db`. Photos live in a `photos` folder next to it.
   - `TRUSTED_PROXIES=1` only when one reverse proxy (nginx, a load balancer) is in front; leave it empty otherwise.
3. `flask --app app migrate`  (a NEW database must be migrated before the first start; run it again after every update that adds a migration).
4. `flask --app app create-admin` (asks for the password; never put it on the command line). Do NOT run `seed` in production (it refuses).
5. `./start.sh` (gunicorn on 127.0.0.1:8000, 2 workers; `BIND` and `WEB_WORKERS` change that). Put it under systemd or your host's process manager so it restarts.
6. Put HTTPS in front (nginx/Caddy or the host's TLS). Session cookies are `Secure` in production, so logins do not work over plain http.
7. Check: `GET /health` answers 200; `/design` answers 404 (it does not exist in production).

The app refuses to start (and says why) when `SECRET_KEY` is missing, short or a placeholder, when `FLASK_DEBUG` is on, or when `TRUSTED_PROXIES` is not 0-5.

## 2. Behind a proxy
Pass the visitor's address: nginx `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` and `proxy_set_header X-Forwarded-Proto $scheme;`, then `TRUSTED_PROXIES=1`.
With 0 the app sees only the proxy's address, so one visitor's wrong passwords would lock login for everyone (docs/security/review-1.md R2). Never set it above the real number of proxies.

## 3. Backups
`python3 scripts/backup.py backup --to /var/backups/campus-barter`
- Makes `campus-barter-backup-<UTC time>/` with `campus_barter.db` (consistent, safe while the app runs, integrity-checked) and `photos/`.
- Uses `DATABASE_PATH` unless `--db` is given. Daily by cron, e.g. `15 2 * * * cd /srv/campus-barter && . .venv/bin/activate && python3 scripts/backup.py backup --to /var/backups/campus-barter`.
- Nothing prunes old backups: add `find /var/backups/campus-barter -maxdepth 1 -name 'campus-barter-backup-*' -mtime +14 -exec rm -r {} +` to the cron job (14 days is a suggestion).
- Copy backups to a second place (another disk or storage); a backup on the same disk does not survive the disk.

## 4. Restore
1. Stop the app.
2. `python3 scripts/backup.py restore /var/backups/campus-barter/campus-barter-backup-<time>`
   It checks the backup first and refuses a damaged one without touching live data. Then it renames the live database (and any `-wal` / `-shm` file) and the `photos` folder to `*.before-restore-<time>`, and copies the backup in.
3. Start the app, check `/health`, log in, open a listing with a photo.
4. Delete the `*.before-restore-*` files once you are sure.
Test the restore on a spare folder (`--db /tmp/x/test.db`) once a month; a backup nobody has restored is only a hope.

## 5. Update
Stop (or keep running; migrations are additive), pull the new code, `pip install -r requirements.txt`, `flask --app app migrate`, restart, check `/health`. Back up first.

## 6. See also (C24)
[backups.md](backups.md) (routine and monthly drill), [rollback.md](rollback.md), [incident.md](incident.md), [monitoring.md](monitoring.md) (uptime monitor on `/health`).
