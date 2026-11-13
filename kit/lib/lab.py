"""The lab's rules and its results log (system brief sections 7, 8 and 13).

A loop is ``lab/loops/<loop>.json`` on the base. ``lab/results.tsv`` is append-only and written only by
``factory lab``, straight on the base branch: one row per no-op control and per experiment, kept or not.
The epoch of a loop is its rows after its last ``REBASELINE`` row; its baseline is the epoch's first
``controls`` control rows, whose noise floor sets the keep threshold at twice it. The metric starts only
in ``measure``, as an argument list, never through a shell.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
from pathlib import Path

from kit.lib import evals, gitstate, production

PATH = "lab/results.tsv"
COLUMNS = ("time", "loop", "kind", "commit", "values", "best", "threshold", "verdict")
HEADER = "\t".join(COLUMNS)
LOOP_KEYS = ("better", "command", "controls", "drift_every", "metric", "ticket")
BETTER = ("higher", "lower")
CONTROLS = (5, 10)
DRIFT_EVERY = (10, 15)
FACTOR = 2
FLOOR = "floor"
NONE = "-"
REBASELINE = "REBASELINE"
ROLE = "program:lab"
ACTOR = "AUTORESEARCH"

_LOOP_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


class LabError(Exception):
    pass


def loop_path(loop: str) -> str:
    return f"lab/loops/{loop}.json"


def _integer(value, low, high) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _shaped(spec) -> bool:
    if not isinstance(spec, dict) or sorted(spec) != list(LOOP_KEYS):
        return False
    metric, command, ticket = spec["metric"], spec["command"], spec["ticket"]
    return (isinstance(metric, str) and bool(metric) and not any(c.isspace() for c in metric)
            and spec["better"] in BETTER
            and isinstance(command, list) and bool(command)
            and all(isinstance(part, str) and part for part in command)
            and _integer(spec["controls"], *CONTROLS)
            and _integer(spec["drift_every"], *DRIFT_EVERY)
            and isinstance(ticket, int) and not isinstance(ticket, bool) and ticket > 0)


def load_loop(root, base: str, loop: str) -> dict:
    """The loop file of ``loop`` at the base; ``LabError`` when the id, the file or its shape is wrong."""
    if not _LOOP_ID.fullmatch(loop):
        raise LabError(f"'{loop}' is not a loop id")
    text = gitstate.show(root, base, loop_path(loop))
    if text is None:
        raise LabError(f"{loop_path(loop)} is not on {base}")
    try:
        spec = json.loads(text)
    except ValueError:
        spec = None
    if not _shaped(spec):
        raise LabError(f"{loop_path(loop)} is not {{metric, better, command, controls, drift_every, ticket}}")
    return spec


def number(value) -> str:
    """A number as the log writes it: rounded to ``evals.PLACES`` places, as JSON."""
    return json.dumps(round(float(value), evals.PLACES))


def read(text: str) -> list[dict]:
    """The rows of the results log ``text``; ``LabError("results line <n>: ...")`` on the first bad line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != HEADER:
        raise LabError("results line 1: the header is not " + " ".join(COLUMNS))
    rows = []
    for n, row_line in enumerate(lines[1:], 2):
        fields = row_line.split("\t")
        if len(fields) != len(COLUMNS):
            raise LabError(f"results line {n}: {len(fields)} columns, not {len(COLUMNS)}")
        rows.append(dict(zip(COLUMNS, fields)))
    return rows


def line(row: dict) -> str:
    """The results line for ``row``, without a newline."""
    return "\t".join(row[name] for name in COLUMNS)


def standing(rows: list, loop: str, spec: dict) -> dict:
    """Where ``loop`` stands in its epoch.

    ``{"controls", "floor", "mean", "threshold", "best", "since"}``: the number of controls in the
    epoch, then the baseline's noise floor and mean, the keep threshold and the best (all None until the
    baseline is complete), and the number of run rows after the epoch's last control row.
    """
    mine = [r for r in rows if r["loop"] == loop]
    ends = [i for i, r in enumerate(mine) if r["verdict"] == REBASELINE]
    epoch = mine[ends[-1] + 1:] if ends else mine
    controls = [i for i, r in enumerate(epoch) if r["kind"] == "control"]
    since = sum(1 for r in epoch[controls[-1] + 1:] if r["kind"] == "run") if controls else 0
    out = {"controls": len(controls), "floor": None, "mean": None, "threshold": None, "best": None,
           "since": since}
    if len(controls) < spec["controls"]:
        return out
    baseline = [float(epoch[i]["values"]) for i in controls[:spec["controls"]]]
    spread = evals.noise(baseline)
    out.update(floor=spread["floor"], mean=spread["mean"], threshold=round(FACTOR * spread["floor"], evals.PLACES),
               best=spread["mean"])
    for r in epoch[controls[spec["controls"] - 1] + 1:]:
        if r["kind"] == "run" and r["verdict"] == "KEEP":
            out["best"] = round(statistics.fmean(float(v) for v in r["values"].split(",")), evals.PLACES)
    return out


def gain(spec: dict, value: float, best: float) -> float:
    """How much better ``value`` is than ``best``, rounded: negative when it is worse."""
    change = value - best if spec["better"] == "higher" else best - value
    return round(change, evals.PLACES)


def drifted(value: float, now: dict) -> bool:
    """Whether a control after the baseline is further from the baseline's mean than the threshold."""
    return round(abs(value - now["mean"]), evals.PLACES) > now["threshold"]


def measure(loop: str, command: list, harness, tree):
    """One value of the metric: ``command`` run with no shell in ``harness`` with ``FACTORY_LAB_TREE`` set
    to ``tree``. The value rounded, or ``FLOOR`` when the metric exits 1; ``LabError`` on any other exit or
    when its last non-empty line is not a number."""
    env = dict(os.environ)
    env["FACTORY_LAB_TREE"] = str(Path(tree).resolve())
    try:
        done = subprocess.run(command, cwd=str(harness), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace")
    except OSError:
        # What a shell reports for a command that cannot be started.
        raise LabError(f"{loop}: the metric exited 127") from None
    if done.returncode == 1:
        return FLOOR
    if done.returncode != 0:
        raise LabError(f"{loop}: the metric exited {done.returncode}")
    printed = [text.strip() for text in done.stdout.splitlines() if text.strip()]
    if not printed or not _NUMBER.fullmatch(printed[-1]):
        raise LabError(f"{loop}: the metric printed no number on its last line")
    return round(float(printed[-1]), evals.PLACES)


def append(p, gh, root, row: dict) -> str:
    """Append ``row`` to the results log, commit it on the base as ``factory`` and push. Returns the line."""
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise LabError(f"the checkout is not on {base} with a clean index")
    new = line(row)
    path = Path(root) / PATH
    current = path.read_text(encoding="utf-8") if path.is_file() else HEADER + "\n"
    content = current + new + "\n"
    committed = gitstate.show(root, "HEAD", PATH) or ""
    if not current.startswith(committed):
        raise LabError(f"{PATH} differs from the base")
    read(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    gitstate.commit(root, [PATH], f"lab: {row['loop']} {row['kind']} {row['verdict']}", "factory")
    gh.push(root, base)
    return new
