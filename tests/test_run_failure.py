"""Tests for the comment `factory run` posts on the ticket of a job that FAILED (kit/lib/cmd_run.py).

The world is ``test_run.World``. The refusing tool is tests/fixtures/run/tools/refuses.py, which raises the
script's ``refuse`` with ``vendor._before``; the other failures use the fake tool. The comments posted are
read from the recorded GitHub's ``comments/3.json``, role ``program:run``.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import ledger, markers, vendor  # noqa: E402
from test_run import PULL, TICKET, FailingPost, World, digest_of, line  # noqa: E402

ROLE = "program:run"
REFUSALS = (vendor.NO_REFERENCE, vendor.NOT_IN_MANIFEST, "reference is framed like a card scan",
            "reference image could not be decoded")


def refusing(w, name, reason):
    """A job of the refusing tool whose script refuses with ``reason``, passed by the real gate; its path and hash."""
    job = w.script(name, refuse=reason)
    data = json.loads(Path(job).read_text())
    data["tool"] = "refuses"
    Path(job).write_text(json.dumps(data, indent=1))
    return job, w.passed(job)


def body(digest, reason, tool, usd, compute_min):
    return (f"Job {digest} failed: {reason}\n"
            f"tool {tool}, pull #{PULL}, usd {usd} compute_min {compute_min}\n")


def posted(w):
    """The bodies of the ``program:run`` comments on the ticket."""
    path = w.dir / "comments" / f"{TICKET}.json"
    found = json.loads(path.read_text()) if path.is_file() else []
    return [c["body"] for c in found if c["role"] == ROLE]


def post_entry(text):
    return {"op": "post", "number": TICKET, "body": text, "role": ROLE}


PUSH = {"op": "push", "number": None, "ref": "trunk"}


def comment(role, text):
    return {"author": "factory", "body": text, "created_at": "2026-11-01T10:00:00Z", "id": 90, "role": role}


class RefusalTest(unittest.TestCase):
    def test_each_refusal_is_posted_on_the_ticket(self):
        for n, reason in enumerate(REFUSALS):
            with self.subTest(reason=reason):
                w = World(self)
                job, digest = refusing(w, f"refuse-{n}", reason)
                before, log = posted(w), w.log()
                self.assertEqual(w.run(job), (1, line(digest, "FAILED", "0", "0") + "\n",
                                              f"run: tool refuses failed: {reason}\n"))
                expected = body(digest, reason, "refuses", "0", "0")
                self.assertEqual(posted(w), before + [expected])
                self.assertEqual(w.log()[len(log):], [post_entry(expected), PUSH])
                self.assertNotIn(job, expected)
                self.assertIsNone(markers.parse(expected))
                self.assertEqual(w.fake_calls(), [digest])
        # The twin: a DONE run posts no such comment.
        w = World(self)
        digest = w.passed()
        self.assertEqual(w.run(), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        self.assertEqual(posted(w), [])


class OtherFailureTest(unittest.TestCase):
    def test_every_failed_verdict_is_posted_with_the_kit_s_reason(self):
        w = World(self)
        job = w.script("other", outputs={}, fail="anything else", usd=0.4, compute_min=5)
        digest = w.passed(job)
        self.assertEqual(w.run(job), (1, line(digest, "FAILED", "0.4", "5") + "\n",
                                      "run: tool fake failed: tool failed\n"))
        self.assertEqual(posted(w), [body(digest, "tool failed", "fake", "0.4", "5")])
        w = World(self)
        digest = w.passed()
        (w.job_dir(digest) / "out").mkdir(parents=True)
        self.assertEqual(w.run(), (1, line(digest, "FAILED", "2.5", "30") + "\n",
                                   "run: tool fake failed: no result\n"))
        self.assertEqual(posted(w), [body(digest, "no result", "fake", "2.5", "30")])
        self.assertEqual(w.fake_calls(), [])


class NoRepostTest(unittest.TestCase):
    def test_a_recorded_run_posts_nothing(self):
        w = World(self)
        job, digest = refusing(w, "refuse", vendor.NO_REFERENCE)
        self.assertEqual(w.run(job)[0], 1)
        self.assertEqual(len(posted(w)), 1)
        log = w.log()
        for _ in range(2):
            self.assertEqual(w.run(job), (1, line(digest, "FAILED", "0", "0") + " (already recorded)\n", ""))
        self.assertEqual(w.log(), log)
        self.assertEqual(len(posted(w)), 1)


class CrashTest(unittest.TestCase):
    def test_a_crash_between_the_post_and_the_row(self):
        w = World(self)
        job, digest = refusing(w, "refuse", vendor.NOT_IN_MANIFEST)
        with mock.patch.object(ledger, "append", side_effect=ledger.LedgerError("disk full")):
            self.assertEqual(w.run(job), (2, "", "run: disk full\n"))
        self.assertEqual(len(posted(w)), 1)
        self.assertEqual(w.rows("run"), [])
        self.assertEqual(w.run(job), (1, line(digest, "FAILED", "0", "0") + "\n",
                                      f"run: tool refuses failed: {vendor.NOT_IN_MANIFEST}\n"))
        self.assertEqual(posted(w), [body(digest, vendor.NOT_IN_MANIFEST, "refuses", "0", "0")])
        self.assertEqual(len(w.rows("run")), 1)
        self.assertEqual(w.fake_calls(), [digest])


class KeyTest(unittest.TestCase):
    def test_the_key_is_the_role_and_the_first_line(self):
        reason = vendor.NO_REFERENCE
        w = World(self)
        job, digest = refusing(w, "refuse", reason)
        expected = body(digest, reason, "refuses", "0", "0")
        w.record(TICKET, comment(ROLE, expected.split("\n", 1)[0] + "\nposted by an earlier run\n"))
        log = w.log()
        self.assertEqual(w.run(job)[0], 1)
        self.assertEqual(w.log()[len(log):], [PUSH])
        self.assertEqual(len(posted(w)), 1)
        for role, recorded in (("fetcher", reason), (ROLE, vendor.NOT_IN_MANIFEST)):
            with self.subTest(role=role, recorded=recorded):
                w = World(self)
                job, digest = refusing(w, "refuse", reason)
                w.record(TICKET, comment(role, f"Job {digest} failed: {recorded}\n"))
                log = w.log()
                self.assertEqual(w.run(job)[0], 1)
                self.assertEqual(w.log()[len(log):], [post_entry(body(digest, reason, "refuses", "0", "0")), PUSH])


class FailedPostTest(unittest.TestCase):
    def test_a_failed_post_records_nothing(self):
        w = World(self)
        job, digest = refusing(w, "refuse", vendor.NO_REFERENCE)
        self.assertEqual(w.run(job, gh=FailingPost(w.dir)), (2, "", f"run: post #{TICKET}: exit 1\n"))
        self.assertEqual(w.rows("run"), [])
        self.assertTrue((w.job_dir(digest) / "result.json").is_file())
        self.assertEqual(posted(w), [])
        self.assertEqual(w.run(job)[0], 1)
        self.assertEqual(posted(w), [body(digest, vendor.NO_REFERENCE, "refuses", "0", "0")])
        self.assertEqual(len(w.rows("run")), 1)
        self.assertEqual(w.fake_calls(), [digest])


class AttemptTest(unittest.TestCase):
    def test_a_new_attempt_posts_again(self):
        w = World(self)
        job, h1 = refusing(w, "refuse", vendor.NO_REFERENCE)
        self.assertEqual(w.run(job)[0], 1)
        w.attempt(job, 2)
        h2 = digest_of(job)
        self.assertEqual(w.gate(job), (0, f"gate job {h2}: PASS cap - reason within caps\n", ""))
        self.assertEqual(w.run(job), (1, line(h2, "FAILED", "0", "0") + "\n",
                                      f"run: tool refuses failed: {vendor.NO_REFERENCE}\n"))
        self.assertEqual(posted(w), [body(h, vendor.NO_REFERENCE, "refuses", "0", "0") for h in (h1, h2)])


class DryRunTest(unittest.TestCase):
    def test_without_apply_nothing_is_posted(self):
        w = World(self)
        job, digest = refusing(w, "refuse", vendor.NO_REFERENCE)
        log = w.log()
        self.assertEqual(w.run(job, apply=False), (0, f"run job {digest}: would call refuses\n", ""))
        self.assertEqual(posted(w), [])
        self.assertEqual(w.log(), log)


if __name__ == "__main__":
    unittest.main()
