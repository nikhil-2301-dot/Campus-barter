# Rollback runbook (C24)

Use this when an update made things worse and you want the old version back. Updating is described in [deploy.md](deploy.md) section 5. Backup and restore commands are not repeated here.

## 0. Before every update (so a rollback is possible)
- Back up (deploy.md section 3) and note the folder name.
- Write down the current code version: `cat VERSION` and `git rev-parse --short HEAD` (or the name of the zip you deployed).
- Write down the current schema version: `curl -s http://127.0.0.1:8000/health` shows `schema_version`.

## 1. Pick the case
**A. The new code has a bug, and the update added no migration** (`schema_version` is the same before and after)
1. Stop the app.
2. Put the old code back: `git checkout <old commit>` or unzip the old release over the folder.
3. `pip install -r requirements.txt` (the old pins), then start the app.
4. Check `/health` is 200 and `"status":"ok"`.
No data is touched, so no restore is needed.

**B. The update added a migration** (`schema_version` went up)
Migrations are additive and are never undone by hand. The old code usually still runs on the newer database, but do not assume it.
1. Stop the app.
2. Restore the backup from step 0 (deploy.md section 4). Everything written after that backup is lost: say so to the users.
3. Put the old code back as in case A.
4. Start the app. `/health` must show the OLD `schema_version` and `pending_migrations` 0.
If the new code stays and only the data is wrong, restore as in step 2 and then run `flask --app app migrate` again after the fix.

**C. The app does not start after an update**
The message on screen says why (missing or short `SECRET_KEY`, debug mode on, bad `TRUSTED_PROXIES`, deploy.md section 1). Fix that first. If you cannot, use case A.

## 2. After a rollback
- `GET /health` is 200, log in, open a listing with a photo.
- Keep the `*.before-restore-*` files for a few days (deploy.md section 4 step 4), then delete them.
- Write what happened and what you will change in the incident note ([incident.md](incident.md)).

## 3. Not verified yet
`gunicorn==23.0.0` in requirements.txt was never installed in the build sandbox. On the first real deploy run `pip freeze | grep -i gunicorn` and correct the pin if it differs. Do this before you rely on case A, so the old pins install cleanly.
