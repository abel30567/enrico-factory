"""Tests for kit/lib/down.py: one open issue per resource that is down, and the accounts stopped under it.

GitHub is an empty recorded directory; the production is a `production.Production` with only the settings
and roles the module reads; the state directory is under a temporary directory and starts absent.
"""

import fcntl
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import down, github, production  # noqa: E402

CAUSE = "login expired or revoked"
SEEN = "launch critic 5-1 on #5"
ROLES = {"critic": {"meta": {"model": "checker", "family": "any"}},
         "judge": {"meta": {"model": "checker", "family": "other"}}}


class World:
    def __init__(self, test, **settings):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.state = self.tmp / "state"
        self.github = self.tmp / "github"
        self.gh = github.Recorded(self.github)
        values = {"STATE_DIR": str(self.state), "MODEL_CHECKER": "checker-model-1",
                  "ACCOUNTS_CHECKER": "acct-one, acct-two", "PROXY_URL": "http://localhost:8317",
                  "MODEL_OTHER": "other-model-1", **settings}
        self.p = production.Production(root=self.tmp / "show", kit=self.tmp / "show" / ".factory", settings=values,
                                       roles=ROLES, formats={}, pipeline=[], kinds={}, actors={})

    def creates(self):
        path = self.github / "log.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [json.loads(line) for line in lines if json.loads(line)["op"] == "create_issue"]

    def stopped_file(self):
        return json.loads((self.state / down.STOPPED).read_text())

    def issue(self, number, title, labels, state="open"):
        (self.github / "issues").mkdir(parents=True, exist_ok=True)
        (self.github / "issues" / f"{number}.json").write_text(json.dumps({
            "number": number, "title": title, "body": "", "state": state, "labels": labels,
            "created_at": "2026-11-10T00:00:00Z"}))


class Report(unittest.TestCase):
    """Behaviour 6."""

    def test_two_reports_one_issue(self):
        w = World(self)
        first = down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one")
        second = down.report(w.p, w.gh, down.PRIMARY, CAUSE, "launch critic 5-2 on #5", "acct-two")
        self.assertEqual(first, second)
        [made] = w.creates()
        self.assertEqual((made["title"], made["labels"]), ("[DOWN] inference.primary", ["resource:down"]))
        self.assertEqual(made["body"], "\n".join([
            "Resource: inference.primary", f"Cause: {CAUSE}", f"Seen by: {SEEN}", "",
            "Launches that need this resource skip a stopped login and wait when none is left.",
            "To clear: renew the login or restart the proxy, then close this issue.",
            "The stopped account is named in stopped.json in the state directory of the machine that ran the "
            "launch."]) + "\n")
        self.assertEqual(w.stopped_file(), {"acct-one": first, "acct-two": first})

    def test_without_an_account_no_stopped_line_and_no_file(self):
        w = World(self)
        number = down.report(w.p, w.gh, down.OTHER, "model proxy unreachable", SEEN)
        [made] = w.creates()
        self.assertEqual(made["number"], number)
        self.assertEqual(made["title"], "[DOWN] inference.other")
        self.assertNotIn("stopped.json", made["body"])
        self.assertTrue(made["body"].endswith("then close this issue.\n"))
        self.assertFalse((w.state / down.STOPPED).exists())

    def test_the_lock_serialises_the_filing(self):
        w = World(self)
        w.state.mkdir(mode=0o700)
        found = []
        with open(w.state / down.LOCK, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            thread = threading.Thread(target=lambda: found.append(
                down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one")))
            thread.start()
            thread.join(0.5)
            self.assertTrue(thread.is_alive())
            self.assertEqual(w.creates(), [])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(w.creates()), 1)
        self.assertEqual(found, [w.creates()[0]["number"]])

    def test_near_matches_are_not_the_issue(self):
        w = World(self)
        w.issue(1, "[DOWN] inference.primary", [])
        w.issue(2, "[DOWN] inference.other", ["resource:down"])
        w.issue(3, "[DOWN] inference.primary", ["resource:down"], state="closed")
        number = down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one")
        self.assertEqual(number, 4)
        self.assertEqual(len(w.creates()), 1)

    def test_an_open_match_is_reused_lowest_first(self):
        w = World(self)
        w.issue(7, "[DOWN] inference.primary", ["resource:down"])
        w.issue(5, "[DOWN] inference.primary", ["resource:down", "other"])
        self.assertEqual(down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one"), 5)
        self.assertEqual(w.creates(), [])

    def test_entries_of_closed_issues_are_dropped(self):
        w = World(self)
        first = down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one")
        w.gh.close_issue(first)
        second = down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-two")
        self.assertNotEqual(first, second)
        self.assertEqual(w.stopped_file(), {"acct-two": second})


class Blocking(unittest.TestCase):
    """Behaviour 6: a read never writes and never creates the state directory."""

    def test_no_state_directory(self):
        w = World(self)
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "critic"))
        self.assertFalse(w.state.exists())

    def test_every_account_stopped(self):
        w = World(self)
        number = down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-one")
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "critic"))
        down.report(w.p, w.gh, down.PRIMARY, CAUSE, SEEN, "acct-two")
        before = sorted(os.listdir(w.state))
        self.assertEqual(down.blocking(w.p, w.gh.issues("open"), "critic"), (down.PRIMARY, number))
        self.assertEqual(sorted(os.listdir(w.state)), before)
        w.gh.close_issue(number)
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "critic"))

    def test_other_waits_on_its_issue(self):
        w = World(self)
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "judge"))
        number = down.report(w.p, w.gh, down.OTHER, "model proxy unreachable", SEEN)
        self.assertEqual(down.blocking(w.p, w.gh.issues("open"), "judge"), (down.OTHER, number))
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "critic"))

    def test_a_setting_that_cannot_be_read_is_not_a_block(self):
        w = World(self, ACCOUNTS_CHECKER="")
        down.report(w.p, w.gh, down.OTHER, "model proxy unreachable", SEEN)
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "critic"))
        self.assertIsNone(down.blocking(w.p, w.gh.issues("open"), "ghost"))


class Source(unittest.TestCase):
    """The label is spelled in one file of the kit."""

    def test_one_spelling(self):
        found = [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "kit").rglob("*"))
                 if path.is_file() and "__pycache__" not in path.parts
                 and "resource:down" in path.read_text(errors="replace")]
        self.assertEqual(found, ["kit/lib/down.py"])


if __name__ == "__main__":
    unittest.main()
