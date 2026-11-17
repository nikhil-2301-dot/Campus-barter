#!/usr/bin/env bash
# Run every automated check. Fails on any error, failing test or SKIPPED test.
set -e
cd "$(dirname "$0")/.."
echo "== 1/2 compile all python =="
python3 -m compileall -q app tests
echo "== 2/2 unit + integration tests =="
python3 -m unittest discover -s tests -v 2>&1 | tee /tmp/cb_test.log
if grep -qiE "skipped|FAILED|ERROR" /tmp/cb_test.log; then
  echo "CHECK FAILED: a test failed, errored or was skipped"; exit 1
fi
echo "ALL CHECKS PASSED"
