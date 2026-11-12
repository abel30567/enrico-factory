"""factory gate <job-file-or-pr> [--root DIR] [--apply]: the producer's gate on a job or a lock.

Prints one line: `gate <job|lock> <hash>: <PASS|STOP> cap <name or -> reason <reason>`. Without
`--apply` it writes nothing. With it, it appends the ledger line on the base, pushes it, and posts
`[PRODUCER] GATE PASS` or `GATE STOP` on the ticket (a job) or the pull (a lock). A decision already
recorded for the same hash is not repeated. Exit 0 PASS, 1 STOP, 2 frozen or refused.
"""

import argparse
import hashlib
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import gitstate, github, ledger, production, tickets  # noqa: E402

JOB_KEYS = ("caller", "expected", "inputs", "ticket", "tool")
EXPECTED_KEYS = ("compute_min", "usd")
INPUT_KEYS = ("path", "sha256")
ERRORS = (ledger.LedgerError, production.ProductionError, github.GitHubError, gitstate.GitError,
          tickets.TicketError)
ALREADY = " (already recorded)"


class Refused(Exception):
    pass


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _job(p, path_text):
    """The job file as a dict and its hash; ``Refused`` when it cannot be read or has the wrong shape."""
    path = Path(path_text).resolve()
    if p.root not in path.parents:
        raise Refused(f"job file {path_text} is outside the production")
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        raise Refused(f"job file {path_text} cannot be read") from None
    except ValueError:
        raise Refused(f"job file {path_text} is not JSON") from None
    shape = f"job file {path_text} is not {{ticket, tool, inputs, expected, caller}}"
    if not isinstance(job, dict) or sorted(job) != list(JOB_KEYS):
        raise Refused(shape)
    expected, inputs = job["expected"], job["inputs"]
    if (not isinstance(job["ticket"], int) or isinstance(job["ticket"], bool)
            or not isinstance(job["tool"], str) or not job["tool"]
            or not isinstance(job["caller"], str) or not job["caller"]
            or not isinstance(expected, dict) or sorted(expected) != list(EXPECTED_KEYS)
            or not all(_number(expected[k]) for k in EXPECTED_KEYS)
            or not isinstance(inputs, list)
            or not all(isinstance(i, dict) and sorted(i) == list(INPUT_KEYS)
                       and isinstance(i["path"], str) and isinstance(i["sha256"], str) for i in inputs)):
        raise Refused(shape)
    canonical = json.dumps(job, sort_keys=True, separators=(",", ":"))
    return job, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_inputs(p, job):
    base = production.base(p)
    for item in job["inputs"]:
        text = gitstate.show(p.root, base, item["path"]) if item["path"] else None
        found = None if text is None else hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()
        if found != item["sha256"]:
            raise Refused(f"input {item['path']} does not match its hash on the base")


def _verdicts(state, number):
    """True when the ticket has one open pull and its lint, checkers and judges all say yes."""
    pulls = [pull for pull in tickets.pulls_of(state, number) if pull["state"] == "open"]
    if len(pulls) != 1:
        return False
    s = pulls[0]["summary"]
    return (s["lint"] is not None and s["lint"]["verb"] == "PASS"
            and all(v is not None and v["verb"] == "APPROVED" for v in s["checkers"].values())
            and all(v is not None for v in s["judges"].values()))


def _job_request(p, state, path_text):
    job, digest = _job(p, path_text)
    _check_inputs(p, job)
    ticket = state.tickets.get(job["ticket"])
    if ticket is None or ticket["state"] != "open":
        raise Refused(f"ticket #{job['ticket']} is not an open ticket")
    entry = production.stage(p, ticket["room"], ticket["stage"])
    caller = job["caller"]
    if (p.kinds.get(caller) != "operator" or p.roles[caller]["meta"].get("gated") != "yes"
            or caller not in production.stage_roles(entry, "maker")):
        raise Refused(f"caller {caller} is not a gated operator of stage {ticket['room']}/{ticket['stage']}")
    return {"kind": "job", "hash": digest, "ticket": ticket, "target": ticket["number"],
            "comments": state.ticket_comments.get(ticket["number"], []), "tool": job["tool"],
            "usd": Decimal(str(job["expected"]["usd"])),
            "compute_min": Decimal(str(job["expected"]["compute_min"])),
            "caller": caller, "verdicts": _verdicts(state, ticket["number"])}


def _lock_request(p, state, number):
    pull = state.pulls.get(number)
    if pull is None or pull["state"] != "open":
        raise Refused(f"#{number} is not an open pull")
    ticket = state.tickets[pull["ticket"]]
    git = pull["git"]
    digest = hashlib.sha256(f"lock\n{number}\n{git['head']}\n{git['manifest']}\n".encode("utf-8")).hexdigest()
    return {"kind": "lock", "hash": digest, "ticket": ticket, "target": number, "comments": pull["comments"],
            "tool": "-", "usd": Decimal(0), "compute_min": Decimal(0),
            "caller": production.orchestrator(p, ticket["room"]), "verdicts": True,
            "pull": pull, "entry": production.stage(p, ticket["room"], ticket["stage"])}


def _cap_line(cap, reason):
    return f"cap: {cap}  reason: {reason}"


def _recorded(comments, kind, digest):
    """The newest gate comment whose ``covers:`` line names this kind and hash, or None."""
    found = None
    for c in sorted(comments, key=lambda c: (c["created_at"], c["id"])):
        if ledger.gate_marker(c) is not None and f"covers: {kind} {digest}" in c["body"].split("\n")[1:]:
            found = c
    return found


def _printed(kind, digest, verdict, cap, reason):
    return f"gate {kind} {digest}: {verdict} cap {cap} reason {reason}"


def _body(request, verdict, cap, reason, row_line):
    lines = [f"[PRODUCER] GATE {verdict}", f"covers: {request['kind']} {request['hash']}",
             _cap_line(cap, reason), "ledger: " + row_line.replace("\t", " | ")]
    if request["kind"] == "lock":
        git = request["pull"]["git"]
        lines.append(f"reviewed head {git['head']} manifest {git['manifest']}")
    return "\n".join(lines) + "\n"


def _covered(request):
    """For a lock the pull's current gate PASS already covers: the line it printed, else None."""
    pull = request["pull"]
    if not ledger.covers(pull["git"], pull["comments"], request["entry"]):
        return None
    gates = [c for c in sorted(pull["comments"], key=lambda c: (c["created_at"], c["id"]))
             if ledger.gate_marker(c) is not None]
    digest = next((line.split()[-1] for line in gates[-1]["body"].split("\n")
                   if line.startswith("covers: lock ")), request["hash"])
    return _printed("lock", digest, "PASS", "-", "within caps") + ALREADY


def _gate(p, gh, state, now, rows, target, apply):
    if target.isdigit():
        request = _lock_request(p, state, int(target))
        covered = _covered(request)
        if covered:
            print(covered)
            return 0
    else:
        request = _job_request(p, state, target)
    ticket = request["ticket"]
    if request["verdicts"]:
        # The request's own earlier rows are left out: its cost is added once, as this request.
        others = [r for r in rows if r["hash"] != request["hash"]]
        verdict, cap, reason = ledger.decide(p, others, state, ticket, request["kind"], request["usd"],
                                             request["compute_min"], now)
    else:
        verdict, cap, reason = "STOP", "-", "verdicts"
    code = 0 if verdict == "PASS" else 1
    printed = _printed(request["kind"], request["hash"], verdict, cap, reason)
    recorded = _recorded(request["comments"], request["kind"], request["hash"])
    if (recorded is not None and ledger.gate_marker(recorded).verb == "GATE " + verdict
            and _cap_line(cap, reason) in recorded["body"].split("\n")):
        print(printed + ALREADY)
        return code
    if apply:
        mine = [r for r in rows if r["kind"] == request["kind"] and r["hash"] == request["hash"]]
        if mine and mine[-1]["verdict"] == verdict and mine[-1]["cap"] == cap:
            # An earlier run appended it and failed before posting, perhaps at its push: push again.
            row_line = ledger.line(mine[-1])
            gh.push(p.root, production.base(p))
        else:
            row_line = ledger.append(p, gh, p.root, {
                "time": now, "kind": request["kind"], "hash": request["hash"], "ticket": ticket["number"],
                "episode": tickets.episode(ticket["unit"]), "tool": request["tool"], "usd": request["usd"],
                "compute_min": request["compute_min"], "caller": request["caller"], "verdict": verdict,
                "cap": cap})
        gh.post(request["target"], _body(request, verdict, cap, reason, row_line), ledger.GATE_ROLE)
    print(printed)
    return code


def main(argv):
    parser = argparse.ArgumentParser(prog="factory gate", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("target", help="a job file, or a pull request number for a lock")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="append the ledger line and post the marker")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        state = tickets.load(p, gh, p.root)
        if state.frozen:
            print("gate: frozen", file=sys.stderr)
            return 2
        now = production.now()
        text = gitstate.show(p.root, production.base(p), ledger.PATH)
        rows = ledger.read(text) if text is not None else []
        return _gate(p, gh, state, now, rows, args.target, args.apply)
    except Refused as error:
        print(f"gate: {error}", file=sys.stderr)
        return 2
    except ERRORS as error:
        print(f"gate: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
