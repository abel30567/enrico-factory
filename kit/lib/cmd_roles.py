"""factory roles [--kit DIR] [--check]: list roles by room, or validate roles, formats and the pipeline."""

import argparse
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import roles  # noqa: E402

DEFAULT_KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    parser = argparse.ArgumentParser(prog="factory roles", description=__doc__.split(": ", 1)[1])
    parser.add_argument("--kit", default=DEFAULT_KIT, help="kit directory (default: this kit)")
    parser.add_argument("--check", action="store_true",
                        help="validate every role file, format and the pipeline")
    args = parser.parse_args(argv)

    if args.check:
        problems = roles.validate(args.kit)
        for problem in problems:
            print(problem)
        if problems:
            return 1
        print("OK")
        return 0

    try:
        loaded = roles.load_roles(os.path.join(args.kit, "roles"))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    by_room = {}
    for role_id, doc in loaded.items():
        by_room.setdefault(doc["meta"].get("room", ""), []).append((role_id, doc["meta"].get("kind", "")))
    if not by_room:
        print("no roles")
    for room in sorted(by_room):
        print(f"{room}:")
        for role_id, kind in sorted(by_room[room]):
            print(f"  {role_id} ({kind})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
