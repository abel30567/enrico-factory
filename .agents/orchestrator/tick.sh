#!/usr/bin/env bash
# tick.sh — the MECHANICAL heartbeat: one `tick.py --apply`, appended to $STATE/logs/tick.log.
# Run every 10 min by the LaunchAgent that install-tick.sh installs. ORCHESTRATOR ONLY.
# No model and no judgement here: tick.py launches builders, verifiers, fixers and blast-radius reviews
# from GitHub's labels and markers, runs the merge gate and syncs the board; whatever needs judgement
# it prints as NEED lines for the orchestrator's wake.
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/config.sh" || exit 1
LOG="$STATE/logs/tick.log"
mkdir -p "$STATE/logs" "$STATE/state"
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 5000000 ] && mv -f "$LOG" "$LOG.1"
{
  echo "=== $(date -u +%FT%TZ) tick ($REPO)"
  python3 -I "$D/tick.py" --apply
  echo "=== exit $?"
} >>"$LOG" 2>&1
