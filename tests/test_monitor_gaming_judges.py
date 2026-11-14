"""Tests for kit/lib/monitor_gaming_judges.py (ISSUE-369): the gaming-the-judges monitor of the producer gate.

Every test runs `factory tick` on the world (15, 16, 17) of tests/test_tick.py: ticket #15 at the stage pitch,
its pitch #16 selected. Each world adds the judges `braintrust`, `audience-sim` and `held-out-judge`, the maker
`retro-agent`, a blind stage `cut` judged by the three, a `retro` stage and `registry/verdicts.tsv`, all written
inside the world; no fixture is added. The monitor is found in kit/lib, as a production finds it.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_tick import LABEL, Case, lines  # noqa: E402

HEADER = "locked_at\tepisode\tstage\tpull\trole\tside\tretell"
CUT = {"room": "story", "stage": "cut", "unit": "episode", "after": "", "maker": "writer", "lint": [],
       "checker": "editor", "judge": ["braintrust", "audience-sim", "held-out-judge"], "blind": True,
       "pitches": 1, "paths": ["episodes/*/cut.md"]}
RETRO = {"room": "story", "stage": "retro", "unit": "episode", "after": "", "maker": "retro-agent", "lint": [],
         "checker": "editor", "judge": "", "blind": False, "pitches": 1, "paths": ["playbook/**"]}
SEEDED = (("ep07", "04", "32"), ("ep08", "05", "34"), ("ep09", "06", "36"))
BASE = ["STEP #15 sync PR #16", "WAIT #15 lockable PR #17 is not the ticket's next step"]
LINE = ("NEED - monitor gaming-judges: ep07, ep08, ep09 won without held-out-judge or the retell; "
        "judges to rotate: audience-sim, braintrust")
FILE = "STEP - monitor gaming-judges file [RETRO-EP09] retro for ep09"
TITLE = "[MONITOR] gaming-judges ep09"
FREEZE = [LABEL, "factory:freeze"]
BODY = ("Monitor: gaming the judges\nEpisodes: ep07, ep08, ep09\nJudges to rotate: audience-sim, braintrust\n"
        "Action: freeze the run, file a retro ticket (system brief section 11).\n"
        "To clear: run factory unfreeze. This window does not trip again.\n")
NO_RETRO = BODY.replace("To clear:", "No retro ticket: the pipeline has no retro stage.\nTo clear:")
RETRO_TITLE = "[RETRO-EP09] retro for ep09"
RETRO_LABELS = ["tracked", "room:story", "stage:retro", "unit:ep09"]
RETRO_BODY = ("Do the stage retro for unit ep09.\n\nMonitor: gaming the judges (system brief section 11)\n"
              "Episodes: ep07, ep08, ep09\nJudges to rotate: audience-sim, braintrust\n"
              "Propose a change to the rubric and prompt of each judge to rotate; the lab replays it "
              "(system brief section 13).\n\nFiled by factory tick.\n")
PREFIX = "FAIL - monitor gaming-judges: registry/verdicts.tsv "


def round_(episode, day, pull, stage="cut", held="old", retell="none", sides=("new", "new")):
    """The rows of one lock: ``braintrust``, ``audience-sim`` and ``held-out-judge``."""
    at = f"2026-11-{day}T09:00:00Z"
    return [f"{at}\t{episode}\t{stage}\t{pull}\tbraintrust\t{sides[0]}\t-",
            f"{at}\t{episode}\t{stage}\t{pull}\taudience-sim\t{sides[1]}\t{retell}",
            f"{at}\t{episode}\t{stage}\t{pull}\theld-out-judge\t{held}\t-"]


def table(rounds):
    return "\n".join([HEADER] + [row for rows in rounds for row in rows]) + "\n"


def seeded(**changed):
    """The seeded rounds, the keyword ``<episode>`` giving that round's changed arguments."""
    return [round_(ep, day, pull, **changed.get(ep, {})) for ep, day, pull in SEEDED]


class GamingJudges(Case):
    def gaming_world(self, text=None, retro=True, registry=True):
        """The world with the roles, ``CUT`` (and ``RETRO``) and ``text`` (default: the seeded registry)."""
        w = self.world((15, 16, 17))
        roles = w.root / ".factory" / "roles"
        weigher, writer = (roles / "weigher.md").read_text(), (roles / "writer.md").read_text()
        for role in ("braintrust", "audience-sim", "held-out-judge"):
            (roles / f"{role}.md").write_text(weigher.replace("weigher", role))
        (roles / "retro-agent.md").write_text(writer.replace("writer", "retro-agent"))
        path = w.root / ".factory" / "pipeline.json"
        entries = json.loads(path.read_text()) + [CUT] + ([RETRO] if retro else [])
        path.write_text("[\n" + ",\n".join(" " + json.dumps(e) for e in entries) + "\n]\n")
        if registry:
            w.write("registry/verdicts.tsv", table(seeded()) if text is None else text)
        w.commit("the judges and the registry")
        w.add(15, "2026-11-10T10:00:00Z", "keeper", "[KEEPER] SELECTED #16\n\nThe pier holds.\n")
        return w

    def created(self, w):
        return [(e["title"], e["body"], e["labels"]) for e in w.ops("create_issue")]

    def control(self):
        """The seeded world trips, so a clean twin below is clean for the measure and not for want of a monitor."""
        self.assertEqual(lines(self.ticked(self.gaming_world())), [LINE, FILE] + BASE)

    def clean(self, w):
        out = self.ticked(w)
        self.assertNotIn("gaming-judges", out)
        self.assertEqual(lines(out), BASE)
        self.assertEqual(lines(self.ticked(w, "--apply")), BASE)
        self.assertEqual(w.ops("create_issue"), [])

    # Behaviour 1
    def test_three_episodes_trip_freeze_and_file_a_retro_ticket(self):
        w = self.gaming_world()
        self.dry_then_apply(w, [LINE, FILE] + BASE)
        self.assertEqual(self.created(w), [(TITLE, BODY, FREEZE), (RETRO_TITLE, RETRO_BODY, RETRO_LABELS)])
        found, out, err = w.tick("--apply")
        self.assertEqual((found, err, out.splitlines()), (0, "", ["FROZEN"]))
        self.assertEqual(len(w.ops("create_issue")), 2)

    # Behaviour 2
    def test_clean_twins(self):
        closed = {"number": 41, "title": TITLE, "body": BODY, "labels": FREEZE, "state": "closed",
                  "created_at": "2026-11-07T09:00:00Z"}
        self.control()
        twins = (("held-out-judge new in ep09", table(seeded(ep09={"held": "new"})), None),
                 ("retell new in ep08", table(seeded(ep08={"retell": "new"})), None),
                 ("retell - in ep08", table(seeded(ep08={"retell": "-"})), None),
                 ("a closed issue", None, closed),
                 ("only ep08 and ep09", table(seeded()[1:]), None))
        for name, text, issue in twins:
            with self.subTest(name):
                w = self.gaming_world(text)
                if issue is not None:
                    (w.github / "issues" / "41.json").write_text(json.dumps(issue, indent=1, sort_keys=True) + "\n")
                self.clean(w)

    # Behaviour 3
    def test_which_round_counts(self):
        tripping = (("a fourth episode before", [round_("ep06", "03", "30", held="new")] + seeded()),
                    ("an earlier lock of ep09", [round_("ep09", "02", "30", held="new")] + seeded()),
                    ("rows out of order", [seeded()[2], seeded()[0], seeded()[1]]))
        for name, rounds in tripping:
            with self.subTest(name):
                w = self.gaming_world(table(rounds))
                self.assertEqual(lines(self.ticked(w)), [LINE, FILE] + BASE)
        clean = (("a later lock of ep09", seeded() + [round_("ep09", "07", "38", held="new")]),
                 ("ep09 at draft", seeded(ep09={"stage": "draft"})))
        for name, rounds in clean:
            with self.subTest(name):
                self.clean(self.gaming_world(table(rounds)))

    # Behaviour 4
    def test_no_retro_stage_still_freezes(self):
        w = self.gaming_world(retro=False)
        self.dry_then_apply(w, [LINE] + BASE)
        self.assertEqual(self.created(w), [(TITLE, NO_RETRO, FREEZE)])

    # Behaviour 5
    def test_no_file_and_no_rows(self):
        self.control()
        for name, kwargs in (("no file", {"registry": False}), ("header only", {"text": HEADER + "\n"})):
            with self.subTest(name):
                w = self.gaming_world(**kwargs)
                self.assertEqual(lines(self.ticked(w)), BASE)

    # Behaviour 6
    def test_a_file_that_does_not_parse(self):
        header = "line 1: the header is not locked_at episode stage pull role side retell"
        bad = (("a csv header", "locked_at,episode\n", header),
               ("an empty file", "", header),
               ("two columns", HEADER + "\na\tb\n", "line 2: 2 columns, not 7"),
               ("side A", table(seeded()).replace("\tbraintrust\tnew\t", "\tbraintrust\tA\t", 1),
                "line 2: side A is not new, old or none"),
               ("retell even", table(seeded(ep07={"retell": "even"})), "line 3: retell even is not new, old, none or -"))
        for name, text, message in bad:
            with self.subTest(name):
                w = self.gaming_world(text)
                self.assertEqual(lines(self.ticked(w, code=1)), [PREFIX + message] + BASE)

    # Behaviour 7
    def test_after_unfreeze_the_window_does_not_trip_again(self):
        w = self.gaming_world()
        self.assertEqual(lines(self.ticked(w, "--apply")), [LINE, FILE] + BASE)
        self.assertEqual(w.record("issues", 18)["title"], TITLE)
        w.set_record("issues", 18, state="closed")
        self.assertEqual(lines(self.ticked(w)), ["STEP #15 lock PR #16",
                                                 "WAIT #15 lockable PR #17 is not the ticket's next step",
                                                 "STEP #19 launch retro-agent on #19"])


if __name__ == "__main__":
    unittest.main()
