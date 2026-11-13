"""Tests for kit/lib/monitors.py and the tick that acts on it (ISSUE-178): the producer's monitors.

Every test runs `factory tick` through ``World.tick`` on the world of tests/test_tick.py. The ledger, the caps,
the comments and the blinding keys are written inside each world; no fixture is added.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import blind, gitstate, ledger, monitors, tickets  # noqa: E402
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


CHARTER = "# Charter\n\nA lighthouse keeper and her daughter guard the harbour through winter storms.\n"
PITCHES = {"ep07": "# Pitch\n\nThe keeper and her daughter guard the harbour lighthouse in a winter storm.\n",
           "ep08": "# Pitch\n\nThe daughter leaves the harbour for a city of glass towers and neon markets.\n",
           "ep09": "# Pitch\n\nA robot detective solves murders on a desert moon full of neon casinos.\n"}
LOCKS = {"ep07": (31, 32, "2026-11-04T09:00:00Z"), "ep08": (33, 34, "2026-11-05T09:00:00Z"),
         "ep09": (35, 36, "2026-11-06T09:00:00Z")}
DRIFT = "ep07, ep08, ep09 move away from the charter (closeness 0.75, 0.22, 0.00)"
DRIFT_LINES = [f"NEED - monitor charter-drift: {DRIFT}", f"NEED #15 charter-drift {DRIFT}"]
CLEAN = ["STEP #15 sync PR #16", "WAIT #15 lockable PR #17 is not the ticket's next step"]
AMENDMENT = {"room": "story", "stage": "charter", "unit": "amendment", "after": "", "maker": "writer", "lint": [],
             "checker": "", "judge": "", "blind": False, "pitches": 1, "paths": ["bible/charter.md"]}


class CharterDrift(Monitors):
    """World (15, 16, 17): ticket #15 at the summary stage pitch, its pitch #16 selected; ep07 to ep09 locked."""

    def record(self, w, kind, number, data):
        path = w.github / kind / f"{number}.json"
        path.write_text(json.dumps({"number": number, **data}, indent=1, sort_keys=True) + "\n")

    def lock(self, w, issue, pull, merged_at, stage, unit):
        """A closed issue and its merged pull, as a lock long ago left them."""
        self.record(w, "issues", issue, {
            "body": f"{stage} for {unit}.\n", "created_at": "2026-11-01T09:00:00Z", "state": "closed",
            "labels": ["room:story", f"stage:{stage}", "tracked", f"unit:{unit}"],
            "title": f"[LK-{issue}] {stage} {unit}"})
        self.record(w, "pulls", pull, {
            "base_ref": "trunk", "body": f"Ticket: #{issue}\n", "created_at": "2026-11-02T09:00:00Z",
            "head_ref": f"{stage}/{unit}", "labels": [], "merged_at": merged_at, "state": "merged", "ticket": issue,
            "title": f"Work for #{issue}"})

    def drift_world(self, selected=True):
        w = self.world((15, 16, 17))
        w.write("bible/charter.md", CHARTER)
        for ep, text in sorted(PITCHES.items()):
            w.write(f"episodes/{ep}/pitch.md", text)
        w.commit("three pitches")
        for ep, (issue, pull, merged_at) in sorted(LOCKS.items()):
            self.lock(w, issue, pull, merged_at, "pitch", ep)
        if selected:
            w.add(15, "2026-11-10T10:00:00Z", "keeper", "[KEEPER] SELECTED #16\n\nThe pier holds.\n")
        return w

    def amendment_world(self):
        w = self.drift_world()
        path = w.root / ".factory" / "pipeline.json"
        path.write_text(json.dumps([AMENDMENT, *json.loads(path.read_text())], indent=1) + "\n")
        w.commit("a charter stage")
        return w

    # Behaviour 1
    def test_three_falling_episodes_hold_the_lock_and_file(self):
        w = self.drift_world()
        self.dry_then_apply(w, DRIFT_LINES)
        self.assertEqual(self.created(w), [(
            "[MONITOR] charter-drift ep09",
            "Monitor: charter drift\nEpisodes: ep07, ep08, ep09\nCloseness: 0.75, 0.22, 0.00\n"
            "Action: force a charter review; no pitch ticket locks until it (system brief section 11). "
            "Routed to keeper.\n"
            "To clear: lock a change to bible/charter.md, or post a RULING on an open pitch ticket, "
            "then close this issue.\n", [LABEL])])
        self.assertEqual(lines(self.ticked(w, "--apply")), DRIFT_LINES)
        self.assertEqual(len(w.ops("create_issue")), 1)
        self.assertEqual({w.record("pulls", n)["state"] for n in (16, 17)}, {"open"})

    def rewrite(self, w, rel, text):
        """The world trips, then ``rel`` on the base holds ``text`` (None: removed)."""
        self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)
        if text is None:
            (w.root / rel).unlink()
        else:
            w.write(rel, text)
        w.commit(f"rewrite {rel}")

    # Behaviour 2
    def test_closeness_that_does_not_fall_strictly(self):
        w = self.drift_world()
        self.rewrite(w, "episodes/ep09/pitch.md", PITCHES["ep07"])
        self.assertEqual(lines(self.ticked(w)), CLEAN)

    # Behaviour 3
    def test_too_few_episodes(self):
        w = self.drift_world()
        self.rewrite(w, "episodes/ep08/pitch.md", None)
        for kind, number in (("issues", 33), ("pulls", 34)):
            (w.github / kind / f"{number}.json").unlink()
        out = self.ticked(w)
        self.assertNotIn("charter-drift", out)
        self.assertEqual(lines(out), CLEAN)

    def test_a_charter_with_no_words(self):
        w = self.drift_world()
        self.rewrite(w, "bible/charter.md", "# Charter\n")
        out = self.ticked(w)
        self.assertNotIn("charter-drift", out)
        self.assertEqual(lines(out), CLEAN)

    # Behaviour 4
    def test_a_ruling_of_the_orchestrator_clears_it(self):
        w = self.drift_world()
        self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)
        w.add(15, "2026-11-10T11:00:00Z", "keeper", "[KEEPER] RULING\nThe charter stands.\n")
        self.assertEqual(lines(self.ticked(w)), CLEAN)

    def test_another_actors_or_an_older_ruling_does_not(self):
        for role, at in (("writer", "2026-11-10T11:00:00Z"), ("keeper", "2026-11-05T09:00:00Z")):
            with self.subTest(role=role, at=at):
                w = self.drift_world()
                w.add(15, at, role, f"[{role.upper()}] RULING\nThe charter stands.\n")
                self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)

    # Behaviour 5
    def test_a_charter_lock_after_the_third_episode_clears_it(self):
        w = self.amendment_world()
        self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)
        self.lock(w, 41, 42, "2026-11-07T09:00:00Z", "charter", "A-2")
        self.assertEqual(lines(self.ticked(w)), CLEAN)

    def test_a_charter_lock_before_the_third_episode_does_not(self):
        w = self.amendment_world()
        self.lock(w, 41, 42, "2026-11-06T08:00:00Z", "charter", "A-2")
        self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)

    # Behaviour 6
    def test_only_the_lock_is_held(self):
        w = self.drift_world(selected=False)
        self.assertEqual(lines(self.ticked(w)), [*DRIFT_LINES, "STEP #15 launch keeper on #15"])

    # Behaviour 7
    def test_a_base_that_cannot_be_read(self):
        w = self.drift_world()
        show = gitstate.show

        def failing(root, ref, path):
            if path == "bible/charter.md":
                raise gitstate.GitError("cat-file", 128, "fatal: bad object\n")
            return show(root, ref, path)

        with mock.patch.object(gitstate, "show", failing):
            out = self.ticked(w, code=1)
        self.assertEqual(lines(out), ["FAIL - monitor charter-drift: git cat-file: exit 128", *CLEAN])

    # Behaviour 8
    def test_order_is_by_lock_time(self):
        w = self.drift_world()
        self.assertEqual(lines(self.ticked(w)), DRIFT_LINES)
        w.set_record("pulls", 32, merged_at=LOCKS["ep09"][2])
        w.set_record("pulls", 36, merged_at=LOCKS["ep07"][2])
        out = self.ticked(w)
        self.assertNotIn("charter-drift", out)
        self.assertEqual(lines(out), CLEAN)


PROBE = """from kit.lib import monitors


def trips(p, state, rows, now, read):
    return [{"line": "monitor probe: ep01 is watched", "title": "[MONITOR] probe ep01",
             "body": "Monitor: probe\\n", "labels": LABELS,
             "tickets": [{"title": "[RETRO-EP01] retro for ep01", "body": "Unit: ep01\\n", "labels": ["room:story"]}]}]


def instead(p, trip, ticket, entry):
    if ticket["stage"] != "pitch" or entry["step"] != "lock":
        return entry
    return {"state": "probe", "step": STEP, "role": None, "pull": None, "reason": "ep01 is watched"}
"""
BASE = ["STEP #15 lock PR #16", "WAIT #15 lockable PR #17 is not the ticket's next step"]
TRIP = "NEED - monitor probe: ep01 is watched"
FILE = "STEP - monitor probe file [RETRO-EP01] retro for ep01"
HOLD = "NEED #15 probe ep01 is watched"
RETRO = "[RETRO-EP01] retro for ep01"


def probe(labels=(), step="wake"):
    return f"LABELS = {list(labels)!r}\nSTEP = {step!r}\n" + PROBE


class MonitorFiles(Monitors):
    """ISSUE-376: monitor files ``monitor_<name>.py`` of ``monitors.MONITORS_DIR``, on the world (15, 16, 17)."""

    def module(self, text, name="monitor_probe.py"):
        """``text`` as the file ``name`` of a new ``MONITORS_DIR``."""
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / name).write_text(text)
        patch = mock.patch.object(monitors, "MONITORS_DIR", folder)
        patch.start()
        self.addCleanup(patch.stop)
        return folder

    def probe_world(self, selected=True):
        w = self.world((15, 16, 17))
        if selected:
            w.add(15, "2026-11-10T10:00:00Z", "keeper", "[KEEPER] SELECTED #16\n\nThe pier holds.\n")
        return w

    def issue(self, w, number, title, labels, state="closed"):
        path = w.github / "issues" / f"{number}.json"
        path.write_text(json.dumps({"body": "An issue.\n", "created_at": "2026-11-05T09:00:00Z", "labels": labels,
                                    "number": number, "state": state, "title": title}, indent=1, sort_keys=True)
                        + "\n")

    def titles(self, w):
        return [(e["title"], e["labels"]) for e in w.ops("create_issue")]

    def test_the_world_without_a_monitor_file(self):
        self.module("", name="README.md")
        self.assertEqual(lines(self.ticked(self.probe_world())), BASE)

    # Behaviour 1
    def test_a_trip_its_label_its_ticket_and_the_freeze(self):
        self.module(probe(["factory:freeze"]))
        w = self.probe_world()
        self.dry_then_apply(w, [TRIP, FILE, HOLD])
        self.assertEqual(self.titles(w), [("[MONITOR] probe ep01", [LABEL, "factory:freeze"]),
                                          (RETRO, ["room:story"])])
        self.assertEqual(self.created(w)[1], (RETRO, "Unit: ep01\n", ["room:story"]))
        self.assertEqual(w.tick("--apply"), (0, "FROZEN\n", ""))
        self.assertEqual(len(w.ops("create_issue")), 2)

    # Behaviour 2
    def test_each_ticket_is_filed_once(self):
        self.module(probe())
        w = self.probe_world()
        self.assertEqual(lines(self.ticked(w, "--apply")), [TRIP, FILE, HOLD])
        self.assertEqual(self.titles(w), [("[MONITOR] probe ep01", [LABEL]), (RETRO, ["room:story"])])
        self.assertEqual(lines(self.ticked(w, "--apply")), [TRIP, HOLD])
        self.assertEqual(len(w.ops("create_issue")), 2)

    # Behaviour 3
    def test_a_title_that_is_taken(self):
        for labels in (["room:story"], ["tracked", "room:story", "stage:final", "unit:ep01"]):
            with self.subTest(labels=labels):
                self.module(probe())
                w = self.probe_world()
                self.issue(w, 41, RETRO, labels)
                self.assertEqual(lines(self.ticked(w, "--apply")), [TRIP, HOLD])
                self.assertEqual(self.titles(w), [("[MONITOR] probe ep01", [LABEL])])

    # Behaviour 4
    def test_a_tracked_issue_that_does_not_parse_files_nothing(self):
        self.module(probe())
        w = self.probe_world()
        self.issue(w, 41, "[X-1] broken", ["tracked", "room:story", "stage:final"], state="open")
        self.assertEqual(lines(self.ticked(w, "--apply")), ["NEED - ticket #41: no unit: label", TRIP, HOLD])
        self.assertEqual(self.titles(w), [("[MONITOR] probe ep01", [LABEL])])

    # Behaviour 5
    def test_the_hold_prints_once(self):
        self.module(probe())
        w = self.probe_world(selected=False)
        self.assertEqual(lines(self.ticked(w)), [TRIP, FILE, HOLD, "STEP #15 launch keeper on #15"])

    # Behaviour 6
    def test_the_name_is_the_file_name(self):
        self.module(probe().replace("probe", "two-words"), name="monitor_two_words.py")
        self.assertEqual(lines(self.ticked(self.probe_world())), [
            "NEED - monitor two-words: ep01 is watched", "STEP - monitor two-words file [RETRO-EP01] retro for ep01",
            "NEED #15 two-words ep01 is watched"])

    def test_other_file_names_are_ignored(self):
        for name in ("monitor_Probe.py", "probe.py"):
            with self.subTest(name=name):
                self.module(probe(), name=name)
                self.assertEqual(lines(self.ticked(self.probe_world())), BASE)

    # Behaviour 7
    def failed(self, text, expected, name="monitor_probe.py"):
        self.module(text, name=name)
        self.assertEqual(lines(self.ticked(self.probe_world(), code=1)), expected)

    def test_a_module_that_does_not_load_or_has_no_trips(self):
        for text in ("def trips(:\n", "def trips(p, state, rows, now, read):\n    return []\n"):
            with self.subTest(text=text):
                self.failed(text, ["FAIL - monitor probe: does not follow the monitor interface", *BASE])

    def test_trips_that_are_not_trips(self):
        text = probe().replace("    return [{", "    return [{\"line\": \"x\"}]\n    [{", 1)
        self.failed(text, ["FAIL - monitor probe: does not follow the monitor interface", *BASE])

    def test_trips_that_raise(self):
        text = probe().replace("    return [{", "    raise ValueError(\"x\")\n    [{", 1)
        self.failed(text, ["FAIL - monitor probe: trips raised ValueError", *BASE])

    def test_trips_that_raise_a_monitor_error_pass_it_on(self):
        text = "import kit.lib.gitstate as g\n" + probe().replace(
            "    return [{", "    raise g.GitError(\"cat-file\", 128, \"fatal\\n\")\n    [{", 1)
        self.failed(text, ["FAIL - monitor probe: git cat-file: exit 128", *BASE])

    def test_the_name_of_a_built_in_monitor(self):
        self.failed(probe(), ["FAIL - monitor charter-drift: monitor_charter_drift.py has the name of a built-in "
                              "monitor", *BASE], name="monitor_charter_drift.py")

    def test_an_instead_that_does_not_follow_the_interface(self):
        self.failed(probe(step="lock"),
                    [TRIP, FILE, "FAIL #15 read: monitor probe: instead does not follow the monitor interface"])

    def test_an_instead_that_raises(self):
        text = probe().replace("    if ticket[", "    raise KeyError(\"x\")\n    if ticket[", 1)
        self.failed(text, [TRIP, FILE, "FAIL #15 read: monitor probe: instead raised KeyError"])

    # Behaviour 8
    def test_no_read_no_monitor_file(self):
        self.module(probe())
        w = self.probe_world()
        p = w.p()
        state = tickets.load(p, w.gh, p.root)
        self.assertEqual(monitors.trips(p, state, [], "2026-11-10T12:00:00Z"), [])
        self.assertEqual(len(monitors.trips(p, state, [], "2026-11-10T12:00:00Z", lambda path: None)), 1)

    def test_an_empty_directory_has_no_monitor_file(self):
        folder = self.module("", name="README.md")
        (folder / "README.md").unlink()
        self.assertEqual(monitors.discover(), [])
        (folder / "monitor_b.py").write_text("")
        (folder / "monitor_a_b.py").write_text("")
        self.assertEqual(monitors.discover(), [("a-b", folder / "monitor_a_b.py"), ("b", folder / "monitor_b.py")])


if __name__ == "__main__":
    unittest.main()
