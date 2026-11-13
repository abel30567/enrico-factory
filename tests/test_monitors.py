"""Tests for kit/lib/monitors.py and the tick that acts on it (ISSUE-178): the producer's monitors.

Every test runs `factory tick` through ``World.tick`` on the world of tests/test_tick.py. The ledger, the caps,
the comments and the blinding keys are written inside each world; no fixture is added.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import blind, ledger  # noqa: E402
from test_tick import LABEL, Case, World, lines  # noqa: E402,F401

LINE = "NEED - monitor budget: {cap} is passed for {scope}"
WAIT = "WAIT #18 budget {cap} is passed for {scope}"
PAINTER = "STEP #18 launch painter on #18"
CANON = "- B1 canon episodes/ep02/ledger.json:3 mira / height: episode says '5 ft 4', canon says '5 ft 2' " \
        "(bible/canon/ledger.tsv:2)"
CANON_LINE = "NEED - monitor canon: #5 [LINT] FAIL on #6 finds a fact against the canon ledger"
CANON_STEP = "NEED #5 canon [LINT] FAIL on #6 finds a fact against the canon ledger"
WRITER = "STEP #5 launch writer on PR #6"
JUDGES = "NEED #3 lock refused: rule 3 judges"
REBREAK = "NEED - monitor no-convergence: {k} blind rounds and no win on #3; re-break to the outline"
REBREAK_STEP = "NEED #3 no-convergence {k} blind rounds and no win on #3; re-break to the outline"
EXPECTED_3 = [LINE.format(cap="CAP_SEASON_USD", scope="the season"),
              LINE.format(cap="CAP_EPISODE_USD", scope="episode ep03"),
              WAIT.format(cap="CAP_SEASON_USD", scope="the season")]
SHIP = "NEED - monitor no-convergence: {k} blind rounds and no win on #3; the locked version ships"


def minute(m):
    return f"2026-11-10T10:{m:02d}:00Z"


def run_row(episode, usd, compute_min, ticket=18):
    return {"time": "2026-11-10T09:00:00Z", "kind": "run", "hash": "a" * 64, "ticket": str(ticket),
            "episode": episode, "tool": "fake", "usd": str(usd), "compute_min": str(compute_min),
            "caller": "painter", "verdict": "DONE", "cap": "-"}


class Monitors(Case):
    def spend(self, w, *rows, text=None):
        """The ledger on the base, committed as ``factory gate`` would leave it."""
        if text is None:
            text = ledger.HEADER + "\n" + "".join(ledger.line(row) + "\n" for row in rows)
        w.write(ledger.PATH, text)
        w.commit("ledger")

    def caps(self, w, **caps):
        with open(w.root / "production.local.env", "a") as handle:
            handle.write("".join(f"{k}={v}\n" for k, v in caps.items()))

    def budget_world(self, keep=(18,), **caps):
        w = self.world(keep)
        self.spend(w, run_row("ep03", 30, 45), run_row("ep02", 30, 45), run_row("ep01", 30, 45),
                   run_row("ep06", 30, 45))
        self.caps(w, **caps)
        return w

    def canon_world(self, finding=CANON):
        w = self.world((5, 6))
        w.add(6, minute(1), "program:lint",
              "[LINT] FAIL\n\n| Check | Result | Reason |\n|---|---|---|\n| media | PASS |  |\n\n"
              f"Blockers:\n{finding}\nChecks required by stage draft: media\n"
              "reviewed head {head:6} manifest none\n")
        return w

    def blind_world(self, verbs, keyless=(), keep=(1, 2, 3, 4)):
        """One head per verb, the last the head of pull #4, each judged ``verb`` in that order; A is the old
        side of each key. A head in ``keyless`` (an index) has no key."""
        w = self.world(keep)
        p = w.p()
        heads = [f"{i + 1:040x}" for i in range(len(verbs) - 1)] + [w.head(w.ref(4))]
        for i, (head, verb) in enumerate(zip(heads, verbs)):
            if i not in keyless:
                path = blind.key_path(p, 4, head)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"A": "old", "B": "new", "head": head, "pull": 4}) + "\n")
            w.add(4, minute(10 + i), "weigher", f"[WEIGHER] {verb}\nA round.\nreviewed head {head} manifest none\n")
        return w

    def created(self, w):
        return [(e["title"], e["body"], e["labels"]) for e in w.ops("create_issue")]


class Budget(Monitors):
    # Behaviour 1
    def test_an_episode_over_its_money_cap_stops_the_launch(self):
        w = self.budget_world(CAP_EPISODE_USD=20)
        scope = "episode ep03"
        self.assertEqual(lines(self.ticked(w)), [LINE.format(cap="CAP_EPISODE_USD", scope=scope),
                                                 WAIT.format(cap="CAP_EPISODE_USD", scope=scope)])

    def test_at_its_cap_or_with_no_cap_the_launch_goes_on(self):
        for caps in ({"CAP_EPISODE_USD": 30}, {}):
            with self.subTest(caps=caps):
                w = self.budget_world(**caps)
                self.assertEqual(lines(self.ticked(w)), [PAINTER])

    # Behaviour 2
    def test_compute_and_season_hours(self):
        for cap, scope in (("CAP_EPISODE_COMPUTE_MIN", "episode ep03"), ("CAP_SEASON_HOURS", "the season")):
            with self.subTest(cap=cap):
                w = self.budget_world(**{cap: 40 if cap == "CAP_EPISODE_COMPUTE_MIN" else 1})
                self.assertEqual(lines(self.ticked(w)), [LINE.format(cap=cap, scope=scope),
                                                         WAIT.format(cap=cap, scope=scope)])

    def test_episode_hours_under_the_cap(self):
        w = self.budget_world(CAP_EPISODE_HOURS=300)
        self.assertEqual(lines(self.ticked(w)), [PAINTER])

    # Behaviour 3
    def test_two_trips_file_two_issues_once(self):
        w = self.budget_world(CAP_EPISODE_USD=20, CAP_SEASON_USD=10)
        self.assertEqual(lines(self.ticked(w, "--apply")), EXPECTED_3)
        self.assertEqual(self.created(w), [
            ("[MONITOR] budget CAP_SEASON_USD season",
             "Monitor: budget\nCap: CAP_SEASON_USD\nScope: the season\n"
             "Action: stop the run, ship the best so far (system brief section 11).\n"
             "To clear: raise the cap in production.env, then close this issue.\n", [LABEL]),
            ("[MONITOR] budget CAP_EPISODE_USD episode ep03",
             "Monitor: budget\nCap: CAP_EPISODE_USD\nScope: episode ep03\n"
             "Action: stop the run, ship the best so far (system brief section 11).\n"
             "To clear: raise the cap in production.env, then close this issue.\n", [LABEL])])
        first = w.ops("create_issue")[0]["number"]
        self.assertEqual(lines(self.ticked(w, "--apply")), EXPECTED_3)
        self.assertEqual(len(w.ops("create_issue")), 2)
        w.gh.close_issue(first)
        self.assertEqual(lines(self.ticked(w, "--apply")), EXPECTED_3)
        self.assertEqual([e["title"] for e in w.ops("create_issue")][2:], ["[MONITOR] budget CAP_SEASON_USD season"])
        self.assertEqual(w.record("issues", first)["state"], "closed")
        self.assertNotIn(LABEL, w.record("issues", 18)["labels"])

    # Behaviour 4
    def test_lint_gate_and_the_orchestrator_are_left_alone(self):
        for keep, ep, step in (((5, 6), "ep02", "STEP #5 lint PR #6"), ((7, 8), "ep01", "STEP #7 gate PR #8"),
                               ((22, 23), "ep06", "STEP #22 launch foreman on PR #23")):
            with self.subTest(keep=keep):
                w = self.budget_world(keep, CAP_EPISODE_USD=20)
                self.assertEqual(lines(self.ticked(w)),
                                 [LINE.format(cap="CAP_EPISODE_USD", scope=f"episode {ep}"), step])

    # Behaviour 5
    def test_a_ledger_that_does_not_parse(self):
        w = self.world((18,))
        self.spend(w, text=ledger.HEADER + "\nx\ty\tz\n")
        self.caps(w, CAP_EPISODE_USD=20)
        self.assertEqual(lines(self.ticked(w, code=1)),
                         ["FAIL - monitor budget: ledger line 2: 3 columns, not 11", PAINTER])

    def test_a_cap_that_is_not_a_number(self):
        w = self.budget_world(CAP_EPISODE_USD="lots")
        self.assertEqual(lines(self.ticked(w, code=1)),
                         ["FAIL - monitor budget: setting CAP_EPISODE_USD is not a number >= 0", PAINTER])


class Canon(Monitors):
    # Behaviour 6
    def test_a_canon_contradiction_blocks_and_files(self):
        w = self.canon_world()
        self.assertEqual(lines(self.ticked(w)), [CANON_LINE, CANON_STEP])
        self.assertEqual(lines(self.ticked(w, "--apply")), [CANON_LINE, CANON_STEP])
        self.assertEqual(self.created(w), [(
            "[MONITOR] canon PR #6",
            "Monitor: canon contradiction\nTicket: #5\nPull request: #6\n"
            "Action: block the lock, route to head of story (keeper) (system brief section 11).\n"
            "To clear: post a RULING on the ticket or the pull, then close this issue.\n", [LABEL])])
        self.assertIn(LABEL, w.record("issues", 5)["labels"])

    def test_other_findings_launch_the_writer(self):
        media = "- B1 media episodes/ep02/draft.md:3 a file is missing"
        self_canon = "- B1 canon bible/canon/ledger.tsv:3 mira / height: two values"
        for finding in (media, self_canon):
            with self.subTest(finding=finding):
                self.assertEqual(lines(self.ticked(self.canon_world(finding))), [WRITER])

    def test_a_ruling_of_the_orchestrator_ends_it(self):
        for number in (6, 5):
            with self.subTest(on=number):
                w = self.canon_world()
                w.add(number, minute(2), "keeper", "[KEEPER] RULING\nThe episode keeps the canon.\n")
                self.assertEqual(lines(self.ticked(w)), [WRITER])

    def test_another_actors_ruling_does_not(self):
        w = self.canon_world()
        w.add(6, minute(2), "editor", "[EDITOR] RULING\nNot mine to rule.\n")
        self.assertEqual(lines(self.ticked(w)), [CANON_LINE, CANON_STEP])


class NoConvergence(Monitors):
    """World (1, 2, 3, 4): ticket #3 at the blind stage draft, with a locked version, judge weigher."""

    # Behaviour 7
    def test_what_is_not_a_trip(self):
        for verbs, keyless in ((["PREFERS A"] * 2, ()), (["PREFERS A", "PREFERS B", "PREFERS A"], ()),
                               (["PREFERS A"] * 3, (0,))):
            with self.subTest(verbs=verbs, keyless=keyless):
                self.assertEqual(lines(self.ticked(self.blind_world(verbs, keyless))), [JUDGES])

    def test_no_locked_version_no_trip(self):
        w = self.blind_world(["PREFERS A"] * 3, keep=(3, 4))
        self.assertNotIn("monitor", self.ticked(w))

    # Behaviours 8 and 9
    def test_three_lost_rounds_ask_for_a_re_break(self):
        for verbs in (["PREFERS A"] * 3, ["NO PREFERENCE", "PREFERS A", "PREFERS A"]):
            with self.subTest(verbs=verbs):
                w = self.blind_world(verbs)
                expected = [REBREAK.format(k=3), REBREAK_STEP.format(k=3)]
                self.assertEqual(lines(self.ticked(w)), expected)
                self.assertEqual(lines(self.ticked(w, "--apply")), expected)
                self.assertEqual(self.created(w), [(
                    "[MONITOR] no-convergence re-break #3",
                    "Monitor: no convergence\nTicket: #3\n"
                    "Action: one re-break to the outline, then ship the best so far (system brief section 11).\n"
                    "To clear: re-break the outline, post a RULING on the ticket, then close this issue. "
                    "After 6 rounds with no win the tick closes the ticket.\n", [LABEL])])
                self.assertIn(LABEL, w.record("issues", 3)["labels"])

    def test_a_ruling_after_round_three_ends_it(self):
        w = self.blind_world(["PREFERS A"] * 3)
        w.add(3, minute(30), "keeper", "[KEEPER] RULING\nKeep going.\n")
        self.assertEqual(lines(self.ticked(w)), [JUDGES])

    # Behaviour 10
    def test_six_lost_rounds_ship_the_locked_version(self):
        w = self.blind_world(["PREFERS A"] * 6)
        w.add(3, minute(30), "keeper", "[KEEPER] RULING\nKeep going.\n")
        self.assertEqual(lines(self.ticked(w, "--apply")), [SHIP.format(k=6), "STEP #3 close #3"])
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(w.record("pulls", 4)["state"], "open")
        self.assertEqual(self.created(w), [(
            "[MONITOR] no-convergence ship #3",
            "Monitor: no convergence\nTicket: #3\n"
            "Action: ship the best so far (system brief section 11). The tick closed the ticket; "
            "the locked version stays.\nTo undo: reopen the ticket, then close this issue.\n", [LABEL])])
        self.assertEqual(self.ticked(w, "--apply"), "tick: 0 steps, 0 need, 0 wait, 0 failed\n")


class DryRun(Monitors):
    """Behaviour 11: without --apply the lines of the --apply tick, nothing written, byte-identical twice."""

    def check(self, w, expected):
        before = w.snapshot()
        out = self.ticked(w)
        self.assertEqual(lines(out), expected)
        self.assertEqual(self.ticked(w), out)
        self.assertEqual(w.snapshot(), before)

    def test_budget(self):
        w = self.budget_world(CAP_EPISODE_USD=20, CAP_SEASON_USD=10)
        self.check(w, EXPECTED_3)

    def test_canon(self):
        self.check(self.canon_world(), [CANON_LINE, CANON_STEP])

    def test_re_break(self):
        self.check(self.blind_world(["PREFERS A"] * 3), [REBREAK.format(k=3), REBREAK_STEP.format(k=3)])

    def test_ship(self):
        self.check(self.blind_world(["PREFERS A"] * 6), [SHIP.format(k=6), "STEP #3 close #3"])


class Order(Monitors):
    def test_the_grant_refresh_comes_before_the_monitors(self):
        w = self.budget_world(CAP_EPISODE_USD=20)
        (w.tmp / "secrets" / "platform.tiktok").write_text("not a grant\n")
        scope = "episode ep03"
        self.assertEqual(lines(self.ticked(w)), ["STEP - refresh platform.tiktok",
                                                 LINE.format(cap="CAP_EPISODE_USD", scope=scope),
                                                 WAIT.format(cap="CAP_EPISODE_USD", scope=scope)])


if __name__ == "__main__":
    unittest.main()
