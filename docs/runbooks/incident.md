# Incident runbook (C24)

For "the site is down or behaving badly". Work top to bottom and stop when it is fixed. Monitoring setup: [monitoring.md](monitoring.md).

## 1. First five minutes
1. Confirm it: open `https://<your domain>/health` from outside.
   - 200 and `"status":"ok"`: the app is fine, look at the proxy, DNS or the user's network.
   - 503: read the `db` field. `unavailable` = the database file cannot be opened. `not migrated` or `migrations pending` = run `flask --app app migrate` (take a backup first).
   - No answer: the app or the proxy is down (step 2).
2. Tell the users in one line where you post notices (a pinned message, the college group). Say what you know, not what you guess.

## 2. Where to look
- App process: your service manager (for systemd: `systemctl status campus-barter`, `journalctl -u campus-barter -n 200`). Restart: `systemctl restart campus-barter`.
- App log: JSON lines on stdout, one per request, with a request id (`X-Request-ID` header; the 500 page shows it). Search by that id to follow one request. Passwords and tokens are never logged.
- Proxy log (nginx or the host's): shows requests that never reached the app.
- Disk: `df -h` on the data disk. A full disk stops the database and photo uploads.
- Database file: `python3 -c "import sqlite3,os; c=sqlite3.connect(os.environ['DATABASE_PATH']); print(c.execute('PRAGMA integrity_check').fetchone())"` should print `('ok',)`.

## 3. Common cases
| What you see | Likely cause | Do |
|---|---|---|
| App refuses to start | Settings (deploy.md section 1) | Read the message, fix the variable |
| 503 on /health, `unavailable` | Wrong `DATABASE_PATH`, disk full, file permissions | Fix path, free space, check the owner of the folder |
| 503, `migrations pending` | Update done without `migrate` | Back up, `flask --app app migrate`, restart |
| Everyone gets "too many attempts" on login | `TRUSTED_PROXIES` is 0 behind a proxy (deploy.md section 2) | Set it to the number of proxies, restart |
| Everyone was logged out | `SECRET_KEY` changed | Expected; put the old key back if it was a mistake |
| Slow pages | Disk full, or too few workers | Free space; raise `WEB_WORKERS` in start.sh settings |
| Data wrong or lost | Bad update or damaged file | [rollback.md](rollback.md) case B, [backups.md](backups.md) |

## 4. Damaged database
1. Stop the app. Copy the damaged file and its `-wal` and `-shm` files somewhere safe (do not delete them).
2. Restore the newest good backup (deploy.md section 4).
3. Start the app, check `/health`, log in, open a listing with a photo.
4. Tell the users which date the data goes back to.

## 5. After it is fixed
Write a short note (date, what the users saw, cause, fix, what changes so it does not repeat). Keep it next to your backups.
