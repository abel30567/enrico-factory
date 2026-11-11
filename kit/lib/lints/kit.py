"""Kit: every role file, format file and the pipeline of the kit validate (brief sections 5, 6 and 10)."""

from __future__ import annotations

from pathlib import Path

from .. import roles
from ..lint import Finding

RUNNING_KIT = Path(__file__).resolve().parents[2]


def kit_dir(root: Path) -> Path:
    """The kit installed in the root (`.factory/`), else the kit that contains the running `lib/`."""
    installed = root / ".factory"
    return installed if installed.is_dir() else RUNNING_KIT


def check(root: Path) -> list[Finding]:
    kit = kit_dir(root)
    found = []
    for problem in roles.validate(kit):
        file, message = problem.split(": ", 1)
        found.append(Finding(f"{kit.name}/{file}", 1, "kit", message))
    return found
