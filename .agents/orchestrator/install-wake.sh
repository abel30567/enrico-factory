#!/usr/bin/env bash
# install-wake.sh [--uninstall] — install (or remove) the judgement heartbeat as a user LaunchAgent that
# runs wake.sh every WAKE_INTERVAL seconds (default 900) and survives reboots. ORCHESTRATOR ONLY.
# launchd starts jobs with a bare environment, so the PATH of the tools a wake needs is pinned here.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/config.sh"
LABEL="com.$REPO_NAME.orch-wake"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then rm -f "$PLIST"; echo "removed $LABEL"; exit 0; fi
# macOS privacy protection: a launchd job cannot read ~/Desktop, ~/Documents or ~/Downloads ("Operation not permitted")
case "$D" in "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
  echo "install-wake: launchd cannot read $D. Clone the repository outside Desktop, Documents and Downloads"
  echo "  (for example ~/orch/$REPO_NAME), copy local.env into it, and run 'orch wake' from that checkout."; exit 1 ;;
esac

[ -f "$D/prompts/wake.md" ] || { echo "install-wake: prompts/wake.md is missing"; exit 1; }
command -v ${WAKE_CMD:-claude} >/dev/null || { echo "install-wake: WAKE_CMD (${WAKE_CMD:-claude}) not found; set it in local.env"; exit 1; }
dirs=""
for t in node python3 git gh; do
  p=$(command -v "$t") || { echo "install-wake: $t not found on PATH"; exit 1; }
  d=$(dirname "$p"); case ":$dirs:" in *":$d:"*) ;; *) dirs="${dirs:+$dirs:}$d" ;; esac
done
PATHV="$dirs:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# ProcessType=Interactive: whatever the session starts inherits the job's class, and Background or Standard throttles it.
mkdir -p "$(dirname "$PLIST")"
cat >"$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$D/wake.sh</string></array>
  <key>StartInterval</key><integer>${WAKE_INTERVAL:-900}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>AbandonProcessGroup</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHV</string><key>HOME</key><string>$HOME</string></dict>
  <key>StandardOutPath</key><string>/tmp/$LABEL.out</string>
  <key>StandardErrorPath</key><string>/tmp/$LABEL.err</string>
</dict>
</plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "installed $LABEL (every ${WAKE_INTERVAL:-900} s; log: $STATE/logs/wake.log)"
