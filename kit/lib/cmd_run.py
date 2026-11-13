"""factory run <job-file> [--pull N] [--root DIR] [--apply]: make the one call of a job the gate passed.

The job file is checked as `factory gate` checks it, with `--pull N` at the head of pull request N. A job
runs once: when the ledger on the base already holds its `run` row, that row is printed with
` (already recorded)` and nothing else happens. Otherwise the job needs a counting `GATE PASS`, an
installed tool (`kit/lib/tools/`) whose resource is usable, and a ticket with exactly one open pull (with
`--pull`, that pull). With `--apply` it brings the local base up to GitHub's, calls the tool once with the
outputs going to `<STATE_DIR>/jobs/<hash>/out/` and the inputs read where the job file was, adds one
manifest row per output on the pull's branch, and appends the `run` row with the real cost to the ledger
on the base. It posts nothing on GitHub. Without `--apply` it makes every check, calls no tool and writes
nothing.

Prints one line, `run job <hash>: <DONE|FAILED> usd <amount> compute_min <amount>`, or what it would do.
Exit 0 DONE, 1 FAILED, 2 frozen or refused. Nothing a tool prints, raises or returns other than its two
amounts and a reason out of its own `REASONS` is repeated anywhere.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import cmd_gate, gitstate, github, ledger, production, resources, tickets  # noqa: E402
from kit.lib.tools import Down, Failed  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent / "tools"
TOOL_NAME = re.compile(r"[a-z][a-z0-9-]*")
OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
RESULT = "result.json"
DONE, FAILED = "DONE", "FAILED"
CRASHED, BROKE, NO_RESULT, TOOL_FAILED = "tool crashed", "tool broke the interface", "no result", "tool failed"
ERRORS = cmd_gate.ERRORS + (OSError,)


class Refused(Exception):
    pass


def _amount(value):
    """``value`` as a Decimal the ledger can hold, or None."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    amount = Decimal(str(value))
    return amount if ledger._AMOUNT.fullmatch(format(amount, "f")) else None


def _text(amount):
    return format(amount, "f")


def _printed(digest, verdict, usd, compute_min):
    return f"run job {digest}: {verdict} usd {usd} compute_min {compute_min}"


@contextlib.contextmanager
def _silenced():
    """Drop everything written to standard output and error, by Python or by a child process."""
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    saved = [os.dup(1), os.dup(2)]
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in saved + [null]:
            os.close(fd)


def _load_tool(name):
    """The tool module ``name``; ``Refused`` when it is not installed or does not follow the interface."""
    path = TOOLS_DIR / f"{name}.py"
    if not TOOL_NAME.fullmatch(name) or not path.is_file():
        raise Refused(f"tool {name} is not installed")
    try:
        with _silenced():
            spec = importlib.util.spec_from_file_location(f"factory_tool_{name.replace('-', '_')}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    except (Exception, SystemExit):
        module = None
    resource = getattr(module, "RESOURCE", None)
    reasons = getattr(module, "REASONS", None)
    if (not isinstance(resource, str) or not resource
            or not isinstance(reasons, tuple) or not all(isinstance(r, str) for r in reasons)
            or not callable(getattr(module, "run", None))):
        raise Refused(f"tool {name} does not follow the tool interface")
    return module


def _resource(name, tool, root):
    """``(resource, settings, register)`` for the tool's resource; ``Refused`` when it is unknown or unusable."""
    register, problems = resources.load(resources.REGISTER)
    if problems:
        raise Refused("; ".join(problems))
    resource = next((r for r in register["resources"] if r["id"] == tool.RESOURCE), None)
    if resource is None:
        raise Refused(f"tool {name} names the unknown resource {tool.RESOURCE}")
    values = resources.settings(root, register)
    ok, reasons = resources.check(resource, values, register, root)
    if not ok:
        raise Refused(f"resource {resource['id']} is not usable: {'; '.join(reasons)}")
    return resource, values, register


def _counting_pass(request, rows):
    recorded = cmd_gate._recorded(request["comments"], "job", request["hash"])
    jobs = [r for r in rows if r["kind"] == "job" and r["hash"] == request["hash"]]
    return (recorded is not None and ledger.gate_marker(recorded).verb == "GATE PASS"
            and bool(jobs) and jobs[-1]["verdict"] == "PASS")


def _open_pull(state, number):
    pulls = [pull for pull in tickets.pulls_of(state, number) if pull["state"] == "open"]
    if len(pulls) != 1:
        raise Refused(f"ticket #{number} does not have exactly one open pull")
    return pulls[0]


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _outputs(out):
    """The outputs in ``out`` sorted by name, or None when an entry breaks the interface."""
    found = []
    for entry in sorted(os.scandir(out), key=lambda e: e.name):
        if (not entry.is_file(follow_symlinks=False) or not OUTPUT_NAME.fullmatch(entry.name)
                or entry.stat(follow_symlinks=False).st_size == 0):
            return None
        path = Path(entry.path)
        found.append({"bytes": path.stat().st_size, "name": entry.name, "sha256": _sha256(path)})
    return found


def _call(tool, call, expected):
    """``(verdict, usd, compute_min, reason, outputs)`` of the one call; ``Down`` passes through."""
    try:
        with _silenced():
            returned = tool.run(call)
    except Down:
        raise
    except Failed as failure:
        reason = failure.reason if failure.reason in tool.REASONS else TOOL_FAILED
        usd, compute_min = _amount(failure.usd), _amount(failure.compute_min)
        return (FAILED, expected["usd"] if usd is None else usd,
                expected["compute_min"] if compute_min is None else compute_min, reason, [])
    except (Exception, SystemExit):
        return FAILED, expected["usd"], expected["compute_min"], CRASHED, []
    if isinstance(returned, dict) and sorted(returned) == ["compute_min", "usd"]:
        usd, compute_min = _amount(returned["usd"]), _amount(returned["compute_min"])
        outputs = _outputs(call["out"])
        if usd is not None and compute_min is not None and outputs is not None:
            return DONE, usd, compute_min, None, outputs
    return FAILED, expected["usd"], expected["compute_min"], BROKE, []


def _write_result(job_dir, verdict, usd, compute_min, reason, outputs):
    data = {"compute_min": _text(compute_min), "outputs": outputs, "reason": reason, "usd": _text(usd),
            "verdict": verdict}
    tmp = job_dir / (RESULT + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, job_dir / RESULT)


def _read_result(job_dir, digest, expected):
    """The recorded call of an earlier run, from its ``result.json``; FAILED ``no result`` without one."""
    path = job_dir / RESULT
    if not path.is_file():
        return FAILED, expected["usd"], expected["compute_min"], NO_RESULT, []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["verdict"] == DONE:
        for item in data["outputs"]:
            found = job_dir / "out" / item["name"]
            if not found.is_file() or found.stat().st_size != item["bytes"] or _sha256(found) != item["sha256"]:
                raise Refused(f"the outputs of job {digest} changed after the call")
    return data["verdict"], Decimal(data["usd"]), Decimal(data["compute_min"]), data["reason"], data["outputs"]


def _add_rows(tree, digest, outputs):
    """Add the outputs' manifest rows in the worktree ``tree``; whether a row was added."""
    path = Path(tree) / gitstate.MANIFEST
    text = path.read_text(encoding="utf-8") if path.is_file() else "\t".join(("id", "sha256", "bytes", "source")) + "\n"
    if not text.endswith("\n"):
        text += "\n"
    ids = {line.split("\t", 1)[0] for line in text.splitlines()[1:]}
    rows = [f"{digest[:16]}-{o['name']}\t{o['sha256']}\t{o['bytes']}\tjob {digest}" for o in outputs
            if f"{digest[:16]}-{o['name']}" not in ids]
    if not rows:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "".join(row + "\n" for row in rows), encoding="utf-8")
    return True


def _manifest(p, gh, branch, digest, outputs):
    """Commit the manifest rows on ``branch`` from a detached worktree, move the branch, push it."""
    tmp = Path(tempfile.mkdtemp(prefix="factory-run-"))
    tree = tmp / "tree"
    try:
        gitstate.add_worktree(p.root, tree, "refs/heads/" + branch)
        try:
            old = gitstate.head(tree)
            if _add_rows(tree, digest, outputs):
                new = gitstate.commit(tree, [gitstate.MANIFEST], f"manifest: job {digest[:12]}", "factory")
                gitstate.git(p.root, "update-ref", "refs/heads/" + branch, new, old)
        finally:
            gitstate.remove_worktree(p.root, tree)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gh.push(p.root, branch)


def _run(p, gh, state, rows, target, apply, number=None):
    request = cmd_gate._job_request(p, state, target, number)
    digest, name = request["hash"], request["tool"]
    expected = {"usd": request["usd"], "compute_min": request["compute_min"]}
    ran = [r for r in rows if r["kind"] == "run" and r["hash"] == digest]
    if ran:
        row = ran[-1]
        print(_printed(digest, row["verdict"], row["usd"], row["compute_min"]) + cmd_gate.ALREADY)
        return 0 if row["verdict"] == DONE else 1
    job_dir = production.state_dir(p) / "jobs" / digest
    earlier = os.path.lexists(job_dir)
    if not earlier:
        if not _counting_pass(request, rows):
            raise Refused(f"job {digest} has no counting GATE PASS")
        tool = _load_tool(name)
        resource, values, register = _resource(name, tool, p.root)
    pull = request["on"] or _open_pull(state, request["ticket"]["number"])
    if not apply:
        print(f"run job {digest}: " + ("would record the earlier call" if earlier else f"would call {name}"))
        return 0
    if earlier:
        verdict, usd, compute_min, reason, outputs = _read_result(job_dir, digest, expected)
    else:
        job = request["job"]
        secrets_dir = resources.expand(values[register["secrets_dir_setting"]])
        os.makedirs(job_dir.parent, exist_ok=True)
        os.mkdir(job_dir)
        out = job_dir / "out"
        out.mkdir()
        call = {
            "hash": digest, "ticket": request["ticket"]["number"], "caller": request["caller"],
            "expected": job["expected"],
            "inputs": [{**item, "text": gitstate.show(p.root, request["ref"], item["path"])} for item in job["inputs"]],
            "out": str(out),
            "settings": {c["name"]: values[c["name"]] for c in resource["checks"] if c["kind"] == "setting"},
            "secrets": {c["name"]: os.path.abspath(os.path.join(secrets_dir, c["name"]))
                        for c in resource["checks"] if c["kind"] == "secret"},
        }
        try:
            verdict, usd, compute_min, reason, outputs = _call(tool, call, expected)
        except Down:
            shutil.rmtree(job_dir)
            raise Refused(f"resource {resource['id']} is down") from None
        _write_result(job_dir, verdict, usd, compute_min, reason, outputs)
    if verdict == DONE and outputs:
        _manifest(p, gh, pull["head_ref"], digest, outputs)
    ticket = request["ticket"]
    ledger.append(p, gh, p.root, {
        "time": production.now(), "kind": "run", "hash": digest, "ticket": ticket["number"],
        "episode": tickets.episode(ticket["unit"]), "tool": name, "usd": usd, "compute_min": compute_min,
        "caller": request["caller"], "verdict": verdict, "cap": "-"})
    if verdict == FAILED:
        print(f"run: tool {name} failed: {reason}", file=sys.stderr)
    elif usd > expected["usd"] or compute_min > expected["compute_min"]:
        print(f"run: job {digest} cost more than expected", file=sys.stderr)
    print(_printed(digest, verdict, _text(usd), _text(compute_min)))
    return 0 if verdict == DONE else 1


def main(argv):
    parser = argparse.ArgumentParser(prog="factory run", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("job", nargs="?", help="the job file the gate passed")
    parser.add_argument("--pull", type=int, help="read the job file and its inputs at the head of this pull")
    parser.add_argument("--read", nargs=2, metavar=("TOPIC", "PLATFORM"),
                        help="instead of a job, read the videos of a topic on a platform (kit/lib/reads.py)")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="make the call and record it")
    args = parser.parse_args(argv)
    try:
        if args.read is None and args.job is None:
            raise Refused("give a job file or --read <topic> <platform>")
        if args.read is not None and (args.job is not None or args.pull is not None):
            raise Refused("--read takes no job file and no --pull")
        p = production.load(production.find_root(args.root))
        if args.read is not None:
            from kit.lib import reads
            return reads.main(p, *args.read, args.apply)
        gh = github.connect(p.settings)
        if args.apply:
            gh.sync_base(p.root, production.base(p))
        state = tickets.load(p, gh, p.root)
        if state.frozen:
            print("run: frozen", file=sys.stderr)
            return 2
        text = gitstate.show(p.root, production.base(p), ledger.PATH)
        rows = ledger.read(text) if text is not None else []
        return _run(p, gh, state, rows, args.job, args.apply, args.pull)
    except (Refused, cmd_gate.Refused) as error:
        print(f"run: {error}", file=sys.stderr)
        return 2
    except ERRORS as error:
        print(f"run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
