"""factory init <dir> [--rooms a,b] [--upgrade]: install the kit into a production repository as `.factory/`.

Without `--upgrade`, also creates the production layout (brief section 16) where it is missing. With
`--upgrade`, replaces `.factory/` only. Exit 0 when done, 1 when already a production (or, with
`--upgrade`, not one), 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import frontmatter  # noqa: E402

SOURCE = Path(__file__).resolve().parent.parent
ROOMS = ("story", "production", "post", "distribution", "lab")
STUDIO = "studio"
DEFAULT_ROOMS = "story"
KIT_DIR = ".factory"
ENV = "production.env"

PRODUCTION_ENV = """\
# Production settings. No secrets and no machine paths here: those go in production.local.env.
REPO=
BASE=main
ROOMS={rooms}
MEDIA_STORE=
MODEL_MAKER=
MODEL_CHECKER=
MODEL_OTHER=
CAP_EPISODE_USD=
CAP_SEASON_USD=
CAP_EPISODE_COMPUTE_MIN=
CAP_SEASON_COMPUTE_MIN=
CAP_TICKET_ITERATIONS=
CAP_EPISODE_HOURS=
CAP_SEASON_HOURS=
"""

LOCAL_ENV_EXAMPLE = """\
# Copy this file to production.local.env, which is ignored by git, and fill it in on this machine.
HARNESS=
STATE_DIR=
SECRETS_DIR=
ACCOUNTS_MAKER=
ACCOUNTS_CHECKER=
PROXY_URL=
FERMI_URL=
SANDBOX=on
GATEWAY=on
"""

GITIGNORE = ("production.local.env", "__pycache__/")
FILES = {
    "production.local.env.example": LOCAL_ENV_EXAMPLE,
    "rules/production-rules.md": "# Production rules\n",
    "media/manifest.tsv": "id\tsha256\tbytes\tsource\n",
}
DIRECTORIES = ("sources", "research", "bible/canon", "bible/amendments", "bible/style", "playbook",
               "calibration", "registry", "rulebook", "ledger", "lab", "episodes")
ROOM_DIRECTORIES = ("production", "post", "distribution")


class UsageError(Exception):
    pass


def warn(message: str) -> None:
    print(message, file=sys.stderr)


def parse_rooms(text: str) -> list[str]:
    """The rooms named in `text`, in the canonical order. Raises UsageError on an unknown or no room."""
    names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = sorted(set(names) - set(ROOMS))
    if unknown:
        raise UsageError(f"unknown room {unknown[0]!r}; rooms are {', '.join(ROOMS)}")
    if not names:
        raise UsageError(f"no room given; rooms are {', '.join(ROOMS)}")
    return [room for room in ROOMS if room in names]


def _skipped(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__" or name.endswith(".pyc")


def _role_ids(source: Path, rooms: list[str], warn) -> set[str]:
    """The role ids to install: their room is one of `rooms` or studio, or their file does not parse."""
    keep = set()
    for path in sorted((source / "roles").glob("*.md")):
        if _skipped(path.name):
            continue
        try:
            meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, frontmatter.FrontMatterError) as error:
            warn(f"init: warning: roles/{path.name} does not parse ({error}); copied as is")
            keep.add(path.stem)
            continue
        if meta.get("room") in rooms or meta.get("room") == STUDIO:
            keep.add(path.stem)
    return keep


def _pipeline(path: Path, rooms: list[str], warn) -> str | None:
    """The pipeline filtered to `rooms`, one entry per line; None (copy as is) when it does not parse."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("not a JSON list")
    except (OSError, UnicodeDecodeError, ValueError) as error:
        warn(f"init: warning: pipeline.json does not parse ({error}); copied as is")
        return None
    kept = [entry for entry in entries if isinstance(entry, dict) and entry.get("room") in rooms]
    if not kept:
        return "[]\n"
    return "[\n" + ",\n".join(" " + json.dumps(e, ensure_ascii=False) for e in kept) + "\n]\n"


def _install(source: Path, dest: Path, rooms: list[str], warn) -> int:
    """Copy the kit at `source` into the new directory `dest` for `rooms`. Returns the number of files."""
    roles = _role_ids(source, rooms, warn)
    count = 0
    for dirpath, dirnames, filenames in os.walk(source):
        here = Path(dirpath)
        rel = here.relative_to(source)
        dirnames[:] = sorted(d for d in dirnames if not _skipped(d))
        if rel == Path("evals"):
            dirnames[:] = [d for d in dirnames if d in roles]
        (dest / rel).mkdir(exist_ok=True)
        shutil.copymode(here, dest / rel)
        for name in sorted(filenames):
            if _skipped(name):
                continue
            if rel == Path("roles") and name.endswith(".md") and name[:-3] not in roles:
                continue
            target = dest / rel / name
            text = _pipeline(here / name, rooms, warn) if rel == Path(".") and name == "pipeline.json" else None
            if text is None:
                shutil.copyfile(here / name, target)
            else:
                target.write_text(text, encoding="utf-8")
            shutil.copymode(here / name, target)
            count += 1
    return count


def _replace_kit(root: Path, rooms: list[str], warn) -> int:
    """Install the kit into `root/.factory`, replacing any old one only once the new one is complete."""
    staging = Path(tempfile.mkdtemp(prefix=KIT_DIR + "-", dir=root))
    try:
        count = _install(SOURCE, staging, rooms, warn)
        if (root / KIT_DIR).exists():
            shutil.rmtree(root / KIT_DIR)
        staging.rename(root / KIT_DIR)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return count


def _create(root: Path, rel: str, text: str, created: list[str]) -> None:
    path = root / rel
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(rel)


def _gitignore(root: Path, created: list[str]) -> None:
    path = root / ".gitignore"
    if not path.exists():
        _create(root, ".gitignore", "".join(line + "\n" for line in GITIGNORE), created)
        return
    text = path.read_text(encoding="utf-8")
    present = {line.strip() for line in text.splitlines()}
    missing = [line for line in GITIGNORE if line not in present]
    if missing:
        prefix = "" if text == "" or text.endswith("\n") else "\n"
        path.write_text(text + prefix + "".join(line + "\n" for line in missing), encoding="utf-8")


def _layout(root: Path, rooms: list[str]) -> list[str]:
    created = []
    _create(root, ENV, PRODUCTION_ENV.format(rooms=",".join(rooms)), created)
    for rel, text in FILES.items():
        _create(root, rel, text, created)
    _gitignore(root, created)
    for directory in DIRECTORIES + tuple(r for r in ROOM_DIRECTORIES if r in rooms):
        _create(root, f"{directory}/.gitkeep", "", created)
    return created


def _env_rooms(path: Path) -> str | None:
    """The value of the `ROOMS=` line of `path`, or None without a file or a line."""
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("ROOMS="):
            return line[len("ROOMS="):]
    return None


def _set_env_rooms(path: Path, rooms: list[str]) -> None:
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("ROOMS="):
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = "ROOMS=" + ",".join(rooms) + ending
            path.write_text("".join(lines), encoding="utf-8")
            return


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory init", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("dir", help="the production repository")
    parser.add_argument("--rooms", help=f"comma-separated rooms of {', '.join(ROOMS)} (default: {DEFAULT_ROOMS}; "
                                        "with --upgrade: ROOMS in production.env)")
    parser.add_argument("--upgrade", action="store_true", help="replace an existing .factory/ only")
    args = parser.parse_args(argv)
    root = Path(args.dir)

    try:
        if not root.is_dir():
            raise UsageError(f"{args.dir}: not a directory")
        rooms = parse_rooms(args.rooms) if args.rooms is not None else None
        if args.upgrade:
            if not (root / KIT_DIR).is_dir():
                print("init: not a production (no .factory; run init without --upgrade)", file=sys.stderr)
                return 1
            if rooms is None:
                configured = _env_rooms(root / ENV)
                if configured is None:
                    raise UsageError(f"no ROOMS line in {ENV}; give --rooms")
                rooms = parse_rooms(configured)
        elif (root / KIT_DIR).exists() or (root / ENV).exists():
            print("init: already a production (use --upgrade)", file=sys.stderr)
            return 1
    except UsageError as error:
        print(f"init: {error}", file=sys.stderr)
        return 2

    if args.upgrade:
        count = _replace_kit(root, rooms, warn)
        if args.rooms is not None:
            _set_env_rooms(root / ENV, rooms)
        print(f"upgraded {KIT_DIR} ({count} files)")
        return 0

    rooms = rooms or parse_rooms(DEFAULT_ROOMS)
    _replace_kit(root, rooms, warn)
    for rel in sorted([KIT_DIR] + _layout(root, rooms)):
        print(f"created {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
