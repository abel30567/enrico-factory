"""factory eval <role> [--runs N] [--harness fake|real] [--cases GLOB] [--out DIR] [--root DIR]: run a role's eval suite.

Each case is copied to a temporary run directory that `build_run_dir` makes a production around it,
the role runs on it through the harness adapter, and the result is graded. Writes `results.jsonl`
and `summary.json` to the output directory: no prompt, no model output beyond the marker and the
graded detail, no setting value. With `--out`, the result object of each run of each case is also
kept as `runs/<run>/<case>.json`. Exit 0 when every run completed, 2 on usage, refusal or a
malformed suite, 3 when a run's harness outcome was not `ok`. The suite under `<kit>/evals/` is
never written.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import cmd_init, evals, harness, production, prompt  # noqa: E402

RUNNING_KIT = Path(__file__).resolve().parent.parent
LIVE = "FACTORY_LIVE"
FAKE = "FACTORY_FAKE_HARNESS"
TOKEN_FD = 3


def _parser():
    parser = argparse.ArgumentParser(prog="factory eval", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("role", help="the role whose suite <kit>/evals/<role>/ runs")
    parser.add_argument("--runs", type=int, default=1, help="repeat the suite N times (default: 1)")
    parser.add_argument("--harness", choices=("fake", "real"), default="fake",
                        help=f"fake: the executable in ${FAKE} (default); real: orchestrator-only")
    parser.add_argument("--cases", default="*", help="run only the case ids matching GLOB")
    parser.add_argument("--out",
                        help="output directory; keeps each result under runs/ (default: a new temporary directory, printed)")
    parser.add_argument("--root", default=".", help="production directory (default: .)")
    return parser


def _kit_and_settings(root):
    """``(kit, settings, is_production)``: the production's above ``root``, else the running kit."""
    try:
        found = production.find_root(root)
    except production.ProductionError:
        return RUNNING_KIT, {}, False
    loaded = production.load(found)
    return loaded.kit, loaded.settings, True


def _fake_settings(settings):
    executable = os.environ[FAKE]
    if "/" in executable:
        executable = os.path.abspath(executable)
    models = {key: settings.get(key, "").strip() or "fake" for key in ("MODEL_MAKER", "MODEL_CHECKER")}
    return {"HARNESS": executable, **models}


def _real_token(settings, meta, family):
    slot = "OTHER" if family == "other" else meta["model"].upper()
    account = None if slot == "OTHER" else harness.accounts(settings, slot)[0]
    return harness.token_path(settings, slot, account)


PAIR_FILES = ("bible/charter.md", "bible/canon/ledger.tsv")


def build_run_dir(kit, case, run, run_dir):
    """Build the directory ``run_dir`` a role works in for ``case`` in ``run``. Starts no harness.

    A ``seeded``, ``clean`` or ``replay`` case: a production as ``factory init`` makes it, with
    ``.factory/`` copied from ``kit`` without ``evals/``, then the case's ``tree/`` over it (a tree file
    wins). A ``pair`` case: ``A/`` and ``B/`` only, each the version ``evals.sides`` gives it, plus the
    init charter and canon ledger where the version has none.
    """
    run_dir = Path(run_dir)
    if case.kind == "pair":
        for side, version in sorted(evals.sides(case.id, run).items()):
            shutil.copytree(case.path / version, run_dir / side)
            for rel in PAIR_FILES:
                path = run_dir / side / rel
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(cmd_init.FILES[rel], encoding="utf-8")
        return
    cmd_init.install(run_dir, Path(kit), cmd_init.parse_rooms(cmd_init.DEFAULT_ROOMS), evals=False)
    shutil.copytree(case.path / "tree", run_dir, dirs_exist_ok=True)


def _run_case(case, run, job):
    """``(harness status, result object or None, graded)`` for one case in one run."""
    base = job["work"] / f"{run}-{case.id}"
    run_dir = base / "run"
    build_run_dir(job["kit"], case, run, run_dir)
    (base / "config").mkdir()
    values = {"TASK": case.task, "TICKET": f"eval {case.id}", "WORKTREE": str(run_dir),
              "REPO": "eval", "RULES": job["rules"], "BRANCH": "eval", "PR": "0"}
    (base / "prompt.md").write_text(prompt.render(job["body"], values), encoding="utf-8")
    harness.write_mcp_config(base / "mcp.json")
    argv = harness.command(job["settings"], job["meta"], base / "mcp.json")
    env = harness.environment(os.environ, job["settings"], job["family"], base / "config", TOKEN_FD)
    process = harness.start(argv, env, run_dir, base / "prompt.md", job["token"], base / "stdout",
                            base / "stderr", TOKEN_FD)
    code = process.wait()
    outcome = harness.read((base / "stdout").read_text(encoding="utf-8", errors="replace"), code,
                           (base / "stderr").read_text(encoding="utf-8", errors="replace"))
    return outcome.status, outcome.result, evals.grade(case, outcome.result, run=run, run_dir=run_dir)


def _run(args):
    kit, settings, is_production = _kit_and_settings(Path(args.root))
    meta, body = evals.role_file(kit, args.role)
    kind = meta["kind"]
    cases = [case for case in evals.load(kit, args.role) if fnmatchcase(case.id, args.cases)]
    if not cases:
        raise evals.EvalError(f"{args.role}: no case to run")
    # A suite that cannot have a score fails before anything runs.
    evals.metrics(kind, [{"kind": case.kind, "pass": False} for case in cases])
    try:
        rules = (kit / "rules-of-power.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise evals.EvalError("the kit has no readable rules-of-power.md") from None
    if args.harness == "fake":
        settings = _fake_settings(settings)
    elif not is_production:
        raise evals.EvalError("--harness real needs a production: no production.env at or above --root")
    model, family = harness.model(settings, meta)
    version = harness.version(settings)

    suite = (kit / "evals").resolve()
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="factory-eval-"))
    if out.resolve() == suite or suite in out.resolve().parents:
        raise evals.EvalError("--out is inside the kit's evals/: the suite is read-only")
    kept = out / "runs" if args.out else None
    if kept is not None and kept.exists():
        raise evals.EvalError(f"--out {args.out} already holds runs/: give a directory without it")
    out.mkdir(parents=True, exist_ok=True)

    lines, per_run, failed = [], [], False
    with tempfile.TemporaryDirectory(prefix="factory-eval-work-") as work:
        work = Path(work)
        if args.harness == "fake":
            token = work / "token"
            token.touch(mode=0o600)
        else:
            token = _real_token(settings, meta, family)
        job = {"kit": kit, "work": work, "rules": rules, "body": body, "meta": meta,
               "settings": settings, "family": family, "token": token}
        for run in range(1, args.runs + 1):
            graded = []
            for case in cases:
                status, returned, result = _run_case(case, run, job)
                if kept is not None and returned is not None:
                    (kept / str(run)).mkdir(parents=True, exist_ok=True)
                    (kept / str(run) / f"{case.id}.json").write_text(
                        json.dumps(returned, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
                failed = failed or status != "ok"
                graded.append(result)
                lines.append(json.dumps({"role": args.role, "run": run, "status": status, **result},
                                        sort_keys=True))
            per_run.append(evals.metrics(kind, graded))
            print(f"run {run}: score {per_run[-1]['score']}")
            if per_run[-1]["blocked"]:
                print(f"run {run}: {per_run[-1]['blocked']} blocked")

    spread = evals.noise([m["score"] for m in per_run])
    summary = {"role": args.role, "kind": kind, "harness": args.harness, "harness_version": version,
               "model": model, "cases": len(cases), "runs": args.runs, "per_run": per_run,
               "noise": spread}
    (out / "results.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    measured = "measured" if spread["measured"] else "not measured"
    print(f"noise floor: {json.dumps(spread['floor'])} over {spread['runs']} runs ({measured})")
    if not args.out:
        print(f"out: {out}")
    return 3 if failed else 0


def main(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.harness == "real" and os.environ.get(LIVE) != "1":
        print(f"eval: live runs are orchestrator-only ({LIVE}=1)", file=sys.stderr)
        return 2
    if args.harness == "fake" and not os.environ.get(FAKE):
        print(f"eval: {FAKE} is not set: it names the fake harness executable", file=sys.stderr)
        return 2
    try:
        return _run(args)
    except (evals.EvalError, harness.HarnessError, production.ProductionError,
            prompt.PromptError) as error:
        print(f"eval: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
