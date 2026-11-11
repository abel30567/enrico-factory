"""Kit: every role file, format file and the pipeline of the kit validate (brief sections 5, 6 and 10)."""

from __future__ import annotations

from pathlib import Path

from .. import roles
from ..lint import Finding


def kit_dir(root: Path) -> Path | None:
    """The kit in the tree: `.factory/`, else `kit/` when it holds a `pipeline.json`, else None.

    Never the kit that contains the running `lib/`: the result depends on the root only.
    """
    if (root / ".factory").is_dir():
        return root / ".factory"
    if (root / "kit" / "pipeline.json").is_file():
        return root / "kit"
    return None


def check(root: Path) -> list[Finding]:
    kit = kit_dir(root)
    if kit is None:
        return []
    found = []
    for problem in roles.validate(kit):
        file, message = problem.split(": ", 1)
        found.append(Finding(f"{kit.name}/{file}", 1, "kit", message))
    return found
