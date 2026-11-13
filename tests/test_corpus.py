"""Tests for `factory corpus`: every check on its seeded-defect corpus. No network, no model."""

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "corpus"
FACTORY = KIT / "factory"
PIPELINE = KIT / "pipeline.json"
SHIPPED = KIT / "evals" / "golden" / "corpus"

sys.path.insert(0, str(ROOT))
from kit.lib import frontmatter, lint, production  # noqa: E402

try:
    from kit.lib import cmd_corpus  # noqa: E402
except ImportError:
    cmd_corpus = None

ACCEPTANCE_2 = ("canon PASS caught 1/1 clean 1/1\n"
                "media PASS caught 2/2 clean 1/1\n"
                "corpus PASS caught 3/3 share 1.00 clean 2/2\n")
EVERY_REASON = """\
canon FAIL caught 0/2 clean 1/1
  canon/02-kiln-ledger seeded missed: no finding matches canon-self
  canon/03-reed-ledger seeded error: episode ledgers present and no bible/canon/ledger.tsv
intake FAIL caught 0/0 clean 0/0
  intake needs at least one seeded case and one clean case
media FAIL caught 2/3 clean 0/1
  media/02-lantern-manifest seeded missed: the check passed
  media/04-bell-manifest clean false alarm: media/manifest.tsv:3 id tide-ref repeats line 2
corpus FAIL caught 2/5 share 0.40 clean 1/2
"""
FREEZE = ("No role and no engineering team may edit the suite it is measured by: a check's cases are not "
          "written in the pull request that writes or changes the check.")
FROM_LEVEL = ("A case is made from a golden level in `evals/golden/<level>/`: its tree holds the level's "
              "recorded files, and a seeded case changes one value.")


def python(*args, cwd=ROOT):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd,
                          stdin=subprocess.DEVNULL)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def tree_state(path):
    digest = hashlib.sha256()
    for item in sorted(Path(path).rglob("*")):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0")
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


def shots_check(root):
    """The stand-in production check: a shot whose `last` frame is below its `first`."""
    rel = "production/ep01/shots.json"
    if not (root / rel).is_file():
        return []
    return [lint.Finding(rel, 1, "shots", f"shot {row['shot']} ends before it starts")
            for row in lint.read_json(root, rel)[1] if row["last"] < row["first"]]


class CorpusTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cmd_corpus, "kit/lib/cmd_corpus.py is missing")
        self.story = self.tempdir() / "pipeline.json"
        write(self.story, json.dumps([e for e in json.loads(PIPELINE.read_text(encoding="utf-8")) if e["room"] == "story"]))

    def tempdir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def copy(self):
        dest = self.tempdir() / "corpus"
        shutil.copytree(FIX, dest)
        return dest

    def corpus(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_corpus.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_fixture_corpus_passes_by_both_entry_points(self):
        self.assertEqual(self.corpus("--corpus", FIX, "--pipeline", self.story), (0, ACCEPTANCE_2, ""))
        by_factory = python(FACTORY, "corpus", "--corpus", FIX, "--pipeline", self.story)
        by_script = python(KIT / "lib" / "cmd_corpus.py", "--corpus", FIX, "--pipeline", self.story)
        self.assertEqual((by_factory.returncode, by_factory.stdout, by_factory.stderr), (0, ACCEPTANCE_2, ""))
        self.assertEqual((by_script.returncode, by_script.stdout, by_script.stderr), (0, ACCEPTANCE_2, ""))

    def test_every_reason_at_once(self):
        c = self.copy()
        write(c / "media/02-lantern-manifest/case.json", '{"kind": "seeded", "catches": ["manifest-id"]}')
        write(c / "canon/02-kiln-ledger/case.json", '{"kind": "seeded", "catches": ["canon-self"]}')
        reed = c / "canon/03-reed-ledger"
        shutil.copytree(c / "canon/02-kiln-ledger/tree/episodes", reed / "tree/episodes")
        write(reed / "case.json", '{"kind": "seeded", "catches": ["x"]}')
        shutil.copytree(c / "media/01-quay-manifest/tree", c / "media/04-bell-manifest/tree")
        write(c / "media/04-bell-manifest/case.json", '{"kind": "clean"}')
        entries = json.loads(self.story.read_text(encoding="utf-8"))
        entries.append({"room": "production", "stage": "intake", "lint": ["intake", "media"]})
        pipeline = self.tempdir() / "pipeline.json"
        write(pipeline, json.dumps(entries))
        first = self.corpus("--corpus", c, "--pipeline", pipeline)
        self.assertEqual(first, (1, EVERY_REASON, ""))
        self.assertEqual(self.corpus("--corpus", c, "--pipeline", pipeline), first)

    def test_malformed_corpus_is_exit_2(self):
        def case(text):
            return lambda c: write(c / "media/02-lantern-manifest/case.json", text)

        faults = [  # (change to the copy, extra arguments, the line after "corpus: ")
            (shutil.rmtree, [], "{c}: not a directory"),
            (lambda c: write(c.parent / "bad.json", "{"), ["--pipeline", "{p}"], "{p}: not a JSON file"),
            (lambda c: (c / "no-such-check").mkdir(), [], "no-such-check/: no such check"),
            (lambda c: shutil.copytree(c / "media/02-lantern-manifest", c / "media/5-bad"), [],
             "media/5-bad: case id is not <nn>-<slug>"),
            (case("nope"), [], "media/02-lantern-manifest/case.json: not a JSON file"),
            (case('{"kind": "seeded", "catches": []}'), [], "media/02-lantern-manifest/case.json: not "
             '{"kind": "clean"} or {"kind": "seeded", "catches": [regex, ...]}'),
            (case('{"kind": "seeded", "catches": ["("]}'), [],
             "media/02-lantern-manifest/case.json: '(' is not a regular expression"),
            (lambda c: shutil.rmtree(c / "media/02-lantern-manifest/tree"), [], "media/02-lantern-manifest: no tree/"),
        ]
        for change, extra, line in faults:
            with self.subTest(line=line):
                c = self.copy()
                change(c)
                paths = {"{c}": str(c), "{p}": str(c.parent / "bad.json")}
                with mock.patch.object(cmd_corpus, "run_case", side_effect=AssertionError("a case ran")):
                    got = self.corpus("--corpus", c, *(paths.get(a, a) for a in extra))
                shown = line.replace("{c}", paths["{c}"]).replace("{p}", paths["{p}"])
                self.assertEqual(got, (2, "", f"corpus: {shown}\n"))
        c = self.copy()
        (c / ".git" / "objects").mkdir(parents=True)
        (c / "media" / ".cache").mkdir()
        self.assertEqual(self.corpus("--corpus", c, "--pipeline", self.story), (0, ACCEPTANCE_2, ""))

    def test_production_check_is_measured(self):
        checks = dict(lint.discover())
        checks["intake"] = types.SimpleNamespace(check=shots_check)
        root = self.tempdir()
        pipeline = root / "pipeline.json"
        write(pipeline, '[{"room": "production", "stage": "intake", "lint": ["intake"]}]')
        c = root / "corpus"
        shots = c / "intake/01-quay/tree/production/ep01/shots.json"
        write(c / "intake/01-quay/case.json", '{"kind": "clean"}')
        write(shots, '[{"shot": "S01", "first": 0, "last": 47}, {"shot": "S02", "first": 48, "last": 95}]')
        write(c / "intake/02-kiln/case.json", '{"kind": "seeded", "catches": ["shots shot S02 "]}')
        write(c / "intake/02-kiln/tree/production/ep01/shots.json", '[{"shot": "S02", "first": 48, "last": 3}]')
        with mock.patch.object(lint, "discover", return_value=checks):
            self.assertEqual(self.corpus("--corpus", c, "--pipeline", pipeline),
                             (0, "intake PASS caught 1/1 clean 1/1\ncorpus PASS caught 1/1 share 1.00 clean 1/1\n", ""))
            write(shots, '[{"shot": "S01", "first": 0, "last": 47}, {"shot": "S02", "first": 48, "last": 3}]')
            code, out, err = self.corpus("--corpus", c, "--pipeline", pipeline)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("  intake/01-quay clean false alarm: production/ep01/shots.json:1 shot S02 ends before it starts\n",
                      out)

    def test_case_runs_on_a_copy(self):
        work = self.tempdir()
        self.assertEqual(subprocess.run(["git", "init", "-q", str(work)], stdin=subprocess.DEVNULL).returncode, 0)
        c = work / "corpus"
        shutil.copytree(FIX, c)
        still = c / "media/04-reed-still"
        write(still / "tree/media/still.png", "x")
        write(still / "case.json", '{"kind": "seeded", "catches": ["^media/still.png:1 media-bytes "]}')
        write(work / ".gitignore", "still.png\n")
        before = tree_state(work)
        code, out, err = self.corpus("--corpus", c, "--pipeline", self.story)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("media PASS caught 3/3 clean 1/1\n", out)
        in_place = python(FACTORY, "lint", "--root", still / "tree", "--only", "media")
        self.assertEqual((in_place.returncode, in_place.stdout), (0, "media PASS\n"))
        self.assertEqual(tree_state(work), before)

    def test_shipped_corpus(self):
        code, out, err = self.corpus()
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out.splitlines()[-1], r"^corpus PASS caught (\d+)/\1 share \S+ clean (\d+)/\2$")
        self.assertEqual(sorted(p.name for p in (KIT / "evals" / "golden").iterdir()), ["corpus"])
        readme = (SHIPPED / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(readme.splitlines()), 40)
        for sentence in (FREEZE, FROM_LEVEL):
            self.assertIn(sentence, readme)
        probes = ("kit/evals/golden/corpus/x/01-a/tree/f", ".factory/evals/golden/corpus/x/01-a/tree/f")
        for path in sorted((KIT / "roles").glob("*.md")):
            meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
            writes = frontmatter.as_list(meta.get("writes"))
            for probe in probes:
                self.assertFalse(production.matches(probe, writes), f"{path.name} writes {probe}")

    def test_init_does_not_install_golden(self):
        target = self.tempdir() / "prod"
        target.mkdir()
        result = python(FACTORY, "init", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / ".factory" / "evals").is_dir())
        self.assertFalse((target / ".factory" / "evals" / "golden").exists())


if __name__ == "__main__":
    unittest.main()
