"""Tests for the harness-fork check of `factory gate` (kit/lib/harnessfork.py, docs/harness.md section 9).

The production is tests/fixtures/gate_fork/production, made into a git repository on branch `trunk` in a
temporary directory; GitHub is a copy of tests/fixtures/gate_fork/github. Issue #1 is the clean fork issue
and #2 an unrelated one. Every defect is made here, in the copy: no defect fixture is committed. Every test
sets ``FACTORY_GITHUB`` and ``FACTORY_NOW``: nothing reaches the network or ``gh``.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, gitstate, github, harnessfork  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "gate_fork"
FAKE_HARNESS = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
NOW = "2026-11-13T09:00:00Z"
LOG = "lab/results.tsv"
CLEAN = json.loads((FIX / "github" / "issues" / "1.json").read_text())["body"]
REPRO = "command: python3 repro/two-tools.py"
PASS = "PASS cap - reason the three conditions hold"


def digest(body, number=1):
    return hashlib.sha256(f"fork\n{number}\n{body}".encode("utf-8")).hexdigest()


def edited(old, new, body=CLEAN):
    """``body`` with its one ``old`` replaced by ``new``."""
    assert body.count(old) == 1, old
    return body.replace(old, new)


def without(name, body=CLEAN):
    """``body`` without its section ``## <name>``."""
    head, rest = body.split(f"## {name}\n", 1)
    return head + (rest[rest.index("## "):] if "## " in rest else "")


class World:
    """The production on branch `trunk` and a recorded GitHub directory, both temporary."""

    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.local(FAKE_HARNESS)
        shutil.copytree(FIX / "github", self.dir)
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def local(self, harness):
        """``production.local.env``, never committed, naming the harness."""
        (self.root / "production.local.env").write_text(f"HARNESS={harness}\n")
        (self.root / ".git" / "info" / "exclude").write_text("production.local.env\n")

    def tip(self):
        return gitstate.head(self.root, "trunk")

    def log_lines(self):
        return (self.root / LOG).read_text().split("\n")[:-1]

    def set_log(self, lines):
        """The results log as ``lines``, committed on `trunk`."""
        (self.root / LOG).write_text("".join(line + "\n" for line in lines))
        self.commit("the log")

    def set_row(self, n, **fields):
        """Line ``n`` of the log with ``fields`` changed, committed on `trunk`."""
        lines = self.log_lines()
        row = dict(zip(("time", "loop", "kind", "commit", "values", "best", "threshold", "verdict"),
                       lines[n - 1].split("\t")))
        row.update(fields)
        lines[n - 1] = "\t".join(row.values())
        self.set_log(lines)

    def issue(self, number, **changes):
        path = self.dir / "issues" / f"{number}.json"
        found = json.loads(path.read_text())
        found.update(changes)
        path.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")

    def comments(self, number):
        return self.gh.comments(number)

    def log(self):
        path = self.dir / "log.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []

    def gate(self, target="1", apply=True):
        """``(exit, stdout, stderr)`` of ``factory gate`` run in-process."""
        out, err = io.StringIO(), io.StringIO()
        argv = [target, "--root", str(self.root)] + (["--apply"] if apply else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_gate.main(argv)
        return code, out.getvalue(), err.getvalue()


class GateFork(unittest.TestCase):

    def stops(self, cases, prepare=None):
        """Each ``(body or None, reason)``: the gate on #1 exits 1 with ``reason``, in a fresh world."""
        for body, reason in cases:
            with self.subTest(reason=reason, body=body):
                w = World(self)
                if prepare is not None:
                    prepare(w)
                if body is not None:
                    w.issue(1, body=body)
                code, out, err = w.gate()
                self.assertEqual((code, err), (1, ""), out)
                self.assertEqual(out, f"gate fork {digest(body or CLEAN)}: STOP cap - reason {reason}\n")

    def passes(self, body):
        w = World(self)
        w.issue(1, body=body)
        code, out, err = w.gate()
        self.assertEqual((code, out, err), (0, f"gate fork {digest(body)}: {PASS}\n", ""))

    def test_1_the_clean_issue_passes_once(self):
        w = World(self)
        tip, log = w.tip(), (w.root / LOG).read_text()
        line = f"gate fork {digest(CLEAN)}: {PASS}\n"
        self.assertEqual(w.gate(), (0, line, ""))
        comments = w.comments(1)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["role"], "program:gate")
        self.assertEqual(comments[0]["body"], f"[PRODUCER] GATE PASS\ncovers: fork {digest(CLEAN)}\n"
                                              "cap: -  reason: the three conditions hold\nledger: -\n")
        self.assertEqual(comments[0]["body"], harnessfork.body(digest(CLEAN), "PASS", harnessfork.HOLD))
        self.assertEqual([e["op"] for e in w.log()], ["post"])
        self.assertEqual((w.tip(), (w.root / LOG).read_text()), (tip, log))
        self.assertEqual(w.git("status", "--porcelain"), "")
        with self.subTest("again"):
            self.assertEqual(w.gate(), (0, line.rstrip("\n") + " (already recorded)\n", ""))
            self.assertEqual(len(w.comments(1)), 1)
            self.assertEqual([e["op"] for e in w.log()], ["post"])
        with self.subTest("crlf"):
            self.passes(CLEAN.replace("\n", "\r\n"))

    def test_2_condition_1_lab_programs(self):
        first, second, third = ("- inspector-eval lines 2-11\n", "- inspector-eval lines 12-17\n",
                                "- inspector-eval lines 18-22\n")
        self.stops([
            (edited(third, ""), "condition 1: ## Lab programs names 2 programs, not 3"),
            (edited(third, third + "- inspector-eval lines 23-24\n"),
             "condition 1: ## Lab programs names 4 programs, not 3"),
            (without("Lab programs"), "condition 1: no section ## Lab programs"),
            (edited(first, "- inspector-eval 2-11\n"),
             "condition 1: line '- inspector-eval 2-11' is not - <loop> lines <first>-<last>"),
            (edited(second, "- inspector-evals lines 12-17\n"),
             "condition 1: lab/loops/inspector-evals.json is not on trunk"),
            (edited(third, "- inspector-eval lines 18-23\n"),
             "condition 1: inspector-eval lines 18-23 are not rows of lab/results.tsv"),
            (edited(first, "- inspector-eval lines 2-6\n"), "condition 1: inspector-eval lines 2-6 hold no run row"),
            (edited(second, "- inspector-eval lines 11-17\n"),
             "condition 1: the three programs are not in order without overlap"),
        ])

        def no_role(w):
            path = w.root / "lab/loops/inspector-eval.json"
            spec = json.loads(path.read_text())
            spec["command"] = ["python3", "lab/metrics/role-eval.py", "painter"]
            path.write_text(json.dumps(spec) + "\n")
            w.commit("another role")
        self.stops([(None, "condition 1: loop inspector-eval does not run the eval of inspector")], no_role)
        self.stops([(edited(first, "- inspector-eval lines 3-11\n"),
                     "condition 1: line 3 of lab/results.tsv is not a row of loop inspector-eval")],
                   lambda w: w.set_row(3, loop="render-cost"))
        self.stops([(None, "condition 1: line 19 of lab/results.tsv is a KEEP of loop inspector-eval")],
                   lambda w: w.set_row(19, verdict="KEEP"))

        def appended(w):
            lines = w.log_lines()
            w.set_log(lines + [lines[-1].replace("\tNOISE", "\tKEEP")])
        self.stops([(None, "condition 1: line 23 of lab/results.tsv is a KEEP of loop inspector-eval")], appended)

        def removed(w):
            (w.root / LOG).unlink()
            w.commit("no log")
        self.stops([(None, "condition 1: no lab/results.tsv on trunk")], removed)
        self.stops([(None, "condition 1: results line 1: the header is not "
                           "time loop kind commit values best threshold verdict")],
                   lambda w: w.set_log(["time\tloop"] + w.log_lines()[1:]))

    def test_3_condition_2_cause_and_reproduction(self):
        cause = "A tool result is dropped when two tools answer in one turn.\n"
        self.stops([
            (without("Cause"), "condition 2: no section ## Cause"),
            (edited(cause, "\n   \n"), "condition 2: section ## Cause is empty"),
            (without("Reproduction"), "condition 2: no section ## Reproduction"),
            (edited("## Reproduction\n" + REPRO, "## Reproduction\n"),
             "condition 2: ## Reproduction has no line command: <command>"),
            (CLEAN.replace(REPRO, "command: python3 .factory/factory launch inspector 1"),
             "condition 2: the reproduction runs the prompt of inspector"),
            (CLEAN.replace(REPRO, "command: cat .factory/roles/inspector.md"),
             "condition 2: the reproduction runs the prompt of inspector"),
        ])
        with self.subTest("twin"):
            self.passes(CLEAN.replace(REPRO, "command: python3 repro/inspector-free.py"))

    def test_4_condition_3_pinned_build(self):
        missing = "condition 3: ## Pinned build has no line {}: <value>"
        self.stops([
            (without("Pinned build"), "condition 3: no section ## Pinned build"),
            (edited("harness: 0.0.0 (fake)\n", ""), missing.format("harness")),
            (edited("\n" + REPRO + "\nexit", "\nexit"), missing.format("command")),
            (edited("exit: 1\n", ""), missing.format("exit")),
            (edited("settings tried: SANDBOX=on, SANDBOX=off\n", ""), missing.format("settings tried")),
            (edited("harness: 0.0.0 (fake)", "harness: 0.0.1 (fake)"),
             "condition 3: harness 0.0.1 (fake) is not the pinned build 0.0.0 (fake)"),
            (edited("\n" + REPRO + "\nexit", "\n" + REPRO + "x\nexit"),
             "condition 3: the command is not the reproduction's"),
            (edited("exit: 1", "exit: 0"), "condition 3: exit 0 is not a failing exit status"),
            (edited("exit: 1", "exit: fails"), "condition 3: exit fails is not a failing exit status"),
        ])
        self.stops([(None, "condition 3: the pinned build's version cannot be read")],
                   lambda w: w.local(w.root / "no-such-harness"))
        with self.subTest("twin"):
            self.passes(edited("exit: 1", "exit: -9"))

    def test_5_the_role(self):
        self.stops([
            (edited("role: inspector\n", ""), "the issue has no line role: <role id>"),
            (edited("role: inspector", "role: nobody"), "role nobody is not a role of the kit"),
        ])

        def no_suite(w):
            (w.root / ".factory/evals/inspector/suite.json").unlink()
            w.commit("no suite")
        self.stops([(None, "role inspector has no eval suite evals/inspector/")], no_suite)

    def test_6_without_apply_nothing_is_posted(self):
        for body, verdict in ((CLEAN, "PASS"), (without("Cause"), "STOP")):
            with self.subTest(verdict=verdict):
                w = World(self)
                w.issue(1, body=body)
                code, out, err = w.gate(apply=False)
                self.assertEqual((code, err), (0 if verdict == "PASS" else 1, ""))
                self.assertTrue(out.startswith(f"gate fork {digest(body)}: {verdict} cap - reason "), out)
                self.assertEqual([e for e in w.log() if e["op"] == "post"], [])
                self.assertEqual(w.comments(1), [])

    def test_7_other_targets_are_unchanged(self):
        with self.subTest("unlabelled issue"):
            self.assertEqual(World(self).gate("2"), (2, "", "gate: #2 is not an open pull\n"))
        with self.subTest("closed fork issue"):
            w = World(self)
            w.issue(1, state="closed")
            self.assertEqual(w.gate(), (2, "", "gate: #1 is a closed harness-fork issue\n"))
            self.assertEqual(w.log(), [])
        with self.subTest("frozen"):
            w = World(self)
            w.issue(2, labels=["factory:freeze"])
            self.assertEqual(w.gate(), (2, "", "gate: frozen\n"))
            self.assertEqual(w.log(), [])

    def test_8_each_section_appears_once(self):
        cases = []
        for name, condition in (("Lab programs", 1), ("Cause", 2), ("Reproduction", 2), ("Pinned build", 3)):
            section = CLEAN[CLEAN.index(f"## {name}\n"):]
            section = section[:section.index("\n## ", 1) + 1] if "\n## " in section else section
            cases.append((CLEAN + "\n" + section, f"condition {condition}: section ## {name} appears 2 times"))
        self.stops(cases)


if __name__ == "__main__":
    unittest.main()
