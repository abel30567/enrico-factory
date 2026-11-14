"""The requests and jobs of a pull request at a stage with an operator.

- The operator of a stage is the first name of its ``maker`` place whose kind is ``operator``. The operator
  of a pull request is the first such name that has a request on it.
- A request of a pull request for an operator is a path the pull changes that exists at its head, fully
  matches ``(?:.+/)?requests/[^/]+\\.json`` and is not inside the operator's ``writes``, while its job path,
  ``jobs/`` followed by the request path, is.
- A request is covered when its job file, read at the head, has the shape ``factory gate`` asks, names
  the pull's ticket and the operator as ``caller``, and its first input is the request with its hash at
  the head.
- A job's gate state is None, ``PASS`` (counting, as ``factory run`` counts it) or ``STOP`` (the newest
  gate comment that covers it); its run state is None, ``DONE`` or ``FAILED`` (its last ``run`` row on the
  base).

Reads git and the ledger; writes nothing.
"""

from __future__ import annotations

import re

from kit.lib import cmd_gate, cmd_run, frontmatter, gitstate, ledger, production, tickets

REQUEST = re.compile(r"(?:.+/)?requests/[^/]+\.json")
JOBS_DIR = "jobs/"


def operator(p, entry) -> str | None:
    """The stage's operator, or None."""
    return next((r for r in production.stage_roles(entry, "maker") if p.kinds.get(r) == "operator"), None)


def job_path(request: str) -> str:
    return JOBS_DIR + request


def requests(p, root, pull, role) -> list:
    """The pull's request paths for the operator ``role``, sorted."""
    head = pull["git"]["head"]
    writes = frontmatter.as_list(p.roles[role]["meta"].get("writes"))
    return [path for path in sorted(pull["git"]["changed"])
            if REQUEST.fullmatch(path) and not production.matches(path, writes)
            and production.matches(job_path(path), writes) and gitstate.show(root, head, path) is not None]


def operator_of(p, root, entry, pull) -> str | None:
    """The first operator of the stage's ``maker`` place with a request on the pull, or None."""
    return next((r for r in production.stage_roles(entry, "maker")
                 if p.kinds.get(r) == "operator" and requests(p, root, pull, r)), None)


def covering(root, pull, ticket, role, request) -> dict | None:
    """The job that covers ``request`` at the pull's head, ``{"path", "request", "job", "hash"}``, or None."""
    head = pull["git"]["head"]
    path = job_path(request)
    text = gitstate.show(root, head, path)
    if text is None:
        return None
    try:
        job, digest = cmd_gate._parsed(text, path)
    except cmd_gate.Refused:
        return None
    first = job["inputs"][0] if job["inputs"] else None
    wanted = {"path": request, "sha256": cmd_gate.sha256(gitstate.show(root, head, request))}
    if job["ticket"] != ticket["number"] or job["caller"] != role or first != wanted:
        return None
    return {"path": path, "request": request, "job": job, "hash": digest}


def _rows(p, root):
    text = gitstate.show(root, production.base(p), ledger.PATH)
    try:
        return ledger.read(text) if text is not None else []
    except ledger.LedgerError as error:
        raise tickets.TicketError(str(error)) from None


def _states(job, comments, rows):
    """``job`` with its gate state and comment, and its run state and row."""
    recorded = cmd_gate._recorded(comments, "job", job["hash"])
    if cmd_run._counting_pass({"comments": comments, "hash": job["hash"]}, rows):
        gate = "PASS"
    elif recorded is not None and ledger.gate_marker(recorded).verb == "GATE STOP":
        gate = "STOP"
    else:
        gate = None
    ran = [r for r in rows if r["kind"] == "run" and r["hash"] == job["hash"]]
    return {**job, "gate": gate, "gate_comment": recorded, "run": ran[-1]["verdict"] if ran else None,
            "run_row": ran[-1] if ran else None}


def of(p, root, state, ticket, pull, role=None) -> dict | None:
    """``{"operator", "requests", "uncovered", "jobs"}`` for an open pull, the jobs in path order; None at a
    stage with no operator or for a pull with no request."""
    if role is None:
        role = operator_of(p, root, production.stage(p, ticket["room"], ticket["stage"]), pull)
        if role is None:
            return None
    found = requests(p, root, pull, role)
    if not found:
        return None
    jobs, uncovered = [], []
    for request in found:
        job = covering(root, pull, ticket, role, request)
        if job is None:
            uncovered.append(request)
        else:
            jobs.append(job)
    if jobs:
        rows = _rows(p, root)
        comments = state.ticket_comments.get(ticket["number"], [])
        jobs = [_states(job, comments, rows) for job in jobs]
    return {"operator": role, "requests": found, "uncovered": uncovered, "jobs": jobs}
