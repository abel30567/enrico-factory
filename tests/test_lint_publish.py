"""Tests for kit/lib/lints/publish.py and kit/lib/posts.py's answer: the publish check of a post.

Each defect is made in a temporary copy of tests/fixtures/lint_publish/clean: the upload request of
`ep01/youtube`, its job, the answer the upload recorded, the media manifest and one rulebook per platform.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, lint, posts  # noqa: E402

CLEAN = ROOT / "tests" / "fixtures" / "lint_publish" / "clean"
REQUEST = "distribution/ep01/youtube/requests/upload.json"
JOB = "jobs/" + REQUEST
ANSWER = "distribution/ep01/youtube/post.json"
RULEBOOK = "rulebook/youtube.json"
MANIFEST = "media/manifest.tsv"
FIELD = "status.containsSyntheticMedia"


def clean_answer():
    return json.loads((CLEAN / ANSWER).read_text())


def video_sha():
    return next(line.split("\t")[1] for line in (CLEAN / MANIFEST).read_text().splitlines()
                if line.startswith("ep01-youtube-video\t"))


class PublishLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(CLEAN, self.root, dirs_exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1, sort_keys=True) + "\n")

    def answer(self, **changes):
        data = clean_answer()
        data.update(changes)
        self.write(ANSWER, data)

    def result(self):
        return lint.run(self.root, ["publish"])["publish"]

    def findings(self):
        result = self.result()
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def assertOne(self, path, rule, message):
        self.assertEqual(self.findings(), [(path, 1, rule, message)])

    def assertError(self, reason):
        result = self.result()
        self.assertEqual((result.status, result.reason), (lint.ERROR, reason))

    # -- clean twins

    def test_clean_passes(self):
        self.assertEqual(self.result().status, lint.PASS)

    def test_no_distribution_passes(self):
        shutil.rmtree(self.root / "distribution")
        self.assertEqual(self.result().status, lint.PASS)

    def test_a_field_value_true_passes(self):
        self.answer(fields={FIELD: True, "other.flag": 3, "status.privacyStatus": "private"})
        self.assertEqual(self.result().status, lint.PASS)

    # -- the upload request

    def test_request_without_media_ids(self):
        message = "requests/upload.json does not name video and captions as media:<id>"
        for request in ({"video": "media:ep01-youtube-video"}, {"video": "file.mp4", "captions": "media:x"},
                        {"video": "media:", "captions": "media:ep01-youtube-captions"}, [], "{"):
            with self.subTest(request=request):
                self.write(REQUEST, request if isinstance(request, str) else json.dumps(request))
                self.assertOne(REQUEST, "publish-upload", message)

    def test_media_id_not_in_the_manifest(self):
        self.write(REQUEST, {"video": "media:ep01-gone", "captions": "media:ep01-youtube-captions"})
        self.assertOne(REQUEST, "publish-upload", "media:ep01-gone is not in media/manifest.tsv")

    def test_no_post_json(self):
        (self.root / ANSWER).unlink()
        self.assertOne(REQUEST, "publish-exists", "no post.json for it: the upload has not run")

    # -- the answer

    def test_answer_problem(self):
        cases = [
            ("[]", "post.json is not a JSON object"),
            ("{", "post.json is not a JSON object"),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                self.write(ANSWER, text)
                self.assertOne(ANSWER, "publish-answer", message)
        data = clean_answer()
        del data["read_at"]
        self.write(ANSWER, data)
        self.assertOne(ANSWER, "publish-answer", "post.json: key read_at is missing")

    def test_platform_of_another_directory(self):
        self.answer(platform="tiktok")
        self.assertOne(ANSWER, "publish-answer", "platform tiktok is not youtube")

    def test_no_job_file(self):
        (self.root / JOB).unlink()
        self.assertOne(ANSWER, "publish-job", f"no job file {JOB}")

    def test_another_job(self):
        digest = cmd_gate._parsed((self.root / JOB).read_text(), JOB)[1]
        self.answer(job="a" * 64)
        self.assertOne(ANSWER, "publish-job", f"post.json names job aaaaaaaaaaaa, not the job {digest[:12]} of {JOB}")

    def test_visibility(self):
        for visibility in ("unlisted", "public"):
            with self.subTest(visibility=visibility):
                self.answer(visibility=visibility)
                self.assertOne(ANSWER, "publish-visibility", f"post yt123 is {visibility}, not private")

    def test_not_processed(self):
        self.answer(processed=False)
        self.assertOne(ANSWER, "publish-processed", "post yt123 is not processed")

    def test_hash(self):
        self.answer(sha256="b" * 64)
        self.assertOne(ANSWER, "publish-hash",
                       f"post yt123 has sha256 bbbbbbbbbbbb, not the {video_sha()[:12]} of media:ep01-youtube-video")

    def test_no_captions(self):
        self.answer(captions=False)
        self.assertOne(ANSWER, "publish-captions", "post yt123 has no captions")

    def test_no_disclosure_field(self):
        self.answer(fields={"status.privacyStatus": "private"})
        self.assertOne(ANSWER, "publish-disclosure", f"post yt123 has no disclosure field {FIELD}")

    def test_disclosure_value(self):
        for value, shown in (("true", '"true"'), (False, "false"), (1, "1")):
            with self.subTest(value=value):
                self.answer(fields={FIELD: value})
                self.assertOne(ANSWER, "publish-disclosure", f"post yt123 sets {FIELD} to {shown}, not true")

    def test_several_findings_on_one_answer(self):
        self.answer(visibility="public", processed=False, captions=False)
        self.assertEqual([rule for _, _, rule, _ in self.findings()],
                         ["publish-captions", "publish-processed", "publish-visibility"])

    # -- the rulebook

    def test_rulebook_missing(self):
        (self.root / RULEBOOK).unlink()
        self.assertError(f"{RULEBOOK} has no disclosure field and value")

    def test_rulebook_value_null(self):
        data = json.loads((self.root / RULEBOOK).read_text())
        data["disclosure"]["value"] = None
        self.write(RULEBOOK, data)
        self.assertError(f"{RULEBOOK} has no disclosure field and value")

    def test_rulebook_not_json_or_field_empty(self):
        self.write(RULEBOOK, "{")
        self.assertError(f"{RULEBOOK} has no disclosure field and value")
        data = json.loads((CLEAN / RULEBOOK).read_text())
        data["disclosure"]["field"] = ""
        self.write(RULEBOOK, data)
        self.assertError(f"{RULEBOOK} has no disclosure field and value")

    def test_fixture_holds_one_rulebook_per_platform(self):
        self.assertEqual(sorted(p.name for p in (CLEAN / "rulebook").iterdir()),
                         ["instagram.json", "tiktok.json", "youtube.json"])


class AnswerProblemsTest(unittest.TestCase):
    """Ruling 3: every problem kind of ``posts.answer_problems``, word for word."""

    def problems(self, **changes):
        data = clean_answer()
        data.update(changes)
        return posts.answer_problems(data)

    def test_clean(self):
        self.assertEqual(posts.answer_problems(clean_answer()), [])

    def test_not_an_object(self):
        for doc in ([], "x", None, 1):
            self.assertEqual(posts.answer_problems(doc), ["post.json is not a JSON object"])

    def test_missing_then_unknown_keys_sorted(self):
        data = clean_answer()
        del data["visibility"], data["captions"]
        data.update(zeta=1, alpha=2)
        self.assertEqual(posts.answer_problems(data), [
            "post.json: key captions is missing", "post.json: key visibility is missing",
            "post.json: unknown key alpha", "post.json: unknown key zeta"])

    def test_each_wrong_value(self):
        cases = [
            ("captions", "yes", "true or false"),
            ("processed", 1, "true or false"),
            ("fields", [], "an object of field names and short values"),
            ("fields", {"bad name": True}, "an object of field names and short values"),
            ("fields", {"a": 1.5}, "an object of field names and short values"),
            ("fields", {"a": "x" * 65}, "an object of field names and short values"),
            ("fields", {"a": "has space"}, "an object of field names and short values"),
            ("fields", {"a": None}, "an object of field names and short values"),
            ("job", "A" * 64, "a sha256"),
            ("sha256", "a" * 63, "a sha256"),
            ("platform", "YouTube", "a platform id"),
            ("post_id", "", "a post id"),
            ("post_id", "a" * 65, "a post id"),
            ("read_at", "2026-11-02 09:30:00", "a time YYYY-MM-DDTHH:MM:SSZ"),
            ("read_at", "2026-13-02T09:30:00Z", "a time YYYY-MM-DDTHH:MM:SSZ"),
            ("visibility", "hidden", "private, unlisted or public"),
        ]
        for key, value, words in cases:
            with self.subTest(key=key, value=value):
                self.assertEqual(self.problems(**{key: value}), [f"post.json: {key} is not {words}"])

    def test_wrong_values_sorted_by_key(self):
        self.assertEqual(self.problems(visibility="x", captions="x"), [
            "post.json: captions is not true or false", "post.json: visibility is not private, unlisted or public"])

    def test_short_values(self):
        self.assertEqual(self.problems(fields={"a": True, "b": 0, "c": "", "d.e_f": "x:y-z.1"}), [])


if __name__ == "__main__":
    unittest.main()
