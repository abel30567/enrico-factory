#!/usr/bin/env python3
# adapters/fleet.py PROMPT_FILE — run one orchestrator worker on a Fermi fleet box (WORKER_KIND=custom).
#
# launch.sh calls this inside the local worktree it made, with ORCH_ROLE, ORCH_KEY, ORCH_PR, ORCH_BASE and
# ORCH_MODEL set. The box does the work in its own clone; the local worktree is only bookkeeping. This
# process launches the box (POST /admin/fleet/launch) and then waits for it, so it IS the local worker:
# its pid file, its log (one line each time the box reports progress) and its run JSON behave as a local
# worker's do, and tick.py needs no fleet-specific code beyond the usage-limit switch.
#
#   config: ~/.config/agent-orch/fermi.env (mode 600): FERMI_URL, FERMI_BEARER_TOKEN, and optionally
#           FLEET_ACCOUNT (default tars), FLEET_INSTANCE (default t3.medium)
#   stdout: one JSON object {"type":"result","is_error":…,"result":…,"fleet":{…}}
#   stderr: progress lines
#   --dry-run: print the box prompt and the launch fields, launch nothing
import json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

TTL = {"builder": 7200, "fixer": 5400, "verifier": 3600, "br": 3600}
POLL_S = 45
BOOT_S = 600   # a box boots in a minute or two
CA = "/etc/ssl/cert.pem"   # the python.org build on macOS ships no CA bundle; the system one is here
CTX = ssl.create_default_context(cafile=CA if os.path.exists(CA) else None)
NARROW = """SCOPE (binding). You are a reviewer with a narrow job. Do exactly the numbered steps of the prompt and nothing
else: do not fix or edit code, do not push, do not open or edit issues, do not review anything outside this pull
request's diff and its acceptance criteria, and post exactly ONE comment. Aim to finish within 40 minutes. If you
cannot finish, do not post a verdict you have not proven: stop and say what is missing in your final message."""
WIDE = """SCOPE (binding). Build exactly what the TASK asks and nothing else. Push work in progress to your branch at least
every 15 minutes (`git push -u origin HEAD`): a restarted box resumes from what is pushed."""


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()


def conf():
    c = {}
    for line in (Path.home() / ".config/agent-orch/fermi.env").read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z_]+)=(.*)$", line)
        if m:
            c[m.group(1)] = m.group(2).strip().strip("'\"")
    return c


def call(c, method, path, body=None, timeout=60):
    req = urllib.request.Request(c["FERMI_URL"].rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + c["FERMI_BEARER_TOKEN"],
                                          "content-type": "application/json", "user-agent": "orch-fleet-adapter"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:   # the launch endpoint answers 409 with a JSON reason
        try:
            return json.loads(e.read().decode())
        except ValueError:
            return {"ok": False, "error": f"http {e.code}"}


def box_prompt(prompt, role, base):
    wt = os.getcwd()
    orch = Path(__file__).resolve().parents[1]
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    repo_dir = common.parent
    state = Path.home() / ".agent-orch" / repo_dir.name
    gh_repo = re.sub(r"^(git@[^:]+:|ssh://git@[^/]+/|https?://[^/]+/)|\.git$", "", git("remote", "get-url", "origin"))
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if role == "fixer":   # the local branch may be an alias; the PR branch is its upstream
        branch = git("rev-parse", "--abbrev-ref", "@{u}").removeprefix("origin/") or branch
    gates = subprocess.run(["bash", "-c", f'. "{orch}/config.sh" >/dev/null 2>&1; printf %s "$GATES"'],
                           capture_output=True, text=True).stdout.strip() or "bash scripts/gates.sh"
    gates_q = "'" + gates.replace("'", "'\\''") + "'"
    for local, on_box in ((wt, "$CLONE"), (str(orch), "$CLONE/.agents/orchestrator"), (str(state), "/tmp/orch-state")):
        prompt = prompt.replace(local, on_box)
    checkout = {"builder": f"git checkout -q -B {branch} origin/{branch} 2>/dev/null || git checkout -q -b {branch} origin/{base}",
                "fixer": f"git checkout -B {branch} origin/{branch}"}.get(role, f"git checkout --detach origin/{base}")
    pre = f"""You are running on a disposable cloud box (Linux, 2 CPUs), not on the orchestrator's machine. The repository
{gh_repo} is already cloned in your starting directory. The prompt below the line was written for a local worktree.
On this box:
- `$CLONE` is that clone. Run SETUP first; wherever the prompt says `$CLONE`, use that directory.
- `git push` and `gh` use the GitHub credentials already configured on the box. If `gh` is missing or not logged
  in, export GH_TOKEN from the GITHUB_TOKEN in your environment. Never print a token.
- There is no dev server and no port. Use `python3`. Run the gates one at a time, never in parallel.
- A rule about "the orchestrator's machine" (reserved ports, logged-in browsers, shared containers) has nothing to
  act on here. Every other rule holds unchanged: never merge, never push to a protected branch, never post
  another role's marker.

SETUP (run once, before anything else; the baseline run takes about five minutes):
```bash
CLONE=$(git rev-parse --show-toplevel) && cd "$CLONE" && git fetch -q origin
mkdir -p /tmp/orch-state && cat > /tmp/orch-state/box-gates.sh <<'BOX_GATES_EOF'
#!/usr/bin/env bash
# box-gates.sh [OUT]: run the gates as this box needs them and write the sorted ids of the failing tests to OUT.
out=${{1:-/tmp/orch-state/gates-now.txt}}
env -u GIT_AUTHOR_NAME -u GIT_AUTHOR_EMAIL -u GIT_COMMITTER_NAME -u GIT_COMMITTER_EMAIL \\
  bash -c {gates_q} > /tmp/orch-state/gates.log 2>&1
rc=$?
grep -oE '^(FAIL|ERROR): [^ ]+ \\([^)]*\\)' /tmp/orch-state/gates.log | sort -u > "$out"
echo "gates exit=$rc; $(wc -l < "$out") failing ids in $out; $(grep -E '^Ran [0-9]+ tests' /tmp/orch-state/gates.log | tail -1)"
BOX_GATES_EOF
git checkout -q --detach origin/{base} && bash /tmp/orch-state/box-gates.sh /tmp/orch-state/baseline.txt
{checkout}
```

GATES ON THIS BOX. The box runs as root on Linux with a preset git identity, so a few tests fail here that pass on
the orchestrator's macOS machine. `/tmp/orch-state/baseline.txt` (written by SETUP on `origin/{base}`) lists them.
- Wherever the prompt tells you to run the gates (`{gates}`), run `bash /tmp/orch-state/box-gates.sh` instead, one
  run at a time, then `diff /tmp/orch-state/baseline.txt /tmp/orch-state/gates-now.txt`. The full output is in
  `/tmp/orch-state/gates.log`. A failing id that is not in the baseline is a finding. Never edit a test or the
  code to make a baseline failure pass, and never report a baseline failure as a finding.
- The check named `gates` on the pull request (GitHub Actions, macOS) is the gate run that counts for the merge.
  Once the pull request exists, read it with `gh pr checks <PR>` (wait up to 20 minutes for it to finish) and
  report its state in your marker comment. A failing `gates` check is a finding even if the box run is clean.

{WIDE if role in ("builder", "fixer") else NARROW}

FINISH. After your marker comment is posted (or after you stop for a reason the prompt allows), write
`/tmp/result.md` holding the first line of the comment you posted (or `NO MARKER` and why), the pull request
number and the full head SHA, and upload it as the artifact named `result.md`. Your final message is the same text.

--------------------------------------------------------------------------------------------------------------
"""
    return pre + prompt, gh_repo


def main():
    dry = "--dry-run" in sys.argv
    pfile = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    role, base = os.environ.get("ORCH_ROLE", "builder"), os.environ.get("ORCH_BASE", "main")
    wid = os.environ.get("ORCH_KEY", "?") + (f" PR#{os.environ['ORCH_PR']}" if os.environ.get("ORCH_PR") else "")
    prompt, gh_repo = box_prompt(Path(pfile).read_text(), role, base)
    c = conf() if not dry else {}
    account = c.get("FLEET_ACCOUNT", "tars")
    ttl = TTL.get(role, 3600)
    fields = {"prompt": prompt, "proof_contract": json.dumps({"kind": "artifact", "name": "result.md", "min_bytes": 20}),
              "route": "claude", "account": account, "model": os.environ.get("ORCH_MODEL") or None,
              "instance_type": c.get("FLEET_INSTANCE", "t3.medium"), "repo": gh_repo, "branch": base,
              "budget_usd": 0.5, "ttl_seconds": ttl}
    if dry:
        print(prompt); print(json.dumps({k: v for k, v in fields.items() if k != "prompt"}, indent=1)); return 0
    t0 = time.time()

    def finish(is_error, text, agent=None, turns=0):
        a = agent or {}
        print(json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text, "num_turns": turns,
                          "duration_ms": int((time.time() - t0) * 1000),
                          "fleet": {k: a.get(k) for k in ("id", "box_id", "status", "exit_reason", "account", "instance_type",
                                                          "cost_usd", "inference_usd", "restart_count")}}))
        return 1 if is_error else 0

    try:
        r = call(c, "POST", "/admin/fleet/launch", fields)
    except (urllib.error.URLError, OSError, ValueError) as e:
        r = {"ok": False, "error": str(e)[:200]}
    if not r.get("ok"):
        return finish(True, f"fleet launch refused: {json.dumps(r)[:400]}")
    aid = r["agent_id"]
    print(f"launched {aid} for {role} {wid} (model {fields['model']}, account {account}, ttl {ttl}s)", file=sys.stderr, flush=True)
    seen, turns, agent, misses = None, 0, {}, 0
    booted_by, relaunches = time.time() + BOOT_S, 0
    while True:
        try:
            d = call(c, "GET", f"/admin/fleet/status?id={aid}", timeout=30)
            agent = d.get("agent") or agent
            misses = 0
        except (urllib.error.URLError, OSError, ValueError) as e:
            misses += 1
            if misses % 10 == 1:
                print(f"status read failed ({misses}): {str(e)[:120]}", file=sys.stderr, flush=True)
            d = {}
        if d and seen is None and agent.get("account") != account:
            return finish(True, f"fleet box {aid} booted on account {agent.get('account')!r}, not {account!r}: DESTROY IT "
                                f"(cloud_agent_destroy) and do not relaunch through this path", agent)
        note = next((e.get("note", "") for e in d.get("events", []) if "turns=" in e.get("note", "")), "")
        m = re.search(r"turns=(\d+)", note)
        turns = int(m.group(1)) if m else turns
        mark = (agent.get("status"), agent.get("last_working_event_at"), agent.get("restart_count"))
        if mark != seen and d:
            seen = mark
            print(f"{time.strftime('%H:%M:%S')} {agent.get('status')} {note or 'no progress note yet'} "
                  f"restarts={agent.get('restart_count')}", file=sys.stderr, flush=True)
        # A box that never leaves "launching" (seen 2026-11-13: 27 min) is replaced, twice at most. The reaper
        # ends the stuck one at its TTL; it never booted, so it runs nothing.
        if agent.get("status") == "launching" and time.time() > booted_by:
            if relaunches == 2:
                return finish(True, f"fleet box {aid} and two replacements never booted", agent)
            try:
                r = call(c, "POST", "/admin/fleet/launch", fields)
            except (urllib.error.URLError, OSError, ValueError) as e:
                r = {"ok": False, "error": str(e)[:200]}
            if not r.get("ok"):
                return finish(True, f"fleet box {aid} never booted and its replacement was refused: {json.dumps(r)[:300]}", agent)
            relaunches += 1
            print(f"{aid} still launching after {BOOT_S}s: replaced by {r['agent_id']} ({relaunches} of 2)", file=sys.stderr, flush=True)
            aid, seen, agent, booted_by = r["agent_id"], None, {}, time.time() + BOOT_S
            continue
        if agent.get("status") in ("done", "failed", "destroyed"):
            task = (d.get("tasks") or [{}])[0]
            text = str(task.get("result") or agent.get("exit_reason") or agent.get("status"))
            bad = agent["status"] != "done"
            return finish(bad, (f"fleet box {agent['status']}: {agent.get('exit_reason')}: " if bad else "") + text, agent, turns)
        if time.time() - t0 > ttl + 1200:   # the reaper ends a box at its TTL; this is only a backstop
            return finish(True, f"fleet box {aid} still {agent.get('status')} 20 min past its TTL", agent, turns)
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
