"""The eval suite of `render-reviewer` in kit/evals/ (brief section 13), run offline (ISSUE-396).

Each case is one case of the seeded-defect corpus in kit/evals/golden/corpus/, named in its
`corpus.txt`. Hand-written results stand in for the role: nothing here runs a model.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit.lib import evals

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
ROLE = "render-reviewer"
SUITE = KIT / "evals" / ROLE
CORPUS = KIT / "evals" / "golden" / "corpus"
FIX = ROOT / "tests" / "fixtures" / "story_evals" / ROLE
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

RELEASE_SHA = hashlib.sha256(b"ep01-video").hexdigest()
ADDED = {"media/manifest.tsv": f"id\tsha256\tbytes\tsource\nep01-video\t{RELEASE_SHA}\t4096\tconform-editor\n",
         "registry/variants.tsv": f"variant\tsha256\nep01-video\t{RELEASE_SHA}\n"}
SOURCE = re.compile(r"[a-z0-9-]+/[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*\n")
APPROVED = {"marker": "APPROVED", "summary": "Approved."}


def files(path):
    return sorted(p.relative_to(path).as_posix() for p in Path(path).rglob("*") if p.is_file())


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def kind(case_dir):
    return json.loads((case_dir / "case.json").read_text(encoding="utf-8"))["kind"]


def corpus_messages(suite, corpus):
    """What the cases of ``suite`` lack against the cases of ``corpus`` they name in `corpus.txt`."""
    messages, sources = [], {}
    for case_dir in sorted(p for p in Path(suite).iterdir() if p.is_dir()):
        case = case_dir.name
        named = case_dir / "corpus.txt"
        text = named.read_text(encoding="utf-8") if named.is_file() else ""
        if not SOURCE.fullmatch(text):
            messages.append(f"{case}: corpus.txt is not one line <check>/<case-id>")
            continue
        source = text[:-1]
        if not (Path(corpus) / source / "case.json").is_file():
            messages.append(f"{case}: corpus.txt names {source}, which is not a case of the corpus")
            continue
        sources.setdefault(source, []).append(case)
        origin = Path(corpus) / source
        if kind(case_dir) != kind(origin):
            messages.append(f"{case}: a {kind(case_dir)} case comes from the {kind(origin)} case {source}")
        tree, theirs = case_dir / "tree", origin / "tree"
        ours, given = files(tree), files(theirs)
        messages += [f"{case}: tree/{rel} of {source} is missing" for rel in given if rel not in ours]
        messages += [f"{case}: tree/{rel} is not in {source}/tree/" for rel in ours
                     if rel not in given and rel not in ADDED]
        messages += [f"{case}: tree/{rel} is not the bytes of {source}/tree/{rel}" for rel in given
                     if rel in ours and (tree / rel).read_bytes() != (theirs / rel).read_bytes()]
        messages += [f"{case}: tree/{rel} is not the release row of ep01-video" for rel in sorted(ADDED)
                     if not (tree / rel).is_file() or (tree / rel).read_text(encoding="utf-8") != ADDED[rel]]
    for source in sorted(sources):
        if len(sources[source]) > 1:
            messages.append(f"{source} is the source of {len(sources[source])} cases: "
                            + ", ".join(sources[source]))
    return messages


def fixture(case):
    return json.loads((FIX / f"{case.id}.json").read_text(encoding="utf-8"))


def run_eval(role, answer):
    """``(completed process, summary)`` of ``factory eval role`` on the fake harness, ``answer(case)``
    the result of each case."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "result": answer(case)}
                 for case in evals.load(KIT, role)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", role, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


class RenderReviewerEvalsTest(unittest.TestCase):
    def copy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / ROLE
        shutil.copytree(SUITE, copy)
        return copy

    def test_each_case_is_a_case_of_the_corpus(self):
        self.assertEqual(corpus_messages(SUITE, CORPUS), [])
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())

    def test_a_case_names_no_corpus_case(self):
        copy = self.copy()
        (copy / "01-quay-steps" / "corpus.txt").unlink()
        (copy / "02-net-shed" / "corpus.txt").write_text("render/09-no-such-case\n", encoding="utf-8")
        self.assertEqual(corpus_messages(copy, CORPUS),
                         ["01-quay-steps: corpus.txt is not one line <check>/<case-id>",
                          "02-net-shed: corpus.txt names render/09-no-such-case, which is not a case of the corpus"])

    def test_a_tree_is_not_its_source(self):
        copy = self.copy()
        tree = copy / "07-kelp-beds" / "tree"
        render = tree / "production" / "ep01" / "Q1-1" / "render" / "render.json"
        text = render.read_text(encoding="utf-8")
        self.assertIn('"nan": [\n    3\n  ]', text)
        render.write_text(text.replace('"nan": [\n    3\n  ]', '"nan": []'), encoding="utf-8")
        (tree / "production" / "ep01" / "shots.json").unlink()
        (tree / "registry" / "variants.tsv").write_text("variant\tsha256\n", encoding="utf-8")
        (tree / "notes.md").write_text("A note.\n", encoding="utf-8")
        self.assertEqual(corpus_messages(copy, CORPUS), [
            "07-kelp-beds: tree/production/ep01/shots.json of render/05-nan-frame is missing",
            "07-kelp-beds: tree/notes.md is not in render/05-nan-frame/tree/",
            "07-kelp-beds: tree/production/ep01/Q1-1/render/render.json is not the bytes of "
            "render/05-nan-frame/tree/production/ep01/Q1-1/render/render.json",
            "07-kelp-beds: tree/registry/variants.tsv is not the release row of ep01-video"])

    def test_a_clean_case_from_a_seeded_source(self):
        copy = self.copy()
        (copy / "02-net-shed" / "corpus.txt").write_text("render/02-black-frame\n", encoding="utf-8")
        messages = corpus_messages(copy, CORPUS)
        self.assertIn("02-net-shed: a clean case comes from the seeded case render/02-black-frame", messages)
        self.assertIn("render/02-black-frame is the source of 2 cases: 01-quay-steps, 02-net-shed", messages)

    def test_a_reviewer_that_catches_scores_one(self):
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(ROLE, lambda c: APPROVED if c.kind == "clean" else fixture(c))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"],
                         [{"blocked": 0, "catch_rate": 1.0, "false_alarm_rate": 0.0, "score": 1.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_reviewer_that_approves_everything_scores_zero(self):
        done, summary = run_eval(ROLE, lambda c: APPROVED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0")
        self.assertEqual(summary["per_run"],
                         [{"blocked": 0, "catch_rate": 0.0, "false_alarm_rate": 0.0, "score": 0.0}])


if __name__ == "__main__":
    unittest.main()
