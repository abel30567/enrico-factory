"""`factory lint --post <pr>`: run the lints a pull request's stage names on its head (brief sections 8, 9, 15).

The tree checked is the pull request's head commit, exported to a temporary directory, never the
caller's working directory. The body ends with ``reviewed head <sha> manifest <hash>``.
"""

from __future__ import annotations

import tempfile

from kit.lib import gitstate, lint, markers, production, tickets

ROLE = "program:lint"
EXIT = {lint.PASS: 0, lint.FAIL: 1, lint.ERROR: 2}


class PostError(Exception):
    """The pull request cannot be linted for posting: not open, no tracked ticket, no stage, no lint."""


def _target(p, gh, number):
    """``(pull, stage entry)`` for an open pull request naming a tracked ticket whose stage names a lint."""
    pull = gh.pull(number)
    if pull["state"] != "open":
        raise PostError(f"pull request #{number} is not open")
    ticket_number = pull.get("ticket")
    ticket = None
    if isinstance(ticket_number, int) and not isinstance(ticket_number, bool):
        ticket = tickets.parse(gh.issue(ticket_number))
    if ticket is None:
        raise PostError(f"pull request #{number} names no tracked ticket")
    entry = production.stage(p, ticket["room"], ticket["stage"])
    if entry is None:
        raise PostError(f"stage {ticket['room']}/{ticket['stage']} is not in the pipeline")
    if not entry.get("lint"):
        raise PostError(f"stage {ticket['stage']} names no lint")
    return pull, entry


def _is_lint(comment):
    marker = markers.parse(comment["body"])
    return comment.get("role") == ROLE and marker is not None and marker.actor == "LINT"


def posted(p, gh, root, number):
    """The newest ``[LINT]`` comment by ``program:lint`` when it counts and names the current head and
    manifest exactly, else None."""
    pull, entry = _target(p, gh, number)
    comments = gh.comments(number)
    ours = [c for c in sorted(comments, key=lambda c: (c["created_at"], c["id"])) if _is_lint(c)]
    if not ours:
        return None
    last = ours[-1]
    current = (gitstate.head(root, pull["head_ref"]), gitstate.manifest(root, pull["base_ref"], pull["head_ref"]))
    if markers.reviewed(last["body"]) != current:
        return None
    if not markers.counts(last, gitstate.pull_state(root, pull, comments), entry, p.kinds)[0]:
        return None
    return last


def run(p, gh, root, number):
    """``(body, results)``: the stage's lints run on the pull request's head, and the ``[LINT]`` body."""
    pull, entry = _target(p, gh, number)
    names = list(entry["lint"])
    head = gitstate.head(root, pull["head_ref"])
    with tempfile.TemporaryDirectory(prefix="factory-lint-") as tree:
        gitstate.export(root, head, tree)
        results = lint.run(tree, only=names)
    manifest = gitstate.manifest(root, pull["base_ref"], pull["head_ref"])
    body = (lint.marker(results) + "\n"
            + f"Checks required by stage {entry['stage']}: {', '.join(names)}\n"
            + f"reviewed head {head} manifest {manifest}\n")
    return body, results
