"""Tests for kit/lib/media.py and `factory media`: the media store client against a fake S3 store.

The fake store is one `ThreadingHTTPServer` on 127.0.0.1, port 0, in a thread. It keeps objects in
memory under `/b/<sha256>`, serves them to anyone under `/pub/<sha256>`, records every request, and
answers 400 to a `PUT` whose body does not have the sha256 of `x-amz-content-sha256`. A production is
a temporary directory with `production.env` and `production.local.env`; the key file is in another
temporary directory. Nothing reaches a host other than 127.0.0.1.
"""

import contextlib
import hashlib
import http.server
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_media, lint, media, resources  # noqa: E402

NOW = "2026-11-02T09:00:00Z"
KEY_ID, SECRET, REGION = "factory-test-key-id", "factory-test-secret-0001", "test-region-7"
H = "08bf13663c2f78761258f428fb93ba07ebf3cb904fd2f692010cdb06faf51e70"
E = hashlib.sha256(b"").hexdigest()
VECTORS = (
    ("PUT", "https://store.invalid/bucket-one/" + H, H, "auto",
     "f6b5370bc105dbb76a23d60642b9d0a341202719b1fcba48fc03e965903c6d81"),
    ("HEAD", "https://store.invalid/bucket-one/" + H, E, "auto",
     "46b9bbba8a4bf678e74eae428136bda281ba2aab421ad64bdb878ce3ce723a74"),
    ("PUT", "http://127.0.0.1:8080/bucket-one/art/" + H, H, "us-east-1",
     "785ae047173d8442f389a8898ba0e16b5260fdd6c83a4f3fc780f67167c107f9"),
)
HEADER = "id\tsha256\tbytes\tsource\n"
PNG = b"\x89PNG\r\n\x1a\ninvented image bytes"
OTHER = b"\x89PNG\r\n\x1a\ninvented image BYTES"  # the same length as PNG


def sha(data):
    return hashlib.sha256(data).hexdigest()


def value(method, url, payload, region, signature):
    return (f"AWS4-HMAC-SHA256 Credential=factory-test-key-id/20261102/{region}/s3/aws4_request,"
            f"SignedHeaders=host;x-amz-content-sha256;x-amz-date,Signature={signature}")


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeStore:
    """An S3 object store on 127.0.0.1. ``status[method] = (code, body)`` makes it answer that instead;
    ``public[sha256]`` is served on the public path instead of the object (None: a 404)."""

    def __init__(self, test):
        self.objects, self.public, self.status, self.requests = {}, {}, {}, []
        self.location = None
        store = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                store.handle(self, "HEAD")

            def do_PUT(self):
                store.handle(self, "PUT")

            def do_GET(self):
                store.handle(self, "GET")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def handle(self, h, method):
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in h.headers.items()}
        self.requests.append({"method": method, "path": h.path, "headers": headers, "body": body})
        name = h.path.rsplit("/", 1)[-1]
        if method in self.status:
            code, payload = self.status[method]
            extra = {"Location": self.location} if self.location and 300 <= code < 400 else {}
            return self.reply(h, method, code, payload, extra)
        if h.path.startswith("/b/") and method == "HEAD":
            if name in self.objects:
                return self.reply(h, method, 200, self.objects[name])
            return self.reply(h, method, 404, b"")
        if h.path.startswith("/b/") and method == "PUT":
            if sha(body) != headers.get("x-amz-content-sha256"):
                return self.reply(h, method, 400, b"bad digest")
            self.objects[name] = body
            return self.reply(h, method, 200, b"")
        if h.path.startswith("/pub/") and method == "GET":
            data = self.public.get(name, self.objects.get(name))
            return self.reply(h, method, 404, b"") if data is None else self.reply(h, method, 200, data)
        return self.reply(h, method, 404, b"")

    def reply(self, h, method, code, payload, extra=None):
        h.send_response(code)
        h.send_header("Content-Length", str(len(payload)))
        for name, text in (extra or {}).items():
            h.send_header(name, text)
        h.end_headers()
        if method != "HEAD":
            h.wfile.write(payload)

    def methods(self):
        return [r["method"] for r in self.requests]


class MediaTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "production"
        (self.root / "media").mkdir(parents=True)
        self.manifest = self.root / "media" / "manifest.tsv"
        self.manifest.write_text(HEADER, encoding="utf-8")
        self.secrets = tmp / "secrets"
        self.secrets.mkdir()
        self.key = self.secrets / "store.media"
        self.write_key(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        self.files = tmp / "files"
        self.files.mkdir()
        self.store = FakeStore(self)
        self.stores = [self.store]
        self.values = {"MEDIA_STORE": self.store.url("/b"), "MEDIA_PUBLIC_BASE": self.store.url("/pub"),
                       "SECRETS_DIR": str(self.secrets)}
        self.write_settings()
        patcher = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_key(self, text, mode=0o600):
        self.key.write_text(text, encoding="utf-8")
        self.key.chmod(mode)

    def write_settings(self, **changes):
        self.values.update(changes)
        (self.root / "production.env").write_text(
            f"MEDIA_STORE={self.values['MEDIA_STORE']}\nMEDIA_PUBLIC_BASE={self.values['MEDIA_PUBLIC_BASE']}\n",
            encoding="utf-8")
        (self.root / "production.local.env").write_text(f"SECRETS_DIR={self.values['SECRETS_DIR']}\n",
                                                         encoding="utf-8")

    def file(self, name="a.png", data=PNG):
        path = self.files / name
        path.write_bytes(data)
        return str(path)

    def media(self, *argv):
        """``(exit, stdout, stderr)`` of `factory media --root <production> <argv>`; nothing secret printed."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_media.main(["--root", str(self.root), *argv])
        printed = out.getvalue() + err.getvalue()
        signatures = [r["headers"]["authorization"].rsplit("=", 1)[1]
                      for s in self.stores for r in s.requests if "authorization" in r["headers"]]
        for secret in [KEY_ID, SECRET, REGION, str(self.secrets), *signatures]:
            self.assertNotIn(secret, printed)
        if self.values["MEDIA_STORE"]:
            self.assertNotIn(self.values["MEDIA_STORE"], printed)
        return code, out.getvalue(), err.getvalue()

    def put(self, path, mid="a", source="test", apply=True):
        return self.media("put", path, "--id", mid, "--source", source, *(["--apply"] if apply else []))

    def link(self, data=PNG):
        return f"{self.values['MEDIA_PUBLIC_BASE'].rstrip('/')}/{sha(data)}"

    def rows(self):
        return self.manifest.read_text(encoding="utf-8")

    def assert_refused(self, result, reason, manifest=HEADER):
        self.assertEqual(result, (2, "", f"media: {reason}\n"))
        self.assertEqual(self.store.requests, [])
        self.assertEqual(self.rows(), manifest)

    def assert_failed(self, result, reason, manifest=HEADER):
        self.assertEqual(result, (1, "", f"media: {reason}\n"))
        self.assertEqual(self.rows(), manifest)


class B1Authorization(unittest.TestCase):
    def test_twin_the_three_vectors(self):
        self.assertEqual(sha(b"factory media vector\n"), H)
        for method, url, payload, region, signature in VECTORS:
            with self.subTest(method=method, url=url):
                self.assertEqual(media.authorization(method, url, payload, KEY_ID, SECRET, region, NOW),
                                 value(method, url, payload, region, signature))

    def test_defect_one_changed_character_gives_another_signature(self):
        method, url, payload, region, signature = VECTORS[0]
        for secret, area, time in (("factory-test-secret-0002", region, NOW), (SECRET, "autp", NOW),
                                   (SECRET, region, "2026-11-02T09:00:01Z")):
            with self.subTest(secret=secret, region=area, time=time):
                got = media.authorization(method, url, payload, KEY_ID, secret, area, time)
                self.assertNotIn(signature, got)
                self.assertNotIn(got.rsplit("=", 1)[1], [v[4] for v in VECTORS])


class B2PutNewFile(MediaTest):
    def test_twin_head_put_get_and_a_row(self):
        path = self.file()
        digest = sha(PNG)
        code, out, err = self.put(path, source="imported from the sample")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"media a: STORED {digest} {len(PNG)} {self.link()}\n")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])
        head, put, get = self.store.requests
        url = self.store.url(f"/b/{digest}")
        self.assertEqual((head["path"], put["path"], get["path"]), (f"/b/{digest}", f"/b/{digest}", f"/pub/{digest}"))
        self.assertEqual(head["headers"]["authorization"],
                         media.authorization("HEAD", url, E, KEY_ID, SECRET, REGION, NOW))
        self.assertEqual(put["headers"]["authorization"],
                         media.authorization("PUT", url, digest, KEY_ID, SECRET, REGION, NOW))
        self.assertEqual((put["headers"]["x-amz-date"], put["headers"]["x-amz-content-sha256"]),
                         ("20261102T090000Z", digest))
        self.assertEqual((put["headers"]["content-length"], put["body"]), (str(len(PNG)), PNG))
        self.assertNotIn("transfer-encoding", put["headers"])
        self.assertFalse([h for h in get["headers"] if h == "authorization" or h.startswith("x-amz")])
        self.assertEqual(self.store.objects, {digest: PNG})
        self.assertEqual(self.rows(), HEADER + f"a\t{digest}\t{len(PNG)}\timported from the sample\n")
        self.assertEqual(lint.overall(lint.run(self.root, only=["media"])), lint.PASS)

    def test_twin_a_missing_manifest_is_created(self):
        self.manifest.unlink()
        self.assertEqual(self.put(self.file())[0], 0)
        self.assertEqual(self.rows(), HEADER + f"a\t{sha(PNG)}\t{len(PNG)}\ttest\n")

    def test_twin_proxy_in_the_environment_is_not_used(self):
        env = {**os.environ, "http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_media.py"), "--root", str(self.root),
                               "put", self.file(), "--id", "a", "--source", "test", "--apply"],
                              env=env, capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, f"media a: STORED {sha(PNG)} {len(PNG)} {self.link()}\n")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])

    def test_defect_empty_missing_or_directory_is_refused(self):
        for path in (self.file("empty.png", b""), str(self.files / "missing.png"), str(self.files)):
            with self.subTest(path=os.path.basename(path)):
                self.assert_refused(self.put(path), "file is not a non-empty regular file")


class B3PutAgain(MediaTest):
    def test_twin_same_put_again_sends_no_put(self):
        path = self.file()
        self.put(path)
        rows = self.rows()
        self.store.requests.clear()
        code, out, err = self.put(path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"media a: STORED {sha(PNG)} {len(PNG)} {self.link()} (already recorded)\n")
        self.assertEqual(self.store.methods(), ["HEAD", "GET"])
        self.assertEqual(self.rows(), rows)

    def test_twin_same_bytes_under_a_second_id(self):
        path = self.file()
        self.put(path)
        self.store.requests.clear()
        code, out, _ = self.put(path, mid="b.copy", source="copy")
        self.assertEqual((code, out), (0, f"media b.copy: STORED {sha(PNG)} {len(PNG)} {self.link()}\n"))
        self.assertEqual(self.store.methods(), ["HEAD", "GET"])
        self.assertEqual(self.rows(), HEADER + f"a\t{sha(PNG)}\t{len(PNG)}\ttest\n"
                         + f"b.copy\t{sha(PNG)}\t{len(PNG)}\tcopy\n")

    def test_defect_same_id_other_bytes_is_refused(self):
        self.put(self.file())
        rows = self.rows()
        self.store.requests.clear()
        self.assert_refused(self.put(self.file("b.png", b"other bytes")), "id a is in the manifest with other bytes",
                            rows)

    def test_defect_store_holds_another_length_under_the_hash(self):
        self.store.objects[sha(PNG)] = b"a longer object under the same name"
        self.assert_failed(self.put(self.file()), "store holds other bytes")
        self.assertEqual(self.store.methods(), ["HEAD"])


class B4PublicLink(MediaTest):
    def test_defect_link_serves_other_bytes_then_twin_repaired(self):
        self.store.public[sha(PNG)] = OTHER
        self.assert_failed(self.put(self.file()), "public link serves other bytes")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])
        del self.store.public[sha(PNG)]
        self.store.requests.clear()
        code, out, _ = self.put(self.file())
        self.assertEqual((code, out), (0, f"media a: STORED {sha(PNG)} {len(PNG)} {self.link()}\n"))
        self.assertEqual(self.store.methods(), ["HEAD", "GET"])
        self.assertEqual(self.rows(), HEADER + f"a\t{sha(PNG)}\t{len(PNG)}\ttest\n")

    def test_defect_link_answers_404(self):
        self.store.public[sha(PNG)] = None
        self.assert_failed(self.put(self.file()), "public link answered 404")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])

    def test_defect_link_serves_more_bytes(self):
        self.store.public[sha(PNG)] = PNG + b"tail"
        self.assert_failed(self.put(self.file()), "public link serves other bytes")


class B5KeyFile(MediaTest):
    def test_defect_bad_key_files_are_refused(self):
        unusable = "resource store.media is not usable: secret store.media: "
        cases = (("missing", None, 0o600, unusable + "missing"),
                 ("empty", "", 0o600, unusable + "empty"),
                 ("0644", f"{KEY_ID}\n{SECRET}\n{REGION}\n", 0o644, unusable + "readable by others"),
                 ("two lines", f"{KEY_ID}\n{SECRET}\n", 0o600, "secret store.media: not three lines"),
                 ("four lines", f"{KEY_ID}\n{SECRET}\n{REGION}\nmore\n", 0o600, "secret store.media: not three lines"),
                 ("an empty line", f"{KEY_ID}\n\n{REGION}\n", 0o600, "secret store.media: not three lines"))
        for name, text, mode, reason in cases:
            with self.subTest(key=name):
                if text is None:
                    self.key.unlink(missing_ok=True)
                else:
                    self.write_key(text, mode)
                self.assert_refused(self.put(self.file()), reason)

    def test_twin_three_lines_mode_0600(self):
        self.write_key(f"{KEY_ID}\n{SECRET}\n{REGION}", 0o600)
        self.assertEqual(self.put(self.file())[0], 0)
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])


class B6Settings(MediaTest):
    def bad(self, port):
        return ("http://store.invalid/b", "ftp://127.0.0.1/b", f"http://127.0.0.1:{port}/b?x=1",
                f"http://someone@127.0.0.1:{port}/b", f"http://127.0.0.1:{port}/b c", f"http://127.0.0.1:{port}/b#f",
                f"http://127.0.0.1:{port}/b?", f"http://127.0.0.1:{port}/b%20c", "store.invalid/b")

    def test_defect_bad_store_settings_are_refused(self):
        self.write_settings(MEDIA_STORE="")
        self.assert_refused(self.put(self.file()), "resource store.media is not usable: setting MEDIA_STORE is empty")
        for url in self.bad(self.store.port):
            with self.subTest(url=url):
                self.write_settings(MEDIA_STORE=url)
                self.assert_refused(self.put(self.file()), "setting MEDIA_STORE is not a store URL")

    def test_defect_bad_public_settings_are_refused(self):
        for url in self.bad(self.store.port):
            with self.subTest(url=url):
                self.write_settings(MEDIA_PUBLIC_BASE=url)
                self.assert_refused(self.put(self.file()), "setting MEDIA_PUBLIC_BASE is not a public URL")
                self.assert_refused(self.media("link", "a"), "setting MEDIA_PUBLIC_BASE is not a public URL")
                self.assert_refused(self.media("check"), "setting MEDIA_PUBLIC_BASE is not a public URL")

    def test_defect_whitespace_the_parser_would_drop(self):
        for url in (" https://store.invalid/b", "https://store.invalid/b\t", "https://store.invalid/b\nc"):
            with self.subTest(url=url):
                self.assertIsNone(media.base(url))

    def test_twin_loopback_with_a_trailing_slash(self):
        self.write_settings(MEDIA_STORE=self.store.url("/b/"), MEDIA_PUBLIC_BASE=self.store.url("/pub/"))
        code, out, _ = self.put(self.file())
        self.assertEqual((code, out), (0, f"media a: STORED {sha(PNG)} {len(PNG)} {self.link()}\n"))
        self.assertEqual([r["path"] for r in self.store.requests], [f"/b/{sha(PNG)}"] * 2 + [f"/pub/{sha(PNG)}"])
        self.assertEqual(media.base("https://store.invalid/bucket-one/art/"), "https://store.invalid/bucket-one/art")
        self.assertEqual(media.base("https://store.invalid"), "https://store.invalid")


class B7NothingLeaks(MediaTest):
    def test_defect_403_whose_body_holds_the_key(self):
        body = f"<Error>{KEY_ID} {SECRET} {REGION}</Error>".encode()
        for method in ("HEAD", "PUT"):
            for code in (401, 403):
                with self.subTest(method=method, code=code):
                    self.store.status = {method: (code, body)}
                    self.store.requests.clear()
                    self.assert_failed(self.put(self.file()), "store refused the key")
                    self.assertNotIn("GET", self.store.methods())

    def test_twin_a_clean_put_prints_no_secret(self):
        code, out, err = self.put(self.file())
        self.assertEqual((code, err), (0, ""))
        self.assertIn(KEY_ID, self.store.requests[0]["headers"]["authorization"])
        self.assertNotIn(KEY_ID, out)


class B8StoreAnswers(MediaTest):
    def test_defect_307_on_put_is_not_followed(self):
        other = FakeStore(self)
        self.stores.append(other)
        self.store.location = other.url(f"/b/{sha(PNG)}")
        self.store.status = {"PUT": (307, b"")}
        self.assert_failed(self.put(self.file()), "store answered 307")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT"])
        self.assertEqual(other.requests, [])

    def test_defect_302_on_head_is_not_followed(self):
        other = FakeStore(self)
        self.stores.append(other)
        self.store.location = other.url(f"/b/{sha(PNG)}")
        self.store.status = {"HEAD": (302, b"")}
        self.assert_failed(self.put(self.file()), "store answered 302")
        self.assertEqual(other.requests, [])

    def test_defect_closed_port(self):
        port = free_port()
        self.write_settings(MEDIA_STORE=f"http://127.0.0.1:{port}/b")
        self.assert_failed(self.put(self.file()), "store unreachable")
        self.write_settings(MEDIA_STORE=self.store.url("/b"), MEDIA_PUBLIC_BASE=f"http://127.0.0.1:{port}/pub")
        self.assert_failed(self.put(self.file()), "public link unreachable")

    def test_defect_500(self):
        for method in ("HEAD", "PUT"):
            with self.subTest(method=method):
                self.store.status = {method: (500, b"")}
                self.assert_failed(self.put(self.file()), "store answered 500")

    def test_twin_any_2xx_on_put_is_success(self):
        self.store.status = {"PUT": (204, b"")}
        self.store.public[sha(PNG)] = PNG
        self.assertEqual(self.put(self.file())[0], 0)
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "GET"])
        self.assertEqual(self.rows(), HEADER + f"a\t{sha(PNG)}\t{len(PNG)}\ttest\n")


class B9DryRun(MediaTest):
    def test_twin_without_apply_sends_and_writes_nothing(self):
        self.manifest.write_bytes(HEADER.encode() + b"x\t" + sha(b"x").encode() + b"\t1\tother")
        before = self.manifest.read_bytes()
        code, out, err = self.put(self.file(), apply=False)
        self.assertEqual((code, out, err), (0, f"media a: would store {sha(PNG)} {len(PNG)}\n", ""))
        self.assertEqual(self.store.requests, [])
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_twin_without_apply_creates_no_manifest(self):
        self.manifest.unlink()
        self.assertEqual(self.put(self.file(), apply=False)[0], 0)
        self.assertFalse(self.manifest.exists())

    def test_defect_without_apply_still_refuses(self):
        cases = (("A", "test", "id must match [a-z0-9][a-z0-9._-]*"),
                 ("a", "two\nlines", "source must be one line with no tab"),
                 ("a", "a\ttab", "source must be one line with no tab"),
                 ("a", "", "source must be one line with no tab"))
        for mid, source, reason in cases:
            with self.subTest(id=mid, source=source):
                self.assert_refused(self.put(self.file(), mid=mid, source=source, apply=False), reason)
        self.manifest.write_text("id\tsha\tbytes\tsource\n", encoding="utf-8")
        self.assert_refused(self.put(self.file(), apply=False), "media/manifest.tsv: bad header",
                            "id\tsha\tbytes\tsource\n")


class B10LinkAndCheck(MediaTest):
    def two_rows(self):
        self.put(self.file("b.png", OTHER), mid="b-two")
        self.put(self.file(), mid="a-one")
        self.store.requests.clear()

    def test_twin_link_prints_and_sends_nothing(self):
        self.two_rows()
        self.assertEqual(self.media("link", "a-one"), (0, self.link() + "\n", ""))
        self.assertEqual(self.store.requests, [])

    def test_twin_check_every_row_sorted(self):
        self.two_rows()
        self.assertEqual(self.media("check"), (0, "media a-one: OK\nmedia b-two: OK\n", ""))
        self.assertEqual([r["path"] for r in self.store.requests], [f"/pub/{sha(PNG)}", f"/pub/{sha(OTHER)}"])
        self.assertEqual(self.media("check", "b-two", "a-one")[1], "media a-one: OK\nmedia b-two: OK\n")

    def test_defect_unknown_id(self):
        self.two_rows()
        rows = self.rows()
        self.assert_refused(self.media("link", "zzz"), "id zzz is not in the manifest", rows)
        self.assert_refused(self.media("check", "a-one", "zzz"), "id zzz is not in the manifest", rows)

    def test_defect_one_replaced_object(self):
        self.two_rows()
        self.store.public[sha(OTHER)] = PNG
        self.assertEqual(self.media("check"),
                         (1, "media a-one: OK\nmedia b-two: FAILED public link serves other bytes\n", ""))


class B11ContentType(MediaTest):
    def test_twin_png(self):
        self.put(self.file("a.png"))
        self.assertEqual(self.store.requests[1]["headers"]["content-type"], "image/png")

    def test_defect_unknown_extension(self):
        self.put(self.file("a.exr"))
        self.assertEqual(self.store.requests[1]["headers"]["content-type"], "application/octet-stream")
        self.assertEqual([media.content_type(n) for n in ("x.JPG", "x.mp4", "x", "x.png.txt")],
                         ["image/jpeg", "video/mp4", "application/octet-stream", "application/octet-stream"])


class B12Register(unittest.TestCase):
    def setUp(self):
        register, problems = resources.load(resources.REGISTER)
        self.assertEqual(problems, [])
        self.register = register
        self.resource = next(r for r in register["resources"] if r["id"] == "store.media")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.values = {"MEDIA_STORE": "https://store.invalid/b", "MEDIA_PUBLIC_BASE": "https://media.invalid/p",
                       "SECRETS_DIR": str(self.tmp)}

    def check(self, values):
        return resources.check(self.resource, values, self.register)

    def test_defect_three_states(self):
        self.assertEqual(self.check({}), (False, ["setting MEDIA_STORE is empty", "setting MEDIA_PUBLIC_BASE is empty",
                                                  "setting SECRETS_DIR is empty"]))
        self.assertEqual(self.check(self.values), (False, ["secret store.media: missing"]))
        key = self.tmp / "store.media"
        key.write_text("one\n", encoding="utf-8")
        key.chmod(0o644)
        self.assertEqual(self.check(self.values), (False, ["secret store.media: readable by others"]))

    def test_twin_one_line_of_mode_0600(self):
        key = self.tmp / "store.media"
        key.write_text("one\n", encoding="utf-8")
        key.chmod(0o600)
        self.assertEqual(self.check(self.values), (True, []))


class EntryPoint(unittest.TestCase):
    def test_media_is_a_command_that_imports_only_the_standard_library(self):
        listed = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "--help"], capture_output=True,
                                text=True)
        self.assertIn("  media\n", listed.stdout)
        own = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_media.py"), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(own.returncode, 0)
        for name in ("media.py", "cmd_media.py"):
            text = (ROOT / "kit" / "lib" / name).read_text(encoding="utf-8")
            imports = [line.split()[1] for line in text.splitlines() if line.startswith(("import ", "from "))]
            self.assertTrue(all(i.split(".")[0] in sys.stdlib_module_names or i == "kit.lib" for i in imports), imports)


if __name__ == "__main__":
    unittest.main()
