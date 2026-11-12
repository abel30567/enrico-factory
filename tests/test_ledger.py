"""Tests for kit/lib/ledger.py: the ledger file, the append on the base, and the producer's decision.

The production is tests/fixtures/gate/production. `append` runs on a copy of it made into a git
repository on branch `trunk` in a temporary directory; GitHub is an empty recorded directory.
"""

import dataclasses
import json
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import gitstate, github, ledger, production, tickets  # noqa: E402
from kit.lib.ledger import LedgerError  # noqa: E402

PRODUCTION = ROOT / "tests" / "fixtures" / "gate" / "production"
HASH = "ab" * 32
HEADER = "time\tkind\thash\tticket\tepisode\ttool\tusd\tcompute_min\tcaller\tverdict\tcap"


def row(**fields):
    base = {"time": "2026-11-01T12:00:00Z", "kind": "job", "hash": HASH, "ticket": "3", "episode": "ep01",
            "tool": "renderbox", "usd": "2.5", "compute_min": "30", "caller": "renderer", "verdict": "PASS",
            "cap": "-"}
    return {**base, **fields}


def text(*rows):
    return HEADER + "\n" + "".join(ledger.line(r) + "\n" for r in rows)


class ReadLineTest(unittest.TestCase):
    def test_round_trip(self):
        rows = [row(), row(kind="lock", tool="-", usd="0", compute_min="0", caller="lead", ticket="1"),
                row(verdict="STOP", cap="CAP_EPISODE_USD", usd="12.0001", episode="ep02")]
        source = text(*rows)
        self.assertEqual(source.split("\n")[0], HEADER)
        read = ledger.read(source)
        self.assertEqual(read, rows)
        self.assertEqual(text(*read), source)
        self.assertEqual(ledger.read(HEADER + "\n"), [])
        self.assertEqual(ledger.read(HEADER), [])

    def test_line_formats_numbers(self):
        self.assertEqual(ledger.line(row(ticket=3, usd=Decimal("2.50"), compute_min=Decimal("1E+1"))).split("\t")[3:8],
                         ["3", "ep01", "renderbox", "2.50", "10"])

    def test_malformed_lines_name_the_line(self):
        good = ledger.line(row())
        cases = {
            "wrong column count": (good + "\textra", r"^ledger line 3: 12 columns, not 11$"),
            "bad verdict": (ledger.line(row()).replace("\tPASS\t", "\tMAYBE\t"),
                            r"^ledger line 3: verdict is not PASS or STOP$"),
            "exponent": (good.replace("\t2.5\t", "\t2e1\t"), r"^ledger line 3: usd is not digits"),
            "five decimal places": (good.replace("\t30\t", "\t0.00001\t"), r"^ledger line 3: compute_min is not digits"),
            "sign": (good.replace("\t2.5\t", "\t-2.5\t"), r"^ledger line 3: usd is not digits"),
            "empty field": (good.replace("\tep01\t", "\t\t"), r"^ledger line 3: episode is empty$"),
            "bad kind": (good.replace("\tjob\t", "\trender\t"), r"^ledger line 3: kind is not job or lock$"),
            "bad cap": (good.replace("\t-", "\tCAP_ANYTHING"), r"^ledger line 3: cap is not a cap name or -$"),
            "lock with a tool": (good.replace("\tjob\t", "\tlock\t"), r"^ledger line 3: tool of a lock is not -$"),
        }
        for name, (bad, pattern) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(LedgerError, pattern):
                ledger.read(HEADER + "\n" + good + "\n" + bad + "\n")

    def test_wrong_header(self):
        for header in ("", HEADER.replace("usd", "dollars"), HEADER + "\textra", ledger.line(row())):
            with self.subTest(header=header), self.assertRaisesRegex(LedgerError, r"^ledger line 1: "):
                ledger.read(header + "\n")

    def test_line_refuses_a_bad_row(self):
        for bad in (row(tool="render\tbox"), row(caller="a\nb"), row(usd="1e3"), row(verdict="pass"),
                    row(ticket=""), {k: v for k, v in row().items() if k != "cap"}, {**row(), "extra": "x"}):
            with self.subTest(row=bad), self.assertRaisesRegex(LedgerError, r"^ledger row: "):
                ledger.line(bad)

    def test_spent(self):
        rows = [row(usd="2.5", compute_min="30"), row(usd="1.25", compute_min="5", episode="ep02"),
                row(usd="100", compute_min="999", verdict="STOP", cap="CAP_EPISODE_USD"),
                row(kind="lock", tool="-", usd="0", compute_min="0")]
        self.assertEqual(ledger.spent(rows), {"usd": Decimal("3.75"), "compute_min": Decimal("35")})
        self.assertEqual(ledger.spent(rows, "ep01"), {"usd": Decimal("2.5"), "compute_min": Decimal("30")})
        self.assertEqual(ledger.spent(rows, "ep09"), {"usd": Decimal(0), "compute_min": Decimal(0)})
        self.assertIsInstance(ledger.spent(rows)["usd"], Decimal)


CAPS_OFF = {name: "" for name in production.CAPS}
NOW = "2026-11-01T12:00:00Z"


def ticket(number, unit, created, stage="render"):
    return {"number": number, "key": f"T-{number}", "summary": "s", "room": "production", "stage": stage,
            "unit": unit, "hold": False, "state": "open", "created_at": created, "labels": []}


def launch(role, n):
    return {"id": n, "created_at": "2026-11-01T10:00:00Z", "role": "lead", "body": f"[LEAD] LAUNCH {role} R-{n}\n"}


class DecideTest(unittest.TestCase):
    """One STOP per rule of Behaviour 5, each beside its clean twin one unit under the cap."""

    @classmethod
    def setUpClass(cls):
        cls.p = production.load(PRODUCTION)

    def setUp(self):
        self.tickets = {3: ticket(3, "ep01", "2026-11-01T00:00:00Z"),
                        5: ticket(5, "ep02/Q1", "2026-10-31T00:00:00Z", stage="frames")}
        self.launches = 0

    def decide(self, caps, rows=(), kind="job", usd="0", compute_min="0", now=NOW):
        p = dataclasses.replace(self.p, settings={**CAPS_OFF, **caps})
        state = tickets.State(tickets=self.tickets,
                              ticket_comments={3: [launch("renderer", n) for n in range(self.launches)]
                                               + [launch("lead", 99)]})
        return ledger.decide(p, list(rows), state, self.tickets[3], kind, Decimal(usd), Decimal(compute_min), now)

    def test_cap_not_set(self):
        everything = {"CAP_EPISODE_USD": "100", "CAP_SEASON_USD": "100",
                      "CAP_EPISODE_COMPUTE_MIN": "100", "CAP_SEASON_COMPUTE_MIN": "100"}
        for name in everything:
            caps = {**everything, name: ""}
            what = "usd" if name.endswith("USD") else "compute_min"
            with self.subTest(name):
                self.assertEqual(self.decide(caps, **{what: "1"}), ("STOP", name, "cap not set"))
                self.assertEqual(self.decide(caps, **{what: "0"}), ("PASS", "-", "within caps"))
        self.assertEqual(self.decide({}, usd="0.0001"), ("STOP", "CAP_EPISODE_USD", "cap not set"))
        self.assertEqual(self.decide({}, compute_min="1"), ("STOP", "CAP_EPISODE_COMPUTE_MIN", "cap not set"))

    def test_over_episode_and_season_caps(self):
        rows = [row(usd="5", compute_min="50"), row(usd="30", compute_min="300", episode="ep02"),
                row(usd="500", compute_min="5000", verdict="STOP", cap="CAP_SEASON_USD")]
        loose = {"CAP_EPISODE_USD": "1000", "CAP_SEASON_USD": "1000",
                 "CAP_EPISODE_COMPUTE_MIN": "10000", "CAP_SEASON_COMPUTE_MIN": "10000"}
        cases = (("CAP_EPISODE_USD", "10", "usd", "6", "4"),        # 5 spent in ep01
                 ("CAP_SEASON_USD", "40", "usd", "6", "4"),          # 35 spent in the season
                 ("CAP_EPISODE_COMPUTE_MIN", "100", "compute_min", "51", "49"),
                 ("CAP_SEASON_COMPUTE_MIN", "400", "compute_min", "51", "49"))
        for name, cap, what, over, under in cases:
            with self.subTest(name):
                caps = {**loose, name: cap}
                self.assertEqual(self.decide(caps, rows, **{what: over}), ("STOP", name, "over cap"))
                self.assertEqual(self.decide(caps, rows, **{what: under}), ("PASS", "-", "within caps"))

    def test_caps_checked_in_order(self):
        caps = {"CAP_EPISODE_USD": "1", "CAP_SEASON_USD": "1", "CAP_EPISODE_COMPUTE_MIN": "1",
                "CAP_SEASON_COMPUTE_MIN": "1"}
        self.assertEqual(self.decide(caps, usd="2", compute_min="2"), ("STOP", "CAP_EPISODE_USD", "over cap"))
        self.assertEqual(self.decide(caps, usd="1", compute_min="2"),
                         ("STOP", "CAP_EPISODE_COMPUTE_MIN", "over cap"))

    def test_episode_hours(self):
        caps = {"CAP_EPISODE_HOURS": "10"}  # ep01 began 2026-11-01T00:00:00Z
        self.assertEqual(self.decide(caps, now="2026-11-01T11:00:00Z"), ("STOP", "CAP_EPISODE_HOURS", "over cap"))
        self.assertEqual(self.decide(caps, now="2026-11-01T09:00:00Z"), ("PASS", "-", "within caps"))

    def test_season_hours(self):
        caps = {"CAP_SEASON_HOURS": "30"}  # the season began with ep02 at 2026-10-31T00:00:00Z
        self.assertEqual(self.decide(caps, now="2026-11-01T07:00:00Z"), ("STOP", "CAP_SEASON_HOURS", "over cap"))
        self.assertEqual(self.decide(caps, now="2026-11-01T05:00:00Z"), ("PASS", "-", "within caps"))
        self.assertEqual(self.decide({"CAP_EPISODE_HOURS": "30"}, now="2026-11-01T07:00:00Z"),
                         ("PASS", "-", "within caps"))

    def test_iterations(self):
        caps = {"CAP_TICKET_ITERATIONS": "3"}
        self.launches = 4
        self.assertEqual(self.decide(caps), ("STOP", "CAP_TICKET_ITERATIONS", "over cap"))
        self.launches = 2
        self.assertEqual(self.decide(caps), ("PASS", "-", "within caps"))

    def test_lock_past_the_iteration_cap_with_money_left(self):
        self.launches = 9
        caps = {"CAP_TICKET_ITERATIONS": "3", "CAP_EPISODE_USD": "10", "CAP_SEASON_USD": "10"}
        self.assertEqual(self.decide(caps, [row(usd="5")], kind="lock"), ("PASS", "-", "within caps"))
        self.assertEqual(self.decide(caps, [row(usd="11")], kind="lock"), ("STOP", "CAP_EPISODE_USD", "over cap"))

    def test_lock_with_no_cap_set(self):
        self.launches = 9
        self.assertEqual(self.decide({}, [row(usd="5")], kind="lock"), ("PASS", "-", "within caps"))

    def test_reason_holds_no_value(self):
        for verdict in (self.decide({"CAP_EPISODE_USD": "7.25", "CAP_SEASON_USD": "7.25"}, usd="8"),
                        self.decide({"CAP_EPISODE_HOURS": "3.5"})):
            self.assertNotIn("7.25", " ".join(verdict))
            self.assertNotIn("3.5", " ".join(verdict))


class Repo:
    """A copy of the production as a git repository on `trunk`, and an empty recorded GitHub."""

    def __init__(self, test):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        shutil.copytree(PRODUCTION, self.root)
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        self.gh = github.Recorded(tmp / "github")
        self.p = production.load(self.root)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def head(self):
        return gitstate.head(self.root, "trunk")

    def ledger(self):
        return (self.root / ledger.PATH).read_text()

    def pushes(self):
        log = self.gh.dir / "log.jsonl"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.is_file() else []


class AppendTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self)

    def test_creates_commits_and_pushes(self):
        before = self.r.head()
        written = ledger.append(self.r.p, self.r.gh, self.r.root, row())
        self.assertEqual(written, ledger.line(row()))
        self.assertEqual(self.r.ledger(), text(row()))
        self.assertEqual(gitstate.show(self.r.root, "trunk", ledger.PATH), text(row()))
        self.assertEqual(self.r.git("log", "-1", "--format=%an|%s|%P", "trunk").strip(),
                         f"factory|ledger: job {HASH[:12]} PASS|{before}")
        self.assertEqual(self.r.git("show", "--name-only", "--format=", "trunk").split(), [ledger.PATH])
        self.assertEqual(self.r.pushes(), [{"op": "push", "number": None, "ref": "trunk"}])
        second = row(verdict="STOP", cap="CAP_SEASON_USD", kind="lock", tool="-")
        ledger.append(self.r.p, self.r.gh, self.r.root, second)
        self.assertEqual(gitstate.show(self.r.root, "trunk", ledger.PATH), text(row(), second))
        self.assertEqual(len(self.r.pushes()), 2)

    def assert_nothing_committed(self, head):
        self.assertEqual(self.r.head(), head)
        self.assertEqual(self.r.pushes(), [])

    def test_edited_earlier_line(self):
        ledger.append(self.r.p, self.r.gh, self.r.root, row())
        (self.r.gh.dir / "log.jsonl").unlink()
        head = self.r.head()
        path = self.r.root / ledger.PATH
        path.write_text(path.read_text().replace("\t2.5\t", "\t0.5\t"))
        edited = path.read_text()
        with self.assertRaisesRegex(LedgerError, r"^ledger/spend.tsv differs from the base$"):
            ledger.append(self.r.p, self.r.gh, self.r.root, row(hash="cd" * 32))
        self.assert_nothing_committed(head)
        self.assertEqual(path.read_text(), edited)

    def test_deleted_file_with_a_committed_ledger(self):
        ledger.append(self.r.p, self.r.gh, self.r.root, row())
        (self.r.gh.dir / "log.jsonl").unlink()
        head = self.r.head()
        (self.r.root / ledger.PATH).unlink()
        with self.assertRaisesRegex(LedgerError, r"differs from the base"):
            ledger.append(self.r.p, self.r.gh, self.r.root, row())
        self.assert_nothing_committed(head)

    def test_other_branch(self):
        head = self.r.head()
        self.r.git("checkout", "-q", "-b", "elsewhere")
        with self.assertRaisesRegex(LedgerError, r"^the checkout is not on trunk with a clean index$"):
            ledger.append(self.r.p, self.r.gh, self.r.root, row())
        self.assert_nothing_committed(head)
        self.assertEqual(gitstate.head(self.r.root, "elsewhere"), head)
        self.assertFalse((self.r.root / ledger.PATH).exists())

    def test_detached(self):
        head = self.r.head()
        self.r.git("checkout", "-q", "--detach")
        with self.assertRaisesRegex(LedgerError, r"^the checkout is not on trunk with a clean index$"):
            ledger.append(self.r.p, self.r.gh, self.r.root, row())
        self.assert_nothing_committed(head)

    def test_staged_file(self):
        head = self.r.head()
        (self.r.root / "notes.md").write_text("a note\n")
        self.r.git("add", "notes.md")
        with self.assertRaisesRegex(LedgerError, r"^the checkout is not on trunk with a clean index$"):
            ledger.append(self.r.p, self.r.gh, self.r.root, row())
        self.assert_nothing_committed(head)
        self.assertFalse((self.r.root / ledger.PATH).exists())

    def test_bad_row_writes_nothing(self):
        head = self.r.head()
        with self.assertRaises(LedgerError):
            ledger.append(self.r.p, self.r.gh, self.r.root, row(usd="1e3"))
        self.assert_nothing_committed(head)
        self.assertFalse((self.r.root / ledger.PATH).exists())


class CoversTest(unittest.TestCase):
    """`covers` on recorded data; the end-to-end cases are in tests/test_gate.py."""

    HEAD, MERGE, NEXT, BASE = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    STAGE = {"maker": "painter", "checker": "inspector", "judge": "", "lint": ["structure"]}

    def git(self, head):
        commits = [{"sha": self.HEAD, "parents": ["0" * 40]}, {"sha": self.MERGE, "parents": [self.HEAD, self.BASE]},
                   {"sha": self.NEXT, "parents": [self.MERGE]}]
        upto = [c["sha"] for c in commits].index(head) + 1
        return {"head": head, "manifest": "none", "commits": commits[:upto], "base_shas": [self.BASE]}

    def gate(self, verb="GATE PASS", role="program:gate", n=10, when="2026-11-01T12:00:00Z", manifest="none"):
        return {"id": n, "created_at": when, "role": role,
                "body": f"[PRODUCER] {verb}\ncovers: lock {HASH}\ncap: -  reason: within caps\nledger: x\n"
                        f"reviewed head {self.HEAD} manifest {manifest}\n"}

    def maker(self, verb, when, n=20):
        return {"id": n, "created_at": when, "role": "painter", "body": f"[PAINTER] {verb}\n"}

    def test_clean(self):
        ready = self.maker("READY", "2026-11-01T11:00:00Z")
        self.assertTrue(ledger.covers(self.git(self.HEAD), [ready, self.gate()], self.STAGE))
        self.assertTrue(ledger.covers(self.git(self.MERGE), [ready, self.gate()], self.STAGE))

    def test_no_gate(self):
        self.assertFalse(ledger.covers(self.git(self.HEAD), [], self.STAGE))

    def test_other_role(self):
        self.assertFalse(ledger.covers(self.git(self.HEAD), [self.gate(role="painter")], self.STAGE))
        self.assertFalse(ledger.covers(self.git(self.HEAD), [self.gate(role="program:lint")], self.STAGE))

    def test_newest_gate_is_a_stop(self):
        comments = [self.gate(), self.gate("GATE STOP", n=11, when="2026-11-01T13:00:00Z")]
        self.assertFalse(ledger.covers(self.git(self.HEAD), comments, self.STAGE))
        # A gate comment recorded by another role is not the newest gate.
        comments = [self.gate(), self.gate("GATE STOP", role="painter", n=11, when="2026-11-01T13:00:00Z")]
        self.assertTrue(ledger.covers(self.git(self.HEAD), comments, self.STAGE))

    def test_head_moved_by_a_maker_commit(self):
        self.assertFalse(ledger.covers(self.git(self.NEXT), [self.gate()], self.STAGE))

    def test_manifest_changed(self):
        self.assertFalse(ledger.covers(self.git(self.HEAD), [self.gate(manifest="f" * 64)], self.STAGE))

    def test_not_newer_than_the_makers_addressed(self):
        for when, n in (("2026-11-01T12:30:00Z", 5), ("2026-11-01T12:00:00Z", 11)):
            with self.subTest(when=when, n=n):
                comments = [self.gate(), self.maker("ADDRESSED", when, n)]
                self.assertFalse(ledger.covers(self.git(self.HEAD), comments, self.STAGE))
        # The same marker from a role that is not the stage's maker does not count.
        other = {"id": 21, "created_at": "2026-11-01T12:30:00Z", "role": "inspector", "body": "[INSPECTOR] ADDRESSED\n"}
        self.assertTrue(ledger.covers(self.git(self.HEAD), [self.gate(), other], self.STAGE))


if __name__ == "__main__":
    unittest.main()
