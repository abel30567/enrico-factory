# .agents/orchestrator: tooling reference

`orch help` lists every command. The pattern is described in the kit's docs (`docs/2-the-pattern.md`);
the rules for agents are in your repo's CLAUDE.md / AGENTS.md (from `templates/AGENT-RULES.md`).

| File | Purpose |
|---|---|
| `orch` | Single entry point. Dispatches to the scripts below. |
| `config.sh`, `conf.py` | Load defaults, then `orch.env` (project, committed), then `local.env` (personal, gitignored). |
| `launch.sh` | Creates the worktree, renders `prompts/<role>.md`, starts one detached worker. |
| `register-prompt.py` | Posts each worker's exact prompt as an `[ORCHESTRATOR] LAUNCH` comment on its issue or PR before it starts. |
| `detach.py` | Starts a worker in a new session so it survives the launching shell. |
| `merge-gate.py` | Merges at most one PR per run, and only when every rule holds. |
| `risk.py` | Decides whether a PR needs a blast-radius review (`risk-patterns.txt`). |
| `heartbeat.py` | `orch status`: workers, each PR's markers and next step, disk. |
| `tick.py` | `orch tick`: the state machine. Launches the next worker for each PR and each `status:ready` issue, runs the merge gate, syncs the board. No model. |
| `tick.sh`, `install-tick.sh` | `orch heartbeat`: a LaunchAgent that runs `tick.py --apply` every 10 minutes. Log: `logs/tick.log`. |
| `wake.sh`, `install-wake.sh`, `prompts/wake.md` | `orch wake`: a LaunchAgent that runs a fresh headless orchestrator session every 15 minutes, for what needs judgement. No chat app is involved, so a busy or stuck chat thread cannot stop it. Log: `logs/wake.log`. A failed run shows a notification and runs `WAKE_ALERT_CMD`. |
| `prompts/wake-project.md` | Optional, yours: this project's standing orders for the wake (where the spec and the plan are, what the orchestrator may write). Appended to `prompts/wake.md`. |
| `wakekick.py` | Lets the tick start the wake at once when it prints a NEED line the previous tick did not, instead of waiting for the timer (tests alongside). |
| `board.py` | Kanban derived from markers: secret gist (`gist`) or GitHub Project (`setup` / `sync`). |
| `baseline.sh` | Runs GATES on a clean base and records `baseline.txt`. |
| `task.sh` | Creates a tracked issue (`ISSUE-<n>` key, or your own key). |
| `doctor.sh` | Checks tools, login, config, labels, branch protection. |
| `cleanup-idle.sh` | Removes finished worktrees whose work is pushed. |
| `usage.py` | Token, minute and dollar ledger from worker results. `--post` (every tick) puts each finished run on its issue or PR as an `[ORCHESTRATOR] USAGE` comment. |
| `hooks/worker-guard.mjs` | PreToolUse hook that blocks forbidden worker commands (tests alongside). |
| `tracker/jira-sync.py` | Optional one-way Jira → GitHub issue mirror. |
| `adapters/` | Run non-Claude agent CLIs as workers. |

Run state lives in `~/.agent-orch/<repo>/` and is never committed:
- `runs/`: worker JSON results and rendered prompts.
- `logs/`: worker logs.
- `state/`: pids and ports, `transitions.log` (every state change the tick saw), `attempts.json`,
  `wake.pending` (new NEED lines the next wake starts with).
- `wake-notes.md`: what each wake did, read by the next. `wake-extra.md`: optional orders for this machine only.
- `inputs/<KEY>/`: files you hand to workers.
- `baseline.txt`, `launches.tsv` and `FREEZE`.

Worktrees live in `../<repo>-wt/` (`<KEY>`, `V<PR>`, `BR<PR>`).
