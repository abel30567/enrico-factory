#!/usr/bin/env bash
# wake.sh — the JUDGEMENT heartbeat: one fresh headless orchestrator session per run. ORCHESTRATOR ONLY.
# Run every 15 min by the LaunchAgent that install-wake.sh installs. It replaces a chat app's scheduled task:
# a chat thread that is busy or stuck queues its wakes, and the loop stops until somebody types. Here every
# run is its own process. launchd never starts a run while the last one is alive, a run older than
# WAKE_MAX_MIN is killed, and nothing carries over between runs but GitHub and the files the prompt names.
#   prompt  prompts/wake.md, then $STATE/wake-extra.md if it exists (this machine's standing orders)
#   log     $STATE/logs/wake.log: one block per run, ending with the session's answer. Full output: logs/wake/
#   config  WAKE_CMD, WAKE_MODEL, WAKE_MAX_MIN in local.env
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/config.sh" || exit 1
WAKE_CMD="${WAKE_CMD:-claude}"; WAKE_MODEL="${WAKE_MODEL:-opus}"; WAKE_MAX_MIN="${WAKE_MAX_MIN:-50}"
LOG="$STATE/logs/wake.log"; OUT="$STATE/logs/wake"; LOCK="$STATE/state/wake.pid"
mkdir -p "$OUT" "$STATE/state"
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 5000000 ] && mv -f "$LOG" "$LOG.1"
say() { echo "$(date -u +%FT%TZ) $*" >>"$LOG"; }

[ -f "$STATE/FREEZE" ] && { say "skip: FREEZE in effect"; exit 0; }
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  say "skip: a wake is already running (pid $(cat "$LOCK"))"; exit 0
fi
echo $$ >"$LOCK"; trap 'rm -f "$LOCK"' EXIT

ts=$(date -u +%Y%m%dT%H%M%SZ); J="$OUT/$ts.json"; E="$OUT/$ts.err"
cd "$REPO" || exit 1
say "=== start ($WAKE_MODEL, limit $WAKE_MAX_MIN min)"
{ cat "$D/prompts/wake.md"; [ -f "$STATE/wake-extra.md" ] && cat "$STATE/wake-extra.md"; } \
  | $WAKE_CMD -p --permission-mode bypassPermissions --model "$WAKE_MODEL" --output-format json >"$J" 2>"$E" &
pid=$!
end=$(( $(date +%s) + WAKE_MAX_MIN * 60 )); killed=""
while kill -0 "$pid" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$end" ]; then
    killed=1; kill "$pid" 2>/dev/null; sleep 10; kill -9 "$pid" 2>/dev/null; break
  fi
  sleep 5
done
wait "$pid"; rc=$?

# One summary block; exit 0 only for a run that ended by itself with a result that is not an error.
python3 -I - "$J" "$E" "$rc" "$killed" >>"$LOG" 2>&1 <<'PY'
import datetime, json, sys
j, e, rc, killed = sys.argv[1:5]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
try:
    d = json.loads(open(j).read().strip().splitlines()[-1])
except (OSError, ValueError, IndexError):
    d = None
ok = bool(d) and not d.get("is_error") and rc == "0" and not killed
head = "KILLED at the time limit" if killed else ("end" if ok else "FAILED")
if d:
    print(f"{now} === {head} rc={rc} turns={d.get('num_turns')} {round((d.get('duration_ms') or 0) / 60000)} min")
    print(str(d.get("result", "")).strip()[:4000])
else:
    print(f"{now} === {head} rc={rc} no result")
    try:
        print(open(e).read().strip()[-1500:])
    except OSError:
        pass
sys.exit(0 if ok else 1)
PY
ok=$?
ls -t "$OUT" | tail -n +401 | while read -r f; do rm -f "$OUT/$f"; done
if [ "$ok" -ne 0 ] && command -v osascript >/dev/null; then
  osascript -e "display notification \"See $LOG\" with title \"$REPO_NAME orchestrator wake failed\"" >/dev/null 2>&1
fi
exit "$ok"
