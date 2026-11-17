#!/usr/bin/env bash
# Pack the whole project into one zip for transfer to another Claude account.
set -e
cd "$(dirname "$0")/.."
OUT="../campus-barter-handoff.zip"
rm -f "$OUT"
python3 - <<'PY'
import os, zipfile
root = "."
skip_dirs = {"__pycache__", ".git", "venv", ".venv", "instance"}
with zipfile.ZipFile("../campus-barter-handoff.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip_dirs]
        for f in files:
            if f.endswith((".pyc", ".db", ".db-wal", ".db-shm")) or f == ".env":
                continue
            p = os.path.join(d, f)
            z.write(p, os.path.join("campus-barter", os.path.relpath(p, root)))
print("packed ->", os.path.abspath("../campus-barter-handoff.zip"))
PY
