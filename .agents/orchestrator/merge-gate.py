# merge-gate.py [--apply] — merge open [KEY] PRs into BASE. A PR is merged only if ALL hold:
#   0. no worker or live check is running on the PR: a verdict on the old head must not race a fix
#   1. no `merge:hold` label, GitHub says MERGEABLE
#   2. latest first-line [VERIFIER] verdict is VERIFIED, newer than the last [BUILDER] READY/ADDRESSED,
#      and its `Reviewed head` is the current head (or only BASE was merged in since)
#   3. risky PRs (risk.py) carry a matching [BLAST-RADIUS] SAFE
# and BASE is known to stay green with it, in one of two ways:
#   STRICT, one PR per run:
#   4. head contains current origin/BASE (otherwise update-branch and wait)
#   5. every GitHub check on the head passed
#   TRAIN, up to TRAIN_MAX PRs per run, only while the last finished CI run on BASE is green:
#   4. the PRs that hold 0-3 are merged in number order onto origin/BASE in a scratch worktree; one that
#      conflicts there waits for the next train
#   5. GATES pass on that tree. Then each member is merged and BASE's tree is compared with the tested tree.
#      GATES fail: nothing is merged and the next train takes half as many. A PR that fails alone is printed
#      as CONFLICT (the tick sends a fixer) and is skipped until its head changes.
#   A red CI run on BASE stops the trains; only the strict way (the fix) merges until BASE is green again.
#   The orchestrator may accept a red BASE it has explained in an [ORCHESTRATOR] comment, for one train:
#   `git rev-parse origin/BASE > $STATE/state/train-accept-red` (it names that one commit, so it lapses
#   when BASE moves).
# Without --apply it only reports. It never touches your issue tracker; it prints TRACKER lines for you.
import fcntl, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk  # noqa: E402
from conf import BASE, C, MERGE_METHOD, R, REPO, STATE, TITLE_RE, WTROOT, alive, first_line  # noqa: E402

APPLY = "--apply" in sys.argv
TRAIN_MAX = int(C.get("TRAIN_MAX") or 1)


def gh(*a):
    p = subprocess.run(["gh", *a], capture_output=True, text=True)
    return p.stdout, p.returncode


def rev(ref):
    return subprocess.run(["git", "-C", REPO, "rev-parse", ref], capture_output=True, text=True).stdout.strip()


def tag(p):
    return f"#{p['number']} {TITLE_RE.match(p['title']).group(1)}"


def blocked(p):
    """Conditions 0 to 3 and GitHub's mergeable flag: the line to print, or None when they all hold."""
    n, head, key = p["number"], p["headRefOid"], TITLE_RE.match(p["title"]).group(1)
    if any(l["name"] == "merge:hold" for l in p["labels"]):
        return f"HOLD     {tag(p)} (merge:hold)"
    if any(alive(w) for w in (key, f"V{n}", f"BR{n}", f"L{n}")):
        return f"WAIT     {tag(p)} a worker or live check is running"
    cs = p["comments"]
    v_word, _ = risk.verdict(cs, "[VERIFIER]", head, n)
    last_v = max((c["createdAt"] for c in cs if first_line(c).startswith("[VERIFIER]")), default="")
    last_b = max((c["createdAt"] for c in cs if re.match(r"\[BUILDER\] (READY|ADDRESSED)", first_line(c))), default="")
    if v_word != "VERIFIED" or last_b > last_v:
        why = "stale (builder pushed after verdict)" if last_b > last_v and last_v else v_word
        return f"WAIT     {tag(p)} verifier={why}"
    p["risky"] = risk.classify(n)
    if p["risky"]["risky"]:
        b_word, _ = risk.verdict(cs, "[BLAST-RADIUS]", head, n)
        if b_word != "SAFE":
            return f"WAIT     {tag(p)} risky ({', '.join(p['risky']['reasons'])}) blast-radius={b_word} -> orch launch br {n}"
    if p["mergeable"] == "UNKNOWN":
        return f"WAIT     {tag(p)} GitHub is still computing mergeability; re-run"
    if p["mergeable"] != "MERGEABLE":
        return f"CONFLICT {tag(p)} mergeable={p['mergeable']} -> orch launch fixer {n} 'merge origin/{BASE}'"
    return None


def merge(p, how):
    """Comment on and merge one PR whose every condition holds. True when GitHub merged it."""
    n, head, key = p["number"], p["headRefOid"], TITLE_RE.match(p["title"]).group(1)
    if p["isDraft"]:
        gh("pr", "ready", str(n), "-R", R)
    gh("pr", "comment", str(n), "-R", R, "--body",
       f"[ORCHESTRATOR] Merging into {BASE}: [VERIFIER] VERIFIED on {head[:7]}"
       + (" + [BLAST-RADIUS] SAFE" if p["risky"]["risky"] else "") + f", {how}.")
    for _ in range(6):  # in a train GitHub recomputes mergeability after the member before this one
        _, rc = gh("pr", "merge", str(n), "-R", R, f"--{MERGE_METHOD}", "--match-head-commit", head)
        if rc == 0 or TRAIN_MAX <= 1:
            break
        time.sleep(10)
    if rc:
        print(f"FAILED   {tag(p)} gh pr merge rc={rc}"); return False
    print(f"MERGED   {tag(p)} -> {BASE}")
    print(f"TRACKER  {key}: merged in PR #{n}; update your tracker if you mirror one")
    return True


def base_ci():
    """Conclusion of the last finished CI run on BASE that was not cancelled ('' when there is none);
    'accepted' when it is not green and the orchestrator accepted that for this BASE commit."""
    runs = json.loads(gh("run", "list", "-R", R, "--branch", BASE, "--event", "push", "--status", "completed",
                         "--limit", "20", "--json", "conclusion")[0] or "[]")
    ci = next((r["conclusion"] for r in runs if r["conclusion"] in ("success", "failure", "timed_out")), "")
    ok = STATE / "state" / "train-accept-red"
    return "accepted" if ci != "success" and ok.exists() and ok.read_text().strip() == base_sha else ci


def train(ready, ci):
    """Merge up to TRAIN_MAX of `ready` together once GATES pass on BASE plus all of them. True if any merged."""
    wt = WTROOT / "TRAIN"

    def git(*a):
        return subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True)

    lock = open(STATE / "state" / "train.lock", "w")  # one train at a time: a second run would reset the worktree
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("WAIT     train: another run is building or testing a train"); return False
    memo = STATE / "state" / "train.json"
    st = json.loads(memo.read_text()) if memo.exists() else {}
    cap, red = min(int(st.get("cap", TRAIN_MAX)), TRAIN_MAX), st.get("red", {})
    if not wt.exists():
        subprocess.run(["git", "-C", REPO, "worktree", "prune"])
        subprocess.run(["git", "-C", REPO, "worktree", "add", "-q", "--detach", str(wt), base_sha], capture_output=True)
    git("merge", "--abort"); git("reset", "-q", "--hard"); git("clean", "-qfdx")
    if git("checkout", "-q", "--detach", base_sha).returncode:
        print(f"ERROR    train: cannot check out {base_sha[:7]} in {wt}"); return False
    members = []
    for p in ready:
        n, head = p["number"], p["headRefOid"]
        if red.get(str(n)) == head:
            print(f"CONFLICT {tag(p)} GATES fail on this PR merged with {BASE} (it failed alone in a train) "
                  f"-> orch launch fixer {n} 'merge origin/{BASE}'"); continue
        if len(members) == cap:
            print(f"WAIT     {tag(p)} the train is full ({cap}); next run"); continue
        subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", f"+refs/pull/{n}/head:refs/remotes/pr/{n}"])
        if git("-c", "user.name=train", "-c", "user.email=train@localhost", "merge", "-q", "--no-ff", "--no-edit", head).returncode:
            git("merge", "--abort")
            print(f"WAIT     {tag(p)} conflicts with the PRs before it in this train; next run"); continue
        members.append(p)
    if not members:
        return False
    names = " ".join(f"#{p['number']}" for p in members)
    tree = git("rev-parse", "HEAD^{tree}").stdout.strip()
    if not APPLY:
        print(f"READY    train {names} on {base_sha[:7]} (dry run; --apply runs GATES on tree {tree[:7]} and merges)")
        return False
    log = STATE / "logs" / f"train-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    with open(log, "w") as f:
        f.write(f"train {names} on {BASE} {base_sha} tree {tree}\n"); f.flush()
        try:
            rc = subprocess.run(["bash", "-c", C["GATES"]], cwd=wt, stdout=f, stderr=subprocess.STDOUT, env=env,
                                timeout=2700).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    if rc:
        if len(members) == 1:
            red[str(members[0]["number"])] = members[0]["headRefOid"]
        memo.write_text(json.dumps({"cap": max(1, len(members) // 2), "red": red}))
        print(f"TRAIN-RED {names} on {base_sha[:7]}: GATES rc={rc} ({log}); "
              + ("it is skipped until its head changes" if len(members) == 1 else f"the next train takes {len(members) // 2}"))
        return False
    memo.write_text(json.dumps({"cap": min(TRAIN_MAX, max(cap, len(members)) * 2), "red": red}))
    subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", BASE])
    if rev(f"origin/{BASE}") != base_sha:
        print(f"WAIT     train {names}: {BASE} moved from {base_sha[:7]} while GATES ran; next run"); return False
    done = 0
    for p in members:
        if not merge(p, f"train {names} on {base_sha[:7]}: GATES passed on {BASE} plus every member "
                        f"(tree {tree[:7]}, {log.name}), "
                        + (f"last CI run on {BASE} green" if ci == "success" else
                           f"the red CI run on {BASE} {base_sha[:7]} accepted by the orchestrator")):
            break
        done += 1
    subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", BASE])
    got = rev(f"origin/{BASE}^{{tree}}")
    if got == tree:
        print(f"TRAIN    {names} merged; {BASE}'s tree is the tree GATES passed on ({tree[:7]})")
    else:
        print(f"MISMATCH train {names}: {done} of {len(members)} merged and {BASE}'s tree {got[:7]} is not the tested "
              f"tree {tree[:7]}; CI on {BASE} decides the next train")
    return done > 0


subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", BASE])
base_sha = rev(f"origin/{BASE}")
prs = json.loads(gh("pr", "list", "-R", R, "--state", "open", "--base", BASE, "--limit", "100",
                    "--json", "number,title,labels,headRefOid,mergeable,isDraft,comments")[0] or "[]")
prs = sorted((p for p in prs if TITLE_RE.match(p["title"])), key=lambda p: p["number"])
ready = []
for p in prs:
    line = blocked(p)
    if line:
        print(line)
    else:
        ready.append(p)
merged = False
if ready and TRAIN_MAX > 1:
    ci = base_ci()
    if ci in ("success", "accepted"):
        merged, ready = train(ready, ci), []
    else:
        print(f"NOTE     no train: the last finished CI run on {BASE} is {ci or 'missing'}; strict, one PR per run")
for p in ready:
    n, head = p["number"], p["headRefOid"]
    subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", f"+refs/pull/{n}/head:refs/remotes/pr/{n}"])
    if subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", base_sha, head]).returncode:
        if APPLY and gh("pr", "update-branch", str(n), "-R", R)[1] == 0:
            print(f"BEHIND   {tag(p)} merged {BASE} in; re-run after checks finish")
        else:
            print(f"BEHIND   {tag(p)} head lacks origin/{BASE} {base_sha[:7]} (run with --apply to update-branch)")
        break  # strict: one front at a time
    checks = json.loads(gh("pr", "checks", str(n), "-R", R, "--json", "name,bucket")[0] or "[]")
    bad = [c["name"] for c in checks if c["bucket"] not in ("pass", "skipping")]
    if bad:
        print(f"CHECKS   {tag(p)} not green: {', '.join(bad)}"); break
    if not checks:
        print(f"NOTE     {tag(p)} has no CI checks; the local gates are the only evidence (see templates/github/pr-gates.yml)")
    if not APPLY:
        print(f"READY    {tag(p)} all gates pass (dry run; --apply merges)"); break
    merged = merge(p, "head contains current base, checks green")
    break
if merged and (STATE / "board-gist-id").exists():
    subprocess.run(["python3", "-I", str(Path(__file__).resolve().parent / "board.py"), "gist", "--apply"], capture_output=True)
if not prs:
    print("no open [KEY] PRs against", BASE)
