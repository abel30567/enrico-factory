"""Tests for kit/lib/citedmedia.py: a checker's launch places the pictures its pull request cites.

The production and GitHub are those of tests/test_launch.py (``World``), with ``media: cited`` added to the
fixture's ``reviewer.md``; the media store is the fake S3 store of tests/test_media.py (``FakeStore``) on
127.0.0.1. Pull #7 is made to cite pictures by a commit on its branch; nothing reaches another host.
"""

import hashlib
import importlib.util
import json
import os
import re
import stat
import struct
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import citedmedia, glb, gitstate  # noqa: E402
from kit.lib.launch import LaunchError  # noqa: E402
from test_launch import FailingPost, World  # noqa: E402
from test_media import FakeStore, free_port  # noqa: E402

BRANCH = "beats/BT-2-drafter-1"
HEADER = "id\tsha256\tbytes\tsource\n"
PNG = b"\x89PNG\r\n\x1a\ninvented image bytes"
OTHER = b"\x89PNG\r\n\x1a\ninvented image BYTES"  # the same length as PNG
JPEG = b"\xff\xd8\xff\xe0invented jpeg bytes"
TEXT = b"invented words, not a picture"
DOWN = "[DOWN] store.media"
JOB = "cd" * 32
UNIT = "production/ep02/beats"
SHEET = b"\x89PNG\r\n\x1a\ninvented sheet"
NOTES = ("4 views: front, right, back, three-quarter", "12 triangles", "flat shading")
HEADER_SHEETS = "Sheets of the models of the job of pull request #7, outside the worktree; open each with Read:"
NO_BOUNDS = "  no bounds: not a GLB the kit can read"
ROLE = ROOT / "kit" / "roles" / "look-reviewer.md"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _pad(data, fill):
    return data + fill * (-len(data) % 4)


def make_glb(document, binary=b""):
    """A GLB of ``document`` (a JSON value) and, when not empty, ``binary``: the lines of tests/test_glb.py."""
    chunks = [(glb.JSON_CHUNK, _pad(json.dumps(document).encode("utf-8"), b" "))]
    if binary:
        chunks.append((glb.BIN_CHUNK, _pad(binary, b"\x00")))
    body = b"".join(struct.pack("<II", len(data), kind) + data for kind, data in chunks)
    return struct.pack("<4sII", glb.MAGIC, glb.VERSION, 12 + len(body)) + body


# The 12 triangles of a box whose corners are numbered by bits: x 1, y 2, z 4.
FACES = (0, 2, 1, 1, 2, 3, 4, 5, 6, 5, 7, 6, 0, 1, 4, 1, 5, 4, 2, 6, 3, 3, 6, 7, 0, 4, 2, 2, 4, 6, 1, 3, 5, 3, 7, 5)


def box_document(*boxes):
    """``(document, binary)`` of one primitive per box ``(low, high)``: ``POSITION`` with ``min`` and
    ``max``, and ``uint16`` indices."""
    doc = {"asset": {"version": "2.0"}, "buffers": [], "bufferViews": [], "accessors": [], "meshes": [],
           "nodes": [], "scenes": [{"nodes": []}], "scene": 0}
    binary, primitives = b"", []
    for low, high in boxes:
        corners = [[(high if bit & n else low)[axis] for axis, bit in enumerate((1, 2, 4))] for n in range(8)]
        positions = b"".join(struct.pack("<3f", *corner) for corner in corners)
        indices = struct.pack(f"<{len(FACES)}H", *FACES)
        for data, target in ((positions, 34962), (indices, 34963)):
            binary += b"\x00" * (-len(binary) % 4)
            doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data),
                                       "target": target})
            binary += data
        views = len(doc["bufferViews"])
        doc["accessors"].append({"bufferView": views - 2, "componentType": 5126, "count": 8, "type": "VEC3",
                                 "min": list(low), "max": list(high)})
        doc["accessors"].append({"bufferView": views - 1, "componentType": 5123, "count": len(FACES),
                                 "type": "SCALAR"})
        count = len(doc["accessors"])
        primitives.append({"attributes": {"POSITION": count - 2}, "indices": count - 1})
    doc["meshes"].append({"primitives": primitives})
    doc["nodes"].append({"mesh": 0})
    doc["scenes"][0]["nodes"].append(0)
    doc["buffers"].append({"byteLength": len(_pad(binary, b"\x00"))})
    return doc, binary


def box(*boxes):
    """A GLB of boxes ``(low, high)``, each a corner of three numbers."""
    return make_glb(*box_document(*boxes))


def sized(x, y, z):
    """One box ``x`` by ``y`` by ``z`` metres, standing on the origin."""
    return box(((0, 0, 0), (x, y, z)))


HUMAN = sized(0.9, 1.75, 0.31)  # a human 1.75 m, A-pose, feet
PLANK = sized(0.63, 0.88, 0.03)  # a card plank
WALL = sized(4, 3, 0.1)
SWORD = sized(0.1, 1.0, 0.02)
EDGE = sized(0.09, 1, 0.5)  # a depth of exactly the line
SHIP = box(((-1, 0, -3), (1, 1.5, 3)), ((-0.05, 1.5, -0.05), (0.05, 7.5, 0.05)))  # a hull and a mast 6 m


def stub(raises=None):
    """A ``kit.lib.preview`` that counts its calls and returns the invented sheet, or raises ``raises``."""
    module = types.ModuleType("kit.lib.preview")

    class PreviewError(Exception):
        pass

    module.PreviewError, module.calls = PreviewError, []

    def glb_sheet(data):
        module.calls.append(data)
        if raises is not None:
            raise raises(module) if callable(raises) else raises
        return types.SimpleNamespace(data=SHEET, notes=NOTES)

    module.glb_sheet = glb_sheet
    return module


def role_problems(text):
    """What the look reviewer's role file lacks for a model3d pull: [] when it has everything."""
    problems = []
    lines = text.splitlines()
    if len(lines) > 120:
        problems.append(f"{len(lines)} lines, over 120")
    reads = next((line for line in lines if line.startswith("reads:")), "")
    if "production/**" not in reads:
        problems.append("reads lacks production/**")
    rubric = text.split("## Rubric", 1)[1].split("\n## ", 1)[0] if "## Rubric" in text else ""
    criteria = re.split(r"\n(?=[0-9]+\. )", rubric)
    terms = ("requests/model3d.json", "pose", "under the line", "Blocker:", "Issue:")
    if sum(all(term in c for term in terms) for c in criteria) != 1:
        problems.append("no one criterion of the model")
    if "a model with no sheet is" not in text:
        problems.append("a model with no sheet does not block")
    return problems


class Cited(unittest.TestCase):
    """A world whose reviewer asks for cited media, and a store that serves what ``serve`` puts in it."""

    KEY = True

    def setUp(self):
        self.w = World(self)
        self.store = FakeStore(self)
        if self.KEY:
            path = self.w.root / ".factory" / "roles" / "reviewer.md"
            path.write_text(path.read_text().replace("gated: no\n", "gated: no\nmedia: cited\n"))
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"))

    def settings(self, **values):
        self.w.local(**values)

    def serve(self, *items):
        for data in items:
            self.store.objects[sha(data)] = data

    def cite(self, text, rows=()):
        """Commit on pull #7's branch, from a worktree of its own, a file holding ``text`` and a manifest of
        ``rows``."""
        tree = self.w.tmp / "cite"
        self.w.git("worktree", "add", "-q", str(tree), BRANCH)
        (tree / "episodes" / "ep02").mkdir(parents=True, exist_ok=True)
        (tree / "episodes" / "ep02" / "beats.md").write_text(text + "\n")
        (tree / "media").mkdir(exist_ok=True)
        (tree / "media" / "manifest.tsv").write_text(HEADER + "".join("\t".join(map(str, r)) + "\n" for r in rows))
        identity = ("-c", "user.name=tester", "-c", "user.email=tester@example.invalid")
        gitstate.git(tree, "add", "-A")
        gitstate.git(tree, *identity, "commit", "-q", "-m", "cite media")
        self.w.git("worktree", "remove", str(tree))

    def row(self, mid, data):
        return (mid, sha(data), len(data), "test")

    def prompt(self, run):
        return (run.dir / "prompt.md").read_text()

    def task(self, run, *lines):
        head = self.w.head(BRANCH)
        return "Task: " + "\n\n".join([f"Review pull request #7 at head {head}.", "\n".join(lines)]) + "\n"

    def placed(self, run):
        directory = run.dir / "media"
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def downs(self):
        return [i for i in self.w.gh.issues("open") if i["title"] == DOWN]

    def runs(self):
        runs = self.w.state / "runs"
        return sorted(p.name for p in runs.iterdir()) if runs.exists() else []


class B1Clean(Cited):
    def test_clean_picture_is_placed_and_named(self):
        self.serve(PNG)
        self.cite("A reference: ![a](media:ama-ref).", [self.row("ama-ref", PNG)])
        run = self.w.prepare("reviewer", 7)
        media_dir = run.dir / "media"
        self.assertEqual((media_dir / "ama-ref.png").read_bytes(), PNG)
        self.assertEqual(mode(media_dir), 0o700)
        self.assertEqual(mode(media_dir / "ama-ref.png"), 0o600)
        self.assertEqual(self.placed(run), ["ama-ref.png"])
        self.assertTrue(run.dir.is_absolute())
        self.assertIn(self.task(run, "Pictures cited by the files of pull request #7, outside the worktree; "
                                     "open each with Read:",
                                f"- media:ama-ref: {run.dir}/media/ama-ref.png"), self.prompt(run))
        self.assertEqual([(r["method"], r["path"]) for r in self.store.requests], [("GET", f"/pub/{sha(PNG)}")])
        self.assertEqual(self.runs(), ["reviewer-7-1"])

    def test_the_ids_are_sorted_once_with_a_jpeg_kept_as_named(self):
        self.serve(PNG, JPEG)
        self.cite("media:zed media:ama-ref and media:ama-ref again, media:look.jpeg",
                  [self.row("zed", PNG), self.row("ama-ref", PNG), ("ama-ref", sha(OTHER), len(OTHER), "later"),
                   self.row("look.jpeg", JPEG)])
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.placed(run), ["ama-ref.png", "look.jpeg", "zed.png"])
        self.assertIn(self.task(run, "Pictures cited by the files of pull request #7, outside the worktree; "
                                     "open each with Read:",
                                f"- media:ama-ref: {run.dir}/media/ama-ref.png",
                                f"- media:look.jpeg: {run.dir}/media/look.jpeg",
                                f"- media:zed: {run.dir}/media/zed.png"), self.prompt(run))


class B1Seeded(Cited):
    KEY = False

    def test_no_key_no_block_no_request(self):
        self.serve(PNG)
        self.cite("A reference: ![a](media:ama-ref).", [self.row("ama-ref", PNG)])
        run = self.w.prepare("reviewer", 7)
        text = self.prompt(run)
        self.assertIn(f"Task: Review pull request #7 at head {self.w.head(BRANCH)}.\n", text)
        self.assertNotIn("media:", text)
        self.assertEqual(self.store.requests, [])
        self.assertFalse((run.dir / "media").exists())


class B2JobPicture(Cited):
    def job(self, data):
        digest = sha(b"an invented job")
        out = self.w.state / "jobs" / digest / "out"
        out.mkdir(parents=True)
        (out / "panel.png").write_bytes(data)
        mid = f"{digest[:16]}-panel.png"
        self.cite(f"The panel: media:{mid}", [(mid, sha(PNG), len(PNG), f"job {digest}")])
        return mid

    def test_clean_from_the_job_output_with_no_store(self):
        self.settings()
        mid = self.job(PNG)
        run = self.w.prepare("reviewer", 7)
        self.assertEqual((run.dir / "media" / mid).read_bytes(), PNG)
        self.assertIn(f"- media:{mid}: {run.dir}/media/{mid}\n", self.prompt(run))
        self.assertEqual(self.store.requests, [])

    def test_seeded_other_bytes_in_the_job_output_come_from_the_store(self):
        self.serve(PNG)
        mid = self.job(OTHER)
        run = self.w.prepare("reviewer", 7)
        self.assertEqual((run.dir / "media" / mid).read_bytes(), PNG)
        self.assertEqual([r["path"] for r in self.store.requests], [f"/pub/{sha(PNG)}"])


class B3Caps(Cited):
    BIG = PNG + b" and five"

    def three(self):
        a, b, c = PNG, OTHER, PNG + b"!"
        self.serve(a, b, c)
        self.cite("media:a media:b media:c", [self.row("a", a), self.row("b", b), self.row("c", c)])
        return a, b, c

    def outcome(self, run, skipped):
        lines = ["Pictures cited by the files of pull request #7, outside the worktree; open each with Read:"]
        lines += [f"- media:{name}: {run.dir}/media/{name}.png" for name in ("a", "b", "c") if name != skipped[0]]
        lines += ["Not fetched:", f"- media:{skipped[0]}: {skipped[1]}"]
        self.assertIn(self.task(run, *lines), self.prompt(run))
        self.assertEqual(self.placed(run), [f"{name}.png" for name in ("a", "b", "c") if name != skipped[0]])

    def test_seeded_bytes(self):
        a, b, c = self.three()
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"), MEDIA_FETCH_BYTES=str(len(a)))
        self.outcome(self.w.prepare("reviewer", 7), ("c", f"{len(c)} bytes, over MEDIA_FETCH_BYTES {len(a)}"))

    def test_seeded_count(self):
        self.three()
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"), MEDIA_FETCH_COUNT="2")
        self.outcome(self.w.prepare("reviewer", 7), ("c", "over MEDIA_FETCH_COUNT 2"))

    def test_seeded_total(self):
        a, b, c = self.three()
        cap = len(a) + len(b)
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"), MEDIA_FETCH_TOTAL=str(cap))
        self.outcome(self.w.prepare("reviewer", 7), ("c", f"over MEDIA_FETCH_TOTAL {cap}"))

    def test_twin_exactly_the_cap_is_placed(self):
        a, b, c = self.three()
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"), MEDIA_FETCH_BYTES=str(len(c)),
                      MEDIA_FETCH_COUNT="3", MEDIA_FETCH_TOTAL=str(len(a) + len(b) + len(c)))
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.placed(run), ["a.png", "b.png", "c.png"])
        self.assertNotIn("Not fetched:", self.prompt(run))

    def test_seeded_bad_setting_refuses(self):
        self.three()
        for name in ("MEDIA_FETCH_TOTAL", "MEDIA_FETCH_COUNT", "MEDIA_FETCH_BYTES"):
            for value in ("0", "x", "01", "-1"):
                with self.subTest(name=name, value=value):
                    self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"), **{name: value})
                    with self.assertRaisesRegex(LaunchError, f"^setting {name} is not a whole number >= 1$"):
                        self.w.prepare("reviewer", 7)
        self.assertEqual(self.store.requests, [])
        self.assertEqual(self.runs(), [])


class B4NotFetched(Cited):
    def test_seeded_no_row_no_size_and_text(self):
        self.serve(TEXT)
        self.cite("See media:nowhere. And media:nosize, media:words.",
                  [("nosize", sha(PNG), "x", "test"), self.row("words", TEXT)])
        run = self.w.prepare("reviewer", 7)
        self.assertIn(self.task(run, "Pictures cited by the files of pull request #7, outside the worktree; "
                                     "open each with Read:",
                                "- none",
                                "Not fetched:",
                                "- media:nosize: its manifest row gives no size",
                                "- media:nowhere: no row in media/manifest.tsv",
                                "- media:words: not a PNG, JPEG, GIF or WebP picture"), self.prompt(run))
        self.assertEqual(self.placed(run), [])

    def test_the_id_alphabet(self):
        found = citedmedia.CITED.findall("media:a. media:b-_ media:C media:ok.png, (media:x_y-z.1)")
        self.assertEqual(found, ["a", "b", "ok.png", "x_y-z.1"])


class B5Outage(Cited):
    def refused(self, number):
        w = self.w
        self.cite("A reference: media:ama-ref", [self.row("ama-ref", PNG)])
        before = w.snapshot()
        with self.assertRaisesRegex(LaunchError, rf"^resource store\.media is down \(#{number}\)$"):
            w.prepare("reviewer", 7)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(self.runs(), [])
        self.assertEqual(list((w.state / "worktrees").iterdir()) if (w.state / "worktrees").exists() else [], [])
        downs = self.downs()
        self.assertEqual([i["number"] for i in downs], [number])
        self.assertIn("resource:down", downs[0]["labels"])
        self.assertIn("a cited picture could not be fetched: ", downs[0]["body"])
        # A second launch with that issue open sends no request.
        self.store.requests.clear()
        with self.assertRaisesRegex(LaunchError, rf"^resource store\.media is down \(#{number}\)$"):
            w.prepare("reviewer", 7)
        self.assertEqual(self.store.requests, [])
        self.assertEqual(len(self.downs()), 1)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(self.runs(), [])

    def number(self):
        return max(x["number"] for x in self.w.gh.issues("all") + self.w.gh.pulls("all")) + 1

    def test_seeded_404(self):
        self.refused(self.number())

    def test_seeded_other_bytes(self):
        self.store.public[sha(PNG)] = OTHER
        self.refused(self.number())

    def test_seeded_closed_port(self):
        self.settings(MEDIA_PUBLIC_BASE=f"http://127.0.0.1:{free_port()}/pub")
        self.refused(self.number())


class B6Setting(Cited):
    def test_seeded_no_public_base(self):
        self.settings()
        self.cite("A reference: media:ama-ref", [self.row("ama-ref", PNG)])
        before = self.w.snapshot()
        with self.assertRaisesRegex(LaunchError, "^cited media: setting MEDIA_PUBLIC_BASE is not a public URL$"):
            self.w.prepare("reviewer", 7)
        self.assertEqual(self.downs(), [])
        self.assertEqual(self.w.snapshot(), before)
        self.assertEqual(self.runs(), [])

    def test_twin_nothing_cited_launches(self):
        self.settings()
        run = self.w.prepare("reviewer", 7)
        self.assertIn(self.task(run, "The files of pull request #7 cite no media:<id>."), self.prompt(run))
        self.assertFalse((run.dir / "media").exists())


class B7Undo(Cited):
    def test_failed_post_leaves_nothing(self):
        self.serve(PNG)
        self.cite("A reference: media:ama-ref", [self.row("ama-ref", PNG)])
        self.w.gh = FailingPost(self.w.dir)
        with self.assertRaisesRegex(LaunchError, "^could not post LAUNCH on #7$"):
            self.w.prepare("reviewer", 7)
        self.assertEqual(len(self.store.requests), 1)
        self.assertEqual(self.runs(), [])


class B8NoBytesInGit(Cited):
    def test_worktree_clean_and_media_lint_passes(self):
        self.serve(PNG)
        self.cite("A reference: ![a](media:ama-ref).", [self.row("ama-ref", PNG)])
        run = self.w.prepare("reviewer", 7)
        self.assertTrue((run.dir / "media" / "ama-ref.png").is_file())
        self.assertEqual(gitstate.git(run.worktree, "status", "--porcelain"), "")
        self.assertFalse(any(p.suffix == ".png" for p in run.worktree.rglob("*")))
        lint = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "lint", "--root", str(run.worktree),
                               "--only", "media"], capture_output=True, text=True)
        self.assertEqual((lint.returncode, lint.stdout), (0, "media PASS\n"), lint.stderr)


class Sheets(Cited):
    """A job on pull #7 whose outputs are in ``<STATE_DIR>/jobs/<JOB>/out``, and a stubbed ``kit.lib.preview``."""

    def job(self, models, kind=None):
        """Commit the job's manifest of ``models`` (``{name: bytes}``) and, with ``kind``, the canon file of
        unit ep02; write each model's bytes to the job's output directory."""
        outputs = [{"name": name, "sha256": sha(data), "bytes": len(data)} for name, data in models.items()]
        files = {f"{UNIT}/manifest.json": json.dumps({"job": JOB, "outputs": outputs, "tool": "invented"})}
        if kind is not None:
            files[f"{dict(citedmedia.KINDS)[kind]}/ep02.md"] = f"# Ep02\n\nAn invented {kind}.\n"
        tree = self.w.tmp / "job"
        self.w.git("worktree", "add", "-q", str(tree), BRANCH)
        for rel, text in files.items():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(text)
        identity = ("-c", "user.name=tester", "-c", "user.email=tester@example.invalid")
        gitstate.git(tree, "add", "-A")
        gitstate.git(tree, *identity, "commit", "-q", "-m", "a job's manifest")
        self.w.git("worktree", "remove", str(tree))
        out = self.w.state / "jobs" / JOB / "out"
        out.mkdir(parents=True, exist_ok=True)
        for name, data in models.items():
            (out / name).write_bytes(data)

    def launch(self, module):
        with mock.patch.dict(sys.modules, {"kit.lib.preview": module}):
            return self.w.prepare("reviewer", 7)

    def ask(self):
        path = self.w.root / ".factory" / "roles" / "reviewer.md"
        path.write_text(path.read_text().replace("gated: no\n", "gated: no\nmedia: cited\n"))
        self.settings(MEDIA_PUBLIC_BASE=self.store.url("/pub"))

    def lines(self, *lines):
        """The sheets block: after the review block, the last of the task."""
        return "\n".join([HEADER_SHEETS, *lines]) + "\n"


class B9SheetClean(Sheets):
    def test_clean_a_character_gets_its_sheet_and_bounds(self):
        self.job({"model.glb": HUMAN}, "character")
        module = stub()
        run = self.launch(module)
        text = self.prompt(run)
        expected = self.lines(f"- review/model.glb: {run.dir}/media/sheets/model.glb.png",
                              "  4 views: front, right, back, three-quarter; 12 triangles; flat shading",
                              "  x 0.9 m, y 1.75 m, z 0.31 m; depth 0.1771 of the height: "
                              "not under the line of 0.09 for a character")
        review = text.index("Files of the job of pull request #7, from production/ep02/beats/manifest.json")
        self.assertIn("\n\n" + expected, text[review:])
        sheet = run.dir / "media" / "sheets" / "model.glb.png"
        self.assertEqual(sheet.read_bytes(), SHEET)
        self.assertEqual(mode(sheet), 0o600)
        self.assertEqual(mode(sheet.parent), 0o700)
        self.assertEqual(mode(run.dir / "media"), 0o700)
        self.assertEqual(module.calls, [HUMAN])
        self.assertEqual((run.worktree / "review" / "model.glb").read_bytes(), HUMAN)
        self.assertFalse((run.worktree / "review" / "sheets").exists())


class B10Depth(Sheets):
    CASES = (
        ("plank", PLANK, "character", "x 0.63 m, y 0.88 m, z 0.03 m; depth 0.0341 of the height: "
                                      "under the line of 0.09 for a character"),
        ("wall", WALL, "set", "x 4 m, y 3 m, z 0.1 m; depth 0.0333 of the height: under the line of 0.09 for a set"),
        ("sword", SWORD, None, "x 0.1 m, y 1 m, z 0.02 m; depth 0.02 of the height: no line for a prop"),
        ("edge", EDGE, "character", "x 0.09 m, y 1 m, z 0.5 m; depth 0.09 of the height: "
                                    "not under the line of 0.09 for a character"),
        ("human", HUMAN, "character", "x 0.9 m, y 1.75 m, z 0.31 m; depth 0.1771 of the height: "
                                      "not under the line of 0.09 for a character"),
        ("ship", SHIP, "set", "x 2 m, y 7.5 m, z 6 m; depth 0.2667 of the height: not under the line of 0.09 for a set"),
    )

    def test_each_kind_and_its_line(self):
        for label, model, kind, line in self.CASES:
            with self.subTest(label):
                self.assertEqual(citedmedia.extents(model)[1], glb.summary(model)["height_m"])
                self.setUp()
                self.job({"model.glb": model}, kind)
                text = self.prompt(self.launch(stub()))
                self.assertIn("flat shading\n  " + line + "\n", text)

    def test_the_extents(self):
        self.assertEqual(citedmedia.extents(SHIP), (2, 7.5, 6))
        self.assertEqual(citedmedia.extents(PLANK), (0.63, 0.88, 0.03))
        self.assertEqual(citedmedia.DEPTH_MIN, 0.09)


class B11NoSheet(Sheets):
    def no_sheet(self, module, text):
        self.job({"model.glb": HUMAN}, "character")
        run = self.launch(module)
        self.assertIn(self.lines(
            f"- review/model.glb: no sheet: {text}",
            "  x 0.9 m, y 1.75 m, z 0.31 m; depth 0.1771 of the height: not under the line of 0.09 for a character"),
            self.prompt(run))
        self.assertFalse((run.dir / "media" / "sheets").exists())
        self.assertTrue((run.dir / "prompt.md").is_file())
        return module

    def test_seeded_preview_error(self):
        module = self.no_sheet(stub(lambda m: m.PreviewError("glb: preview took longer than 180 s")),
                               "glb: preview took longer than 180 s")
        self.assertEqual(len(module.calls), 1)

    def test_seeded_not_installed(self):
        self.no_sheet(None, "kit.lib.preview is not installed")

    def test_seeded_other_exception(self):
        self.no_sheet(stub(RuntimeError("x")), "RuntimeError")

    def test_seeded_a_sheet_that_cannot_be_written_fails_the_launch(self):
        self.job({"model.glb": HUMAN}, "character")
        with mock.patch.object(citedmedia, "_write_sheet", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(LaunchError, "^cited media: Permission denied$"):
                self.launch(stub())
        self.assertEqual(self.runs(), [])


class B12Bounds(Sheets):
    def seeded(self):
        doc, binary = box_document(((0, 0, 0), (1, 1, 1)))
        yield "not a GLB", b"invented words, not a GLB"
        for label, change in (
                ("no min", lambda d: d["accessors"][0].pop("min")),
                ("max of 2", lambda d: d["accessors"][0].update(max=[1, 1])),
                ("min true", lambda d: d["accessors"][0].update(min=[0, True, 0])),
                ("index -1", lambda d: d["meshes"][0]["primitives"][0]["attributes"].update(POSITION=-1)),
                ("meshes a string", lambda d: d.update(meshes="meshes")),
                ("height 0", lambda d: d["accessors"][0].update(max=[1, 0, 1])),
                ("no primitive", lambda d: d["meshes"][0].update(primitives=[])),
                ("index true", lambda d: d["meshes"][0]["primitives"][0]["attributes"].update(POSITION=True)),
                ("min infinite", lambda d: d["accessors"][0].update(min=[0, float("-inf"), 0]))):
            copy = json.loads(json.dumps(doc))
            change(copy)
            yield label, make_glb(copy, binary)

    def test_seeded_each_raises_broken(self):
        for label, data in self.seeded():
            with self.subTest(label):
                with self.assertRaises(glb.Broken) as caught:
                    citedmedia.extents(data)
                self.assertIs(type(caught.exception), glb.Broken)

    def test_twin_a_box_reads(self):
        self.assertEqual(citedmedia.extents(box(((0, 0, 0), (1, 1, 1)))), (1, 1, 1))

    def test_in_a_launch_no_bounds_follows_the_sheet(self):
        doc, binary = box_document(((0, 0, 0), (1, 1, 1)))
        doc["accessors"][0].pop("min")
        self.job({"model.glb": make_glb(doc, binary)}, "character")
        run = self.launch(stub())
        self.assertIn(self.lines(
            f"- review/model.glb: {run.dir}/media/sheets/model.glb.png",
            "  4 views: front, right, back, three-quarter; 12 triangles; flat shading", NO_BOUNDS), self.prompt(run))


class B13NotAsked(Sheets):
    KEY = False

    def test_seeded_no_key_no_sheet(self):
        self.job({"model.glb": HUMAN}, "character")
        module = stub()
        run = self.launch(module)
        self.assertNotIn("Sheets of the models", self.prompt(run))
        self.assertFalse((run.dir / "media").exists())
        self.assertEqual(module.calls, [])

    def test_seeded_a_png_only_is_not_drawn(self):
        self.ask()
        self.job({"pose.png": PNG}, "character")
        module = stub()
        run = self.launch(module)
        self.assertNotIn("Sheets of the models", self.prompt(run))
        self.assertFalse((run.dir / "media").exists())
        self.assertEqual(module.calls, [])

    def test_no_review_directory_creates_nothing(self):
        staged = self.w.tmp / "staged"
        module = stub()
        self.assertIsNone(citedmedia.sheets(self.w.root, {"unit": "ep02"}, {"number": 7}, self.w.tmp / "none",
                                            staged, self.w.tmp / "final", module))
        self.assertFalse(staged.exists())
        self.assertEqual(module.calls, [])

    def test_the_models_are_sorted(self):
        self.ask()
        self.job({"b.glb": SWORD, "a.glb": HUMAN}, "character")
        module = stub()
        run = self.launch(module)
        text = self.prompt(run)
        self.assertLess(text.index("- review/a.glb: "), text.index("- review/b.glb: "))
        self.assertEqual(module.calls, [HUMAN, SWORD])
        self.assertEqual(sorted(p.name for p in (run.dir / "media" / "sheets").iterdir()),
                         ["a.glb.png", "b.glb.png"])


@unittest.skipUnless(importlib.util.find_spec("kit.lib.preview"), "needs kit.lib.preview")
class B14RealPreview(Sheets):
    def test_a_real_sheet_of_a_box(self):
        self.job({"model.glb": HUMAN}, "character")
        run = self.w.prepare("reviewer", 7)
        sheet = (run.dir / "media" / "sheets" / "model.glb.png").read_bytes()
        self.assertTrue(sheet.startswith(glb.PNG))
        self.assertEqual(sheet[12:16], b"IHDR")
        self.assertEqual(struct.unpack_from(">II", sheet, 16), (1024, 1024))
        self.assertIn("\n  4 views: front, right, back, three-quarter; 12 triangles;", self.prompt(run))


class B15RoleFile(unittest.TestCase):
    def test_clean_the_shipped_role_file(self):
        self.assertEqual(role_problems(ROLE.read_text(encoding="utf-8")), [])

    def test_seeded_no_criterion_of_the_model(self):
        text = ROLE.read_text(encoding="utf-8")
        start = text.index("8. **The model.**")
        end = text.index("\n## ", start)
        self.assertEqual(role_problems(text[:start] + text[end + 1:]), ["no one criterion of the model"])

    def test_seeded_121_lines(self):
        text = ROLE.read_text(encoding="utf-8")
        longer = text + "\n" * (121 - len(text.splitlines()))
        self.assertEqual(len(longer.splitlines()), 121)
        self.assertEqual(role_problems(longer), ["121 lines, over 120"])


if __name__ == "__main__":
    unittest.main()


class B8PullAddsRows(Cited):
    """The rows the pull adds to the manifest against the merge base are cited; no other row is."""

    PAD = sha(b"an invented pad job")
    PID = f"{PAD[:16]}-a-pad.png"

    def manifest(self, tree, rows):
        (tree / "media").mkdir(exist_ok=True)
        (tree / "media" / "manifest.tsv").write_text(HEADER + "".join("\t".join(map(str, r)) + "\n" for r in rows))

    def trunk(self, rows, message):
        """Commit on ``trunk`` a manifest of ``rows``."""
        self.manifest(self.w.root, rows)
        self.w.commit(message)

    def branch_point(self, rows):
        """A manifest of ``rows`` on ``trunk``, merged into pull #7's branch: the merge base holds them."""
        self.trunk(rows, "the base manifest")
        tree = self.w.tmp / "merge"
        self.w.git("worktree", "add", "-q", str(tree), BRANCH)
        gitstate.git(tree, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid",
                     "merge", "-q", "--no-edit", "trunk")
        self.w.git("worktree", "remove", str(tree))

    def pad(self, data):
        out = self.w.state / "jobs" / self.PAD / "out"
        out.mkdir(parents=True)
        (out / "a-pad.png").write_bytes(data)
        return (self.PID, sha(PNG), len(PNG), f"job {self.PAD}")

    def lines(self, run, *names):
        return [f"- media:{name}: {run.dir}/media/{name}" for name in sorted(names)]

    def test_clean_the_added_row_and_the_cited_source_are_placed(self):
        self.serve(OTHER)
        self.branch_point([self.row("a.png", OTHER)])
        self.cite('{"source": "media:a.png"}', [self.row("a.png", OTHER), self.pad(PNG)])
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.placed(run), sorted(["a.png", self.PID]))
        self.assertEqual((run.dir / "media" / self.PID).read_bytes(), PNG)
        self.assertEqual((run.dir / "media" / "a.png").read_bytes(), OTHER)
        self.assertIn(self.task(run, "Pictures cited by the files of pull request #7, outside the worktree; "
                                     "open each with Read:", *self.lines(run, "a.png", self.PID)), self.prompt(run))
        self.assertEqual([r["path"] for r in self.store.requests], [f"/pub/{sha(OTHER)}"])

    def test_seeded_a_row_at_the_merge_base_reordered_is_not_placed(self):
        self.serve(OTHER, JPEG)
        self.branch_point([self.row("old.png", PNG), self.row("a.png", OTHER), self.row("kept.jpeg", JPEG)])
        self.cite('{"source": "media:a.png"} and media:kept.jpeg',
                  [self.row("a.png", OTHER), self.row("kept.jpeg", JPEG), self.pad(PNG), self.row("old.png", PNG)])
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.placed(run), sorted(["a.png", "kept.jpeg", self.PID]))
        self.assertNotIn("media:old.png", self.prompt(run))
        self.assertNotIn(f"/pub/{sha(PNG)}", [r["path"] for r in self.store.requests])

    def test_seeded_a_row_the_base_added_after_the_branch_point_is_not_placed(self):
        self.serve(OTHER, JPEG)
        self.branch_point([self.row("a.png", OTHER)])
        self.cite('{"source": "media:a.png"}', [self.row("a.png", OTHER), self.pad(PNG)])
        self.trunk([self.row("a.png", OTHER), self.row("b.png", JPEG)], "the base moves")
        self.assertIn("b.png", gitstate.show(self.w.root, "trunk", gitstate.MANIFEST))
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.placed(run), sorted(["a.png", self.PID]))
        self.assertNotIn("media:b.png", self.prompt(run))
        self.assertEqual([r["path"] for r in self.store.requests], [f"/pub/{sha(OTHER)}"])
