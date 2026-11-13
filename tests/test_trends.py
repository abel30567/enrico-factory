"""Tests for kit/lib/trends.py and kit/lib/cmd_trends.py.

The fixture tests/fixtures/trends/ is copied to a temporary directory: `production/` is the production and
`state/` its STATE_DIR, written to production.local.env. Each seeded defect is one edit in the copy.

Recorded: the YouTube read of 2026-11-12T09:00:00Z (yt-a to yt-f) and the one 12 hours earlier (the same
videos less yt-b, at half the views); the TikTok read of 2026-11-12T09:30:00Z (tt-q, tt-p, tt-r, order only).
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_trends, trends  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "trends"
TOPIC = "harbour-tales"
YT = "reads/harbour-tales/youtube/20261112T090000Z.json"
YT_EARLIER = "reads/harbour-tales/youtube/20261111T210000Z.json"
TT = "reads/harbour-tales/tiktok/20261112T093000Z.json"
CARD = "research/trends/harbour-tales.{}.tsv"
TIKTOK = """\
trends harbour-tales tiktok: read 2026-11-12T09:30:00Z, 3 videos, 0 under the view floor 1000
platform-order 1 tt-q 1
platform-order 2 tt-p 2
platform-order 3 tt-r 3
skip engagement-rate:unknown: 3 of 3 videos lack views, likes or comments
skip views-per-hour:unknown: 3 of 3 videos lack views or a publish time
skip outlier-score:unknown: 3 of 3 videos lack views or 10 views of the author
skip comment-rate:unknown: 3 of 3 videos lack views or comments
skip reach:unknown: 3 of 3 videos lack views or followers
skip velocity:unknown: 3 of 3 videos lack views in this read or the one before
"""
RANKING_NAMES = [name for name, _ in trends.RANKINGS]


class World:
    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.tmp = tmp
        shutil.copytree(FIX, tmp, dirs_exist_ok=True)
        self.root = tmp / "production"
        self.state = tmp / "state"
        self.tree = tmp / "tree"
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state}\n")

    def read(self, rel):
        return json.loads((self.state / rel).read_text())

    def put(self, rel, doc):
        path = self.state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1) + "\n")

    def edit(self, rel, change):
        doc = self.read(rel)
        change(doc)
        self.put(rel, doc)

    def video(self, rel, vid, **fields):
        def change(doc):
            video = next(v for v in doc["videos"] if v["id"] == vid)
            for key, value in fields.items():
                if value is ABSENT:
                    video.pop(key, None)
                else:
                    video[key] = value
        self.edit(rel, change)

    def run(self, platform="youtube", *extra, topic=TOPIC):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_trends.main([topic, platform, "--root", str(self.root), *extra])
        return code, out.getvalue(), err.getvalue()

    def card(self, platform="youtube"):
        return self.tree / CARD.format(platform)


ABSENT = object()


class TrendsTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assertRefused(self, line, *args, code=1, **kwargs):
        got, out, err = self.w.run(*args, **kwargs)
        self.assertEqual((got, out, err), (code, "", f"trends: {line}\n"))
        self.assertNotIn(str(self.w.state), err)

    def lines(self, *args):
        code, out, err = self.w.run(*args)
        self.assertEqual((code, err), (0, ""))
        return out.splitlines()

    def test_1_same_order_on_every_run(self):
        first = self.w.run()
        self.assertEqual(first[0], 0)
        self.assertEqual(first, self.w.run())
        self.w.edit(YT, lambda doc: doc["videos"].reverse())
        self.w.edit(YT_EARLIER, lambda doc: doc["videos"].reverse())
        self.assertEqual(first, self.w.run())

    def test_2_view_floor(self):
        lines = self.lines()
        self.assertTrue(lines[0].endswith(", 6 videos, 1 under the view floor 1000"), lines[0])
        self.assertFalse([line for line in lines[1:] if "yt-c" in line.split()])
        lines = self.lines("youtube", "--view-floor", "400")
        self.assertTrue(lines[0].endswith(", 0 under the view floor 400"), lines[0])
        self.assertIn("platform-order 3 yt-c 3", lines)
        self.assertIn("engagement-rate:short 1 yt-c 0.2200", lines)

    def test_3_no_counts(self):
        code, out, err = self.w.run("tiktok")
        self.assertEqual((code, out, err), (0, TIKTOK, ""))
        names = {line.split()[0].split(":")[0] for line in self.lines()[1:] if not line.startswith("skip ")}
        self.assertEqual(names, set(RANKING_NAMES))

    def test_4_missing_field_is_skipped_and_said(self):
        said = ["skip engagement-rate:long: 1 of 2 videos lack views, likes or comments",
                "skip outlier-score:long: 1 of 2 videos lack views or 10 views of the author",
                "skip reach:short: 1 of 2 videos lack views or followers"]

        def check():
            lines = self.lines()
            self.assertFalse([line for line in lines if line.startswith("engagement-rate") and " yt-e " in line])
            self.assertFalse([line for line in lines if line.startswith("outlier-score") and " yt-d " in line])
            self.assertFalse([line for line in lines if line.startswith("reach") and " yt-b " in line])
            for line in said:
                self.assertIn(line, lines)
        check()
        self.w.video(YT, "yt-e", likes=ABSENT)
        self.w.video(YT, "yt-b", followers=ABSENT)
        check()
        self.w.video(YT, "yt-b", followers=0)
        check()
        self.w.video(YT, "yt-d", author_views=ABSENT)
        check()

    def test_5_velocity(self):
        lines = self.lines()
        self.assertIn("velocity:short 1 yt-a 2000.0000", lines)
        self.assertIn("skip velocity:short: 1 of 2 videos lack views in this read or the one before", lines)
        (self.w.state / YT_EARLIER).unlink()
        skips = [line for line in self.lines() if line.startswith("skip velocity")]
        self.assertEqual(skips, ["skip velocity:short: 2 of 2 videos lack views in this read or the one before",
                                 "skip velocity:long: 2 of 2 videos lack views in this read or the one before",
                                 "skip velocity:unknown: 1 of 1 videos lack views in this read or the one before"])
        self.assertFalse([line for line in self.lines() if line.startswith("velocity")])

    def test_6_bands(self):
        lines = self.lines()
        self.assertIn("views-per-hour:unknown 1 yt-f 83.3333", lines)
        self.w.video(YT, "yt-a", duration_s=239)
        self.assertIn("views-per-hour:short 1 yt-a 2000.0000", self.lines())
        self.w.video(YT, "yt-a", duration_s=240)
        lines = self.lines()
        self.assertIn("views-per-hour:long 1 yt-a 2000.0000", lines)
        self.assertFalse([line for line in lines if line.startswith("views-per-hour:short") and " yt-a " in line])

    def test_7_never_merged(self):
        for key, value in (("platform", "tiktok"), ("topic", "other-tales")):
            with self.subTest(key=key):
                original = self.w.read(YT)
                self.w.edit(YT, lambda doc: doc.__setitem__(key, value))
                self.assertRefused(f"{YT}: topic or platform is not harbour-tales youtube",
                                   "youtube", "--write", str(self.w.tree))
                self.assertFalse(self.w.card().exists())
                self.w.put(YT, original)

    def test_8_every_refusal(self):
        cases = [
            ("no topic file", lambda: (self.w.root / "research/topics/harbour-tales.json").unlink(), "no research/topics/harbour-tales.json", "youtube"),
            ("not watched", lambda: None, "research/topics/harbour-tales.json does not watch instagram",
             "instagram"),
            ("no reads", lambda: shutil.rmtree(self.w.state / "reads/harbour-tales/tiktok"),
             "no reads of harbour-tales on tiktok", "tiktok"),
            ("bad JSON", lambda: (self.w.state / YT).write_text("{not json\n"), f"{YT}: not a JSON object",
             "youtube"),
            ("read_at", lambda: self.w.edit(YT, lambda d: d.__setitem__("read_at", "2026-11-12T09:00:01Z")),
             f"{YT}: read_at does not match the file name", "youtube"),
            ("videos", lambda: self.w.edit(YT, lambda d: d.__setitem__("videos", {})),
             f"{YT}: videos is not a list", "youtube"),
            ("link", lambda: self.w.video(YT, "yt-c", link=ABSENT), f"{YT}: video 2 has no id or link", "youtube"),
            ("repeated", lambda: self.w.video(YT, "yt-c", id="yt-a"), f"{YT}: video yt-a is repeated", "youtube"),
            ("views -1", lambda: self.w.video(YT, "yt-c", views=-1),
             f"{YT}: video yt-c: views is not a whole number >= 0 or null", "youtube"),
            ("views '12'", lambda: self.w.video(YT_EARLIER, "yt-c", views="12"),
             f"{YT_EARLIER}: video yt-c: views is not a whole number >= 0 or null", "youtube"),
            ("likes true", lambda: self.w.video(YT, "yt-c", likes=True),
             f"{YT}: video yt-c: likes is not a whole number >= 0 or null", "youtube"),
            ("published_at", lambda: self.w.video(YT, "yt-c", published_at="2026-11-11 09:00"),
             f"{YT}: video yt-c: published_at is not valid", "youtube"),
            ("duration_s", lambda: self.w.video(YT, "yt-c", duration_s=False),
             f"{YT}: video yt-c: duration_s is not valid", "youtube"),
            ("author_views", lambda: self.w.video(YT, "yt-c", author_views=[1, -2]),
             f"{YT}: video yt-c: author_views is not valid", "youtube"),
        ]
        for name, seed, line, platform in cases:
            with self.subTest(name):
                self.w = World(self)
                seed()
                self.assertRefused(line, platform, "--write", str(self.w.tree))
                self.assertFalse(self.w.tree.exists())
        self.w = World(self)
        self.assertRefused("topic must match [a-z0-9][a-z0-9-]*", "youtube", topic="Harbour_Tales", code=2)
        self.assertRefused("--view-floor must be a whole number >= 1", "youtube", "--view-floor", "0", code=2)
        (self.w.root / "production.local.env").unlink()
        self.assertRefused("setting STATE_DIR is empty", "youtube", code=2)

    def test_9_card_holds_no_count(self):
        code, out, err = self.w.run("youtube", "--write", str(self.w.tree))
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("wrote research/trends/harbour-tales.youtube.tsv: 31 rows\n"), out)
        text = self.w.card().read_text(encoding="utf-8")
        rows = text.split("\n")
        self.assertEqual(rows.pop(), "")
        self.assertEqual(rows[0], "ranking\trank\tid\tlink\tread_at\tnote")
        self.assertEqual(len(rows), 32)
        counts, titles = set(), []
        for rel in (YT, YT_EARLIER):
            for video in self.w.read(rel)["videos"]:
                titles.append(video["title"])
                counts.update(str(video[k]) for k in trends.COUNTS if video.get(k) is not None)
                counts.update(str(v) for v in video.get("author_views") or [])
        for row in rows:
            fields = row.split("\t")
            self.assertEqual(len(fields), 6, row)
            self.assertFalse(counts & set(fields), row)
        for title in titles:
            self.assertNotIn(title, text)
        self.assertEqual(rows[1:], [f"{line.split()[0]}\t{line.split()[1]}\t{line.split()[2]}\t"
                                    f"https://www.youtube.com/watch?v={line.split()[2]}\t2026-11-12T09:00:00Z\t"
                                    for line in out.splitlines()[1:32]])

    def test_10_notes_survive(self):
        card = self.w.card("tiktok")
        card.parent.mkdir(parents=True)
        card.write_text("ranking\trank\tid\tlink\tread_at\tnote\n"
                        "platform-order\t2\ttt-q\thttps://x.invalid/q\t2026-11-11T09:30:00Z\tthe sea shanty one\n"
                        "platform-order\t1\ttt-gone\thttps://x.invalid/g\t2026-11-11T09:30:00Z\tno longer read\n"
                        "platform-order\t3\ttt-r\thttps://x.invalid/r\t2026-11-11T09:30:00Z\t\n")
        code, out, err = self.w.run("tiktok", "--write", str(self.w.tree))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, TIKTOK + "dropped note platform-order tt-gone\n"
                         "wrote research/trends/harbour-tales.tiktok.tsv: 3 rows\n")
        self.assertEqual(card.read_text(encoding="utf-8"), (
            "ranking\trank\tid\tlink\tread_at\tnote\n"
            "platform-order\t1\ttt-q\thttps://www.tiktok.com/@harbourtales/video/tt-q\t2026-11-12T09:30:00Z\t"
            "the sea shanty one\n"
            "platform-order\t2\ttt-p\thttps://www.tiktok.com/@harbourtales/video/tt-p\t2026-11-12T09:30:00Z\t\n"
            "platform-order\t3\ttt-r\thttps://www.tiktok.com/@harbourtales/video/tt-r\t2026-11-12T09:30:00Z\t\n"))
        broken = card.read_text(encoding="utf-8") + "platform-order\t4\ttt-s\thttps://x.invalid/s\t2026\n"
        card.write_text(broken)
        self.assertRefused("research/trends/harbour-tales.tiktok.tsv is not a trend card (line 5)",
                           "tiktok", "--write", str(self.w.tree))
        self.assertEqual(card.read_text(encoding="utf-8"), broken)
        card.write_text("rank\tid\n")
        self.assertRefused("research/trends/harbour-tales.tiktok.tsv is not a trend card (line 1)",
                           "tiktok", "--write", str(self.w.tree))

    def test_11_no_machine_path(self):
        seeds = [lambda: self.w.edit(TT, lambda d: d.__setitem__("videos", None)),
                 lambda: (self.w.state / TT).write_text("[]"),
                 lambda: shutil.rmtree(self.w.state / "reads"),
                 lambda: self.w.video(TT, "tt-p", order="1")]
        for seed in seeds:
            self.w = World(self)
            seed()
            code, out, err = self.w.run("tiktok")
            self.assertEqual((code, out), (1, ""))
            self.assertTrue(err.startswith("trends: "), err)
            self.assertNotIn(str(self.w.state), err)
            self.assertNotIn(str(self.w.tmp), err)

    def test_12_as_a_script(self):
        args = [TOPIC, "youtube", "--root", str(self.w.root)]
        script = subprocess.run([sys.executable, str(ROOT / "kit/lib/cmd_trends.py"), *args],
                                capture_output=True, cwd=self.w.tmp)
        factory = subprocess.run([sys.executable, str(ROOT / "kit/factory"), "trends", *args],
                                 capture_output=True, cwd=self.w.tmp)
        self.assertEqual(script.returncode, 0, script.stderr)
        self.assertEqual((script.returncode, script.stdout, script.stderr),
                         (factory.returncode, factory.stdout, factory.stderr))
        self.assertEqual(script.stdout.decode(), self.w.run()[1])


if __name__ == "__main__":
    unittest.main()
