"""Markers: every recorded comment's first line is a marker its role may post (brief sections 8 and 10)."""

from __future__ import annotations

from pathlib import Path

from .. import markers, roles
from ..lint import Finding, LintError, json_strings, read_json
from .kit import kit_dir

COMMENTS = ".factory-state/comments.json"
PROGRAM = "program:"
# The "By" column of brief section 8 for the markers a program posts: command -> {actor: verbs}.
PROGRAM_ACTORS = {
    "lint": {"LINT": markers.PROGRAM_VERBS["LINT"], "PLATFORM-GATE": markers.PROGRAM_VERBS["PLATFORM-GATE"]},
    "gate": {"PRODUCER": markers.PROGRAM_VERBS["PRODUCER"]},
}


def _kinds(root: Path) -> dict[str, str]:
    try:
        loaded = roles.load_roles(kit_dir(root) / "roles")
    except ValueError as e:
        raise LintError(f"cannot load the kit's roles: {e}") from None
    return {role_id: doc["meta"].get("kind", "") for role_id, doc in loaded.items()}


def _comments(root: Path) -> tuple[list[dict], list[int]]:
    """The recorded comments and the line each one starts on."""
    raw, data = read_json(root, COMMENTS)
    if not isinstance(data, list):
        raise LintError(f"{COMMENTS}: must be a JSON list of comments")
    for n, c in enumerate(data, 1):
        if not (isinstance(c, dict) and "id" in c and isinstance(c.get("role"), str)
                and isinstance(c.get("body"), str)):
            raise LintError(f"{COMMENTS}: entry {n} is not {{id, role, body}} with string role and body")
    lines = [line for line, depth in json_strings(raw)[1] if depth == 1]
    return data, lines


def _rule3(role: str, marker: markers.Marker, kinds: dict[str, str]) -> tuple[str, str] | None:
    """(rule, reason) when `role` may not post `marker`: wrong actor, or a verb it may not post."""
    if role.startswith(PROGRAM):
        command = role[len(PROGRAM):]
        if command == "lock":
            actors = {r.upper(): ("LOCK",) for r, k in kinds.items() if k == "orchestrator"}
        elif command in PROGRAM_ACTORS:
            actors = PROGRAM_ACTORS[command]
        else:
            return "marker-actor", f"program {command!r} posts no markers"
        if marker.actor not in actors:
            return "marker-actor", f"actor {marker.actor} is not one {role} posts as ({', '.join(sorted(actors))})"
        if marker.verb not in actors[marker.actor]:
            return "marker-verb", f"{role} may not post {marker.verb}"
        return None
    if role not in kinds:
        return "marker-actor", f"role {role!r} is not a role in the kit"
    if marker.actor != role.upper():
        return "marker-actor", f"actor {marker.actor} does not match role {role} ({role.upper()})"
    if not markers.may_post(kinds[role], marker.verb):
        return "marker-verb", f"a {kinds[role] or 'role without a kind'} may not post {marker.verb}"
    return None


def _check_comment(comment: dict, line: int, kinds: dict[str, str]) -> list[Finding]:
    cid, role, body = comment["id"], comment["role"], comment["body"]
    first = body.split("\n", 1)[0].strip()
    marker = markers.parse(body)
    found = []
    if marker is None:
        if not role.startswith(PROGRAM):
            found.append(("marker-missing", f"first line {first!r} is not a marker; a role's comment starts with one"))
    else:
        bad = _rule3(role, marker, kinds)
        if bad:
            found.append((bad[0], f"{first!r}: {bad[1]}"))
        if (marker.actor == "LINT" or marker.verb in markers.ROLE_VERDICTS) and markers.reviewed(body) is None:
            found.append(("marker-reviewed", f"{first!r}: verdict does not end with "
                                             f"'reviewed head <sha> manifest <hash>'"))
    return [Finding(COMMENTS, line, rule, f"comment {cid}: {message}") for rule, message in found]


def check(root: Path) -> list[Finding]:
    if not (root / COMMENTS).exists():
        return []
    comments, lines = _comments(root)
    kinds = _kinds(root)
    found = []
    for comment, line in zip(comments, lines):
        found += _check_comment(comment, line, kinds)
    return found
