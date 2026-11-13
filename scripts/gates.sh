#!/usr/bin/env bash
# The gates every worker runs. Standard library only.
# The test files run side by side, GATES_JOBS at a time (default: one per CPU). Each file is its own
# process, as with `unittest discover` on that one file. A failing file prints its whole output.
set -euo pipefail
cd "$(dirname "$0")/.."
# kit/evals holds fixture trees: a compiled scene.py would leave a .pyc that the size test of the suites reads.
if [ -d kit ]; then python3 -m compileall -q -x 'kit/evals/' kit; fi
if ls tests/test_*.py >/dev/null 2>&1; then
  jobs="${GATES_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
  logs="$(mktemp -d)"
  trap 'rm -rf "$logs"' EXIT
  : > "$logs/_pass"; : > "$logs/_fail"
  start=$(date +%s)
  run='m=$(basename "$0")
    if python3 -m unittest discover -s tests -p "$m" > "$LOGS/$m.log" 2>&1; then echo "$m" >> "$LOGS/_pass"; else echo "$m" >> "$LOGS/_fail"; fi'
  # tests/test_lint.py writes a temporary check into kit/lib/lints/, where every other lint test would find
  # it and then lose it, so that file runs alone first.
  alone="tests/test_lint.py"
  if [ -f "$alone" ]; then LOGS="$logs" sh -c "$run" "$alone" || true; fi
  ls tests/test_*.py | grep -vxF "$alone" | LOGS="$logs" xargs -P "$jobs" -n 1 sh -c "$run" || true
  ran=$(cat "$logs"/test_*.log | awk '/^Ran [0-9]+ tests? in /{s+=$2} END{print s+0}')
  files=$(ls tests/test_*.py | wc -l | tr -d ' ')
  done_files=$(cat "$logs/_pass" "$logs/_fail" | wc -l | tr -d ' ')
  if [ -s "$logs/_fail" ] || [ "$done_files" != "$files" ]; then
    for m in $(sort "$logs/_fail"); do echo "=== $m"; cat "$logs/$m.log"; done
    echo "Ran $ran tests in $files files, $(( $(date +%s) - start )) s, $jobs at a time"
    echo "GATES FAILED: $(sort "$logs/_fail" | tr '\n' ' ')($done_files of $files files finished)"
    exit 1
  fi
  echo "Ran $ran tests in $files files, $(( $(date +%s) - start )) s, $jobs at a time"
fi
echo "GATES OK"
