"""markers: rules 2 to 5."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX as LINT_FIX, ROOT, LintCase, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_markers"
COMMENTS = ".factory-state/comments.json"
HEAD = "reviewed head " + "a1" * 20 + " manifest none"
ROLES = ("chief", "editor", "panel", "uploader", "watcher", "writer")


def comments(*entries):
    """A comments.json with one comment per entry (id, role, body)."""
    return json.dumps([{"id": i, "role": r, "body": b} for i, r, b in entries], indent=2) + "\n"


class Markers(LintCase):
    CHECKS = ("kit", "markers")

    def markers(self, root):
        return lint.run(root, only=["markers"])["markers"]

    def with_comments(self, text, extra=None):
        return self.variant({COMMENTS: text, **(extra or {})}, base=FIX / "clean")

    def test_clean_passes_both_checks(self):
        results = lint.run(FIX / "clean", only=self.CHECKS)
        self.assertEqual({k: (r.status, r.findings) for k, r in results.items()},
                         {"kit": (lint.PASS, []), "markers": (lint.PASS, [])})

    def test_rule2_no_comments_file_passes_with_zero_findings(self):
        for root in (ROOT / "tests" / "fixtures" / "lint_kit" / "clean", LINT_FIX / "clean"):
            with self.subTest(root.name):
                result = self.markers(root)
                self.assertEqual((result.status, result.findings, result.reason), (lint.PASS, [], ""))

    def test_rule2_every_shipped_check_on_the_framework_fixture_is_not_error(self):
        results = lint.run(LINT_FIX / "clean")
        self.assertEqual((results["markers"].status, results["markers"].findings), (lint.PASS, []))
        self.assertNotEqual(results["kit"].status, lint.ERROR, results["kit"].reason)

    def test_rule2_comments_file_present_but_unreadable_is_error(self):
        result = self.markers(self.with_comments("[{"))
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn(COMMENTS, result.reason)
        result = self.markers(self.with_comments('[{"id": 1, "body": "x"}]'))
        self.assertEqual(result.status, lint.ERROR)

    def test_rule2_comments_recorded_with_no_kit_in_the_tree_is_error(self):
        tree = tempfile.TemporaryDirectory()
        self.addCleanup(tree.cleanup)
        root = Path(tree.name)
        (root / COMMENTS).parent.mkdir()
        (root / COMMENTS).write_text((FIX / "clean" / COMMENTS).read_text(), encoding="utf-8")
        result = self.markers(root)
        self.assertEqual((result.status, result.findings), (lint.ERROR, []))
        self.assertEqual(result.reason, f"{COMMENTS} is recorded but the tree has no kit to judge it by "
                                        f"(neither .factory/ nor kit/pipeline.json)")

    def test_rule3_actor_does_not_match_role(self):
        root = self.with_comments((FIX / "r3_actor.json").read_text())
        self.assertOnly(root, "markers", [(COMMENTS, 2, "marker-actor")])
        self.assertEqual(self.markers(root).findings[0].message,
                         "comment 201: '[EDITOR] NOTES': actor EDITOR does not match role writer (WRITER)")

    def test_rule3_verb_the_kind_may_not_post(self):
        root = self.with_comments((FIX / "r3_verb.json").read_text())
        self.assertOnly(root, "markers", [(COMMENTS, 2, "marker-verb")])
        self.assertEqual(self.markers(root).findings[0].message,
                         "comment 202: '[EDITOR] READY': a checker may not post READY")

    def test_rule3_program_actor(self):
        root = self.with_comments(comments(
            (1, "program:lint", "[LINT] PASS\n\n" + HEAD),
            (2, "program:lint", "[PLATFORM-GATE] PASS\n\nfile hash"),
            (3, "program:gate", "[PRODUCER] GATE STOP\n\ncap"),
            (4, "program:lock", "[CHIEF] LOCK\n\nlocked"),
            (5, "program:lint", "a progress note that is not a marker"),
            (6, "program:gate", "[LINT] PASS\n\n" + HEAD),
            (7, "program:lock", "[WRITER] LOCK\n\nlocked"),
            (8, "program:lock", "[CHIEF] RULING\n\nruled"),
            (9, "program:render", "[LINT] PASS\n\n" + HEAD),
        ))
        result = self.markers(root)
        self.assertEqual([(f.rule, f.message) for f in result.findings], [
            ("marker-actor", "comment 6: '[LINT] PASS': actor LINT is not one program:gate posts as (PRODUCER)"),
            ("marker-actor", "comment 7: '[WRITER] LOCK': actor WRITER is not one program:lock posts as (CHIEF)"),
            ("marker-verb", "comment 8: '[CHIEF] RULING': program:lock may not post RULING"),
            ("marker-actor", "comment 9: '[LINT] PASS': program 'render' posts no markers"),
        ])

    def test_rule3_role_not_in_the_kit(self):
        root = self.with_comments(comments((1, "ghost", "[GHOST] READY\n\ndone")))
        self.assertOnly(root, "markers", [(COMMENTS, 2, "marker-actor")])
        self.assertIn("role 'ghost' is not a role in the kit", self.markers(root).findings[0].message)

    def test_rule4_verdict_without_reviewed_head_line(self):
        root = self.with_comments((FIX / "r4_reviewed.json").read_text())
        self.assertOnly(root, "markers", [(COMMENTS, 2, "marker-reviewed")])
        self.assertEqual(self.markers(root).findings[0].message,
                         "comment 203: '[EDITOR] APPROVED': verdict does not end with "
                         "'reviewed head <sha> manifest <hash>'")

    def test_rule4_every_verdict_kind(self):
        root = self.with_comments(comments(
            (1, "editor", "[EDITOR] APPROVED\n\nok"),
            (2, "editor", "[EDITOR] NOTES\n\nB1 x"),
            (3, "panel", "[PANEL] PREFERS A\n\n" + HEAD + "\nlater line"),
            (4, "panel", "[PANEL] NO PREFERENCE"),
            (5, "program:lint", "[LINT] FAIL\n\nB1 x"),
            (6, "writer", "[WRITER] READY\n\nnot a verdict, no reviewed line needed"),
            (7, "panel", "[PANEL] PREFERS B\n\n" + HEAD),
        ))
        self.assertEqual([(f.rule, f.message.split(":")[0]) for f in self.markers(root).findings],
                         [("marker-reviewed", f"comment {n}") for n in (1, 2, 3, 4, 5)])

    def test_rule4_braintrust_vote_without_reviewed_head_line(self):
        root = self.with_comments(comments((1, "braintrust", "[BRAINTRUST] VOTE 5-2\n\ntally")),
                                  self.role("panel", "braintrust"))
        self.assertEqual(self.findings(root), [
            ("marker-reviewed", "comment 1: '[BRAINTRUST] VOTE 5-2': verdict does not end with "
                                "'reviewed head <sha> manifest <hash>'")])

    def test_rule4_braintrust_vote_with_reviewed_head_line(self):
        self.assertClean(self.with_comments(
            comments((1, "braintrust", "[BRAINTRUST] VOTE 5-2\n\ntally\n" + HEAD)),
            self.role("panel", "braintrust")))

    def test_rule5_role_comment_that_is_not_a_marker(self):
        root = self.with_comments((FIX / "r5_not_marker.json").read_text())
        self.assertOnly(root, "markers", [(COMMENTS, 2, "marker-missing")])
        self.assertEqual(self.markers(root).findings[0].message,
                         "comment 204: first line 'Done, see the draft.' is not a marker; "
                         "a role's comment starts with one")

    def test_ac3_checker_ready_maker_approved(self):
        root = self.with_comments(comments(
            (1, "editor", "[EDITOR] READY\n\ndone"),
            (2, "writer", "[WRITER] APPROVED\n\n" + HEAD),
        ))
        self.assertEqual([(f.line, f.rule, f.message) for f in self.markers(root).findings], [
            (2, "marker-verb", "comment 1: '[EDITOR] READY': a checker may not post READY"),
            (7, "marker-verb", "comment 2: '[WRITER] APPROVED': a maker may not post APPROVED"),
        ])

    def test_ac3_any_role_posting_lint_pass(self):
        lint_role = (FIX / "clean" / ".factory" / "roles" / "editor.md").read_text().replace(
            "role: editor", "role: lint")
        root = self.with_comments(
            comments(*((n, r, "[LINT] PASS\n\n" + HEAD) for n, r in enumerate(ROLES + ("lint",), 1))),
            {".factory/roles/lint.md": lint_role})
        self.assertEqual(lint.run(root, only=["kit"])["kit"].status, lint.PASS)
        found = self.markers(root).findings
        self.assertEqual([f.message.split(":")[0] for f in found], [f"comment {n}" for n in range(1, 8)])
        self.assertEqual({f.rule for f in found[:6]}, {"marker-actor"})
        self.assertEqual(found[6].message, "comment 7: '[LINT] PASS': a checker may not post PASS")

    # The four markers whose actor is not the posting role's id in upper case (brief section 8).

    def role(self, base, role_id, actor=None):
        """The text of a role file made from the clean fixture's `base` role, as `role_id`."""
        text = (FIX / "clean" / ".factory" / "roles" / f"{base}.md").read_text()
        text = text.replace(f"role: {base}\n", f"role: {role_id}\n" + (f"actor: {actor}\n" if actor else ""))
        return {f".factory/roles/{role_id}.md": text}

    def assertClean(self, root):
        results = lint.run(root, only=self.CHECKS)
        self.assertEqual({k: (r.status, r.findings) for k, r in results.items()},
                         {"kit": (lint.PASS, []), "markers": (lint.PASS, [])})

    def findings(self, root):
        return [(f.rule, f.message) for f in self.markers(root).findings]

    def test_actor_analyst_symptoms_from_an_analyst(self):
        self.assertClean(self.with_comments(
            comments((1, "scout", "[ANALYST] SYMPTOMS\n\nB2 drags")), self.role("watcher", "scout", "ANALYST")))

    def test_actor_analyst_symptoms_from_another_kind(self):
        root = self.with_comments(comments((1, "critic", "[ANALYST] SYMPTOMS\n\nB2 drags")),
                                  self.role("editor", "critic", "ANALYST"))
        self.assertEqual(self.findings(root), [
            ("marker-verb", "comment 1: '[ANALYST] SYMPTOMS': a checker may not post SYMPTOMS")])

    def test_actor_analyst_symptoms_with_another_actor(self):
        root = self.with_comments(comments((1, "watcher", "[ANALYST] SYMPTOMS\n\nB2 drags")))
        self.assertEqual(self.findings(root), [
            ("marker-actor", "comment 1: '[ANALYST] SYMPTOMS': actor ANALYST does not match role watcher (WATCHER)")])

    def test_actor_braintrust_vote_from_a_judge(self):
        self.assertClean(self.with_comments(
            comments((1, "braintrust", "[BRAINTRUST] VOTE 5-2\n\ntally\n" + HEAD)), self.role("panel", "braintrust")))

    def test_actor_braintrust_vote_from_another_kind(self):
        root = self.with_comments(comments((1, "jury", "[BRAINTRUST] VOTE 5-2\n\ntally\n" + HEAD)),
                                  self.role("editor", "jury", "BRAINTRUST"))
        self.assertEqual(self.findings(root), [
            ("marker-verb", "comment 1: '[BRAINTRUST] VOTE 5-2': a checker may not post VOTE")])

    def test_actor_braintrust_vote_with_another_actor(self):
        root = self.with_comments(comments((1, "panel", "[BRAINTRUST] VOTE 5-2\n\ntally\n" + HEAD)))
        self.assertEqual(self.findings(root), [
            ("marker-actor", "comment 1: '[BRAINTRUST] VOTE 5-2': actor BRAINTRUST does not match role panel (PANEL)")])

    def test_actor_channel_posted_verified_from_an_operator(self):
        self.assertClean(self.with_comments(comments(
            (1, "tube", "[CHANNEL:TUBE] POSTED\n\npost id"),
            (2, "tube", "[CHANNEL:TUBE] VERIFIED\n\npost id"),
        ), self.role("uploader", "tube", "CHANNEL:TUBE")))

    def test_actor_channel_posted_verified_from_another_kind(self):
        root = self.with_comments(comments(
            (1, "tube", "[CHANNEL:TUBE] POSTED\n\npost id"),
            (2, "tube", "[CHANNEL:TUBE] VERIFIED\n\npost id"),
        ), self.role("writer", "tube", "CHANNEL:TUBE"))
        self.assertEqual(self.findings(root), [
            ("marker-verb", "comment 1: '[CHANNEL:TUBE] POSTED': a maker may not post POSTED"),
            ("marker-verb", "comment 2: '[CHANNEL:TUBE] VERIFIED': a maker may not post VERIFIED")])

    def test_actor_channel_posted_verified_with_another_actor(self):
        root = self.with_comments(comments(
            (1, "uploader", "[CHANNEL:TUBE] POSTED\n\npost id"),
            (2, "clips", "[CHANNEL:TUBE] VERIFIED\n\npost id"),
        ), self.role("uploader", "clips", "CHANNEL:CLIPS"))
        self.assertEqual(self.findings(root), [
            ("marker-actor", "comment 1: '[CHANNEL:TUBE] POSTED': actor CHANNEL:TUBE does not match role "
                             "uploader (UPLOADER)"),
            ("marker-actor", "comment 2: '[CHANNEL:TUBE] VERIFIED': actor CHANNEL:TUBE does not match role "
                             "clips (CHANNEL:CLIPS)")])

    def test_actor_autoresearch_from_the_lab(self):
        self.assertClean(self.with_comments(comments(
            *((n, "program:lab", f"[AUTORESEARCH] {verb}\n\nmetric")
              for n, verb in enumerate(("KEEP", "NOISE", "DISCARD", "REBASELINE"), 1)))))

    def test_actor_autoresearch_from_a_role(self):
        root = self.with_comments(comments(
            (1, "chief", "[AUTORESEARCH] KEEP\n\nmetric"),
            (2, "lab", "[AUTORESEARCH] KEEP\n\nmetric"),
        ), self.role("watcher", "lab", "AUTORESEARCH"))
        self.assertEqual(self.findings(root), [
            ("marker-actor", "comment 1: '[AUTORESEARCH] KEEP': actor AUTORESEARCH does not match role chief (CHIEF)"),
            ("marker-verb", "comment 2: '[AUTORESEARCH] KEEP': a analyst may not post KEEP")])

    def test_actor_autoresearch_with_another_program_or_actor(self):
        root = self.with_comments(comments(
            (1, "program:lint", "[AUTORESEARCH] KEEP\n\nmetric"),
            (2, "program:lab", "[LINT] PASS\n\n" + HEAD),
        ))
        self.assertEqual(self.findings(root), [
            ("marker-actor", "comment 1: '[AUTORESEARCH] KEEP': actor AUTORESEARCH is not one program:lint "
                             "posts as (LINT, PLATFORM-GATE)"),
            ("marker-actor", "comment 2: '[LINT] PASS': actor LINT is not one program:lab posts as (AUTORESEARCH)")])

    def test_output_is_deterministic(self):
        root = self.with_comments((FIX / "r3_verb.json").read_text())
        self.assertEqual(lint.text(lint.run(root, only=["markers"])),
                         lint.text(lint.run(root, only=["markers"])))


if __name__ == "__main__":
    unittest.main()
