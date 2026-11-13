"""Metric amendments: a lab loop never changes its own metric (system brief section 13, "The lab").

A metric change is a difference in `metric`, `better` or `command` between the loop file `lab/loops/<loop>.json`
on the base and at a ref. It is accepted only if the new metric orders strictly more pairs of held-out episodes
as their real retention does than the old one. The held-out list and the real retention are read in an export
of the base head, never at the ref. The comparison is `lab/metric-amendments/<loop>.tsv` at the ref: a header
`episode`, the old metric, the new metric, then one row per held-out episode with its value under each.
"""

from __future__ import annotations

import decimal
import json
import re
import tempfile

from kit.lib import agreement, gitstate, lab

PLACES = 4
CHANGED = ("metric", "better", "command")

_VALUE = re.compile(r"-?[0-9]+(\.[0-9]+)?")


class MetricError(Exception):
    pass


ERRORS = (MetricError, agreement.AgreementError, lab.LabError, gitstate.GitError)


def comparison_path(loop: str) -> str:
    return f"lab/metric-amendments/{loop}.tsv"


def share(agree: int, counted: int) -> str:
    return json.dumps(round(agree / counted, PLACES))


def comparison(text, loop: str, ref: str, old: str, new: str, episodes: list) -> dict:
    """The values `{episode: (old, new)}` of the comparison `text` at `ref`; `MetricError` on the first problem."""
    rel = comparison_path(loop)
    if text is None:
        raise MetricError(f"{rel} is missing at {ref}")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise MetricError(f"{rel} is not UTF-8 text") from None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "\t".join(("episode", old, new)):
        raise MetricError(f"{rel} line 1: header is not episode, {old}, {new}")
    held = set(episodes)
    seen = {}
    values = {}
    for n, line in enumerate(lines[1:], 2):
        where = f"{rel} line {n}: "
        cells = line.split("\t")
        if len(cells) != 3:
            raise MetricError(where + f"{len(cells)} cells, not 3")
        episode = cells[0]
        if episode not in held:
            raise MetricError(where + f"episode {episode} is not held out")
        if episode in seen:
            raise MetricError(where + f"episode {episode} is repeated (first on line {seen[episode]})")
        seen[episode] = n
        for value in cells[1:]:
            if not _VALUE.fullmatch(value):
                raise MetricError(where + f"{value} is not a number")
        values[episode] = (decimal.Decimal(cells[1]), decimal.Decimal(cells[2]))
    for episode in episodes:
        if episode not in values:
            raise MetricError(f"{rel}: no row for held-out episode {episode}")
    return values


def _ordered(better: str, kept, lost) -> bool:
    """Whether a metric orders a pair as real retention: `kept` is the value of the episode that kept more."""
    return kept > lost if better == "higher" else kept < lost


def check(root, base: str, loop: str, ref: str) -> dict:
    """The amendment of `loop` at `ref` against `base`; raises one of `ERRORS` on the first refusal.

    `{"changed": False, "old"}` when the metric is unchanged, else `{"changed": True, "old", "new", "episodes",
    "counted", "equal", "a", "b"}`: `a` and `b` are the counted pairs the old and the new metric order as real
    retention.
    """
    before = lab.load_loop(root, base, loop)
    try:
        gitstate.head(root, ref)
    except gitstate.GitError:
        raise MetricError(f"{ref} is not a commit") from None
    after = lab.load_loop(root, ref, loop)
    old, new = before["metric"], after["metric"]
    if all(before[key] == after[key] for key in CHANGED):
        return {"changed": False, "old": old}
    if old == new:
        raise MetricError(f"{lab.loop_path(loop)} at {ref} changes the metric {old} and keeps its name")
    held = agreement.held_out_path(loop)
    if gitstate.show(root, base, held) != gitstate.show(root, ref, held):
        raise MetricError(f"{held} differs at {ref}: the held-out episodes are frozen for an amendment")
    with tempfile.TemporaryDirectory(prefix="factory-metric-") as tmp:
        gitstate.export(root, base, tmp)
        spec = agreement.held_out(tmp, loop)
        episodes = spec["episodes"]
        real = {e: agreement.retention(tmp, f"{e}/{spec['platform']}") for e in episodes}
    values = comparison(gitstate.show(root, ref, comparison_path(loop)), loop, ref, old, new, episodes)
    counted = equal = a = b = 0
    for i, first in enumerate(episodes):
        for second in episodes[i + 1:]:
            if real[first] == real[second]:
                equal += 1
                continue
            counted += 1
            kept, lost = (first, second) if real[first] > real[second] else (second, first)
            a += _ordered(before["better"], values[kept][0], values[lost][0])
            b += _ordered(after["better"], values[kept][1], values[lost][1])
    if not counted:
        raise MetricError(f"the held-out episodes of loop {loop} all have the same real retention")
    return {"changed": True, "old": old, "new": new, "episodes": len(episodes), "counted": counted,
            "equal": equal, "a": a, "b": b}
