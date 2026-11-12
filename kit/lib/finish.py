"""The second half of one launch: run the worker, then post its marker (docs/harness.md, section 3, steps 7 and 8).

``supervise`` picks an account, builds the environment, the command line and the sandbox profile through
``kit.lib.harness`` and ``kit.lib.sandbox``, starts the worker in the run's working directory, waits for
it and hands the outcome to ``post``. ``post`` is the only place a role's marker is rendered: it refuses
a result the role may not post, commits a maker's files inside its ``writes`` globs, reads the head and
the manifest hash from git itself, pushes, opens the pull request and posts. A worker that dies, times
out or returns no valid result is answered with ``[<ROLE>] BLOCKED``: a missing result is never approval.

Nothing written here (comments, ``supervisor.log``) holds a setting value, a token or a path under
``SECRETS_DIR``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from kit.lib import blind, frontmatter, gitstate, harness, launch, markers, production, steps, tickets
from kit.lib.github import GitHubError
from kit.lib.gitstate import GitError
from kit.lib.harness import HarnessError, Outcome
from kit.lib.production import ProductionError
from kit.lib.sandbox import SandboxError
from kit.lib import sandbox

EXHAUSTED_HOURS = 5
ACCOUNTS = "accounts.json"
TOKEN_FD = 3
LAUNCHER_SUMMARY = "The launcher posted this: the run ended without an accepted result."
_NEWLINE = re.compile(r"\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


# -- the marker

def _line(value) -> str:
    """``value`` on one line: every line break becomes a space."""
    return _NEWLINE.sub(" ", str(value))


def _cell(value) -> str:
    return _line(value).replace("|", "/")


def _blinding_id(p, meta) -> str:
    if not meta["blind"]:
        return "none"
    key = blind.key_path(p, meta["pull"], meta["head_sha"])
    return json.loads(key.read_text(encoding="utf-8"))["id"]


def _result_lines(p, meta, result) -> list[str]:
    """Evidence, notes, addressed entries, vote reasons, the preference and the role's own BLOCKED lines."""
    lines = []
    if result.get("evidence"):
        lines += ["| Criterion | Result | Command |", "|---|---|---|"]
        lines += [f"| {_cell(e['criterion'])} | {_cell(e['result'])} | {_cell(e['command'])} |"
                  for e in result["evidence"]]
    notes = result.get("notes", [])
    for severity in ("Blocker", "Issue"):
        lines += [f"{_line(n['id'])} ({severity}): {_line(n['text'])} Evidence: {_line(n['evidence'])}"
                  for n in notes if n["severity"] == severity]
    lines += [f"- {_line(a['note'])} by {_line(a['by'])}: {a['action']}. {_line(a['text'])}"
              for a in result.get("addressed", [])]
    if "vote" in result:
        lines += [f"- {_line(reason)}" for reason in result["vote"]["reasons"]]
    if meta["kind"] == "judge" and "prefers" in result:
        cited = "; ".join(_line(c) for c in result["prefers"]["cited"]) or "none"
        lines.append(f"Prefers: {result['prefers']['choice']}. Cited: {cited}. "
                     f"Blinding id: {_blinding_id(p, meta)}")
    if "blocked" in result:
        blocked = result["blocked"]
        lines += [f"Missing: {_line(blocked['missing'])}", f"What was tried: {_line(blocked['tried'])}",
                  f"Resume from: {_line(blocked['resume_from'])}"]
    return lines


def render(p, run, result, missing, dropped, head, manifest) -> str:
    """The marker body. ``missing`` set: the launcher's BLOCKED; ``dropped`` is None for a checker or judge."""
    meta = run.meta
    if missing is not None:
        verb, summary = "BLOCKED", LAUNCHER_SUMMARY
    else:
        verb, summary = result["marker"], result["summary"]
    first = f"[{meta['actor']}] {verb}"
    if verb == "SELECTED":
        first += f" #{result['selected']}"
    elif verb == "VOTE":
        first += f" {result['vote']['for']}-{result['vote']['against']}"
    lines = [first, "", _line(summary), ""]
    if missing is None:
        lines += _result_lines(p, meta, result)
    else:
        where = f"{meta['branch']} @ {head}" if meta["kind"] == "maker" else f"PR #{meta['pull']} @ {head}"
        lines += [f"Missing: {_line(missing)}", f"What was tried: launch {meta['id']}", f"Resume from: {where}"]
    if dropped is not None:
        lines.append("Dropped (outside writes): " + (", ".join(_line(d) for d in dropped) or "none"))
    lines.append(f"Launch: {meta['id']}")
    reviewed = "reviewed " if markers.is_verdict(markers.parse(first)) else ""
    lines.append(f"{reviewed}head {head} manifest {manifest}")
    return "\n".join(lines) + "\n"


# -- what a run leaves behind

def _missing(meta, outcome):
    """Why the outcome is not a result the role may post, or None."""
    if outcome.status != "ok":
        return f"a valid result ({outcome.reason})"
    problems = harness.validate_result(meta["kind"], meta["actor"], outcome.result)
    return f"a valid result ({problems[0]})" if problems else None


def _ticket(gh, meta) -> dict:
    ticket = tickets.parse(gh.issue(meta["ticket"]))
    if ticket is None:
        raise tickets.TicketError(f"ticket #{meta['ticket']} is not tracked")
    return ticket


def _finish_maker(p, gh, root, run, result, missing):
    """Commit inside ``writes``, push and open the pull: ``(missing, dropped, head, number to post on)``."""
    meta, worktree = run.meta, run.worktree
    branch = meta["branch"]
    start = meta["head_sha"] or meta["base_sha"]
    writes = frontmatter.as_list(p.roles[meta["role"]]["meta"].get("writes"))
    dropped = [path for path in gitstate.changed(worktree, start, "HEAD")
               if not production.matches(path, writes)]
    if dropped:
        gitstate.git(worktree, "reset", "--hard", start)
        missing = missing or "commits only inside writes"
    accepted = missing is None and result["marker"] != "BLOCKED"
    if accepted:
        ticket = _ticket(gh, meta)
        paths = gitstate.status(worktree)
        kept = [path for path in paths if production.matches(path, writes)]
        dropped = [path for path in paths if path not in kept]
        if kept:
            message = f"[{ticket['key']}] {meta['stage']}: {_line(result['summary'])[:60]}"
            gitstate.commit(worktree, kept, message, meta["role"])
        gitstate.restore(worktree, dropped)
        if meta["pull"] is None and gitstate.head(worktree) == start:
            missing, accepted = "any change inside writes", False
    head = gitstate.head(worktree)
    number = meta["pull"] or meta["ticket"]
    if accepted:
        gh.push(root, branch)
        number = gh.open_pull(branch, meta["base_ref"], f"[{ticket['key']}] {ticket['summary']}",
                              f"Ticket: #{meta['ticket']}\n")
    return missing, dropped, head, number


def post(p, gh, root, run, outcome):
    """Post the role's marker for ``outcome`` (a ``harness.Outcome``) and return the comment."""
    meta = run.meta
    missing = _missing(meta, outcome)
    result = outcome.result if missing is None else None
    if meta["kind"] == "maker":
        missing, dropped, head, number = _finish_maker(p, gh, root, run, result, missing)
        manifest = gitstate.manifest(root, meta["base_ref"], meta["branch"])
    else:
        # Nothing of a checker's or judge's is committed; the head under review is the one prepare read.
        dropped, head, number = None, meta["head_sha"], meta["pull"]
        manifest = gitstate.manifest(root, meta["base_ref"], head)
    body = render(p, run, result, missing, dropped, head, manifest)
    return gh.post(number, body, meta["role"])


# -- running the worker

def _secrets_dir(p) -> str:
    value = production.setting(p, "SECRETS_DIR")
    return os.path.expanduser(value) if value.startswith("~/") else value


def _endpoints(settings, family) -> tuple:
    """The model endpoint, and for family ``other`` the local proxy's port."""
    found = ["*:443"]
    if family == "other":
        url = urllib.parse.urlsplit(settings.get("PROXY_URL", ""))
        try:
            port = url.port
        except ValueError:
            port = None
        port = port or {"http": 80, "https": 443}.get(url.scheme)
        if port is None:
            raise SandboxError("setting PROXY_URL names no port")
        found.append(f"localhost:{port}")
    return tuple(found)


def _exhausted(state_dir) -> dict:
    """``{account: time it is usable again}`` from ``accounts.json``; empty when there is none."""
    try:
        data = json.loads((state_dir / ACCOUNTS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HarnessError(f"{ACCOUNTS} is not a JSON object")
    return data


def _exhaust(state_dir, account, now) -> None:
    """Mark ``account`` out of usage for EXHAUSTED_HOURS from ``now``: read again, write, replace."""
    data = _exhausted(state_dir)
    until = datetime.strptime(now, production.TIME_FORMAT) + timedelta(hours=EXHAUSTED_HOURS)
    data[account] = until.strftime(production.TIME_FORMAT)
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".accounts-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, state_dir / ACCOUNTS)


def _pick(settings, slot, state_dir, now):
    return harness.pick(harness.accounts(settings, slot), _exhausted(state_dir), now)


def _run_once(argv, env, run, token, timeout_min) -> Outcome:
    out, err = run.dir / "stdout", run.dir / "stderr"
    process = harness.start(argv, env, run.worktree, run.dir / "prompt.md", token, out, err, TOKEN_FD)
    try:
        code = process.wait(timeout=timeout_min * 60)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return Outcome("failed", None, "timed out")
    return harness.read(out.read_text(encoding="utf-8", errors="replace"), code,
                        err.read_text(encoding="utf-8", errors="replace"))


def _outcome(p, root, run) -> Outcome:
    settings, meta = p.settings, run.meta
    role_meta = p.roles[meta["role"]]["meta"]
    slot = "OTHER" if meta["family"] == "other" else role_meta["model"].upper()
    state_dir = production.state_dir(p)
    now = production.now()
    try:
        timeout_min = float(production.setting(p, "RUN_TIMEOUT_MIN", steps.RUN_TIMEOUT_MIN))
    except ValueError:
        raise ProductionError("setting RUN_TIMEOUT_MIN is not a number") from None
    account = None
    if slot != "OTHER":
        account = _pick(settings, slot, state_dir, now)
        if account is None:
            return Outcome("out_of_usage", None, "every account is out of usage")
    env = harness.environment(os.environ, settings, meta["family"], run.dir / "config", TOKEN_FD)
    argv = harness.command(settings, role_meta, run.dir / "mcp.json")
    profile = sandbox.profile(root, run.worktree, run.dir, state_dir, _secrets_dir(p), str(Path.home()),
                              web=sandbox.web(role_meta), endpoints=_endpoints(settings, meta["family"]))
    sandbox.write(run.dir / "sandbox.sb", profile)
    argv = sandbox.apply(settings, argv, run.dir / "sandbox.sb")
    for attempt in range(2):
        if attempt:
            account = _pick(settings, slot, state_dir, now)
            if account is None:
                return Outcome("out_of_usage", None, "every account is out of usage")
        outcome = _run_once(argv, env, run, harness.token_path(settings, slot, account), timeout_min)
        if outcome.status != "out_of_usage" or account is None:
            return outcome
        _exhaust(state_dir, account, now)
    return outcome


def describe(error) -> str:
    """An error as fixed words, key names and exit statuses: never a value, a token or a path."""
    if isinstance(error, (GitHubError, HarnessError, SandboxError, ProductionError, tickets.TicketError)):
        return str(error)
    if isinstance(error, GitError):
        return f"git {error.command}: exit {error.status}"
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return type(error).__name__


def _log(run, text) -> None:
    with open(run.dir / "supervisor.log", "a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def supervise(p, gh, root, run) -> int:
    """Run the worker and post its marker: 0 a marker, 3 BLOCKED, 2 nothing could be posted."""
    try:
        outcome = _outcome(p, root, run)
    except (HarnessError, SandboxError, ProductionError, OSError) as error:
        outcome = Outcome("failed", None, describe(error))
    code = 2
    try:
        comment = post(p, gh, root, run, outcome)
    except Exception as error:  # whatever stopped the post, the run must say so without a secret
        _log(run, f"launch {run.meta['id']}: nothing posted: {type(error).__name__}: {describe(error)}")
    else:
        verb = markers.parse(comment["body"]).verb
        code = 3 if verb == "BLOCKED" else 0
        print(f"posted [{run.meta['actor']}] {verb} for launch {run.meta['id']}")
    if run.meta["kind"] != "maker":
        try:
            launch.cleanup(root, run)
        except GitError as error:
            _log(run, f"launch {run.meta['id']}: worktree not removed: {describe(error)}")
    return code
