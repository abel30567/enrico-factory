"""Role evals (brief section 13): the case format, the loader, the grader, suite metrics and noise.

A case is a directory ``<kit>/evals/<role>/<case-id>/`` holding ``case.json``, ``task.md`` and
``tree/`` (for a ``pair`` case, ``old/`` and ``new/``). Nothing here runs a role or writes a file:
``factory eval`` (cmd_eval.py) copies a case, runs it through the harness adapter and calls ``grade``.
A grade's detail is built from the case and fixed words, never from model output.
"""

import hashlib
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from kit.lib import frontmatter, harness, lint, roles

CASE_KEYS = {"seeded": ("catches",), "clean": (), "replay": ("lint", "expect_files"),
             "refusal": ("lint", "expect_files", "forbid"), "pair": ("better",), "verdict": ("verdicts",),
             "ruling": ("results",), "selection": ("selected",)}
CASE_KINDS = {"checker": ("seeded", "clean"), "maker": ("replay", "refusal", "verdict"), "judge": ("pair",),
              "analyst": ("verdict",), "orchestrator": ("ruling", "selection")}
# The kinds whose suite needs at least one case, an orchestrator's a ruling or selection case; a role
# of another kind may have none.
NEEDS_CASES = ("checker", "maker", "judge", "orchestrator")
RULINGS = ("acted on", "rejected")
VERDICTS = ("usable", "not usable")
SIDES = ("old", "new")
PLACES = 4
MEASURED_RUNS = 5

_ROLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class EvalError(Exception):
    pass


@dataclass(frozen=True)
class Case:
    role: str
    id: str
    kind: str
    path: Path
    task: str
    spec: dict
    role_kind: str
    actor: str


def role_file(kit, role):
    """``(meta, body)`` of ``<kit>/roles/<role>.md``."""
    if not _ROLE_ID.fullmatch(role):
        raise EvalError(f"{role!r}: not a role id")
    try:
        text = (Path(kit) / "roles" / f"{role}.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EvalError(f"{role}: no role file roles/{role}.md") from None
    except (OSError, UnicodeDecodeError):
        raise EvalError(f"{role}: roles/{role}.md cannot be read") from None
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        raise EvalError(f"{role}: roles/{role}.md: {error}") from None
    if meta.get("kind") not in roles.KINDS:
        raise EvalError(f"{role}: kind {meta.get('kind')!r} is not one of {', '.join(roles.KINDS)}")
    return meta, body


def _is_strings(value):
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _spec_problem(spec, role_kind, lint_ids):
    """The first thing wrong with a parsed ``case.json``, or None."""
    if not isinstance(spec, dict):
        return "case.json is not an object"
    kind = spec.get("kind")
    if kind not in CASE_KEYS:
        return f"unknown kind {kind!r}"
    unknown = sorted(set(spec) - {"kind"} - set(CASE_KEYS[kind]))
    if unknown:
        return f"unknown key {unknown[0]!r}"
    for key in CASE_KEYS[kind]:
        if key not in spec:
            return f"missing key {key!r}"
    if kind not in CASE_KINDS.get(role_kind, ()):
        return f"a {kind} case does not fit a role of kind {role_kind}"
    if kind == "verdict":
        verdicts = spec["verdicts"]
        if not isinstance(verdicts, dict) or not verdicts \
                or any(not isinstance(vid, str) or not vid for vid in verdicts) \
                or any(value not in VERDICTS for value in verdicts.values()):
            return "verdicts is not an object of video id to usable or not usable"
    if kind == "ruling":
        results = spec["results"]
        if not isinstance(results, dict) or not results \
                or any(not isinstance(note, str) or not note for note in results) \
                or any(value not in RULINGS for value in results.values()):
            return "results is not an object of note to acted on or rejected"
    if kind == "selection":
        selected = spec["selected"]
        if not isinstance(selected, int) or isinstance(selected, bool) or selected < 1:
            return "selected is not a pull request number >= 1"
    if kind == "seeded":
        if not _is_strings(spec["catches"]) or not spec["catches"]:
            return "catches is not a non-empty list of regexes"
        for pattern in spec["catches"]:
            try:
                re.compile(pattern)
            except re.error:
                return f"catches: regex {pattern!r} does not compile"
    if kind == "refusal":
        if not _is_strings(spec["forbid"]) or not spec["forbid"]:
            return "forbid is not a non-empty list of regexes"
        for pattern in spec["forbid"]:
            try:
                re.compile(pattern)
            except re.error:
                return f"forbid: regex {pattern!r} does not compile"
        if spec["expect_files"] == []:
            return "expect_files is empty"
    if kind in ("replay", "refusal"):
        if not _is_strings(spec["lint"]) or not spec["lint"]:
            return "lint is not a non-empty list of check ids"
        for check in spec["lint"]:
            if check not in lint_ids:
                return f"lint: {check!r} is not a lint"
        if not _is_strings(spec["expect_files"]):
            return "expect_files is not a list of globs"
        for pattern in spec["expect_files"]:
            if pattern.startswith("/"):
                return f"expect_files: {pattern!r} is not relative"
    if kind == "pair" and spec["better"] not in SIDES:
        return f"better {spec['better']!r} is not one of {', '.join(SIDES)}"
    return None


def _case(path, role, role_kind, actor, lint_ids):
    where = f"{role}/{path.name}"
    try:
        spec = json.loads((path / "case.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError(f"{where}: missing case.json") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise EvalError(f"{where}: case.json is not readable JSON") from None
    problem = _spec_problem(spec, role_kind, lint_ids)
    if problem:
        raise EvalError(f"{where}: {problem}")
    try:
        task = (path / "task.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EvalError(f"{where}: missing task.md") from None
    except (OSError, UnicodeDecodeError):
        raise EvalError(f"{where}: task.md cannot be read") from None
    for name in (SIDES if spec["kind"] == "pair" else ("tree",)):
        if not (path / name).is_dir():
            raise EvalError(f"{where}: missing {name}/")
    if spec["kind"] == "pair" and (path / "media").exists():
        raise EvalError(f"{where}: a pair case holds no media/")
    return Case(role, path.name, spec["kind"], path, task, spec, role_kind, actor)


def load(kit, role):
    """The cases of ``<kit>/evals/<role>/``, sorted by id.

    A checker, maker, judge or orchestrator needs at least one case; a role of another kind may have none.
    """
    meta, _ = role_file(kit, role)
    role_kind = meta["kind"]
    suite = Path(kit) / "evals" / role
    paths = sorted((p for p in suite.iterdir() if p.is_dir()), key=lambda p: p.name) \
        if suite.is_dir() else []
    if not paths and role_kind in NEEDS_CASES:
        raise EvalError(f"{role}: no cases in evals/{role}/")
    # Only a maker may hold replay and refusal cases, the only ones that name lints.
    lint_ids = set(lint.discover()) if role_kind == "maker" else set()
    actor = roles.actor({**meta, "role": role})
    return [_case(path, role, role_kind, actor, lint_ids) for path in paths]


def sides(case_id, run):
    """Which version each side of a pair case shows: A is ``new`` when the hash's first byte is even."""
    first = hashlib.sha256(f"{case_id}\n{run}".encode("utf-8")).digest()[0]
    a = "new" if first % 2 == 0 else "old"
    return {"A": a, "B": "old" if a == "new" else "new"}


def _grade_seeded(case, result):
    if result["marker"] != "NOTES":
        return False, f"not caught: marker {result['marker']}"
    blockers = [n for n in result.get("notes", []) if n["severity"] == "Blocker"]
    missed = [p for p in case.spec["catches"]
              if not any(re.search(p, n["text"]) or re.search(p, n["evidence"]) for n in blockers)]
    if missed:
        return False, "not caught: no Blocker matches " + ", ".join(missed)
    return True, "caught"


def _grade_clean(case, result):
    if result["marker"] == "BLOCKED":
        return False, "blocked: marker BLOCKED"
    count = sum(n["severity"] == "Blocker" for n in result.get("notes", []))
    if result["marker"] == "NOTES" and count:
        return False, f"false alarm: {count} Blocker(s)"
    return True, f"no alarm: marker {result['marker']}"


def _grade_replay(case, result, run_dir):
    if result["marker"] != "READY":
        return False, f"marker {result['marker']}, not READY"
    run_dir = Path(run_dir)
    missing = [g for g in case.spec["expect_files"] if not any(p.is_file() for p in run_dir.glob(g))]
    status = lint.overall(lint.run(run_dir, only=case.spec["lint"]))
    parts = [f"lint {status}"] + (["missing " + ", ".join(missing)] if missing else [])
    return status == lint.PASS and not missing, "; ".join(parts)


def _grade_refusal(case, result, run_dir):
    """A refusal case passes on BLOCKED naming every forbid regex, or on READY that uses none."""
    forbid = case.spec["forbid"]
    if result["marker"] == "BLOCKED":
        missed = [p for p in forbid if not re.search(p, result["blocked"]["missing"])]
        if missed:
            return False, "blocked: missing does not match " + ", ".join(missed)
        return True, "refused: marker BLOCKED"
    if result["marker"] != "READY":
        return False, f"marker {result['marker']}, not READY or BLOCKED"
    passed, detail = _grade_replay(case, result, run_dir)
    run_dir = Path(run_dir)
    paths = sorted({p for g in case.spec["expect_files"] for p in run_dir.glob(g) if p.is_file()})
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in paths]
    named = [p for p in forbid if any(re.search(p, text) for text in texts)]
    if named:
        return False, detail + "; names " + ", ".join(named)
    return passed, detail


def _found_verdicts(case, result, run_dir):
    """``{video id: set of verdicts}`` the result gives: a maker's from the notes of its trend cards
    (``research/trends/*.tsv`` in ``run_dir``), an analyst's from the start of its notes' text."""
    found = {}
    if case.role_kind == "analyst":
        for note in result.get("notes", []):
            for vid in case.spec["verdicts"]:
                m = re.match(re.escape(vid) + r" (usable|not usable): \S", note["text"])
                if m:
                    found.setdefault(vid, set()).add(m.group(1))
        return found
    for path in sorted(Path(run_dir).glob("research/trends/*.tsv")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            fields = line.split("\t")
            m = re.match(r"(usable|not usable): \S", fields[5]) if len(fields) == 6 else None
            if m:
                found.setdefault(fields[2], set()).add(m.group(1))
    return found


def _grade_verdict(case, result, run_dir):
    """A verdict case passes when each video's verdicts are exactly the case's one."""
    want = "SYMPTOMS" if case.role_kind == "analyst" else "READY"
    if result["marker"] != want:
        return False, f"marker {result['marker']}, not {want}"
    found = _found_verdicts(case, result, run_dir)
    wrong = sorted(vid for vid, verdict in case.spec["verdicts"].items() if found.get(vid) != {verdict})
    if wrong:
        return False, "wrong or no verdict: " + ", ".join(wrong)
    return True, "every verdict right"


def _grade_ruling(case, result):
    """A ruling case passes when each note's evidence results are exactly the case's one."""
    if result["marker"] != "RULING":
        return False, f"marker {result['marker']}, not RULING"
    found = {}
    for entry in result.get("evidence", []):
        found.setdefault(entry["criterion"].strip(), set()).add(entry["result"].strip())
    wrong = sorted(note for note, value in case.spec["results"].items() if found.get(note) != {value})
    if wrong:
        return False, "wrong or no ruling: " + ", ".join(wrong)
    return True, "every note ruled as given"


def _grade_selection(case, result):
    if result["marker"] != "SELECTED":
        return False, f"marker {result['marker']}, not SELECTED"
    got, want = result["selected"], case.spec["selected"]
    return got == want, f"selected #{got}, better is #{want}"


def _grade_pair(case, result, run):
    choice = harness.PREFERS.get(result["marker"])
    if choice is None:
        return False, f"marker {result['marker']}, no preference given"
    if choice == "none":
        return False, "no preference"
    chosen = sides(case.id, run)[choice]
    return chosen == case.spec["better"], f"preferred {chosen}, better is {case.spec['better']}"


def grade(case, result, run=1, run_dir=None):
    """``{"case", "kind", "marker", "pass", "detail"}`` for one result of one case.

    ``run`` is the run index (it decides the sides of a pair case); ``run_dir`` is the directory the
    role worked in (a replay or refusal case's files and lints, a maker's trend cards, are read there). A result that is None
    or fails ``harness.validate_result`` does not pass and has marker None.
    """
    graded = {"case": case.id, "kind": case.kind, "marker": None, "pass": False, "detail": "no result"}
    if result is None:
        return graded
    problems = harness.validate_result(case.role_kind, case.actor, result)
    if problems:
        graded["detail"] = f"invalid result: {len(problems)} problem(s)"
        return graded
    if case.kind == "seeded":
        passed, detail = _grade_seeded(case, result)
    elif case.kind == "clean":
        passed, detail = _grade_clean(case, result)
    elif case.kind == "replay":
        passed, detail = _grade_replay(case, result, run_dir)
    elif case.kind == "refusal":
        passed, detail = _grade_refusal(case, result, run_dir)
    elif case.kind == "verdict":
        passed, detail = _grade_verdict(case, result, run_dir)
    elif case.kind == "ruling":
        passed, detail = _grade_ruling(case, result)
    elif case.kind == "selection":
        passed, detail = _grade_selection(case, result)
    else:
        passed, detail = _grade_pair(case, result, run)
    graded.update(marker=result["marker"], detail=detail)
    graded["pass"] = passed
    return graded


def _blocked(graded):
    return graded.get("marker") == "BLOCKED"


def _rate(graded, kind, counts):
    """The share of ``kind`` cases for which ``counts`` holds; None with no such case."""
    group = [g for g in graded if g["kind"] == kind]
    return sum(bool(counts(g)) for g in group) / len(group) if group else None


def _passed(graded):
    return graded["pass"] is True


def metrics(kind, graded):
    """The suite's rates and score for one run of a role of ``kind``, rounded to 4 places.

    A checker: catch rate on seeded cases less false-alarm rate on clean ones (a clean case that does
    not pass and is not ``BLOCKED`` counts as an alarm). A maker: lint pass rate on replay cases and,
    with a refusal case, refusal rate; its score is the share passed over both kinds; with a verdict
    case, also verdict rate. A maker with verdict cases only, and an analyst: verdict rate, its score.
    A judge: agreement.
    An orchestrator: decision rate, the share of its ruling and selection cases passed, its score.
    A blocked case counts in each denominator and in no numerator; ``blocked`` is the number of
    graded cases whose marker is ``BLOCKED``. A rate with no case to count is None, and then there is
    no score: ``EvalError``.
    """
    if kind == "checker":
        rates = {"catch_rate": _rate(graded, "seeded", _passed),
                 "false_alarm_rate": _rate(graded, "clean",
                                           lambda g: g["pass"] is False and not _blocked(g))}
    elif kind == "analyst" or kind == "maker" and graded and all(g["kind"] == "verdict" for g in graded):
        rates = {"verdict_rate": _rate(graded, "verdict", _passed)}
    elif kind == "maker":
        rates = {"lint_pass_rate": _rate(graded, "replay", _passed)}
        if any(g["kind"] == "refusal" for g in graded):
            rates["refusal_rate"] = _rate(graded, "refusal", _passed)
        if any(g["kind"] == "verdict" for g in graded):
            rates["verdict_rate"] = _rate(graded, "verdict", _passed)
    elif kind == "judge":
        rates = {"agreement": _rate(graded, "pair", _passed)}
    elif kind == "orchestrator":
        decided = [g for g in graded if g["kind"] in CASE_KINDS["orchestrator"]]
        rates = {"decision_rate": sum(_passed(g) for g in decided) / len(decided) if decided else None}
    else:
        raise EvalError(f"no eval score for a role of kind {kind}")
    for name, value in rates.items():
        if value is None:
            raise EvalError(f"{kind} score: {name} is null, no case to count")
    values = list(rates.values())
    if kind == "checker":
        score = values[0] - values[1]
    elif "lint_pass_rate" in rates:
        counted = [g for g in graded if g["kind"] in ("replay", "refusal")]
        score = sum(_passed(g) for g in counted) / len(counted)
    else:
        score = values[0]
    out = {name: round(value, PLACES) for name, value in rates.items()}
    out["score"] = round(score, PLACES)
    out["blocked"] = sum(_blocked(g) for g in graded)
    return out


def noise(scores):
    """``{"runs", "mean", "floor", "measured"}``: the floor is the sample standard deviation of the
    suite score over repeated runs, None under 2 runs, and measured only with 5 runs or more."""
    scores = list(scores)
    return {
        "runs": len(scores),
        "mean": round(statistics.fmean(scores), PLACES) if scores else None,
        "floor": round(statistics.stdev(scores), PLACES) if len(scores) >= 2 else None,
        "measured": len(scores) >= MEASURED_RUNS,
    }
