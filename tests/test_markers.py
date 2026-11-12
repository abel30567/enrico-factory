import copy
import json
import re
import unittest
from pathlib import Path

from kit.lib import markers

FIXTURES = Path(__file__).parent / "fixtures" / "markers"
SHA = {c: c * 40 for c in "1234567"}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def rows(name):
    return (FIXTURES / name).read_text().splitlines()


class ParseTest(unittest.TestCase):
    def test_every_row_of_the_table(self):
        for case in load("first_lines.json")["valid"]:
            with self.subTest(line=case["line"]):
                self.assertEqual(
                    markers.parse(case["line"]),
                    markers.Marker(case["actor"], case["verb"], tuple(case["args"])))

    def test_not_a_marker(self):
        for case in load("first_lines.json")["invalid"]:
            with self.subTest(why=case["why"]):
                self.assertIsNone(markers.parse(case["line"]))


class MayPostTest(unittest.TestCase):
    def test_kinds(self):
        allowed = {
            "maker": {"READY", "ADDRESSED", "BLOCKED"},
            "operator": {"READY", "ADDRESSED", "BLOCKED"},
            "checker": {"APPROVED", "NOTES", "BLOCKED"},
            "judge": {"PREFERS A", "PREFERS B", "NO PREFERENCE", "BLOCKED"},
            "analyst": {"SYMPTOMS", "BLOCKED"},
            "orchestrator": {"LAUNCH", "RULING", "SELECTED", "BLOCKED"},
        }
        verbs = set(markers.ROLE_VERBS) | {"SYMPTOMS", "VOTE", "PASS", "FAIL", "ERROR",
                                           "GATE PASS", "GATE STOP", "POSTED", "KEEP"}
        for kind, ok in allowed.items():
            for verb in sorted(verbs):
                with self.subTest(kind=kind, verb=verb):
                    self.assertEqual(markers.may_post(kind, verb), verb in ok)

    def test_program_markers_belong_to_no_kind(self):
        for kind in ("maker", "operator", "checker", "judge", "analyst", "orchestrator", "program"):
            for verb in ("LOCK", "PASS", "FAIL", "ERROR", "GATE PASS", "GATE STOP"):
                self.assertFalse(markers.may_post(kind, verb), (kind, verb))


class MayPostActorTest(unittest.TestCase):
    """``may_post(kind, verb, actor)``: the kind's verbs plus the actor's own row, for its one kind."""

    def test_no_actor_is_the_kind_alone(self):
        for verb in ("VOTE", "POSTED", "VERIFIED"):
            for kind in markers.MAY_POST:
                self.assertFalse(markers.may_post(kind, verb), (kind, verb))

    def test_actor_keeps_the_kinds_verbs(self):
        for kind, verbs in markers.MAY_POST.items():
            for verb in verbs:
                for actor in ("WRITER", "ANALYST", "BRAINTRUST", "CHANNEL:TUBE"):
                    self.assertTrue(markers.may_post(kind, verb, actor), (kind, verb, actor))

    def test_analyst_symptoms(self):
        self.assertTrue(markers.may_post("analyst", "SYMPTOMS", "ANALYST"))
        for kind in ("maker", "operator", "checker", "judge", "orchestrator"):
            self.assertFalse(markers.may_post(kind, "SYMPTOMS", "ANALYST"), kind)

    def test_braintrust_vote(self):
        self.assertTrue(markers.may_post("judge", "VOTE", "BRAINTRUST"))
        for kind in ("maker", "operator", "checker", "analyst", "orchestrator"):
            self.assertFalse(markers.may_post(kind, "VOTE", "BRAINTRUST"), kind)
        for actor in ("PANEL", "ANALYST", "CHANNEL:TUBE"):
            self.assertFalse(markers.may_post("judge", "VOTE", actor), actor)

    def test_channel_posted_verified(self):
        for verb in ("POSTED", "VERIFIED"):
            for actor in ("CHANNEL:TUBE", "CHANNEL:CLIPS"):
                self.assertTrue(markers.may_post("operator", verb, actor), (verb, actor))
            for kind in ("maker", "checker", "judge", "analyst", "orchestrator"):
                self.assertFalse(markers.may_post(kind, verb, "CHANNEL:TUBE"), (kind, verb))
            for actor in ("UPLOADER", "BRAINTRUST", "ANALYST"):
                self.assertFalse(markers.may_post("operator", verb, actor), (verb, actor))

    def test_an_actors_row_is_only_its_own(self):
        self.assertFalse(markers.may_post("operator", "VOTE", "CHANNEL:TUBE"))
        self.assertFalse(markers.may_post("analyst", "VOTE", "ANALYST"))
        self.assertFalse(markers.may_post("judge", "POSTED", "BRAINTRUST"))

    def test_program_verbs_belong_to_no_actor(self):
        for kind in markers.MAY_POST:
            for actor in ("ANALYST", "BRAINTRUST", "CHANNEL:TUBE", "AUTORESEARCH", "LINT"):
                for verb in ("KEEP", "NOISE", "DISCARD", "REBASELINE", "PASS", "GATE PASS", "LOCK"):
                    self.assertFalse(markers.may_post(kind, verb, actor), (kind, actor, verb))


class ManifestHashTest(unittest.TestCase):
    def setUp(self):
        self.base = rows("manifest_base.tsv")
        self.head = rows("manifest_head.tsv")

    def test_none_when_nothing_added(self):
        self.assertEqual(markers.manifest_hash(self.base, self.base), "none")
        self.assertEqual(markers.manifest_hash(self.base, self.base[:2]), "none")

    def test_hash_of_added_rows_sorted(self):
        import hashlib
        added = "\n".join(sorted(self.head[3:]))
        self.assertEqual(markers.manifest_hash(self.base, self.head),
                         hashlib.sha256(added.encode()).hexdigest())

    def test_independent_of_row_order(self):
        want = markers.manifest_hash(self.base, self.head)
        self.assertEqual(markers.manifest_hash(list(reversed(self.base)), list(reversed(self.head))), want)

    def test_many_rows_hash_in_sorted_order(self):
        import hashlib
        added = ["media/shot-%03d.png\t%d" % (i, i) for i in range(200, 0, -1)]
        want = hashlib.sha256("\n".join(sorted(added)).encode("utf-8")).hexdigest()
        self.assertEqual(markers.manifest_hash([], added), want)
        self.assertEqual(markers.manifest_hash([], list(reversed(added))), want)

    def test_fixed_rows_literal_digest(self):
        added = ["media/shot-03.png\t1003\tsha256:33",
                 "media/shot-01.png\t1001\tsha256:11",
                 "media/shot-02.png\t1002\tsha256:22"]
        self.assertEqual(markers.manifest_hash([], added),
                         "eb1c85e3684ab54f6a026cae3f6a710e082498afce2f6561afc12f97beb772a7")

    def test_one_byte_changes_the_hash(self):
        want = markers.manifest_hash(self.base, self.head)
        changed = list(self.head)
        changed[-1] = changed[-1].replace("8192", "8193")
        self.assertNotEqual(markers.manifest_hash(self.base, changed), want)

    def test_a_changed_base_row_counts(self):
        changed = list(self.base)
        changed[1] = changed[1].replace("1024", "1025")
        self.assertNotEqual(markers.manifest_hash(self.base, changed), "none")


class ReviewedTest(unittest.TestCase):
    def test_last_non_empty_line(self):
        body = "[LINT] PASS\nreviewed head %s manifest none\n\n  \n" % SHA["1"]
        self.assertEqual(markers.reviewed(body), (SHA["1"], "none"))

    def test_manifest_hash(self):
        h = "e" * 64
        self.assertEqual(markers.reviewed("x\nreviewed head %s manifest %s" % (SHA["2"], h)), (SHA["2"], h))

    def test_absent_or_not_last(self):
        for body in ("[LINT] PASS", "",
                     "[LINT] PASS\nreviewed head %s manifest none\nmore" % SHA["1"],
                     "[LINT] PASS\nreviewed head %s" % SHA["1"],
                     "[LINT] PASS\nreviewed head XYZ manifest none"):
            with self.subTest(body=body):
                self.assertIsNone(markers.reviewed(body))

    def test_only_a_full_lower_case_sha(self):
        for sha in (SHA["1"][:7], SHA["1"][:39], SHA["1"] + "1", "A" * 40):
            with self.subTest(sha=sha):
                self.assertIsNone(markers.reviewed("[LINT] PASS\nreviewed head %s manifest none" % sha))


class OnlyBaseMergesTest(unittest.TestCase):
    def setUp(self):
        self.pr = load("pr_current.json")["pr"]

    def check(self, frm, to):
        return markers.only_base_merges(self.pr["commits"], frm, to, self.pr["base_shas"])

    def test_same_commit(self):
        self.assertTrue(self.check(SHA["3"], SHA["3"]))

    def test_only_base_merge_after(self):
        self.assertTrue(self.check(SHA["2"], SHA["3"]))

    def test_non_merge_after(self):
        self.assertFalse(self.check(SHA["1"], SHA["3"]))
        self.assertFalse(self.check(SHA["1"], SHA["2"]))

    def test_merge_of_another_branch(self):
        self.pr["commits"][2]["parents"][1] = "f" * 40
        self.assertFalse(self.check(SHA["2"], SHA["3"]))

    def test_unknown_or_backwards(self):
        self.assertFalse(self.check("9" * 40, SHA["3"]))
        self.assertFalse(self.check(SHA["3"], SHA["2"]))


class CountsTest(unittest.TestCase):
    def setUp(self):
        fx = load("pr_current.json")
        self.pr, self.stage, self.roles = fx["pr"], fx["stage"], fx["roles"]
        self.approved = self.comment(103)

    def comment(self, cid):
        return copy.deepcopy(next(c for c in self.pr["comments"] if c["id"] == cid))

    def counts(self, verdict):
        return markers.counts(verdict, self.pr, self.stage, self.roles)

    def names_head(self, verdict, sha):
        verdict["body"] = re.sub(r"head [0-9a-f]+ ", "head %s " % sha, verdict["body"])
        return verdict

    def test_current_head_counts(self):
        for cid in (102, 103, 104):
            self.assertEqual(self.counts(self.comment(cid)), (True, "counts"), cid)

    def test_older_head_followed_only_by_base_merges_counts(self):
        self.assertEqual(self.counts(self.names_head(self.approved, SHA["2"])), (True, "counts"))

    def test_older_head(self):
        ok, reason = self.counts(self.names_head(self.approved, SHA["1"]))
        self.assertFalse(ok)
        self.assertEqual(reason, "head: commit %s after %s is not a merge of the base" % (SHA["2"], SHA["1"]))

    def test_head_one_non_merge_commit_behind(self):
        self.pr["commits"].append({"sha": SHA["4"], "parents": [SHA["3"]]})
        self.pr["head"] = SHA["4"]
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertEqual(reason, "head: commit %s after %s is not a merge of the base" % (SHA["4"], SHA["3"]))

    def test_head_not_in_pull_request(self):
        ok, reason = self.counts(self.names_head(self.approved, "9" * 40))
        self.assertFalse(ok)
        self.assertEqual(reason, "head: %s is not in the pull request" % ("9" * 40))

    def test_different_manifest(self):
        self.approved["body"] = re.sub(r"manifest [0-9a-f]{64}", "manifest none", self.approved["body"])
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("manifest: none is not the current manifest"), reason)

    def test_older_than_last_addressed(self):
        self.pr["comments"].append({"id": 105, "created_at": "2026-11-11T10:11:00Z",
                                    "body": "[STORY-WRITER] ADDRESSED\nI1: done"})
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertEqual(reason, "order: not newer than the maker's last ADDRESSED (comment 105)")

    def test_older_than_last_ready(self):
        self.approved["created_at"] = "2026-11-11T09:59:00Z"
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertEqual(reason, "order: not newer than the maker's last READY (comment 101)")

    def test_role_the_stage_does_not_name(self):
        self.approved["body"] = self.approved["body"].replace("STORY-EDITOR", "CONTINUITY-SUPERVISOR")
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertEqual(reason, "role: continuity-supervisor is not a checker or judge the stage names")

    def test_verb_the_role_may_not_post(self):
        self.approved["body"] = self.approved["body"].replace("STORY-EDITOR] APPROVED", "PITCH-JUDGE] APPROVED")
        ok, reason = self.counts(self.approved)
        self.assertFalse(ok)
        self.assertEqual(reason, "role: a judge may not post APPROVED")

    def test_lint_on_a_stage_without_lints(self):
        self.stage["lint"] = []
        self.assertEqual(self.counts(self.comment(102)), (False, "role: the stage names no lint"))

    def test_no_reviewed_head_line(self):
        self.approved["body"] = "[STORY-EDITOR] APPROVED\ncriterion 1: beats in order"
        self.assertEqual(self.counts(self.approved), (False, "reviewed: no reviewed head line"))

    def test_abbreviated_sha_is_no_reviewed_head_line(self):
        self.names_head(self.approved, SHA["3"][:7])
        self.assertEqual(self.counts(self.approved), (False, "reviewed: no reviewed head line"))

    def test_not_a_verdict(self):
        self.assertEqual(self.counts(self.comment(101)),
                         (False, "marker: not a lint, checker or judge verdict"))

    def test_first_failing_condition_is_named(self):
        self.names_head(self.approved, SHA["1"])
        self.approved["created_at"] = "2026-11-11T09:00:00Z"
        self.assertTrue(self.counts(self.approved)[1].startswith("head: "))


class SummaryTest(unittest.TestCase):
    def summary(self, fx):
        return markers.summary(fx["pr"], fx["stage"], fx["roles"])

    def test_three_rounds(self):
        s = self.summary(load("pr_three_rounds.json"))
        self.assertEqual(s["rounds"], 3)
        self.assertTrue(s["needs_ruling"])
        self.assertEqual(s["blockers"], [
            {"by": "story-editor", "note": "B3", "comment": 210},
            {"by": "story-editor", "note": "B4", "comment": 210},
        ])
        self.assertEqual(s["maker"]["id"], 208)
        self.assertEqual(s["maker"]["verb"], "ADDRESSED")
        self.assertEqual((s["lint"]["id"], s["lint"]["verb"]), (209, "PASS"))
        self.assertEqual(list(s["checkers"]), ["story-editor"])
        self.assertEqual(s["checkers"]["story-editor"]["id"], 210)
        self.assertEqual(s["judges"]["pitch-judge"]["verb"], "PREFERS B")

    def test_blockers_close_on_addressed(self):
        fx = load("pr_three_rounds.json")
        fx["pr"]["comments"].append({"id": 213, "created_at": "2026-11-11T12:00:00Z",
                                     "body": "[STORY-WRITER] ADDRESSED\nB3: rewrote the conclusion."})
        s = self.summary(fx)
        self.assertEqual(s["blockers"], [])
        self.assertIsNone(s["checkers"]["story-editor"])
        self.assertIsNone(s["lint"])
        self.assertEqual(s["rounds"], 3)

    def checker_verdict(self, fx, cid, actor, verb, notes=""):
        fx["pr"]["comments"].append({
            "id": cid, "created_at": "2026-11-11T12:%02d:00Z" % (cid - 200),
            "body": "[%s] %s\n%sreviewed head %s manifest none" % (actor, verb, notes, SHA["6"])})

    def test_only_the_latest_notes_lists_open_blockers(self):
        fx = load("pr_three_rounds.json")
        self.checker_verdict(fx, 213, "STORY-EDITOR", "NOTES", "B4: runtime is still 50 s.\n")
        self.assertEqual(self.summary(fx)["blockers"],
                         [{"by": "story-editor", "note": "B4", "comment": 213}])

    def test_the_same_checkers_approved_closes_its_blockers(self):
        fx = load("pr_three_rounds.json")
        self.checker_verdict(fx, 213, "STORY-EDITOR", "APPROVED")
        s = self.summary(fx)
        self.assertEqual(s["checkers"]["story-editor"]["verb"], "APPROVED")
        self.assertEqual(s["blockers"], [])

    def test_another_checkers_approved_does_not_close_them(self):
        fx = load("pr_three_rounds.json")
        fx["stage"]["checker"] = ["story-editor", "continuity-supervisor"]
        self.checker_verdict(fx, 213, "CONTINUITY-SUPERVISOR", "APPROVED")
        s = self.summary(fx)
        self.assertEqual(s["checkers"]["continuity-supervisor"]["verb"], "APPROVED")
        self.assertEqual(s["blockers"], [
            {"by": "story-editor", "note": "B3", "comment": 210},
            {"by": "story-editor", "note": "B4", "comment": 210},
        ])

    def test_two_rounds_need_no_ruling(self):
        fx = load("pr_three_rounds.json")
        fx["pr"]["comments"] = [c for c in fx["pr"]["comments"] if c["id"] < 208]
        fx["pr"]["head"] = SHA["5"]
        fx["pr"]["commits"] = fx["pr"]["commits"][:2]
        s = self.summary(fx)
        self.assertEqual(s["rounds"], 2)
        self.assertFalse(s["needs_ruling"])
        self.assertEqual([b["note"] for b in s["blockers"]], ["B2"])

    def test_current_pull_request(self):
        s = self.summary(load("pr_current.json"))
        self.assertEqual(s["rounds"], 0)
        self.assertEqual(s["checkers"]["story-editor"]["verb"], "APPROVED")
        self.assertEqual(s["judges"]["pitch-judge"]["verb"], "PREFERS A")
        self.assertEqual(s["blockers"], [])

    def lint_after(self, verb):
        fx = load("pr_current.json")
        last = next(c for c in fx["pr"]["comments"] if c["id"] == 102)
        fx["pr"]["comments"].append({
            "id": 106, "created_at": "2026-11-11T10:20:00Z",
            "body": "[LINT] ERROR\n| structure | ERROR | format file unreadable |\n"
                    + last["body"].splitlines()[-1]})
        last["body"] = last["body"].replace("[LINT] PASS", "[LINT] " + verb)
        return self.summary(fx)["lint"]

    def test_lint_error_is_never_pass_or_fail(self):
        for earlier in ("PASS", "FAIL"):
            with self.subTest(earlier=earlier):
                lint = self.lint_after(earlier)
                self.assertEqual((lint["id"], lint["verb"]), (106, "ERROR"))

    def test_deterministic(self):
        fx = load("pr_three_rounds.json")
        a = json.dumps(self.summary(fx))
        fx["pr"]["comments"].reverse()
        self.assertEqual(json.dumps(self.summary(fx)), a)


class BraintrustVoteTest(unittest.TestCase):
    """A ``[BRAINTRUST] VOTE`` on a ``bible`` pull request is the braintrust's judge verdict."""

    def setUp(self):
        fx = load("pr_bible_vote.json")
        self.pr, self.stage, self.roles = fx["pr"], fx["stage"], fx["roles"]
        self.vote = next(c for c in self.pr["comments"] if c["id"] == 304)

    def counts(self):
        return markers.counts(self.vote, self.pr, self.stage, self.roles)

    def judge(self):
        return markers.summary(self.pr, self.stage, self.roles)["judges"].get("braintrust")

    def add(self, cid, minute, first_line):
        self.pr["comments"].append({
            "id": cid, "created_at": "2026-11-11T10:%02d:00Z" % minute,
            "body": first_line + "\n" + self.vote["body"].splitlines()[-1]})

    def test_clean_vote_counts_and_fills_the_judge(self):
        self.assertEqual(self.counts(), (True, "counts"))
        self.assertEqual(markers.summary(self.pr, self.stage, self.roles)["judges"], {
            "braintrust": {"id": 304, "created_at": "2026-11-11T10:15:00Z",
                           "actor": "BRAINTRUST", "verb": "VOTE"}})

    def assert_does_not_count(self, prefix):
        ok, reason = self.counts()
        self.assertFalse(ok)
        self.assertTrue(reason.startswith(prefix), reason)
        self.assertIsNone(self.judge())

    def test_no_reviewed_head_line(self):
        self.vote["body"] = "[BRAINTRUST] VOTE 5-2\n| 1 | for | the arc holds |"
        self.assert_does_not_count("reviewed: ")

    def test_reviewed_head_not_current(self):
        self.vote["body"] = self.vote["body"].replace("head %s " % SHA["3"], "head %s " % SHA["1"])
        self.assert_does_not_count("head: ")

    def test_manifest_not_current(self):
        self.vote["body"] = re.sub(r"manifest [0-9a-f]{64}", "manifest none", self.vote["body"])
        self.assert_does_not_count("manifest: ")

    def test_older_than_last_addressed(self):
        self.pr["comments"].append({"id": 305, "created_at": "2026-11-11T10:20:00Z",
                                    "body": "[CANON-KEEPER] ADDRESSED\nB1: cited the source."})
        self.assert_does_not_count("order: ")

    def test_braintrust_as_checker(self):
        self.roles["braintrust"] = "checker"
        self.assert_does_not_count("role: ")

    def test_braintrust_named_checker_and_of_kind_checker(self):
        self.roles["braintrust"] = "checker"
        self.stage["checker"] = ["canon-reviewer", "braintrust"]
        self.assertEqual(self.counts(), (False, "role: a checker may not post VOTE"))

    def test_stage_does_not_name_braintrust_as_judge(self):
        self.stage["judge"] = "pitch-judge"
        self.roles["pitch-judge"] = "judge"
        self.assert_does_not_count("role: ")
        self.assertIsNone(markers.summary(self.pr, self.stage, self.roles)["judges"]["pitch-judge"])

    def test_malformed_tally_fills_nothing(self):
        for tally in ("5", "five-two"):
            with self.subTest(tally=tally):
                self.vote["body"] = re.sub(r"VOTE \S+", "VOTE " + tally, self.vote["body"], count=1)
                self.assertIsNone(markers.parse(self.vote["body"]))
                self.assertIsNone(self.judge())

    def test_later_no_preference_replaces_the_vote(self):
        self.assertEqual(self.judge()["verb"], "VOTE")
        self.add(305, 20, "[BRAINTRUST] NO PREFERENCE")
        self.assertEqual(self.judge()["verb"], "NO PREFERENCE")

    def test_later_vote_replaces_no_preference(self):
        self.vote["created_at"] = "2026-11-11T10:20:00Z"
        self.add(305, 16, "[BRAINTRUST] NO PREFERENCE")
        self.assertEqual((self.judge()["id"], self.judge()["verb"]), (304, "VOTE"))

    def test_later_vote_replaces_an_earlier_one(self):
        self.add(305, 20, "[BRAINTRUST] VOTE 6-1")
        self.assertEqual((self.judge()["id"], self.judge()["verb"]), (305, "VOTE"))

    def test_tally_is_not_judged(self):
        self.vote["body"] = self.vote["body"].replace("VOTE 5-2", "VOTE 4-3")
        self.assertEqual(self.counts(), (True, "counts"))
        self.assertEqual(self.judge(), {"id": 304, "created_at": "2026-11-11T10:15:00Z",
                                        "actor": "BRAINTRUST", "verb": "VOTE"})

    def test_vote_from_another_actor_is_no_marker(self):
        self.vote["body"] = self.vote["body"].replace("[BRAINTRUST]", "[PITCH-JUDGE]")
        self.assertIsNone(markers.parse(self.vote["body"]))
        self.assertEqual(self.counts(), (False, "marker: not a lint, checker or judge verdict"))


class NoNetworkTest(unittest.TestCase):
    def test_module_imports_nothing_that_reaches_out(self):
        source = Path(markers.__file__).read_text()
        self.assertIsNone(re.search(r"subprocess|urllib|socket|http", source))


if __name__ == "__main__":
    unittest.main()
