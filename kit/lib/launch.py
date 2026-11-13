"""Prepare one launch, up to and including its LAUNCH record (docs/harness.md, section 3, steps 1 to 5).

``prepare`` runs every check, creates the worktree, renders the prompt, writes it with its sha256
outside the worktree and posts ``[<ORCHESTRATOR>] LAUNCH <role> <id>`` (brief section 8). It starts no
process of the role. A failure at any step launches nothing: a check leaves nothing behind, and an
error after the worktree exists removes what this call created.

The harness is reached only through ``kit.lib.harness``; the prompt is rendered only by ``kit.lib.prompt``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple

from kit.lib import down, gateway, gitstate, harness, jobs, markers, production, prompt, steps, tickets
from kit.lib.gitstate import GitError
from kit.lib.harness import HarnessError
from kit.lib.production import ProductionError
from kit.lib.prompt import PromptError

REFUSED_KINDS = {
    "analyst": "analyst launches are not built in wave 2",
}
# The kinds launched on a branch: a maker's own, or the pull request's for an operator.
BRANCHED = ("maker", "operator")


class LaunchError(Exception):
    """A launch that did not happen. The message never holds a setting value or prompt text."""


class Run(NamedTuple):
    dir: Path
    worktree: Path
    meta: dict


def _order(comment):
    return (comment["created_at"], comment["id"])


def _target(state, number):
    """``(ticket, pull or None)`` for the number given."""
    if number in state.pulls:
        pull = state.pulls[number]
        return state.tickets[pull["ticket"]], pull
    if number in state.tickets:
        return state.tickets[number], None
    raise LaunchError(f"#{number} is not a tracked ticket or a pull request of one")


def _check_open(state, ticket, pull):
    t = ticket["number"]
    if ticket["state"] != "open":
        raise LaunchError(f"ticket #{t} is closed")
    if tickets.locked_pull(state, t) is not None:
        raise LaunchError(f"ticket #{t} is locked")
    if ticket["hold"]:
        raise LaunchError(f"ticket #{t} is on hold")
    if pull is not None and pull["state"] != "open":
        raise LaunchError(f"pull request #{pull['number']} is not open")


def _running(p, comments, role):
    """Whether the newest ``LAUNCH <role>`` in ``comments`` has no later marker of the role's actor
    other than a ``LAUNCH``."""
    marked = [markers.parse(c["body"]) for c in sorted(comments, key=_order)]
    launched = [i for i, m in enumerate(marked) if m and m.verb == "LAUNCH" and m.args[0] == role]
    if not launched:
        return False
    return not any(m and m.actor == p.actors[role] and m.verb != "LAUNCH" for m in marked[launched[-1] + 1:])


def _body(pull, entry):
    """The body of the pull's comment that a ``markers.summary`` entry names."""
    return next(c["body"] for c in pull["comments"] if c["id"] == entry["id"])


def _ruling(p, state, ticket, pull):
    """The body of the newest ``RULING`` of the room's orchestrator on the pull or the ticket, or None."""
    actor = p.actors[production.orchestrator(p, ticket["room"])]
    comments = pull["comments"] + state.ticket_comments.get(ticket["number"], [])
    found = None
    for comment in sorted(comments, key=_order):
        m = markers.parse(comment["body"])
        if m and m.verb == "RULING" and m.actor == actor:
            found = comment["body"]
    return found


def _joined(parts):
    return "\n\n".join(part.rstrip("\n") for part in parts)


def _judged(state, ticket, pull):
    """The pull requests a judgment of the orchestrator reads: the pull given, else the ticket's open ones."""
    if pull is not None:
        return [pull]
    found = [x for x in tickets.pulls_of(state, ticket["number"]) if x["state"] == "open"]
    return sorted(found, key=lambda x: x["number"])


def _orchestrator_task(p, ticket, pull, judged):
    entry = production.stage(p, ticket["room"], ticket["stage"])
    if pull is not None:
        n = pull["number"]
        parts = [f"Judgment 1, a ruling after three rounds of notes, on pull request #{n} at head "
                 f"{pull['git']['head']}. Its files are in pr-{n}/."]
        actors = {p.actors[r] for key in ("maker", "checker") for r in production.stage_roles(entry, key)}
        orchestrator = p.actors[production.orchestrator(p, ticket["room"])]
        for comment in sorted(pull["comments"], key=_order):
            m = markers.parse(comment["body"])
            if m and (m.actor in actors or m.actor == orchestrator and m.verb == "RULING"):
                parts.append(comment["body"])
        return _joined(parts)
    numbers = ", ".join(f"#{x['number']}" for x in judged)
    parts = [f"Judgment 2, a pitch selection, on ticket #{ticket['number']}. "
             f"The pitches are the pull requests {numbers}."]
    for x in judged:
        n, summary = x["number"], x["summary"]
        parts.append(f"Pull request #{n} at head {x['git']['head']}. Its files are in pr-{n}/.")
        if summary["maker"]:
            parts.append(_body(x, summary["maker"]))
        for key in ("checkers", "judges"):
            for role in sorted(summary[key]):
                if summary[key][role]:
                    parts.append(_body(x, summary[key][role]))
    return _joined(parts)


def _media(root, head, digest):
    """The ids of the manifest rows at ``head`` whose source is ``job <digest>``, comma separated, or none."""
    text = gitstate.show(root, head, gitstate.MANIFEST) or ""
    ids = [fields[0] for fields in (line.split("\t") for line in text.splitlines()[1:])
           if len(fields) == 4 and fields[3] == f"job {digest}"]
    return ", ".join(ids) or "none"


def _operator_task(root, role, ticket, pull, found):
    n = pull["number"]
    if found["uncovered"]:
        lines = [f"Turn each request on pull request #{n} into a job file."]
        lines += [f"- {request} -> {jobs.job_path(request)}" for request in found["uncovered"]]
        lines.append(f"Each job names ticket {ticket['number']} and caller {role}. "
                     "The ticket names the tool and the expected cost.")
        return "\n".join(lines)
    lines = [f"Report on the jobs of pull request #{n}. Write no file."]
    for job in found["jobs"]:
        row = job["run_row"]
        lines.append(f"- {job['path']}: job {job['hash']} {row['verdict']} usd {row['usd']} "
                     f"compute_min {row['compute_min']}; media: {_media(root, pull['git']['head'], job['hash'])}")
    return "\n".join(lines)


def _default_task(p, state, kind, ticket, pull, judged):
    if kind == "orchestrator":
        return _orchestrator_task(p, ticket, pull, judged)
    if kind == "maker" and pull is None:
        return f"Do the stage {ticket['stage']} for unit {ticket['unit']}."
    n = pull["number"]
    if kind == "maker":
        summary = pull["summary"]
        parts = [f"Address every open note on pull request #{n}."]
        if summary["lint"] and summary["lint"]["verb"] == "FAIL":
            parts.append(_body(pull, summary["lint"]))
        for role in sorted(summary["checkers"]):
            verdict = summary["checkers"][role]
            if verdict and verdict["verb"] == "NOTES":
                parts.append(_body(pull, verdict))
        ruling = _ruling(p, state, ticket, pull)
        if ruling is not None:
            parts.append(ruling)
        return _joined(parts)
    if kind == "checker":
        return f"Review pull request #{n} at head {pull['git']['head']}."
    return f"Judge pull request #{n}."


def _fence(text):
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(4, longest + 1)


def _checked_out(root, branch):
    """Resolved paths of the worktrees that have ``refs/heads/<branch>`` checked out."""
    found, path = [], None
    for line in gitstate.git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
        elif line == "branch refs/heads/" + branch:
            found.append(path)
    return found


def _branch_exists(root, branch):
    try:
        gitstate.head(root, "refs/heads/" + branch)
    except GitError:
        return False
    return True


def _mkdirs(path):
    """Create ``path`` and its missing parents, each with mode 0700."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _undo(root, run_dir, worktree, branch, plain=False):
    """Remove what one ``prepare`` created: its worktree (or plain working directory), its new branch
    and its run directory."""
    if worktree is not None and plain:
        shutil.rmtree(worktree, ignore_errors=True)
    elif worktree is not None:
        with contextlib.suppress(GitError):
            gitstate.remove_worktree(root, worktree)
    if branch is not None:
        with contextlib.suppress(GitError):
            gitstate.git(root, "branch", "-D", branch)
    shutil.rmtree(run_dir, ignore_errors=True)


def prepare(p, gh, root, role, number, task_file=None, now=None, pair_dir=None) -> Run:
    """Check, create the worktree, write the prompt and post LAUNCH; the run, or ``LaunchError``."""
    try:
        return _prepare(p, gh, Path(root), role, number, task_file, now, pair_dir)
    except ProductionError as error:
        raise LaunchError(str(error)) from None


def _prepare(p, gh, root, role, number, task_file, now, pair_dir):
    now = now or production.now()
    # Check 1
    if tickets.frozen(gh):
        raise LaunchError("production is frozen")
    state = tickets.load(p, gh, root)
    # Check 2
    if role not in p.roles:
        raise LaunchError(f"no role {role}")
    kind = p.kinds[role]
    if kind in REFUSED_KINDS:
        raise LaunchError(REFUSED_KINDS[kind])
    # Check 3
    ticket, pull = _target(state, number)
    t = ticket["number"]
    _check_open(state, ticket, pull)
    # Check 4: an orchestrator is its room's; the other kinds are maker, checker and judge, each the
    # stage key it may fill.
    entry = production.stage(p, ticket["room"], ticket["stage"])
    if kind == "orchestrator":
        if role != production.orchestrator(p, ticket["room"]):
            raise LaunchError(f"{role} is not the orchestrator of room {ticket['room']}")
    elif role not in production.stage_roles(entry, "maker" if kind == "operator" else kind):
        raise LaunchError(f"stage {ticket['stage']} does not name {role}")
    # Check 5
    ok, reason = tickets.upstream(p, state, ticket, root)
    if not ok:
        raise LaunchError(reason)
    # Checks 8 and 6 for an orchestrator: not running on the number given, and a judgment is due there.
    if kind == "orchestrator":
        comments = pull["comments"] if pull is not None else state.ticket_comments.get(t, [])
        if _running(p, comments, role):
            raise LaunchError(f"{role} is already running on #{number}")
        if pull is not None:
            found = [e for e in steps.all_steps(p, state, ticket, root, now) if e["pull"] == number]
            due = bool(found) and found[0]["state"] == "ruling"
        else:
            due = steps.next_step(p, state, ticket, root, now)["state"] == "selection"
        if not due:
            raise LaunchError(f"no judgment is due on #{number}")
    # Check 6
    elif pull is None and kind != "maker":
        raise LaunchError("give the pull request number")
    elif pull is None and steps.next_step(p, state, ticket, root, now)["state"] != "ready":
        raise LaunchError(f"ticket #{t} has no free pitch; give the pull request number")
    # An operator writes the jobs of the requests that have none; once every request has one, it reports
    # on them, and then every job needs a counting GATE PASS and a run.
    requested = None
    if kind == "operator":
        requested = jobs.of(p, root, state, ticket, pull, role)
        if requested is None:
            raise LaunchError(f"#{number} has no request for {role}")
        if not requested["uncovered"]:
            for job in requested["jobs"]:
                if job["gate"] != "PASS":
                    raise LaunchError(f"job {job['hash']} has no counting GATE PASS")
            for job in requested["jobs"]:
                if job["run"] is None:
                    raise LaunchError(f"job {job['hash']} has not run")
    # Check 7
    reasons = gateway.check(p.settings, p.roles[role]["meta"].get("knowledge"))
    if reasons:
        raise LaunchError("the knowledge gateway is not usable: " + "; ".join(reasons))
    # Check 8
    if kind != "orchestrator" and pull is not None and _running(p, pull["comments"], role):
        raise LaunchError(f"{role} is already running on #{number}")
    if pair_dir is not None:
        if kind != "judge":
            raise LaunchError("a pair directory is only for a judge")
        if not Path(pair_dir).is_dir():
            raise LaunchError("the pair directory is not an existing directory")
    # Check 9: a resource the role needs is down.
    issues = gh.issues("open")
    found = down.blocking(p, issues, role)
    if found is not None:
        raise LaunchError(f"resource {found[0]} is down (#{found[1]})")
    # Check 10: no account of the role's slot has usage left.
    found = down.waiting(p, issues, role, now)
    if found is not None:
        raise LaunchError(f"slot {found[0]} is out of usage until {found[1]}")

    n = tickets.launches(state, t, role) + 1
    run_id = f"{number}-{n}"
    name = f"{role}-{run_id}"
    state_dir = production.state_dir(p)
    run_dir = state_dir / "runs" / name
    trees = state_dir / "worktrees"
    if os.path.lexists(run_dir) or os.path.lexists(trees / name):
        raise LaunchError(f"run {name} already exists")
    workdir = Path(pair_dir) if pair_dir is not None else trees / name
    base = production.base(p)
    head = pull["git"]["head"] if pull is not None else None
    branch = None
    judged = _judged(state, ticket, pull) if kind == "orchestrator" else []
    if kind in BRANCHED:
        branch = pull["head_ref"] if pull is not None else f"{ticket['stage']}/{ticket['key']}-{role}-{n}"
        if pull is None and _branch_exists(root, branch):
            raise LaunchError(f"branch {branch} already exists")

    # The prompt, the model and the harness version: all read before anything is created.
    if task_file is not None:
        try:
            task = Path(task_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise LaunchError("cannot read the task file") from None
    elif kind == "operator":
        task = _operator_task(root, role, ticket, pull, requested)
    else:
        task = _default_task(p, state, kind, ticket, pull, judged)
    issue = gh.issue(t)
    values = {
        "REPO": production.setting(p, "REPO"),
        "TICKET": f"#{t} [{ticket['key']}] {ticket['summary']}\n\n{issue['body']}",
        "WORKTREE": str(workdir),
        "TASK": task,
        "RULES": production.rules_text(p),
    }
    if kind in BRANCHED:
        values["BRANCH"] = branch
    elif kind != "orchestrator":
        values["PR"] = str(number)
    try:
        text = prompt.render(p.roles[role]["body"], values)
    except PromptError as error:
        raise LaunchError(str(error)) from None
    if not text.endswith("\n"):
        text += "\n"
    if kind == "checker" and pull["summary"]["maker"]:
        text += "\n## Maker's report\n\n" + _body(pull, pull["summary"]["maker"])
        if not text.endswith("\n"):
            text += "\n"
    ruling = _ruling(p, state, ticket, pull) if kind == "checker" else None
    if ruling is not None:
        text += "\n## Ruling\n\n" + ruling
        if not text.endswith("\n"):
            text += "\n"
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    try:
        model, family = harness.model(p.settings, p.roles[role]["meta"])
        version = harness.version(p.settings)
    except HarnessError as error:
        raise LaunchError(str(error)) from None
    base_sha = gitstate.head(root, base)
    orchestrator = production.orchestrator(p, ticket["room"])
    fence = _fence(text)
    record = "\n".join([
        f"[{p.actors[orchestrator]}] LAUNCH {role} {run_id}",
        f"prompt sha256: {digest}",
        f"model: {model}",
        f"family: {family}",
        f"harness: {version}",
        f"base: {base_sha}",
        f"head: {head or 'none'}",
        "",
        fence,
    ]) + "\n" + text + fence + "\n"

    # A relaunched maker's earlier worktree holds its branch; only one under the run worktrees may go.
    stale = []
    if kind in BRANCHED and pull is not None:
        stale = _checked_out(root, branch)
        mine = trees.resolve()
        if any(mine not in path.parents for path in stale):
            raise LaunchError(f"worktree: branch {branch} is checked out outside the run worktrees")

    _mkdirs(run_dir.parent)
    created = None
    plain = kind == "orchestrator"
    if plain:
        _mkdirs(trees)
        try:
            workdir.mkdir(mode=0o700)
            created = workdir
            os.chmod(workdir, 0o700)
            for x in judged:
                gitstate.export(root, x["git"]["head"], workdir / f"pr-{x['number']}")
        except GitError as error:
            _undo(root, run_dir, created, None, plain)
            raise LaunchError(f"worktree: {error}") from None
        except OSError as error:
            _undo(root, run_dir, created, None, plain)
            raise LaunchError(f"worktree: {error.strerror}") from None
    elif pair_dir is None:
        _mkdirs(trees)
        try:
            for path in stale:
                gitstate.remove_worktree(root, path)
            if kind in BRANCHED:
                gitstate.add_worktree(root, workdir, branch if pull is not None else base, branch=branch)
            else:
                gitstate.add_worktree(root, workdir, head)
        except GitError as error:
            raise LaunchError(f"worktree: {error}") from None
        created = workdir
    new_branch = branch if kind == "maker" and pull is None else None

    done = False
    try:
        try:
            run_dir.mkdir(mode=0o700)
            os.chmod(run_dir, 0o700)
            _write(run_dir / "prompt.md", data)
            _write(run_dir / "prompt.sha256", (digest + "\n").encode("ascii"))
            _write(run_dir / "mcp.json", b"")
            harness.write_mcp_config(run_dir / "mcp.json")
            (run_dir / "config").mkdir(mode=0o700)
        except OSError as error:
            raise LaunchError(f"run {name}: {error.strerror}") from None
        try:
            comment = gh.post(number, record, orchestrator)
        except Exception:
            raise LaunchError(f"could not post LAUNCH on #{number}") from None
        done = True
    finally:
        if not done:
            _undo(root, run_dir, created, new_branch, plain)

    meta = {
        "id": run_id, "role": role, "kind": kind, "actor": p.actors[role], "number": number,
        "ticket": t, "pull": pull["number"] if pull is not None else None, "room": ticket["room"],
        "stage": ticket["stage"], "unit": ticket["unit"], "branch": branch, "worktree": str(workdir),
        "blind": pair_dir is not None, "base_ref": base, "base_sha": base_sha, "head_sha": head,
        "model": model, "family": family, "harness_version": version, "prompt_sha256": digest,
        "started_at": now, "launch_comment": comment["id"],
    }
    if plain:
        meta["pulls"] = [x["number"] for x in judged]
    _write(run_dir / "run.json", (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return Run(run_dir, workdir, meta)


def load_run(run_dir) -> Run:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return Run(run_dir, Path(meta["worktree"]), meta)


def cleanup(root, run: Run) -> None:
    """Remove the run's worktree; a blind run's pair directory is not a worktree and is left alone, and
    an orchestrator's plain working directory is removed with its content."""
    if run.meta["blind"]:
        return
    if run.meta["kind"] == "orchestrator":
        shutil.rmtree(run.worktree)
        return
    gitstate.remove_worktree(root, run.worktree)
