#!/usr/bin/env python3
# tick.py [--apply] — one MECHANICAL heartbeat of the development loop. ORCHESTRATOR ONLY. No model runs here.
# This is the dev loop's tick (builders, verifiers, fixers of this repository). The product's own tick is
# `factory tick` under kit/.
# GitHub is the state: every launch is decided from labels, markers and pid files, never from a chat session.
# Idempotent: a step starts only when no live worker owns it, so two ticks in a row never launch it twice.
# Without --apply it only prints the plan. Order of a tick:
#   0. FREEZE -> report only. `git pull --ff-only` the orchestrator checkout. Resource guards (load, memory,
#      workers alive across every repo, a usage-limit rejection) are preconditions on every launch: a failed
#      guard prints one `WAIT guard:` line, starts nothing and never touches running work.
#   1. Each open [KEY] PR gets one state (BUILDING BLOCKED VERIFYING FIXING BLAST_RADIUS MERGEABLE STALLED):
#        builder READY/ADDRESSED, verifier NONE/STALE  -> orch launch verifier PR "<issue body>"
#        verifier CHANGES REQUESTED                   -> orch launch fixer PR (max 3 rounds, then hold + NEED)
#        VERIFIED, risky, blast radius NONE/STALE     -> orch launch br PR
#        builder BLOCKED, UNSAFE, a harness PR        -> NEED (the orchestrator decides)
#        a live worker silent for STALL_MIN minutes   -> STALLED: NEED once, never killed
#      Every state change is appended to STATE/state/transitions.log.
#   2. Each open issue labelled `tracked` + `status:ready` with no PR and no live builder -> orch launch builder
#      KEY slug "<issue body>". `status:ready` is the orchestrator's judgement (dependencies closed, files
#      disjoint from work in flight); the launch itself is mechanical. At most MAX_ATTEMPTS launches per issue.
#   3. orch merge --apply (one merge, or one train of up to TRAIN_MAX); a CONFLICT gets a fixer.
#   4. orch board sync --apply (after a launch, a hold or a merge, else every 30 min).
#   5. A NEED line the previous tick did not print starts the judgement wake now (wakekick.py), when
#      `orch wake` is installed.
# Output: ACTION lines (what it did), NEED lines (judgement for the orchestrator's wake), WAIT, STATE.
import datetime as dt, fcntl, json, os, re, subprocess, sys
from pathlib import Path

ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ORCH))
import risk, wakekick  # noqa: E402
from conf import BASE, C, R, REPO, STATE, TITLE_RE, WTROOT, alive, first_line  # noqa: E402

APPLY = "--apply" in sys.argv
MAX_FIX_ROUNDS = 3
MAX_ATTEMPTS = 2
STALL_MIN = 30
# PRs touching these are orchestrator-owned: workers may not edit them, so no fixer
HARNESS = re.compile(r"^(\.agents/|scripts/|\.github/)")
NOW = dt.datetime.now(dt.timezone.utc)
MAX_LOAD = float(os.environ.get("TICK_MAX_LOAD", 9))
MIN_FREE_PCT = float(os.environ.get("TICK_MIN_FREE_PCT", 20))
MAX_AGENTS = int(os.environ.get("TICK_MAX_AGENTS", 6))
LIMIT_WINDOW_S = 3600        # hold after a rejection that names no reset time
LIMIT_MAX_HOLD_S = 8 * 86400 # never hold longer than this, whatever the reset time says
LIMIT_RE = re.compile(r"You've hit your [^\n]*limit|usage limit reached|out of extra usage|rate_limit[^\n]*\bresets\s|\"rate_limit_event\"[^\n]*\"rejected\"")
ALL_STATE = STATE.parent     # ~/.agent-orch: one state dir per repository on this machine
TRANSCRIPTS = (Path.home() / ".hbar/projects", Path.home() / ".claude/projects")
TLOG = STATE / "state" / "transitions.log"
ATTEMPTS = STATE / "state" / "attempts.json"   # {"<wid>@<head7>": launches}: caps relaunches of a step that keeps dying
BOARD_STAMP = STATE / "state" / "board-sync.stamp"
WAKE_LABEL = f"com.{C['REPO_NAME']}.orch-wake"   # the LaunchAgent install-wake.sh installs
NEEDS = []                   # every NEED line of this tick, for wakekick


def sh(*a):
    p = subprocess.run(a, capture_output=True, text=True, cwd=REPO)
    return (p.stdout + ("\n" + p.stderr if p.returncode and p.stderr else "")).strip(), p.returncode


def gh_json(*a):
    out, rc = sh("gh", *a)
    return json.loads(out) if rc == 0 and out else None


def say(kind, msg):
    print(f"{kind:7} {msg}")
    if kind == "NEED":
        NEEDS.append(msg)


def act(desc, *cmd):
    if not APPLY:
        say("PLAN", desc); return ""
    out, rc = sh(*cmd)
    say("ACTION" if rc == 0 else "ERROR", f"{desc}: {out.splitlines()[-1] if out else rc}")
    return out


def launch(desc, *cmd):
    """act() for a step that starts a worker (counted against MAX_AGENTS)."""
    global launched, changed
    launched += 1; changed = True
    return act(desc, *cmd)


def attempts():
    try:
        return json.loads(ATTEMPTS.read_text())
    except (OSError, ValueError):
        return {}


def capped(wid, at, what):
    """True (and a NEED line) once `wid` was launched MAX_ATTEMPTS times at `at` (a head sha, or "issue") with no result."""
    k = f"{wid}@{at[:7]}"
    if attempts().get(k, 0) >= MAX_ATTEMPTS:
        say("NEED", f"{what}: {wid} ran {MAX_ATTEMPTS}x at {at[:7]} with no result; orch logs {wid}, then decide")
        return True
    if APPLY:
        a = attempts(); a[k] = a.get(k, 0) + 1
        ATTEMPTS.write_text(json.dumps(a, indent=1))
    return False


def issue_of(pr_body, key):
    m = re.search(r"Ref #(\d+)", pr_body or "") or re.match(r"ISSUE-(\d+)$", key)
    return m.group(1) if m else None


# --- resource guards ---------------------------------------------------------------------------------------
def free_pct():
    """What the kernel counts as available memory. Free plus inactive pages stay low on an idle Mac (#286)."""
    out, rc = sh("/usr/bin/memory_pressure")
    m = re.search(r"System-wide memory free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if rc == 0 and m else None


def workers_alive():
    """Live workers of every repository on this machine (the limit is shared), by pid file."""
    n = 0
    for f in ALL_STATE.glob("*/state/*.pid"):
        try:
            os.kill(int(f.read_text().strip()), 0); n += 1
        except (OSError, ValueError):
            pass
    return n


RESET_RE = re.compile(r"resets\s+(?:([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", re.I)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def reset_at(text, written):
    """The reset time a rejection names ("resets Nov 10 at 1am (America/New_York)", "resets 3am (Europe/Madrid)"),
    as the first such moment after `written` (a UTC datetime), or None if the text names none."""
    m = RESET_RE.search(text)
    if not m:
        return None
    mon, day, hour, minute, ampm, zone = m.groups()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(zone) if zone else dt.timezone.utc
    except Exception:  # noqa: BLE001
        tz = dt.timezone.utc
    hour = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    base = written.astimezone(tz)
    try:
        if mon:
            t = base.replace(month=MONTHS.index(mon.lower()) + 1, day=int(day), hour=hour, minute=int(minute or 0), second=0, microsecond=0)
            if t < base - dt.timedelta(days=1):
                t = t.replace(year=t.year + 1)
        else:
            t = base.replace(hour=hour, minute=int(minute or 0), second=0, microsecond=0)
            if t <= base:
                t += dt.timedelta(days=1)
    except ValueError:
        return None
    return t.astimezone(dt.timezone.utc)


def limit_hit():
    """(file name, held until) for the latest-ending usage-limit hold, or None. A rejection holds until the reset
    time it names, else for LIMIT_WINDOW_S after it was written; never longer than LIMIT_MAX_HOLD_S. Every
    repository's worker logs count: the workers share the accounts."""
    since = NOW.timestamp() - LIMIT_MAX_HOLD_S
    try:   # touched by the orchestrator when the workers move to an account that is not out of usage
        since = max(since, (STATE / "state" / "limit-cleared").stat().st_mtime)
    except OSError:
        pass
    hold = None
    for f in [*ALL_STATE.glob("*/logs/*"), *ALL_STATE.glob("*/runs/*.json")]:
        try:
            if f.name in ("heartbeat.log", "tick.log") or not f.is_file() or f.stat().st_mtime < since:
                continue
            if f.suffix == ".json":   # a worker's final JSON: only an error result counts (a PR may quote the text)
                d = json.loads(f.read_text())
                text = str(d.get("result", "")) if isinstance(d, dict) and d.get("is_error") else ""
            else:
                with f.open("rb") as fh:
                    fh.seek(max(0, f.stat().st_size - 65536)); text = fh.read().decode(errors="replace")
        except (OSError, ValueError):
            continue
        m = LIMIT_RE.search(text)
        if not m:
            continue
        written = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc)
        line = text[text.rfind("\n", 0, m.start()) + 1:].split("\n", 1)[0]
        until = reset_at(line, written) or written + dt.timedelta(seconds=LIMIT_WINDOW_S)
        until = min(until, written + dt.timedelta(seconds=LIMIT_MAX_HOLD_S))
        if until > NOW and (hold is None or until > hold[1]):
            hold = (f.name, until)
    return hold


def static_guards():
    why = []
    load = os.getloadavg()[0]
    if load >= MAX_LOAD:
        why.append(f"load {load:.1f} >= {MAX_LOAD:g}")
    free = free_pct()
    if free is not None and free < MIN_FREE_PCT:
        why.append(f"memory {free}% free < {MIN_FREE_PCT:g}%")
    hit = limit_hit()
    if hit:
        why.append(f"usage-limit rejection in {hit[0]} (held until {hit[1]:%Y-%m-%d %H:%M}Z)")
    return why


_guard_said = False


def guarded(desc):
    """True when a guard holds the launch `desc`: one `WAIT guard:` line per tick, one held line each."""
    global _guard_said
    why = list(GUARDS)
    if ALIVE0 + launched >= MAX_AGENTS:
        why.append(f"{ALIVE0 + launched} workers alive across repos (max {MAX_AGENTS})")
    if why and not _guard_said:
        say("WAIT", "guard: " + "; ".join(why)); _guard_said = True
    if why and desc:
        say("WAIT", f"{desc}: held by guard")
    return bool(why)


def stalled(w):
    """Minutes since worker `w` last wrote anything (pid file, logs, run JSON, session transcripts), when
    that is STALL_MIN or more; else 0. A headless worker writes its result only at the end."""
    fs = [STATE / "state" / f"{w}.pid", *(STATE / "logs").glob(f"*-{w}.*"), *(STATE / "runs").glob(f"*-{w}.json")]
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(WTROOT / w))
    fs += [f for d in TRANSCRIPTS if (d / slug).is_dir() for f in (d / slug).rglob("*.jsonl")]
    times = []
    for f in fs:
        try:
            times.append(f.stat().st_mtime)
        except OSError:
            pass
    idle = int((NOW.timestamp() - max(times, default=NOW.timestamp())) // 60)
    return idle if idle >= STALL_MIN else 0


# --- transitions ---------------------------------------------------------------------------------------------
def last_states():
    """The latest recorded (non-PLAN) state of each PR or issue, from transitions.log."""
    st = {}
    try:
        lines = TLOG.read_text().splitlines()
    except OSError:
        return st
    for line in lines:
        f = line.split()
        if len(f) >= 5 and f[1] != "PLAN" and f[3] == "->":
            st[f[1]] = f[4]
    return st


def mark(n, state, reason):
    """Record PR or issue n entering `state` (once per change; a dry run writes it as PLAN and remembers nothing)."""
    prev = PREV.get(f"#{n}", "-")
    if prev == state:
        return
    with TLOG.open("a") as f:
        f.write(f"{NOW:%Y-%m-%dT%H:%M:%SZ} {'' if APPLY else 'PLAN '}#{n} {prev} -> {state} {reason}\n")
    if APPLY:
        PREV[f"#{n}"] = state


def pr_state(p):
    """(state, lines, step) for one open PR, from GitHub (markers, labels) and the pid files. `lines` are the
    (word, message) lines the tick prints; `step` is what it does next: None, ("verifier",), ("fixer", rounds),
    ("hold", rounds), ("br", reasons) or ("stalled", wid, minutes)."""
    n, head, cs, key = p["number"], p["headRefOid"], p["comments"], TITLE_RE.match(p["title"]).group(1)
    tag = f"#{n} {key}"
    labels = {l["name"] for l in p["labels"]}
    builder = next((first_line(c) for c in reversed(cs) if first_line(c).startswith("[BUILDER]")), "")
    running = [w for w in (f"BR{n}", f"V{n}", key) if alive(w)]
    if running:
        w = running[0]
        busy = [("BUSY", f"{tag} {w} is running")]
        idle = stalled(w)
        if idle:
            return "STALLED", busy, ("stalled", w, idle)
        readied = any(re.match(r"\[BUILDER\] (READY|ADDRESSED)", first_line(c)) for c in cs)
        return {f"BR{n}": "BLAST_RADIUS", f"V{n}": "VERIFYING"}.get(w, "FIXING" if readied else "BUILDING"), busy, None
    if "BLOCKED" in builder:
        # resolved once a [VERIFIER] newer than the block reviewed the current head: go on as if ADDRESSED
        blocked_at = max(c["createdAt"] for c in cs if first_line(c).startswith("[BUILDER] BLOCKED"))
        after = [c for c in cs if c["createdAt"] > blocked_at]
        if risk.verdict(after, "[VERIFIER]", head, n)[0] in ("NONE", "STALE"):
            return "BLOCKED", [("NEED", f"{tag} builder BLOCKED: rule on it in an [ORCHESTRATOR] comment, then orch launch fixer {n}")], None
        builder = "[BUILDER] ADDRESSED"
    if not re.match(r"\[BUILDER\] (READY|ADDRESSED)", builder):
        return "BUILDING", [("NEED", f"{tag} no [BUILDER] READY and no live builder: orch logs {key}; relaunch or close")], None
    v, _ = risk.verdict(cs, "[VERIFIER]", head, n)
    last_v = max((c["createdAt"] for c in cs if first_line(c).startswith("[VERIFIER]")), default="")
    last_b = max((c["createdAt"] for c in cs if re.match(r"\[BUILDER\] (READY|ADDRESSED)", first_line(c))), default="")
    if v in ("NONE", "STALE") or last_b > last_v:
        return "VERIFYING", [], ("verifier",)
    if v == "CHANGES":
        files, _ = sh("gh", "pr", "diff", str(n), "-R", R, "--name-only")
        if any(HARNESS.search(f) for f in files.split()):
            return "FIXING", [("NEED", f"{tag} orchestrator-owned (changes the harness; workers may not edit it): address the findings yourself")], None
        rounds = sum(1 for c in cs if first_line(c).startswith("[BUILDER] ADDRESSED"))
        if rounds >= MAX_FIX_ROUNDS:
            return "BLOCKED", [("NEED", f"{tag} {rounds} fix rounds and still CHANGES REQUESTED: rule with [ORCHESTRATOR] or close")], \
                (None if "merge:hold" in labels else ("hold", rounds))
        return "FIXING", [], ("fixer", rounds)
    r = risk.classify(n)
    if r["risky"]:
        b = risk.verdict(cs, "[BLAST-RADIUS]", head, n)[0]
        if b in ("NONE", "STALE"):
            return "BLAST_RADIUS", [], ("br", r["reasons"])
        if b == "UNSAFE":
            return "FIXING", [("NEED", f"{tag} [BLAST-RADIUS] UNSAFE: launch a fixer with its findings or close")], None
    if "merge:hold" in labels:
        return "BLOCKED", [("NEED", f"{tag} every tier passed on {head[:7]} but merge:hold is set: lift it once the ruling is satisfied")], None
    return "MERGEABLE", [("READY", f"{tag} every tier passed; merge gate next")], None


# one tick at a time: the mechanical heartbeat (launchd) and the orchestrator may both run it
_lock = open(STATE / "state" / "tick.lock", "w")
try:
    fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    say("BUSY", "another tick is running"); sys.exit(0)

# GitHub budget: every gh call below is GraphQL, shared with every tool on the owner's account. Probe
# first; if GraphQL is refusing, skip the whole tick rather than act on half-empty data.
MIN_GRAPHQL = 300
probe, prc = sh("gh", "api", "graphql", "-f", "query={viewer{login}}")
gql = (gh_json("api", "rate_limit") or {}).get("resources", {}).get("graphql", {})   # REST: costs no GraphQL points
left = gql.get("remaining")
if prc or (isinstance(left, int) and left < MIN_GRAPHQL):
    reset = dt.datetime.fromtimestamp(gql["reset"], dt.timezone.utc).strftime("%H:%MZ") if gql.get("reset") else "?"
    why = f"probe failed: {probe.splitlines()[-1][:120] if probe else prc}" if prc else f"budget below {MIN_GRAPHQL}"
    say("WAIT", f"GitHub GraphQL {why} ({left if left is not None else '?'}/{gql.get('limit', '?')} left, resets {reset}); skipping this tick")
    sys.exit(0)

if (STATE / "FREEZE").exists():
    say("NEED", f"FREEZE is on ({STATE}/FREEZE): reporting only"); APPLY = False

# only the base branch is the trusted harness: follow it, never pull anything else
branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")[0]
if branch != BASE:
    say("NEED", f"orchestrator checkout is on {branch}, not {BASE}")
elif sh("git", "pull", "-q", "--ff-only")[1]:
    say("NEED", "orchestrator checkout did not fast-forward; push or fix it before trusting the harness")

# what each finished run used goes on its issue or pull request (usage.py); a failed post is retried next tick
if APPLY:
    for line in sh("python3", "-I", str(ORCH / "usage.py"), "--post")[0].splitlines():
        say("ACTION" if line.startswith("usage: posted") or "recorded" in line else "ERROR", line)

ALIVE0 = workers_alive()
launched = 0
changed = False   # a worker started, a label changed or a PR merged: the board is out of date
GUARDS = static_guards()
guarded("")   # the WAIT guard line, once, even when nothing would start
PREV = last_states()

# 1. open PRs
prs = gh_json("pr", "list", "-R", R, "--state", "open", "--base", BASE, "--limit", "100",
              "--json", "number,title,body,headRefOid,comments,labels")
listed = prs is not None   # None = the listing failed, which is not the same as an empty queue
if not listed:
    say("ERROR", "could not list PRs (GitHub API); PR steps skipped this tick")
prs = sorted((p for p in prs or [] if TITLE_RE.match(p["title"])), key=lambda p: p["number"])
for p in prs:
    try:
        n, head, key = p["number"], p["headRefOid"], TITLE_RE.match(p["title"]).group(1)
        tag = f"#{n} {key}"
        state, lines, step = pr_state(p)
        kind = step[0] if step else None
        if kind == "stalled":
            _, w, idle = step
            if PREV.get(f"#{n}") == "STALLED":
                lines = [("BUSY", f"{tag} {w} is running (STALLED, reported)")]
            else:
                lines = [("NEED", f"{tag} STALLED: {w} has written nothing for {idle} min; orch logs {w}, then stop it or let it run")]
        for word, msg in lines:
            say(word, msg)
        reason = lines[-1][1].removeprefix(tag).strip() if lines else ""
        if kind == "verifier":
            reason = "launch verifier"
            if not guarded(f"{tag} launch verifier") and not capped(f"V{n}", head, tag):
                iss = issue_of(p["body"], key)
                body = sh("gh", "issue", "view", iss, "-R", R, "--json", "body", "--jq", ".body")[0] if iss else "(see the PR body)"
                crit = (f"Ticket #{iss}. Its section 'Rulings from the orchestrator' and every [ORCHESTRATOR] comment on the "
                        f"issue and on this PR are part of the acceptance criteria. The ticket's text follows.\n{body}")
                launch(f"{tag} launch verifier", str(ORCH / "orch"), "launch", "verifier", str(n), crit)
        elif kind == "fixer":
            reason = f"launch fixer (round {step[1] + 1})"
            if not guarded(f"{tag} {reason}"):
                launch(f"{tag} {reason}", str(ORCH / "orch"), "launch", "fixer", str(n),
                       "Address every finding F1..Fn in the latest [VERIFIER] CHANGES REQUESTED comment, then post [BUILDER] ADDRESSED.")
        elif kind == "hold":
            changed = True
            act(f"{tag} hold after {step[1]} fix rounds", "gh", "pr", "edit", str(n), "-R", R, "--add-label", "merge:hold,status:blocked")
        elif kind == "br":
            reason = f"launch blast radius ({', '.join(step[1])})"
            if not guarded(f"{tag} {reason}") and not capped(f"BR{n}", head, tag):
                launch(f"{tag} {reason}", str(ORCH / "orch"), "launch", "br", str(n),
                       "Prove by running code what this change cannot break outside its diff.")
        elif kind == "stalled":
            reason = f"{step[1]} silent for {step[2]} min"
        mark(n, state, reason)
    except Exception as e:  # noqa: BLE001  (a bad gh response must not stop the other PRs or the merge gate)
        say("ERROR", f"#{p.get('number')} skipped this tick: {type(e).__name__}: {str(e)[:160]}")

# 2. ready issues: `status:ready` is the orchestrator's go-ahead; the builder launch is mechanical
ready = gh_json("issue", "list", "-R", R, "--state", "open", "--label", "tracked", "--label", "status:ready", "--limit", "100",
                "--json", "number,title,body,labels,comments") if listed else None
if listed and ready is None:
    say("ERROR", "could not list the status:ready issues (GitHub API); builder launches skipped this tick")
pr_keys = {TITLE_RE.match(p["title"]).group(1) for p in prs}
building = 0
for i in sorted(ready or [], key=lambda i: i["number"]):
    try:
        m = TITLE_RE.match(i["title"])
        if not m or m.group(1) in pr_keys or any(l["name"] == "status:blocked" for l in i["labels"]):
            continue
        n, key = i["number"], m.group(1)
        tag = f"#{n} {key}"
        if alive(key):
            building += 1
            idle = stalled(key)
            if idle and PREV.get(f"#{n}") != "STALLED":
                say("NEED", f"{tag} STALLED: the builder has written nothing for {idle} min; orch logs {key}, then stop it or let it run")
            else:
                say("BUSY", f"{tag} the builder is running")
            mark(n, "STALLED" if idle else "BUILDING", "builder running")
            continue
        cs = i["comments"]
        blocked = max((c["createdAt"] for c in cs if first_line(c).startswith("[BUILDER] BLOCKED")), default="")
        ruled = max((c["createdAt"] for c in cs if first_line(c).startswith("[ORCHESTRATOR]")
                     and not first_line(c).startswith(("[ORCHESTRATOR] LAUNCH", "[ORCHESTRATOR] USAGE"))), default="")
        if blocked > ruled:
            say("NEED", f"{tag} builder BLOCKED on the issue: rule on it in an [ORCHESTRATOR] comment; the next tick relaunches")
            mark(n, "BLOCKED", "builder BLOCKED"); continue
        closed = gh_json("pr", "list", "-R", R, "--state", "closed", "--search", f'"[{key}]" in:title', "--json", "number,title") or []
        closed = [c["number"] for c in closed if c["title"].startswith(f"[{key}]")]
        if closed:
            say("NEED", f"{tag} is status:ready but PR #{closed[0]} for it is closed: close the issue or remove the label")
            mark(n, "BLOCKED", f"PR #{closed[0]} closed"); continue
        if guarded(f"{tag} launch builder") or capped(key, "issue", tag):
            continue
        # a relaunch must reuse the branch a first launch made; else the slug comes from the title
        refs, _ = sh("git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin")
        old = re.search(rf"/{re.escape(key)}-([a-z0-9][a-z0-9-]*)$", refs, re.M)
        slug = old.group(1) if old else "-".join(re.findall(r"[a-z0-9]+", i["title"][m.end():].lower())[:4]) or "task"
        task = (f"{i['body']}\n\nEvery [ORCHESTRATOR] comment on issue #{n} is a ruling and part of this ticket: read them first.")
        launch(f"{tag} launch builder ({slug})", str(ORCH / "orch"), "launch", "builder", key, slug, task)
        building += 1
        mark(n, "BUILDING", "launch builder")
    except Exception as e:  # noqa: BLE001
        say("ERROR", f"issue #{i.get('number')} skipped this tick: {type(e).__name__}: {str(e)[:160]}")

# 3. merge gate: at most one merge, or one train, per tick
out = act("merge gate", str(ORCH / "orch"), "merge", "--apply") if prs else ""
# act() reports only the gate's last line; a CONFLICT anywhere else would leave its PR waiting forever
for m in re.finditer(r"^CONFLICT\s+#(\d+) (\S+)", out or "", re.M):
    n, key = int(m.group(1)), m.group(2)
    p = next((x for x in prs if x["number"] == n), None)
    if not p or any(alive(w) for w in (f"V{n}", f"BR{n}", key)):
        continue
    if not guarded(f"#{n} {key} launch fixer (merge conflict)") and not capped(f"MX{n}", p["headRefOid"], f"#{n} {key}"):
        launch(f"#{n} {key} launch fixer (merge conflict)", str(ORCH / "orch"), "launch", "fixer", str(n),
               f"Merge origin/{BASE} into this branch and resolve every conflict so both sides survive: keep all of {BASE}'s "
               "changes and this PR's intent. Re-run GATES, then post [BUILDER] ADDRESSED with the proof.")
        mark(n, "FIXING", "merge conflict")
for line in re.findall(r"^(?:TRAIN-RED|MISMATCH) .*", out or "", re.M):
    say("NEED", f"merge gate: {line}")
merged = re.findall(r"^MERGED\s+#(\d+) (\S+)", out or "", re.M)
for num, key in merged:
    say("NEED", f"merged #{num} {key}: tick the plan, and label status:ready the tickets it unblocks "
                "(dependencies closed, files disjoint from work in flight)")

# 4. board
if changed or merged or not BOARD_STAMP.exists() or NOW.timestamp() - BOARD_STAMP.stat().st_mtime > 1800:
    out = act("board sync", str(ORCH / "orch"), "board", "sync", "--apply")
    if APPLY and "rate limit" not in out and "error" not in out.lower():   # a failed sync is retried next tick
        BOARD_STAMP.touch()

# state for the orchestrator's wake
live = sorted(f.stem for f in (STATE / "state").glob("*.pid") if alive(f.stem))
if merged:
    sh("git", "pull", "-q", "--ff-only")
open_prs = len(prs) - len(merged)
main_sha = sh("git", "rev-parse", "--short", f"origin/{BASE}")[0]
say("STATE", f"{BASE}@{main_sha} open_prs={open_prs} building={building} live={','.join(live) or '-'}")
if listed and ready is not None and open_prs + building < 2:
    say("NEED", f"{open_prs + building} work stream(s) in flight, fewer than two: write the next ticket and label it status:ready")

# 5. something new needs judgement: start the wake now
if APPLY and (Path.home() / "Library/LaunchAgents" / f"{WAKE_LABEL}.plist").exists():
    n = wakekick.note(NEEDS, STATE / "state", bool(os.environ.get("ORCH_WAKE")), alive("wake"),
                      lambda: sh("launchctl", "kickstart", f"gui/{os.getuid()}/{WAKE_LABEL}")[1] == 0)
    if n:
        say("ACTION", f"wake started for {n} new NEED line(s)")
