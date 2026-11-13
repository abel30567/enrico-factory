#!/usr/bin/env bash
# install-tick.sh [--uninstall] — install (or remove) the mechanical heartbeat as a user LaunchAgent that
# runs tick.sh every 10 minutes and survives reboots. ORCHESTRATOR ONLY.
# launchd starts jobs with a bare environment, so the PATH of the tools a tick needs is pinned here.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/config.sh"
LABEL="com.$REPO_NAME.orch-tick"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then rm -f "$PLIST"; echo "removed $LABEL"; exit 0; fi

dirs=""
for t in node python3 git gh; do
  p=$(command -v "$t") || { echo "install-tick: $t not found on PATH"; exit 1; }
  d=$(dirname "$p"); case ":$dirs:" in *":$d:"*) ;; *) dirs="${dirs:+$dirs:}$d" ;; esac
done
PATHV="$dirs:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# ProcessType=Interactive: workers inherit the job's class, and Background or Standard throttles them.
mkdir -p "$(dirname "$PLIST")"
cat >"$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$D/tick.sh</string></array>
  <key>StartInterval</key><integer>600</integer>
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
echo "installed $LABEL (every 10 min; log: $STATE/logs/tick.log)"
