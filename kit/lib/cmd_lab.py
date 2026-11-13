"""factory lab control|run <loop> [<ref>] [--root DIR] [--apply]: one no-op control or one experiment of a lab loop.

The loop file `lab/loops/<loop>.json` and the results log `lab/results.tsv` are read on the base, and the
metric runs in an export of the base head: the harness, the metric and the threshold are frozen for the
experimenter. `control` measures the base head itself; `run` measures the commit `<ref>` names.
Prints one line per row: `lab <loop> <kind> <commit>: <verdict> values <values> best <best> threshold
<threshold>`. Without `--apply` it writes nothing. With it, it first brings the local base up to GitHub's,
appends the row on the base, pushes it and, for a verdict, posts `[AUTORESEARCH] <VERDICT>` on the loop's
ticket. Exit 0 when a row is printed, whatever its verdict; 2 on usage, refusal or error.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import gitstate, github, lab, production, tickets  # noqa: E402

KINDS = ("control", "run")
ERRORS = (lab.LabError, production.ProductionError, github.GitHubError, gitstate.GitError)


def _text(value) -> str:
    return lab.NONE if value is None else lab.number(value)


def _values(values) -> list:
    return [v if v == lab.FLOOR else lab.number(v) for v in values]


def _due(loop, spec, now, kind):
    """``LabError`` when a run must wait for controls."""
    if kind != "run":
        return
    if now["threshold"] is None:
        raise lab.LabError(f"{loop}: {now['controls']} of {spec['controls']} no-op controls; "
                           f"run factory lab control {loop} first")
    if now["since"] >= spec["drift_every"]:
        raise lab.LabError(f"{loop}: a no-op control is due after {spec['drift_every']} runs; "
                           f"run factory lab control {loop} first")


def _control(loop, spec, now, harness):
    value = lab.measure(loop, spec["command"], harness, harness)
    if value == lab.FLOOR:
        raise lab.LabError(f"{loop}: the no-op control breaks a frozen floor (metric exit 1)")
    if now["threshold"] is None:
        return [value], lab.NONE
    return [value], lab.REBASELINE if lab.drifted(value, now) else lab.NONE


def _run(loop, spec, now, harness, tree):
    first = lab.measure(loop, spec["command"], harness, tree)
    if first == lab.FLOOR:
        return [first], "DISCARD"
    gained = lab.gain(spec, first, now["best"])
    if gained > now["threshold"]:
        second = lab.measure(loop, spec["command"], harness, tree)
        if second == lab.FLOOR:
            return [first, second], "DISCARD"
        if lab.gain(spec, second, now["best"]) > now["threshold"]:
            return [first, second], "KEEP"
        return [first, second], "NOISE"
    if gained < -now["threshold"]:
        return [first], "DISCARD"
    return [first], "NOISE"


def _body(loop, spec, row, now, row_line) -> str:
    return "\n".join([
        f"[{lab.ACTOR}] {row['verdict']}",
        "",
        f"loop: {loop}",
        f"metric: {spec['metric']} ({spec['better']} is better)",
        f"commit: {row['commit']}",
        "runs: " + ", ".join(row["values"].split(",")),
        f"best: {row['best']}",
        f"noise floor: {_text(now['floor'])} over {spec['controls']} controls",
        f"threshold: {row['threshold']}",
        f"results: {row_line}",
    ]) + "\n"


def _lab(p, gh, kind, loop, ref, apply) -> int:
    base = production.base(p)
    spec = lab.load_loop(p.root, base, loop)
    if kind == "run" and ref is None:
        raise lab.LabError(f"{loop}: run needs a ref")
    if kind == "control" and ref is not None:
        raise lab.LabError(f"{loop}: a control takes no ref")
    base_head = gitstate.head(p.root, base)
    commit = base_head
    if kind == "run":
        commit = gitstate.head(p.root, ref)
        if commit == base_head:
            raise lab.LabError(f"{loop}: {ref} is the base head; a no-op is a control")
    if apply:
        gh.issue(spec["ticket"])
    text = gitstate.show(p.root, base, lab.PATH)
    rows = lab.read(text) if text is not None else []
    now = lab.standing(rows, loop, spec)
    _due(loop, spec, now, kind)
    with tempfile.TemporaryDirectory(prefix="factory-lab-") as tmp:
        harness = Path(tmp) / "harness"
        gitstate.export(p.root, base_head, harness)
        if kind == "control":
            values, verdict = _control(loop, spec, now, harness)
        else:
            tree = Path(tmp) / "tree"
            gitstate.export(p.root, commit, tree)
            values, verdict = _run(loop, spec, now, harness, tree)
    row = {"time": production.now(), "loop": loop, "kind": kind, "commit": commit,
           "values": ",".join(_values(values)), "best": _text(now["best"]),
           "threshold": _text(now["threshold"]), "verdict": verdict}
    row_line = lab.line(row)
    if apply:
        row_line = lab.append(p, gh, p.root, row)
        if verdict != lab.NONE:
            gh.post(spec["ticket"], _body(loop, spec, row, now, row_line), lab.ROLE)
    print(f"lab {loop} {kind} {commit}: {verdict} values {row['values']} best {row['best']} "
          f"threshold {row['threshold']}")
    if kind == "control" and now["threshold"] is None and now["controls"] + 1 == spec["controls"]:
        done = lab.standing(rows + [row], loop, spec)
        print(f"lab {loop} threshold {lab.number(done['threshold'])}: twice the noise floor "
              f"{lab.number(done['floor'])} over {spec['controls']} controls, best {lab.number(done['best'])}")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="factory lab", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("kind", choices=KINDS, help="a no-op control of the base head, or a run of <ref>")
    parser.add_argument("loop", help="the loop id: lab/loops/<loop>.json on the base")
    parser.add_argument("ref", nargs="?", help="the commit a run measures")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="append the row on the base and post the marker")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        if args.apply:
            gh.sync_base(p.root, production.base(p))
        if tickets.frozen(gh):
            print("lab: frozen", file=sys.stderr)
            return 2
        return _lab(p, gh, args.kind, args.loop, args.ref, args.apply)
    except ERRORS as error:
        print(f"lab: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
