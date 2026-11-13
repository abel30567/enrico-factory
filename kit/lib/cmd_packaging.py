"""factory packaging assign <loop> <ep> <change>|measure <loop> [--root DIR] [--apply]: the live packaging loop, one change per post, measured on its frozen window.

The loop file `lab/packaging/<loop>.json`, the experiment files, the assignment log `lab/assignments.tsv`,
the results log `lab/results.tsv` and every snapshot are read on the base. `assign` gives the post
`<ep>/<platform>` its one change (`-` for a no-op control) before its window. `measure` reads the loop's
posts in the order they were assigned: each no-op control is one control row, each change one run row
from its first post's window, and a gain above the threshold waits for the change's second post. Prints
one line per event. Without `--apply` it writes nothing. With it, it first brings the local base up to
GitHub's, appends each row on the base and pushes it and, for a verdict, posts `[AUTORESEARCH] <VERDICT>` on
the loop's ticket. Exit 0 when it ran, whatever it printed; 2 on usage, refusal or error.
"""

import argparse
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import gitstate, github, lab, packaging, production, tickets  # noqa: E402

VERBS = ("assign", "measure")
ERRORS = (packaging.PackagingError, lab.LabError, production.ProductionError, github.GitHubError,
          gitstate.GitError)


def _text(value) -> str:
    return lab.NONE if value is None else lab.number(value)


def _assign(p, gh, loop, ep, change, apply) -> int:
    base = production.base(p)
    spec = packaging.load_loop(p.root, base, loop)
    post = f"{packaging.episode(ep)}/{spec['platform']}"
    if packaging.change_id(change) != lab.NONE and gitstate.show(p.root, base,
                                                                 packaging.experiment_path(change)) is None:
        raise packaging.PackagingError(f"{packaging.experiment_path(change)} is not on {base}")
    rows = packaging.assignments(p.root, base)
    for row in rows:
        if row["post"] == post:
            raise packaging.PackagingError(f"{post} already carries a change: {packaging.PATH} line {row['line']}")
    if gitstate.show(p.root, base, packaging.snapshot_path(ep, spec["platform"])) is not None:
        raise packaging.PackagingError(f"{packaging.snapshot_path(ep, spec['platform'])} is on {base}: "
                                       "a change is assigned before the window")
    if change != lab.NONE:
        mine = [row for row in rows if row["change"] == change]
        for row in mine:
            if row["loop"] != loop:
                raise packaging.PackagingError(f"{change} is a change of loop {row['loop']}")
        if len(mine) >= packaging.POSTS:
            raise packaging.PackagingError(f"{change} is already on {packaging.POSTS} posts")
    if apply:
        packaging.append(p, gh, p.root, {"time": production.now(), "loop": loop, "post": post, "change": change})
    print(f"packaging {loop} assign {post}: {change}")
    return 0


def _units(rows, loop):
    """The loop's units in order: one per no-op control row, one per change at the line of its first post."""
    units, runs = [], {}
    for row in rows:
        if row["loop"] != loop:
            continue
        if row["change"] == lab.NONE:
            units.append({"kind": "control", "change": lab.NONE, "rows": [row]})
        elif row["change"] in runs:
            runs[row["change"]]["rows"].append(row)
        else:
            runs[row["change"]] = {"kind": "run", "change": row["change"], "rows": [row]}
            units.append(runs[row["change"]])
    return units


def _values(p, base, spec, loop, unit, say):
    """The unit's ``(row, value)`` pairs in order, up to its first post that waits, and that post or None."""
    found = []
    for row in unit["rows"]:
        kind, what = packaging.value(p.root, base, spec, row["post"].split("/", 1)[0])
        if kind == packaging.WAITS:
            return found, row
        if kind == packaging.SKIPPED:
            say(f"packaging {loop} {unit['kind']} {row['post']}: skipped: {what}")
            continue
        found.append((row, what))
    return found, None


def _verdict(spec, now, unit, found):
    """``(pairs measured, verdict)``, or ``(None, gain)`` when a first gain above the threshold waits."""
    if unit["kind"] == "control":
        if now["threshold"] is None:
            return found[:1], lab.NONE
        return found[:1], lab.REBASELINE if lab.drifted(found[0][1], now) else lab.NONE
    gained = lab.gain(spec, found[0][1], now["best"])
    if gained > now["threshold"]:
        if len(found) < 2:
            return None, gained
        if lab.gain(spec, found[1][1], now["best"]) > now["threshold"]:
            return found[:2], "KEEP"
        return found[:2], "NOISE"
    if gained < -now["threshold"]:
        return found[:1], "DISCARD"
    return found[:1], "NOISE"


def _body(loop, spec, unit, posts, row, now, row_line) -> str:
    return "\n".join([
        f"[{lab.ACTOR}] {row['verdict']}",
        "",
        f"loop: {loop}",
        f"metric: {spec['metric']} on {spec['platform']} ({spec['better']} is better)",
        f"change: {unit['change']}",
        "posts: " + ", ".join(posts),
        f"commit: {row['commit']}",
        "runs: " + ", ".join(row["values"].split(",")),
        f"best: {row['best']}",
        f"noise floor: {_text(now['floor'])} over {spec['controls']} controls",
        f"threshold: {row['threshold']}",
        f"results: {row_line}",
    ]) + "\n"


def _measure(p, gh, loop, apply) -> int:
    base = production.base(p)
    spec = packaging.load_loop(p.root, base, loop)
    if apply:
        gh.issue(spec["ticket"])
    rows = packaging.results(p.root, base)
    assigned = packaging.assignments(p.root, base)
    if apply:
        packaging.ready(p, p.root, lab.PATH)
    settled = {r["commit"] for r in rows if r["loop"] == loop}
    printed = []

    def say(text):
        printed.append(text)
        print(text)

    for unit in _units(assigned, loop):
        if any(row["commit"] in settled for row in unit["rows"]):
            continue
        first = unit["rows"][0]["post"]
        found, waiting = _values(p, base, spec, loop, unit, say)
        if not found:
            if waiting is not None:
                say(f"packaging {loop} {unit['kind']} {waiting['post']}: waits: no frozen window")
            continue
        now = lab.standing(rows, loop, spec)
        if unit["kind"] == "run" and now["threshold"] is None:
            say(f"packaging {loop} run {first}: waits: {now['controls']} of {spec['controls']} no-op controls")
            continue
        if unit["kind"] == "run" and now["since"] >= spec["drift_every"]:
            say(f"packaging {loop} run {first}: waits: a no-op control is due after {spec['drift_every']} runs")
            continue
        measured, verdict = _verdict(spec, now, unit, found)
        if measured is None:
            say(f"packaging {loop} run {first}: waits: {unit['change']} beats the best by {lab.number(verdict)}; "
                "a second post confirms it")
            continue
        posts = [row["post"] for row, _ in measured]
        row = {"time": production.now(), "loop": loop, "kind": unit["kind"], "commit": measured[0][0]["commit"],
               "values": ",".join(lab.number(v) for _, v in measured), "best": _text(now["best"]),
               "threshold": _text(now["threshold"]), "verdict": verdict}
        row_line = lab.line(row)
        if apply:
            row_line = lab.append(p, gh, p.root, row)
            if verdict != lab.NONE:
                gh.post(spec["ticket"], _body(loop, spec, unit, posts, row, now, row_line), lab.ROLE)
        rows.append(row)
        settled.add(row["commit"])
        say(f"packaging {loop} {unit['kind']} {','.join(posts)}: {verdict} values {row['values']} "
            f"best {row['best']} threshold {row['threshold']}")
        if unit["kind"] == "control" and now["threshold"] is None and now["controls"] + 1 == spec["controls"]:
            done = lab.standing(rows, loop, spec)
            say(f"packaging {loop} threshold {lab.number(done['threshold'])}: twice the noise floor "
                f"{lab.number(done['floor'])} over {spec['controls']} controls, best {lab.number(done['best'])}")
    if not printed:
        print(f"packaging {loop}: nothing to measure")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="factory packaging",
                                     description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("verb", choices=VERBS, help="give a post its change, or measure the loop's windows")
    parser.add_argument("loop", help="the loop id: lab/packaging/<loop>.json on the base")
    parser.add_argument("rest", nargs="*", metavar="<ep> <change>",
                        help="assign: the episode and its change (- for a no-op control)")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="append the rows on the base and post the markers")
    args = parser.parse_args(argv)
    if args.verb == "assign" and len(args.rest) != 2:
        print("packaging: assign needs <ep> and <change>", file=sys.stderr)
        return 2
    if args.verb == "measure" and args.rest:
        print("packaging: measure takes only <loop>", file=sys.stderr)
        return 2
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        if args.apply:
            gh.sync_base(p.root, production.base(p))
        if tickets.frozen(gh):
            print("packaging: frozen", file=sys.stderr)
            return 2
        if args.verb == "assign":
            return _assign(p, gh, args.loop, args.rest[0], args.rest[1], args.apply)
        return _measure(p, gh, args.loop, args.apply)
    except ERRORS as error:
        print(f"packaging: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
