# Backups runbook (C24)

The commands, the cron line and the restore steps are in [deploy.md](deploy.md), sections 3 (Backups) and 4 (Restore). This page is the routine around them.

## Daily
- Look at the newest folder in your backup directory: its date is today or yesterday, and it holds `campus_barter.db` and `photos/`.
- The cron job from deploy.md section 3 prints a problem when the backup cannot be made; send its output to a mail address or a log you read.

## Weekly
- Check that the copy in the second place (other disk or storage) has the same newest folder.
- Look at the free disk space: `df -h` on the data disk and on the backup disk. Backups plus photos grow slowly, a full disk stops the app.

## Monthly: the restore drill
1. Pick the newest backup folder.
2. Restore it into a spare place, never over live data: `python3 scripts/backup.py restore <folder>` with `DATABASE_PATH` pointing at a scratch file (see deploy.md section 4, last paragraph).
3. Start a second copy of the app on another port with that scratch `DATABASE_PATH`, open `/health` (answers 200 and `"status":"ok"`), log in, open a listing that has a photo.
4. Delete the scratch copy. Write the date of the drill in your own notes.

## Before risky work
Take a fresh backup first: an update with a migration, a restore, or any manual change to the database. Rollback steps: [rollback.md](rollback.md).

## When the backup itself is damaged
`restore` checks a backup before touching anything and refuses a damaged one. Try the next older folder, then the copy in the second place. Tell the users which date they are back to (see [incident.md](incident.md)).
