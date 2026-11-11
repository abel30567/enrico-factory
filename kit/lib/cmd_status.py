"""factory status [--root DIR] [--room R] [--json]: every open ticket and pull request, its state and next step.

A pure reading (system brief section 15): the state and the next step come from ``kit.lib.steps``,
nothing is written to GitHub or git. Exit 0, also when some tickets are in error; 2 when the
production, GitHub or git cannot be read.
"""

import argparse
import json
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import github, production, steps, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402
from kit.lib.github import GitHubError  # noqa: E402
from kit.lib.production import ProductionError  # noqa: E402
from kit.lib.tickets import TicketError  # noqa: E402


def _pull_row(pull, entries, ticket_entry):
    entry = next((e for e in entries if e["pull"] == pull["number"]), ticket_entry)
    summary = pull["summary"]
    return {"number": pull["number"], "state": entry["state"], "step": entry["step"], "role": entry["role"],
            "head": pull["git"]["head"], "lint": summary["lint"]["verb"] if summary["lint"] else None,
            "rounds": summary["rounds"], "blockers": len(summary["blockers"])}


def rows(p, state, now, room=None) -> list:
    """One row per open ticket (of ``room`` when given), by room then number, with its open pulls."""
    found = []
    for ticket in sorted((t for t in state.tickets.values()
                          if t["state"] == "open" and room in (None, t["room"])),
                         key=lambda t: (t["room"], t["number"])):
        entry = steps.next_step(p, state, ticket, p.root, now)
        entries = steps.all_steps(p, state, ticket, p.root, now)
        pulls = [_pull_row(pull, entries, entry) for pull in tickets.pulls_of(state, ticket["number"])
                 if pull["state"] == "open"]
        found.append({"number": ticket["number"], "room": ticket["room"], "stage": ticket["stage"],
                      "unit": ticket["unit"], "state": entry["state"], "step": entry["step"],
                      "role": entry["role"], "reason": entry["reason"], "pulls": pulls})
    return found


def _next(row):
    return row["step"] if row["role"] is None else f"{row['step']}:{row['role']}"


def text(frozen, errors, found) -> str:
    lines = ["FROZEN"] if frozen else []
    for t in found:
        lines.append(f"#{t['number']} {t['room']} {t['stage']} {t['unit']} {t['state']} next={_next(t)} {t['reason']}")
        for pr in t["pulls"]:
            lines.append(f"  PR #{pr['number']} {pr['state']} next={_next(pr)} head={pr['head'][:12]} "
                         f"lint={pr['lint'] or '-'} rounds={pr['rounds']} blockers={pr['blockers']}")
    lines.append(f"errors: {len(errors)}")
    lines += errors
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(prog="factory status", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory in the production (default: .)")
    parser.add_argument("--room", help="only this room's tickets")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        if args.room is not None and args.room not in production.rooms(p):
            print(f"status: unknown room {args.room}", file=sys.stderr)
            return 2
        gh = github.connect(p.settings)
        state = tickets.load(p, gh, p.root)
        found = rows(p, state, production.now(), args.room)
    except (ProductionError, GitHubError, GitError, TicketError) as error:
        print("status: " + " ".join(str(error).split()), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"frozen": state.frozen, "errors": state.errors, "tickets": found},
                         indent=2, sort_keys=True))
    else:
        print(text(state.frozen, state.errors, found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
