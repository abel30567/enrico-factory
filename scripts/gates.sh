#!/usr/bin/env bash
# The gates every worker runs. Standard library only.
# The test files run side by side, GATES_JOBS at a time (default: one per CPU). Each file is its own
# process, as with `unittest discover` on that one file. A failing file prints its whole output.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d kit ]; then python3 -m compileall -q kit; fi
if ls tests/test_*.py >/dev/null 2>&1; then
  jobs="${GATES_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
  logs="$(mktemp -d)"
  trap 'rm -rf "$logs"' EXIT
  : > "$logs/_pass"; : > "$logs/_fail"
  start=$(date +%s)
  ls tests/test_*.py | LOGS="$logs" xargs -P "$jobs" -n 1 sh -c '
    m=$(basename "$0")
    if python3 -m unittest discover -s tests -p "$m" > "$LOGS/$m.log" 2>&1; then echo "$m" >> "$LOGS/_pass"; else echo "$m" >> "$LOGS/_fail"; fi
  ' || true
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
