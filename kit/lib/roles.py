"""Role files, format files and the pipeline: loaders and the validator (brief sections 4 to 6)."""

import json
import re
from pathlib import Path

from kit.lib import frontmatter
from kit.lib.frontmatter import as_list

ROLE_REQUIRED = ("role", "room", "kind", "analog", "stage", "reads", "model", "family", "tools",
                 "knowledge", "secrets", "gated")
ROLE_OPTIONAL = ("writes", "checked_by", "may_not")
ROOMS = ("story", "production", "post", "distribution", "lab", "studio")
KINDS = ("orchestrator", "maker", "checker", "judge", "operator", "analyst")
MODELS = ("maker", "checker")
FAMILIES = ("any", "other")
GATED = ("yes", "no")
TOOLS = ("Read", "Edit", "Write", "Grep", "Glob", "Bash", "WebSearch", "WebFetch")
KNOWLEDGE = ("skills", "memory-read", "memory-write", "search", "skills-write")
PLACEHOLDERS = ("REPO", "TICKET", "WORKTREE", "TASK", "RULES")
PLACEHOLDERS_BY_KIND = {"maker": ("BRANCH",), "operator": ("BRANCH",),
                        "checker": ("PR",), "judge": ("PR",)}

FORMAT_REQUIRED = ("format", "runtime", "beats", "shots", "skips")

PIPELINE_KEYS = ("room", "stage", "unit", "after", "maker", "lint", "checker", "judge", "blind",
                 "pitches", "paths")
STAGE_ROLE_KINDS = (("maker", ("maker", "operator")), ("checker", ("checker",)),
                    ("judge", ("judge",)))

_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")
_RANGE = re.compile(r"(\d+)-(\d+)")


def _load_dir(directory, label):
    """Parse every `*.md` in `directory`. Returns ({stem: doc}, [problems])."""
    docs, problems = {}, []
    for path in sorted(Path(directory).glob("*.md")):
        name = f"{label}/{path.name}"
        try:
            meta, body = frontmatter.parse(path.read_text(encoding="utf-8"))
        except frontmatter.FrontMatterError as error:
            problems.append(f"{name}: {error}")
            continue
        docs[path.stem] = {"file": name, "meta": meta, "body": body}
    return docs, problems


def _load_or_raise(directory, label):
    docs, problems = _load_dir(directory, label)
    if problems:
        raise ValueError(problems[0])
    return docs


def load_roles(directory):
    """{role id: {"file", "meta", "body"}} for every `<id>.md` in `directory`."""
    return _load_or_raise(directory, "roles")


def load_formats(directory):
    """{format id: {"file", "meta", "body"}} for every `<id>.md` in `directory`."""
    return _load_or_raise(directory, "formats")


def load_pipeline(path):
    """The list of stage entries in `pipeline.json`."""
    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("must be a JSON list of objects")
    return entries


def _names(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value if v.strip()]
    return None


def _check_choice(problems, where, meta, key, choices):
    if key in meta and meta[key] not in choices:
        problems.append(f"{where}: {key} {meta[key]!r} is not one of {', '.join(choices)}")


def _check_subset(problems, where, meta, key, allowed):
    for item in as_list(meta.get(key)):
        if item not in allowed:
            problems.append(f"{where}: {key} {item!r} is not one of {', '.join(allowed)}")


def _validate_role(problems, role_id, doc, roles):
    where, meta, kind = doc["file"], doc["meta"], doc["meta"].get("kind")
    # Rule 1
    if "role" in meta and meta["role"] != role_id:
        problems.append(f"{where}: role {meta['role']!r} does not match the file name")
    for key in ROLE_REQUIRED:
        if key not in meta:
            problems.append(f"{where}: missing key {key!r}")
    for key in sorted(set(meta) - set(ROLE_REQUIRED) - set(ROLE_OPTIONAL)):
        problems.append(f"{where}: unknown key {key!r}")
    # Rules 2 and 3
    _check_choice(problems, where, meta, "room", ROOMS)
    _check_choice(problems, where, meta, "kind", KINDS)
    _check_choice(problems, where, meta, "model", MODELS)
    _check_choice(problems, where, meta, "family", FAMILIES)
    _check_choice(problems, where, meta, "gated", GATED)
    if kind in KINDS and meta.get("gated") in GATED:
        expected = "yes" if kind == "operator" else "no"
        if meta["gated"] != expected:
            problems.append(f"{where}: gated must be {expected!r} for kind {kind}")
    # Rule 4
    if kind in ("maker", "operator") and not as_list(meta.get("writes")):
        problems.append(f"{where}: writes is empty for kind {kind}")
    if kind == "maker":
        checkers = as_list(meta.get("checked_by"))
        if not checkers:
            problems.append(f"{where}: checked_by is empty for kind maker")
        for name in checkers:
            if name not in roles:
                problems.append(f"{where}: checked_by {name!r} is not a role")
            elif roles[name]["meta"].get("kind") not in ("checker", "judge"):
                problems.append(f"{where}: checked_by {name!r} has kind "
                                f"{roles[name]['meta'].get('kind')}, not checker or judge")
    # Rules 5 and 6
    _check_subset(problems, where, meta, "tools", TOOLS)
    _check_subset(problems, where, meta, "knowledge", KNOWLEDGE)
    if kind == "judge" and as_list(meta.get("knowledge")):
        problems.append(f"{where}: knowledge must be empty for kind judge")
    # Rule 7
    required = PLACEHOLDERS + PLACEHOLDERS_BY_KIND.get(kind, ())
    found = set(_PLACEHOLDER.findall(doc["body"]))
    for name in required:
        if name not in found:
            problems.append(f"{where}: body is missing placeholder {{{{{name}}}}}")
    for name in sorted(found - set(required)):
        problems.append(f"{where}: body has placeholder {{{{{name}}}}} not allowed for kind {kind}")


def _check_range(problems, where, meta, key):
    if key not in meta:
        return
    match = _RANGE.fullmatch(meta[key])
    if not match or int(match.group(1)) > int(match.group(2)):
        problems.append(f"{where}: {key} {meta[key]!r} is not min-max integers with min <= max")


def _validate_format(problems, format_id, doc, stages):
    # Rule 8
    where, meta = doc["file"], doc["meta"]
    for key in FORMAT_REQUIRED:
        if key not in meta:
            problems.append(f"{where}: missing key {key!r}")
    if "format" in meta and meta["format"] != format_id:
        problems.append(f"{where}: format {meta['format']!r} does not match the file name")
    _check_range(problems, where, meta, "runtime")
    _check_range(problems, where, meta, "shots")
    if "beats" in meta and not as_list(meta["beats"]):
        problems.append(f"{where}: beats is empty")
    for stage in as_list(meta.get("skips")):
        if stage not in stages:
            problems.append(f"{where}: skips {stage!r} is not a stage in the pipeline")


def _validate_entry(problems, entry, seen, roles, lint_names):
    where = f"pipeline.json: {entry.get('room')}/{entry.get('stage')}"
    # Rule 9
    for key in PIPELINE_KEYS:
        if key not in entry:
            problems.append(f"{where}: missing key {key!r}")
    room_stages = seen.setdefault(entry.get("room"), [])
    stage, after = entry.get("stage"), entry.get("after")
    if "stage" in entry and stage in room_stages:
        problems.append(f"{where}: stage {stage!r} is not unique in room {entry.get('room')}")
    if "after" in entry and after != "" and after not in room_stages:
        problems.append(f"{where}: after {after!r} is not an earlier stage of room "
                        f"{entry.get('room')}")
    room_stages.append(stage)
    pitches = entry.get("pitches")
    if "pitches" in entry and (isinstance(pitches, bool) or not isinstance(pitches, int)
                               or pitches < 1):
        problems.append(f"{where}: pitches {pitches!r} is not an integer >= 1")
    paths = entry.get("paths")
    if "paths" in entry and (not isinstance(paths, list) or not paths
                             or not all(isinstance(p, str) and p for p in paths)):
        problems.append(f"{where}: paths must be a non-empty list of globs")
    # Rule 10
    for key, kinds in STAGE_ROLE_KINDS:
        if key not in entry:
            continue
        names = _names(entry[key])
        if names is None:
            problems.append(f"{where}: {key} must be a string or a list of strings")
            continue
        for name in names:
            if name not in roles:
                problems.append(f"{where}: {key} {name!r} is not a role")
            elif roles[name]["meta"].get("kind") not in kinds:
                problems.append(f"{where}: {key} {name!r} has kind "
                                f"{roles[name]['meta'].get('kind')}, not {' or '.join(kinds)}")
    # Rule 11
    if "lint" in entry:
        lints = entry["lint"]
        if not isinstance(lints, list) or not all(isinstance(i, str) for i in lints):
            problems.append(f"{where}: lint must be a list of lint ids")
        else:
            for lint_id in lints:
                if lint_id not in lint_names:
                    problems.append(f"{where}: lint {lint_id!r} is not a lint")


def validate(kit_dir, lint_names=None):
    """Every problem in the kit's role files, format files and pipeline, sorted. Empty is valid."""
    kit = Path(kit_dir)
    if lint_names is None:
        lint_names = {p.stem for p in (kit / "lib" / "lints").glob("*.py")
                      if not p.stem.startswith("_")}
    problems = []
    roles, found = _load_dir(kit / "roles", "roles")
    problems += found
    formats, found = _load_dir(kit / "formats", "formats")
    problems += found
    try:
        pipeline = load_pipeline(kit / "pipeline.json")
    except OSError as error:
        problems.append(f"pipeline.json: cannot read: {error.strerror}")
        pipeline = []
    except ValueError as error:
        problems.append(f"pipeline.json: {error}")
        pipeline = []

    for role_id, doc in roles.items():
        _validate_role(problems, role_id, doc, roles)
    stages = {entry.get("stage") for entry in pipeline}
    for format_id, doc in formats.items():
        _validate_format(problems, format_id, doc, stages)
    seen = {}
    for entry in pipeline:
        _validate_entry(problems, entry, seen, roles, set(lint_names))
    return sorted(problems)
