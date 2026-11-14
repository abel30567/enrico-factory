"""factory run <job-file> [--pull N] [--root DIR] [--apply]: make the one call of a job the gate passed.

The job file is checked as `factory gate` checks it, with `--pull N` at the head of pull request N. A job
runs once: when the ledger on the base already holds its `run` row, that row is printed with
` (already recorded)` and nothing else happens. Otherwise the job needs a counting `GATE PASS`, an
installed tool (`kit/lib/tools/`) whose resource is usable, and a ticket with exactly one open pull (with
`--pull`, that pull). With `--apply` it brings the local base up to GitHub's, calls the tool once with the
outputs going to `<STATE_DIR>/jobs/<hash>/out/` and the inputs read where the job file was, adds one
manifest row per output on the pull's branch (for a tool with ``STAGE_FILES``: puts every other output in the
asset store and commits those in the ticket's production directory instead) and, in the same commit, the post's
answer the tool recorded at `distribution/<unit>/post.json`, and appends the `run` row with the real cost to the ledger
on the base. It posts nothing on GitHub, except the down issue (`kit/lib/down.py`) when the tool's resource is down.
Without `--apply` it makes every check, calls no tool and writes nothing.

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

from kit.lib import (assets, cmd_gate, down, gitstate, github, ledger, media, posts, production,  # noqa: E402
                     resources, tickets)
from kit.lib.tools import Down, Failed  # noqa: E402
from kit.lib import release  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent / "tools"
TOOL_NAME = re.compile(r"[a-z][a-z0-9-]*")
OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
UNIT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
STORE = "store.assets"
RESULT = "result.json"
RECORD = "record"
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
    stage_files = getattr(module, "STAGE_FILES", ())
    if (not isinstance(resource, str) or not resource
            or not isinstance(reasons, tuple) or not all(isinstance(r, str) for r in reasons)
            or not isinstance(stage_files, tuple) or not all(isinstance(f, str) for f in stage_files)
            or not callable(getattr(module, "run", None))):
        raise Refused(f"tool {name} does not follow the tool interface")
    return module


def _register(root):
    """``(register, settings)``; ``Refused`` when the register has problems."""
    register, problems = resources.load(resources.REGISTER)
    if problems:
        raise Refused("; ".join(problems))
    return register, resources.settings(root, register)


def _usable(resource, values, register, root):
    ok, reasons = resources.check(resource, values, register, root)
    if not ok:
        raise Refused(f"resource {resource['id']} is not usable: {'; '.join(reasons)}")


def _resource(name, tool, root):
    """``(resource, settings, register)`` for the tool's resource; ``Refused`` when it is unknown or unusable.

    For a tool with ``STAGE_FILES``, ``store.assets`` must be usable too.
    """
    register, values = _register(root)
    found = {r["id"]: r for r in register["resources"]}
    resource = found.get(tool.RESOURCE)
    if resource is None:
        raise Refused(f"tool {name} names the unknown resource {tool.RESOURCE}")
    _usable(resource, values, register, root)
    if getattr(tool, "STAGE_FILES", None):
        if STORE not in found:
            raise Refused(f"tool {name} names the unknown resource {STORE}")
        _usable(found[STORE], values, register, root)
    return resource, values, register


def _unit_dir(p, room, stage, unit):
    """The ticket's production directory relative to the root, from its stage's unit kind; None when refused."""
    entry = production.stage(p, room, stage)
    parts = unit.split("/") if isinstance(unit, str) else []
    if entry is None or not parts or not all(UNIT_PART.fullmatch(part) for part in parts):
        return None
    kind = entry.get("unit")
    if kind == "shot" and len(parts) == 2:
        return f"production/{parts[0]}/{parts[1]}/{stage}"
    if kind == "episode" and len(parts) == 1:
        return f"production/{parts[0]}/{stage}"
    if kind == "asset" and len(parts) == 1:
        return f"production/assets/{parts[0]}/{stage}"
    return None


def _ticket_dir(p, ticket):
    found = _unit_dir(p, ticket["room"], ticket["stage"], ticket["unit"])
    if found is None:
        raise Refused(f"unit {ticket['unit']} of stage {ticket['room']}/{ticket['stage']} has no production directory")
    return found


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


def _record(directory, ticket, digest):
    """The record a DONE call left in ``directory``: ``[]`` when it is empty, the listing of its one
    ``post.json`` when that is the sound answer of this job for a ticket of room ``distribution``, else None."""
    entries = sorted(os.scandir(directory), key=lambda e: e.name)
    if not entries:
        return []
    if (len(entries) != 1 or entries[0].name != posts.ANSWER.rsplit("/", 1)[1]
            or not entries[0].is_file(follow_symlinks=False) or _answer_path(ticket) is None):
        return None
    path = Path(entries[0].path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if posts.answer_problems(doc) or doc["job"] != digest:
        return None
    return [{"bytes": path.stat().st_size, "name": entries[0].name, "sha256": _sha256(path)}]


def _answer_path(ticket):
    """Where a post's recorded answer is committed, ``distribution/<unit>/post.json``; None for a ticket of
    another room or a unit that is not ``<ep>/<platform>``."""
    parts = ticket["unit"].split("/")
    if ticket["room"] != posts.ROOM or len(parts) != 2 or not all(UNIT_PART.fullmatch(part) for part in parts):
        return None
    return posts.ANSWER.format(ticket["unit"])


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


def _write_result(job_dir, verdict, usd, compute_min, reason, outputs, record=()):
    data = {"compute_min": _text(compute_min), "outputs": outputs, "reason": reason, "usd": _text(usd),
            "verdict": verdict}
    if record:
        data["record"] = list(record)
    tmp = job_dir / (RESULT + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, job_dir / RESULT)


def _read_result(job_dir, digest, expected):
    """The recorded call of an earlier run, from its ``result.json``; FAILED ``no result`` without one."""
    path = job_dir / RESULT
    if not path.is_file():
        return FAILED, expected["usd"], expected["compute_min"], NO_RESULT, [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data.get("record", [])
    if data["verdict"] == DONE:
        for folder, items in (("out", data["outputs"]), (RECORD, record)):
            for item in items:
                found = job_dir / folder / item["name"]
                if (not found.is_file() or found.stat().st_size != item["bytes"]
                        or _sha256(found) != item["sha256"]):
                    raise Refused(f"the outputs of job {digest} changed after the call")
    return (data["verdict"], Decimal(data["usd"]), Decimal(data["compute_min"]), data["reason"], data["outputs"],
            record)


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


def _on_branch(p, gh, branch, change, message):
    """Commit the paths ``change(tree)`` writes and returns on ``branch`` from a detached worktree, move the
    branch when they changed, push it."""
    tmp = Path(tempfile.mkdtemp(prefix="factory-run-"))
    tree = tmp / "tree"
    try:
        gitstate.add_worktree(p.root, tree, "refs/heads/" + branch)
        try:
            old = gitstate.head(tree)
            new = gitstate.commit(tree, change(tree), message, "factory")
            if new is not None:
                gitstate.git(p.root, "update-ref", "refs/heads/" + branch, new, old)
        finally:
            gitstate.remove_worktree(p.root, tree)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gh.push(p.root, branch)


def _manifest(p, gh, branch, digest, outputs, answer=None, recorded=None):
    """Commit the manifest rows on ``branch``, with the recorded answer ``recorded`` at ``answer`` when given."""
    def change(tree):
        paths = [gitstate.MANIFEST] if _add_rows(tree, digest, outputs) else []
        if answer is not None:
            (tree / answer).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(recorded, tree / answer)
            paths.append(answer)
        return paths
    _on_branch(p, gh, branch, change, f"manifest: job {digest[:12]}")


def _stage_files(p, gh, branch, digest, out, directory, names):
    """Write the outputs ``names`` of ``out`` in ``directory`` on ``branch``, in one commit."""
    def change(tree):
        paths = [f"{directory}/{name}" for name in names]
        for name, rel in zip(names, paths):
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / name, tree / rel)
        return paths
    _on_branch(p, gh, branch, change, f"stage files: job {digest[:12]}")


def _store(values, register, out, names):
    """Put the outputs ``names`` of ``out`` in the asset store, in name order."""
    key_file = os.path.join(resources.expand(values[register["secrets_dir_setting"]]), STORE)
    for name in sorted(names):
        try:
            assets.put(values, key_file, str(out / name))
        except (media.Refused, media.Failed) as error:
            raise Refused(f"the asset store did not take {name}: {error}") from None


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
    if earlier:
        tool = _load_tool(name)
        if getattr(tool, "STAGE_FILES", ()):
            register, values = _register(p.root)
    else:
        if not _counting_pass(request, rows):
            raise Refused(f"job {digest} has no counting GATE PASS")
        tool = _load_tool(name)
        resource, values, register = _resource(name, tool, p.root)
        found = down._matching(gh.issues("open"), resource["id"])
        if found:
            raise Refused(f"resource {resource['id']} is down (#{found[0]})")
    stage_files = getattr(tool, "STAGE_FILES", ())
    directory = _ticket_dir(p, request["ticket"]) if stage_files else None
    pull = request["on"] or _open_pull(state, request["ticket"]["number"])
    if not apply:
        print(f"run job {digest}: " + ("would record the earlier call" if earlier else f"would call {name}"))
        return 0
    if earlier:
        verdict, usd, compute_min, reason, outputs, record = _read_result(job_dir, digest, expected)
    else:
        job = request["job"]
        secrets_dir = resources.expand(values[register["secrets_dir_setting"]])
        os.makedirs(job_dir.parent, exist_ok=True)
        os.mkdir(job_dir)
        out = job_dir / "out"
        out.mkdir()
        kept = job_dir / RECORD
        kept.mkdir()
        call = {
            "hash": digest, "ticket": request["ticket"]["number"], "caller": request["caller"],
            "expected": job["expected"],
            "inputs": [{**item, "text": gitstate.show(p.root, request["ref"], item["path"])} for item in job["inputs"]],
            "out": str(out),
            "record": str(kept),
            "settings": {c["name"]: values[c["name"]] for c in resource["checks"] if c["kind"] == "setting"},
            "secrets": {c["name"]: os.path.abspath(os.path.join(secrets_dir, c["name"]))
                        for c in resource["checks"] if c["kind"] == "secret"},
        }
        try:
            verdict, usd, compute_min, reason, outputs = _call(tool, call, expected)
        except Down:
            shutil.rmtree(job_dir)
            issue = down.report(p, gh, resource["id"], f"tool {name} could not use it; nothing was spent",
                                f"run job {digest[:12]} on #{request['ticket']['number']}")
            raise Refused(f"resource {resource['id']} is down (#{issue})") from None
        record = _record(kept, request["ticket"], digest) if verdict == DONE else []
        if record is None:
            verdict, usd, compute_min, reason, outputs, record = (
                FAILED, expected["usd"], expected["compute_min"], BROKE, [], [])
        if not os.listdir(kept):
            kept.rmdir()
        _write_result(job_dir, verdict, usd, compute_min, reason, outputs, record)
    if verdict == DONE and outputs and stage_files:
        names = [o["name"] for o in outputs]
        _store(values, register, job_dir / "out", [n for n in names if n not in stage_files])
        _stage_files(p, gh, pull["head_ref"], digest, job_dir / "out", directory,
                     [n for n in names if n in stage_files])
    elif verdict == DONE and (outputs or record):
        if record:
            _manifest(p, gh, pull["head_ref"], digest, outputs, _answer_path(request["ticket"]),
                      job_dir / RECORD / record[0]["name"])
        else:
            _manifest(p, gh, pull["head_ref"], digest, outputs)
    if verdict == DONE:
        release.write(p, gh, request, job_dir, outputs)
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
    parser.add_argument("--analyse", nargs=3, metavar=("PLATFORM", "ID", "LINK"),
                        help="instead of a job, analyse a temporary copy of one video (kit/lib/analysis.py)")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="make the call and record it")
    args = parser.parse_args(argv)
    try:
        if args.analyse is not None:
            if args.job is not None or args.pull is not None or args.read is not None:
                raise Refused("--analyse takes no job file, no --pull and no --read")
            from kit.lib import analysis
            try:
                return analysis.main(production.load(production.find_root(args.root)), *args.analyse, args.apply)
            except analysis.Refused as error:
                print(f"run: {error}", file=sys.stderr)
                return 2
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
