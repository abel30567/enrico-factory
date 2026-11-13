#!/usr/bin/env python3
# scout.py — draft builder tickets on Fermi fleet boxes, one box per issue. ORCHESTRATOR ONLY.
#
#   scout.py launch N [N...] [--note-file F] [--in-flight "text"]   start one ticket drafter per issue number
#   scout.py brief KEY FILE                                        start one drafter with the brief in FILE; KEY names its draft
#   scout.py collect                                                fetch finished drafts, print each final message
#   scout.py status                                                 one line per drafter
#
# A drafter is read-only: it researches the repository in its own clone and uploads one file, the draft.
# `collect` saves it as $STATE/inputs/ISSUE-<N>.draft.md (or <KEY>.draft.md). Nothing reaches GitHub from here: the orchestrator
# reviews the draft, rules on what it left open, files it as the issue body and labels the issue status:ready.
# A note file holds lines `<N>: <the orchestrator's triage note for that issue>`; the drafter gets its own line.
import json, re, subprocess, sys, time
from pathlib import Path

ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ORCH / "adapters"))
import fleet  # noqa: E402  (conf, call: the fleet admin API)

REPO_DIR = Path(subprocess.run(["git", "-C", str(ORCH), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                               capture_output=True, text=True).stdout.strip()).parent
STATE = Path.home() / ".agent-orch" / REPO_DIR.name
LEDGER = STATE / "state" / "scouts.tsv"   # issue, agent id, launched (epoch), collected (0/1)
GH_REPO = re.sub(r"^(git@[^:]+:|ssh://git@[^/]+/|https?://[^/]+/)|\.git$", "",
                 subprocess.run(["git", "-C", str(REPO_DIR), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip())
MODEL = "claude-opus-5-5"

BRIEF = """You are drafting one builder ticket for the GitHub repository `{repo}`. You run on a disposable cloud box and the
repository is cloned in your starting directory. Start with `git fetch -q origin && git checkout -q --detach origin/main`,
then `git log --oneline -1`: measure against that commit and name it in the draft.

This is research and writing only. Do not push, do not open or edit issues, pull requests, comments or labels (no `gh`
command that writes), and do not run any real model or agent harness. Read issues with
`gh api repos/{repo}/issues/<n> --jq .body` and `gh api repos/{repo}/issues/<n>/comments --jq '.[].body'`. You may run
single test files (`python3 -m unittest tests/test_x.py`) and one-line commands to measure something; do not run the whole
test suite. Do any prototype in a copy of the tree under `/tmp`.

Your one output is the file `/tmp/result.md`: the draft ticket. Write it early (a skeleton after your first measurements)
and extend it as you go. When it is finished, upload it as the artifact named `result.md`.

I am the orchestrator of that repository. I will review your draft, rule on the open points, file it as the body of the
issue and label it `status:ready`; a builder is then launched with it. The builder is a headless agent that cannot ask
questions and may not edit `docs/**`. Every choice you leave open is one it will guess. So make each ruling yourself,
give exact output lines and messages word for word, and back each claim about the code with file, function and line.
Everything in the draft must be measured against the code and the documents, not guessed.

## Background

The repository builds a "content factory" kit (`kit/`, a Python CLI `kit/factory`, standard library only) that is installed
into a production repository as `.factory/` by `factory init`. A production makes an animated show through rooms (story,
production, post, distribution); each stage of a room is a ticket worked by a maker role (a role file under `kit/roles/`)
and checked by checker or judge roles; GitHub pull request comments carry first-line markers; `factory tick` is the
product's own mechanical dispatcher, `factory launch` starts one role on the agent harness (a fake harness for tests),
`factory lint` runs the mechanical lints of a stage (`kit/lib/lints/`), `factory lock` locks a stage
(`kit/lib/lockrules.py`, `kit/lib/gitstate.py`). The stage ladder is `kit/pipeline.json`. The specification is
`docs/system-brief.md`, with `docs/harness.md` and `docs/architecture.md`. Read the repository's `CLAUDE.md` for the
project rules (the kit is project-agnostic; tests never call GitHub, a model or the network and use recorded JSON
fixtures under `tests/fixtures/`; workers do not edit `docs/**`, `plan/`, `.agents/`, `kit/resources.json`, `CLAUDE.md`,
`README.md`), and `plan/lessons.md` for mistakes not to repeat. Role files have a line cap that a test enforces: measure
the headroom of any role file your ticket would edit.

## The ticket to write: issue #{n}

Read the issue's body and every comment on it (a comment whose first line starts with `[ORCHESTRATOR]` is a binding
ruling). Read the issues it names as dependencies and the sections of the brief it names.

The orchestrator's triage note for this issue (a hint measured earlier; verify it, and say where it is wrong):
{note}

Other work in flight right now, by other builders:
{in_flight}
Your ticket's `## Scope` must not include a file that work changes. If the ticket cannot be done without such a file,
say so in the first lines of the draft (`Waits for: #<n> (<file>)`) and still write the rest.

## What the draft must contain

Model it on two finished tickets, the bodies of issues #235 and #97 (`gh api repos/{repo}/issues/235 --jq .body`, the
same for 97): same section order, tone and level of measurement. The first three lines of the file are exactly:
`Depends on: <issue numbers, or none>`, then `Files: <comma-separated list of every path the builder may change or
create>`, then `Waits for: <#n (file), ... or nothing>`. Then: `## Context` (numbered facts with file, function and line
references, plus a "Measured on main at <sha>" table of commands you actually ran and their results); `## Scope` (files
that may change or be created; files out of scope); `## Rulings from the orchestrator ({today}), part of this ticket`
(the rulings you recommend: each a decision the builder would otherwise have to guess, with exact output lines and
messages word for word); `## Behaviour` (numbered; each with a seeded defect and a clean twin where that applies);
`## Acceptance criteria` (each checkable offline by a command); `## Verification` (exact commands, including the gates
command from `.agents/orchestrator/orch.env`); `## Not decided here`; and the section
`## Conventions shared by every wave 2 ticket`, copied verbatim from the body of issue #97.

Writing rules: plain, concrete English; short sentences; no em dashes; name files, functions and commands exactly; do
not invent labels or abbreviations the repository does not use; do not pad. Target 10 to 18 KB. Keep the scope to what
the issue's "Done when" needs; anything larger goes to `## Not decided here` as a named follow-up. Where a fixture
needs an invented third-party name (a work or a character that is "not ours"), use `Skarrowfen` (the work) and
`Wickabel` (its character) and invent no others. Cite a document by section number, subsection title and a quoted
sentence; a line number is only a hint.

## Your final message

Under 250 words, plain text: the commit you measured against; the rulings where you had a real choice, which option you
took and why; anything in the documents that contradicts the code; the exact file set and its overlap with the work in
flight; anything you could not measure; any other defect you noticed (name it, do not fix it).
"""


def rows():
    if not LEDGER.exists():
        return []
    return [l.split("\t") for l in LEDGER.read_text().splitlines() if l.strip()]


def save(rs):
    LEDGER.write_text("".join("\t".join(r) + "\n" for r in rs))


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    c = fleet.conf()
    rs = rows()
    if cmd in ("launch", "brief"):
        nums = [a for a in sys.argv[2:] if a.isdigit()] if cmd == "launch" else [sys.argv[2]]
        notes = {}
        if opt("--note-file"):
            for line in Path(opt("--note-file")).read_text().splitlines():
                m = re.match(r"(\d+):\s*(.*)$", line)
                if m:
                    notes[m.group(1)] = m.group(2)
        in_flight = opt("--in-flight", "none")
        for n in nums:
            if any(r[0] == n and r[3] == "0" for r in rs):
                print(f"#{n}: a drafter is already recorded; collect it first"); continue
            prompt = Path(sys.argv[3]).read_text() if cmd == "brief" else BRIEF.format(repo=GH_REPO, n=n, note=notes.get(n, "none"), in_flight=in_flight, today=time.strftime("%Y-%m-%d"))
            r = fleet.call(c, "POST", "/admin/fleet/launch", {
                "prompt": prompt, "proof_contract": json.dumps({"kind": "artifact", "name": "result.md", "min_bytes": 4000}),
                "route": "claude", "account": c.get("FLEET_ACCOUNT", "tars"), "model": MODEL,
                "instance_type": c.get("FLEET_INSTANCE", "t3.medium"), "repo": GH_REPO, "branch": "main",
                "budget_usd": 0.3, "ttl_seconds": 3600})
            if not r.get("ok"):
                print(f"#{n}: launch refused: {json.dumps(r)[:300]}"); continue
            rs.append([n, r["agent_id"], str(int(time.time())), "0"]); save(rs)
            print(f"#{n}: {r['agent_id']}")
        return 0
    for r in rs:
        if r[3] != "0":
            continue
        d = fleet.call(c, "GET", f"/admin/fleet/status?id={r[1]}", timeout=30)
        a = d.get("agent") or {}
        age = (int(time.time()) - int(r[2])) // 60
        note = next((e.get("note", "") for e in d.get("events", []) if "turns=" in e.get("note", "")), "")
        if cmd == "status" or a.get("status") not in ("done", "failed", "destroyed"):
            print(f"#{r[0]} {a.get('status')} {age} min {note} account={a.get('account')}"); continue
        text = str((d.get("tasks") or [{}])[0].get("result") or a.get("exit_reason"))
        out = STATE / "inputs" / (f"ISSUE-{r[0]}.draft.md" if r[0].isdigit() else f"{r[0]}.draft.md")
        got = subprocess.run(["curl", "-sS", "-m", "60", "-K", "-", "-o", str(out),
                              f"{c['FERMI_URL'].rstrip('/')}/admin/fleet/artifact?key=artifacts/{r[1]}/result.md"],
                             input=f'header = "Authorization: Bearer {c["FERMI_BEARER_TOKEN"]}"\n', text=True, capture_output=True)
        size = out.stat().st_size if out.exists() else 0
        ok = a.get("status") == "done" and got.returncode == 0 and size >= 4000
        r[3] = "1"; save(rs)
        print(f"=== #{r[0]} {a.get('status')} ({a.get('exit_reason')}) draft {'saved ' + str(out) if ok else 'MISSING or short'} ({size} bytes)\n{text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
