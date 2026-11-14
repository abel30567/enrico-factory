"""The judges' verdicts of each blind lock: ``registry/verdicts.tsv`` on the base (system brief section 11).

When ``factory lock`` locks a pull of a ``blind`` stage that has a locked version, it appends one row per judge
the lock relied on: ``locked_at``, ``episode``, ``stage``, ``pull``, ``role``, ``side`` (``new``, ``old`` or
``none``, from the blinding key) and ``retell``, the side the result of criterion 1 of ``audience-sim``'s evidence
names, ``-`` for another judge or when it cannot be read. Only ``factory lock`` writes the file.
"""

import re
from pathlib import Path

from kit.lib import blind, gitstate, ledger, lockrules, markers, production, tickets

PATH = "registry/verdicts.tsv"
COLUMNS = ("locked_at", "episode", "stage", "pull", "role", "side", "retell")
HEADER = "\t".join(COLUMNS)
RETELL = "audience-sim"
_CRITERION = re.compile(r"1(?![0-9])")
_RESULT = re.compile(r"(A|B|[Ee]ven)\b")


class VerdictsError(ledger.LedgerError):
    pass


def retell(p, pull, found) -> str:
    """``new``, ``old`` or ``none``: the side the result of criterion 1 in the verdict ``found`` names; ``-``
    when no table row is criterion 1 or its result does not start with ``A``, ``B`` or ``even``."""
    body = next(c["body"] for c in pull["comments"] if c["id"] == found["id"])
    reviewed = markers.reviewed(body)
    if reviewed is None:
        return "-"
    for line in body.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5 or parts[0] or parts[4]:
            continue
        if not _CRITERION.match(parts[1].strip()):
            continue
        m = _RESULT.match(parts[2].strip())
        if m is None:
            return "-"
        if m.group(1) in ("A", "B"):
            return blind.resolve(p, pull["number"], reviewed[0], "PREFERS " + m.group(1))
        return "none"
    return "-"


def rows(p, state, ticket, pull) -> list:
    """One row per judge the lock of ``pull`` relies on, by role; ``[]`` unless its stage is blind and has a
    locked version."""
    sides = lockrules.judge_sides(p, state, pull)
    if sides is None:
        return []
    locked_at = production.now()
    found = []
    for role in sorted(sides):
        side = retell(p, pull, pull["summary"]["judges"][role]) if role == RETELL else "-"
        found.append({"locked_at": locked_at, "episode": tickets.episode(ticket["unit"]), "stage": ticket["stage"],
                      "pull": str(pull["number"]), "role": role, "side": sides[role], "retell": side})
    return found


def record(p, gh, root, state, ticket, pull) -> list:
    """After the lock of ``pull``: append its rows, commit them on the base as ``factory`` and push. The lines to
    print; ``[]`` and nothing written when there is no row."""
    found = rows(p, state, ticket, pull)
    if not found:
        return []
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise VerdictsError(f"the checkout is not on {base} with a clean index")
    path = Path(root) / PATH
    committed = gitstate.show(root, "HEAD", PATH)
    current = path.read_text(encoding="utf-8", errors="surrogateescape") if path.is_file() else None
    if current != committed:
        raise VerdictsError(f"{PATH} differs from the base")
    if current is None:
        current = HEADER + "\n"
    elif current.split("\n", 1)[0] != HEADER:
        raise VerdictsError(f"{PATH} line 1: the header is not {' '.join(COLUMNS)}")
    elif not current.endswith("\n"):
        current += "\n"
    content = current + "".join("\t".join(row[name] for name in COLUMNS) + "\n" for row in found)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="surrogateescape")
    gitstate.commit(root, [PATH], f"registry: verdicts PR #{pull['number']}", "factory")
    gh.push(root, base)
    return [f"{PATH}: PR #{pull['number']} {ticket['stage']} {ticket['unit']}, {len(found)} judge(s)"]
