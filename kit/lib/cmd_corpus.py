"""factory corpus [--corpus DIR] [--pipeline FILE]: run every check on its seeded-defect corpus.

A case is `<corpus>/<check>/<case-id>/` holding `case.json` and `tree/`. Each tree is copied to a new
temporary directory and `<check>` runs there, so the corpus is never written. Prints, per check, the
seeded cases caught and the clean cases passed. The checks measured are those with cases and the
production checks (the `lint` ids of every pipeline entry whose `room` is not `story`). Exit 0 when
every check catches every defect and passes every clean case, 1 otherwise, 2 on usage or a malformed
corpus.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import lint  # noqa: E402

RUNNING_KIT = Path(__file__).resolve().parent.parent
CORPUS = RUNNING_KIT / "evals" / "golden" / "corpus"
PIPELINE = RUNNING_KIT / "pipeline.json"
CASE_ID = re.compile(r"[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*")
SHAPE = 'not {"kind": "clean"} or {"kind": "seeded", "catches": [regex, ...]}'


class CorpusError(Exception):
    """The corpus or the pipeline file is malformed: one line, exit 2."""


def _parser():
    parser = argparse.ArgumentParser(prog="factory corpus", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--corpus", metavar="DIR", help="the corpus directory (default: <kit>/evals/golden/corpus)")
    parser.add_argument("--pipeline", metavar="FILE", help="the pipeline file (default: <kit>/pipeline.json)")
    return parser


def _json(path: Path, shown: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise CorpusError(f"corpus: {shown}: not a JSON file") from None


def _subdirs(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))


def _case(path: Path, shown: str) -> dict:
    if not CASE_ID.fullmatch(path.name):
        raise CorpusError(f"corpus: {shown}: case id is not <nn>-<slug>")
    data = _json(path / "case.json", f"{shown}/case.json")
    seeded = (isinstance(data, dict) and set(data) == {"kind", "catches"} and data["kind"] == "seeded"
              and isinstance(data["catches"], list) and data["catches"]
              and all(isinstance(r, str) and r for r in data["catches"]))
    if data != {"kind": "clean"} and not seeded:
        raise CorpusError(f"corpus: {shown}/case.json: {SHAPE}")
    for regex in data.get("catches", []):
        try:
            re.compile(regex)
        except re.error:
            raise CorpusError(f"corpus: {shown}/case.json: {regex!r} is not a regular expression") from None
    if not (path / "tree").is_dir():
        raise CorpusError(f"corpus: {shown}: no tree/")
    return data


def load(corpus: Path, shown: str) -> dict[str, dict[str, tuple[Path, dict]]]:
    """`{check: {case_id: (case directory, case.json)}}`, or CorpusError on the first fault."""
    if not corpus.is_dir():
        raise CorpusError(f"corpus: {shown}: not a directory")
    known = lint.discover()
    out = {}
    for check in _subdirs(corpus):
        if check.name not in known:
            raise CorpusError(f"corpus: {check.name}/: no such check")
        out[check.name] = {case.name: (case, _case(case, f"{check.name}/{case.name}"))
                           for case in _subdirs(check)}
    return out


def production_checks(pipeline: Path, shown: str) -> set[str]:
    entries = _json(pipeline, shown)
    ids = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("room") != "story" and isinstance(entry.get("lint"), list):
            ids.update(cid for cid in entry["lint"] if isinstance(cid, str))
    return ids


def run_case(check: str, path: Path) -> lint.Result:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(path / "tree", tree, symlinks=True)
        result = lint.run(tree, [check])[check]
        for shown in sorted({str(tree.resolve()), str(tree)}, key=len, reverse=True):
            result.reason = result.reason.replace(shown, "tree")
    return result


def grade(case: dict, result: lint.Result) -> str:
    """The reason a case does not behave, or "" when it does."""
    if result.status == lint.ERROR:
        return f"error: {result.reason}"
    if case["kind"] == "seeded":
        if result.status == lint.PASS:
            return "missed: the check passed"
        written = [f"{f.path}:{f.line} {f.rule} {f.message}" for f in result.findings]
        for regex in case["catches"]:
            if not any(re.search(regex, line) for line in written):
                return f"missed: no finding matches {regex}"
        return ""
    if result.status == lint.FAIL:
        first = result.findings[0]
        return f"false alarm: {first.path}:{first.line} {first.message}"
    return ""


def report(cases: dict[str, dict[str, tuple[Path, dict]]], checks: set[str]) -> tuple[list[str], bool]:
    lines, ok = [], True
    total = {"caught": 0, "seeded": 0, "passed": 0, "clean": 0}
    for check in sorted(checks):
        count = {"caught": 0, "seeded": 0, "passed": 0, "clean": 0}
        below = []
        for case_id, (path, case) in sorted(cases.get(check, {}).items()):
            kind = case["kind"]
            reason = grade(case, run_case(check, path))
            count[kind] += 1
            if reason:
                below.append(f"  {check}/{case_id} {kind} {reason}")
            else:
                count["caught" if kind == "seeded" else "passed"] += 1
        if not count["seeded"] or not count["clean"]:
            below.append(f"  {check} needs at least one seeded case and one clean case")
        status = "FAIL" if below else "PASS"
        ok = ok and not below
        lines.append(f"{check} {status} caught {count['caught']}/{count['seeded']} "
                     f"clean {count['passed']}/{count['clean']}")
        lines += below
        for key in total:
            total[key] += count[key]
    share = f"{total['caught'] / total['seeded']:.2f}" if total["seeded"] else "-"
    lines.append(f"corpus {'PASS' if ok else 'FAIL'} caught {total['caught']}/{total['seeded']} "
                 f"share {share} clean {total['passed']}/{total['clean']}")
    return lines, ok


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    corpus = Path(args.corpus) if args.corpus is not None else CORPUS
    pipeline = Path(args.pipeline) if args.pipeline is not None else PIPELINE
    try:
        cases = load(corpus, args.corpus if args.corpus is not None else str(CORPUS))
        checks = set(cases) | production_checks(pipeline, args.pipeline if args.pipeline is not None
                                                else str(PIPELINE))
    except CorpusError as e:
        print(e, file=sys.stderr)
        return 2
    lines, ok = report(cases, checks)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
