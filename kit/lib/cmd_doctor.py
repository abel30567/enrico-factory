"""factory doctor [--root DIR] [--rooms a,b] [--wave N] [--json]: check every resource the rooms need.

Prints a name and a state per resource, never a value. A resource held by the knowledge gateway is
skipped, with the reason `GATEWAY=off` and without running its checks, when the setting GATEWAY is
`off`. A grant whose key file shows it lapses within 14 days gets the reason `secret <id>: lapses within
14 days`; one that has lapsed is missing, with `secret <id>: lapsed`. It also lists, as missing, each cap of
`production.CAPS` that is empty or not a number, and `render.node` is missing when `RENDER_PROVIDER` names no
driver of `kit/lib/node.py`. Exit 0 when nothing is missing, 1 when something is, 2 when the register is invalid.
"""

import argparse
import json
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import grants, node, production, resources  # noqa: E402
from kit.lib.tools import render  # noqa: E402

REGISTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources.json")
STATES = ("ok", "missing", "absent", "skipped")
ALL_ROOMS = [room for room in resources.ROOMS if room != "all"]
CAP_TITLE = "A cap factory gate reads"


def _rooms(text):
    return {room.strip() for room in text.split(",") if room.strip()}


def _caps(values):
    """``(name, reason)`` for each cap of ``production.CAPS`` that is empty or not a number, in that order."""
    found = []
    for name in production.CAPS:
        value = values.get(name, "")
        if not value:
            found.append((name, f"setting {name} is empty"))
        elif not production._NUMBER.fullmatch(value):
            found.append((name, f"setting {name} is not a number >= 0"))
    return found


def main(argv):
    parser = argparse.ArgumentParser(prog="factory doctor", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="production directory holding the settings files (default: .)")
    parser.add_argument("--rooms", help="comma-separated rooms to check (default: setting ROOMS, else every room)")
    parser.add_argument("--wave", type=int, default=7, help="check only resources of this wave or earlier (default: 7)")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)

    register, problems = resources.load(REGISTER)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"doctor: invalid register, {len(problems)} problems", file=sys.stderr)
        return 2

    values = resources.settings(args.root, register)
    if args.rooms is not None:
        needed = _rooms(args.rooms)
    elif values.get("ROOMS"):
        needed = _rooms(values["ROOMS"])
    else:
        needed = set(ALL_ROOMS)
    try:
        now = grants.now()
    except ValueError as error:
        print(f"doctor: {error}", file=sys.stderr)
        return 2

    results = []
    for resource in sorted(register["resources"], key=lambda r: r["id"]):
        rooms = set(resource["rooms"])
        if ("all" in rooms or rooms & needed) and resource["wave"] <= args.wave:
            if resource["holder"] == "gateway" and values.get("GATEWAY") == "off":
                state, reasons = "skipped", ["GATEWAY=off"]
            else:
                passed, reasons = resources.check(resource, values, register, args.root)
                if passed and resource["id"] == render.RESOURCE and values.get("RENDER_PROVIDER") not in node.PROVIDERS:
                    passed = False
                    reasons = [f"setting RENDER_PROVIDER is not one of: {', '.join(sorted(node.PROVIDERS))}"]
                if passed:
                    state = "ok"
                    if resource["id"] in (grants.TIKTOK, grants.INSTAGRAM):
                        secrets_dir = resources.expand(values[register["secrets_dir_setting"]])
                        path = os.path.join(secrets_dir, resource["id"])
                        lapse = grants.lapse(resource["id"], path, now)
                        if lapse is not None:
                            reasons = [*reasons, f"secret {resource['id']}: {lapse}"]
                        if lapse == "lapsed":
                            state = "missing"
                else:
                    state = "absent" if resource.get("optional") else "missing"
        else:
            state, reasons = "skipped", []
        results.append((resource, state, reasons))

    caps = _caps(values)
    counts = {state: sum(1 for _, s, _ in results if s == state) for state in STATES}
    counts["missing"] += len(caps)
    if args.json:
        rows = [{"id": r["id"], "state": s, "reasons": reasons} for r, s, reasons in results]
        cap_rows = [{"name": name, "state": "missing", "reasons": [reason]} for name, reason in caps]
        print(json.dumps({"resources": rows, "caps": cap_rows, "counts": counts}, indent=2, sort_keys=True))
    else:
        for resource, state, reasons in results:
            print(f"{state}\t{resource['id']}\t{resource['title']}")
            for reason in reasons:
                print(f"  {reason}")
        for name, reason in caps:
            print(f"missing\t{name}\t{CAP_TITLE}")
            print(f"  {reason}")
        print("doctor: " + ", ".join(f"{counts[state]} {state}" for state in STATES))
    return 1 if counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
