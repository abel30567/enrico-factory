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

from kit.lib import gitstate, harness, markers, production, prompt, steps, tickets
from kit.lib.gitstate import GitError
from kit.lib.harness import HarnessError
from kit.lib.production import ProductionError
from kit.lib.prompt import PromptError

REFUSED_KINDS = {
    "orchestrator": "an orchestrator is not launched by factory launch",
    "operator": "gated launches are not built in wave 2",
    "analyst": "analyst launches are not built in wave 2",
}


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


def _running(p, pull, role):
    """Whether the pull's newest ``LAUNCH <role>`` has no later marker of the role's actor."""
    marked = [markers.parse(c["body"]) for c in sorted(pull["comments"], key=_order)]
    launched = [i for i, m in enumerate(marked) if m and m.verb == "LAUNCH" and m.args[0] == role]
    if not launched:
        return False
    return not any(m and m.actor == p.actors[role] for m in marked[launched[-1] + 1:])


def _body(pull, entry):
    """The body of the pull's comment that a ``markers.summary`` entry names."""
    return next(c["body"] for c in pull["comments"] if c["id"] == entry["id"])


def _default_task(kind, ticket, pull):
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
        return "\n\n".join(part.rstrip("\n") for part in parts)
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


def _undo(root, run_dir, worktree, branch):
    """Remove what one ``prepare`` created: its worktree, its new branch and its run directory."""
    if worktree is not None:
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
    # Check 4: the kinds left are maker, checker and judge, each the stage key it may fill.
    entry = production.stage(p, ticket["room"], ticket["stage"])
    if role not in production.stage_roles(entry, kind):
        raise LaunchError(f"stage {ticket['stage']} does not name {role}")
    # Check 5
    ok, reason = tickets.upstream(p, state, ticket, root)
    if not ok:
        raise LaunchError(reason)
    # Check 6
    if pull is None and kind != "maker":
        raise LaunchError("give the pull request number")
    if pull is None and steps.next_step(p, state, ticket, root, now)["state"] != "ready":
        raise LaunchError(f"ticket #{t} has no free pitch; give the pull request number")
    # Check 7
    if p.settings.get("GATEWAY", "") == "on":
        raise LaunchError("the knowledge gateway is not built; set GATEWAY=off")
    # Check 8
    if pull is not None and _running(p, pull, role):
        raise LaunchError(f"{role} is already running on #{number}")
    if pair_dir is not None:
        if kind != "judge":
            raise LaunchError("a pair directory is only for a judge")
        if not Path(pair_dir).is_dir():
            raise LaunchError("the pair directory is not an existing directory")

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
    if kind == "maker":
        branch = pull["head_ref"] if pull is not None else f"{ticket['stage']}/{ticket['key']}-{role}-{n}"
        if pull is None and _branch_exists(root, branch):
            raise LaunchError(f"branch {branch} already exists")

    # The prompt, the model and the harness version: all read before anything is created.
    if task_file is not None:
        try:
            task = Path(task_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise LaunchError("cannot read the task file") from None
    else:
        task = _default_task(kind, ticket, pull)
    issue = gh.issue(t)
    values = {
        "REPO": production.setting(p, "REPO"),
        "TICKET": f"#{t} [{ticket['key']}] {ticket['summary']}\n\n{issue['body']}",
        "WORKTREE": str(workdir),
        "TASK": task,
        "RULES": production.rules_text(p),
    }
    if kind == "maker":
        values["BRANCH"] = branch
    else:
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
    if kind == "maker" and pull is not None:
        stale = _checked_out(root, branch)
        mine = trees.resolve()
        if any(mine not in path.parents for path in stale):
            raise LaunchError(f"worktree: branch {branch} is checked out outside the run worktrees")

    _mkdirs(run_dir.parent)
    created = None
    if pair_dir is None:
        _mkdirs(trees)
        try:
            for path in stale:
                gitstate.remove_worktree(root, path)
            if kind == "maker":
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
            _undo(root, run_dir, created, new_branch)

    meta = {
        "id": run_id, "role": role, "kind": kind, "actor": p.actors[role], "number": number,
        "ticket": t, "pull": pull["number"] if pull is not None else None, "room": ticket["room"],
        "stage": ticket["stage"], "unit": ticket["unit"], "branch": branch, "worktree": str(workdir),
        "blind": pair_dir is not None, "base_ref": base, "base_sha": base_sha, "head_sha": head,
        "model": model, "family": family, "harness_version": version, "prompt_sha256": digest,
        "started_at": now, "launch_comment": comment["id"],
    }
    _write(run_dir / "run.json", (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return Run(run_dir, workdir, meta)


def load_run(run_dir) -> Run:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return Run(run_dir, Path(meta["worktree"]), meta)


def cleanup(root, run: Run) -> None:
    """Remove the run's worktree; a blind run's pair directory is not a worktree and is left alone."""
    if run.meta["blind"]:
        return
    gitstate.remove_worktree(root, run.worktree)
