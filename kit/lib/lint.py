"""The lint framework: discover checks by file name, run them, report PASS, FAIL or ERROR."""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
LINTS_DIR = Path(__file__).resolve().parent / "lints"


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str


@dataclass
class Result:
    status: str
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""


class LintError(Exception):
    """An input a check needs cannot be read. The check reports ERROR with this reason."""


def discover() -> dict[str, object]:
    """Every check module in kit/lib/lints/, keyed by its file name."""
    importlib.invalidate_caches()
    names = sorted(p.stem for p in LINTS_DIR.glob("*.py") if not p.stem.startswith("_"))
    return {name: importlib.import_module(f"{__package__}.lints.{name}") for name in names}


def _run_one(module, root: Path) -> Result:
    try:
        findings = sorted(module.check(root))
    except LintError as e:
        return Result(ERROR, reason=str(e))
    except Exception as e:  # a check that raises is ERROR, never PASS
        return Result(ERROR, reason=f"{type(e).__name__}: {e}")
    return Result(FAIL if findings else PASS, findings)


def run(root: Path, only: list[str] | None = None) -> dict[str, Result]:
    root = Path(root)
    checks = discover()
    ids = sorted(set(only)) if only else sorted(checks)
    results = {}
    for cid in ids:
        if cid not in checks:
            results[cid] = Result(ERROR, reason="no such check")
        elif not root.is_dir():
            results[cid] = Result(ERROR, reason="root is not a directory")
        else:
            results[cid] = _run_one(checks[cid], root)
    return results


def overall(results: dict[str, Result]) -> str:
    statuses = {r.status for r in results.values()}
    return ERROR if ERROR in statuses else FAIL if FAIL in statuses else PASS


def exit_code(results: dict[str, Result]) -> int:
    return {PASS: 0, FAIL: 1, ERROR: 2}[overall(results)]


def _summary(r: Result) -> str:
    if r.status == ERROR:
        return r.reason
    if r.status == FAIL:
        return f"{len(r.findings)} finding" + ("" if len(r.findings) == 1 else "s")
    return ""


def blockers(results: dict[str, Result]) -> list[str]:
    """Findings as `B<n> <id> <path>:<line> <message>`, sorted by check, path, line."""
    rows = [(cid, f) for cid in sorted(results) for f in results[cid].findings]
    return [f"B{n} {cid} {f.path}:{f.line} {f.message}" for n, (cid, f) in enumerate(rows, 1)]


def text(results: dict[str, Result]) -> str:
    lines = [f"{cid} {r.status}" + (f" {_summary(r)}" if _summary(r) else "")
             for cid, r in sorted(results.items())]
    return "\n".join(lines + blockers(results)) + "\n"


def to_json(results: dict[str, Result]) -> str:
    data = {
        "status": overall(results),
        "checks": {
            cid: {
                "status": r.status,
                "reason": r.reason,
                "findings": [vars(f) for f in r.findings],
            }
            for cid, r in sorted(results.items())
        },
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def marker(results: dict[str, Result]) -> str:
    """The `[LINT]` comment body: status line, per-check table, Blockers."""
    lines = [f"[LINT] {overall(results)}", "", "| Check | Result | Reason |", "|---|---|---|"]
    lines += [f"| {cid} | {r.status} | {_summary(r)} |" for cid, r in sorted(results.items())]
    found = blockers(results)
    lines += ["", "Blockers:" if found else "Blockers: none"]
    lines += [f"- {b}" for b in found]
    return "\n".join(lines) + "\n"


# Helpers shared by checks.

KIT_DIR = ".factory"


def walk(root: Path, suffix: str | None = None) -> list[str]:
    """Every file under root as a sorted posix path relative to root.

    Skips `.git/` and the top-level `.factory/` (the installed kit, read only by the `kit` check).
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not (rel == Path(".") and d == KIT_DIR))
        for name in filenames:
            if suffix is None or name.endswith(suffix):
                out.append((rel / name).as_posix())
    return sorted(out)


def files(root: Path) -> list[Path]:
    """The files a check reads, as sorted paths relative to root: the ones git would commit.

    Inside a git work tree: tracked files and untracked files no ignore rule excludes, less any path
    that is gone from disk or is a directory or is under the top-level `.factory/`. Otherwise, or when
    git cannot run: `walk(root)`.
    """
    root = Path(root)
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard"], stdin=subprocess.DEVNULL, capture_output=True)
    except OSError:
        p = None
    if p is None or p.returncode != 0:
        return [Path(rel) for rel in walk(root)]
    rels = {Path(os.fsdecode(raw)) for raw in p.stdout.split(b"\0") if raw}
    return sorted(rel for rel in rels if not (len(rel.parts) > 1 and rel.parts[0] == KIT_DIR)
                  and os.path.lexists(root / rel) and not (root / rel).is_dir())


def read_text(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise LintError(f"cannot read {rel}: {type(e).__name__}") from None


def read_json(root: Path, rel: str):
    raw = read_text(root, rel)
    try:
        return raw, json.loads(raw)
    except json.JSONDecodeError as e:
        raise LintError(f"cannot parse {rel}:{e.lineno}: {e.msg}") from None


def json_strings(text: str):
    """Scan valid JSON text in document order.

    Returns (strings, objects): `strings` is (line, key, value) for every string that is not a key,
    where `key` is the object key it is the direct value of, else None; `objects` is (line, depth)
    for every `{`, depth counting the containers around it.
    """
    strings, objects = [], []
    line, depth, i, n = 1, 0, 0, len(text)
    key, after_colon = None, False
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            value = json.loads(text[i:j + 1])
            k = j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == ":":
                key = value
            else:
                strings.append((line, key if after_colon else None, value))
            after_colon = False
            i = j + 1
            continue
        if c == "\n":
            line += 1
        elif c == ":":
            after_colon = True
        elif c in "{[":
            if c == "{":
                objects.append((line, depth))
            depth += 1
            after_colon = False
        elif c in "}]":
            depth -= 1
        elif c == ",":
            after_colon = False
        i += 1
    return strings, objects


_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_INLINE_CODE = re.compile(r"(`+).*?\1")
LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)|^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")


def markdown_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) outside fenced code blocks, with inline code spans removed."""
    out, fence = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is None:
            out.append((n, _INLINE_CODE.sub("", line)))
    return out


def link_targets(line: str) -> list[str]:
    return [a or b for a, b in LINK_TARGET.findall(line)]


def read_tsv(root: Path, rel: str) -> list[tuple[int, list[str]]]:
    """Non-empty lines of a TSV file as (line number, fields), header included."""
    return [(n, row.split("\t")) for n, row in enumerate(read_text(root, rel).splitlines(), 1)
            if row.strip()]
