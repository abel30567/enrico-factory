"""Tests for kit/lib/assets.py and `factory assets`: the asset store client against a fake private S3 store.

The fake store subclasses `FakeStore` of tests/test_media.py: one `ThreadingHTTPServer` on 127.0.0.1, port
0, in a thread. It keeps objects in memory under `/b/<sha256>`, records every request, answers 403 to any
request whose `Authorization` is not the test key's signature of it, and 400 to a `PUT` whose body does not
have the signed sha256. A production is a temporary directory with `production.env` and
`production.local.env`; the key file is in another temporary directory. Nothing reaches a host other than
127.0.0.1, and no file is larger than a few bytes: the size limit is tested with `assets.MAX_BYTES` patched.
"""

import contextlib
import errno
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import assets, cmd_assets, media, resources  # noqa: E402
from test_media import FakeStore, free_port  # noqa: E402

NOW = "2026-11-02T09:00:00Z"
KEY_ID, SECRET, REGION = "factory-test-key-id", "factory-test-secret-0001", "test-region-7"
H = "08bf13663c2f78761258f428fb93ba07ebf3cb904fd2f692010cdb06faf51e70"
E = hashlib.sha256(b"").hexdigest()
VECTORS = (
    ("https://store.invalid/bucket-one/" + H, "auto",
     "074dcbde18f1d50b7e61a25e4264027988a8bbbaa0e7be510fc4d31afdfab915"),
    ("http://127.0.0.1:8080/bucket-one/engine/" + H, "us-east-1",
     "7683823bb35ae72fde35eecd2d483bc59fdd9c75017685207e2dca1f7d39315f"),
)
BIN = b"invented engine binary\x00\x01\x02"
OTHER = b"invented engine BINARY\x00\x01\x02"  # the same length as BIN


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AssetStore(FakeStore):
    """A private S3 store on 127.0.0.1. ``status[method] = (code, body)`` makes it answer that to anyone;
    ``serve[sha256]`` is sent on `GET` instead of the object; ``cut[sha256] = n`` sends only the first ``n``
    bytes under the full `Content-Length`."""

    def __init__(self, test, key=(KEY_ID, SECRET, REGION)):
        super().__init__(test)
        self.key, self.serve, self.cut = key, {}, {}

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
        payload_sha256 = headers.get("x-amz-content-sha256", "")
        signed = media.authorization(method, self.url(h.path), payload_sha256, *self.key,
                                     headers.get("x-amz-date", ""))
        if headers.get("authorization") != signed or (method != "PUT" and payload_sha256 != E):
            return self.reply(h, method, 403, b"<Error>AccessDenied</Error>")
        if not h.path.startswith("/b/"):
            return self.reply(h, method, 404, b"")
        if method == "PUT":
            if sha(body) != payload_sha256:
                return self.reply(h, method, 400, b"bad digest")
            self.objects[name] = body
            return self.reply(h, method, 200, b"")
        data = self.objects.get(name)
        if data is None:
            return self.reply(h, method, 404, b"")
        data = self.serve.get(name, data)
        if name in self.cut:
            h.send_response(200)
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data[:self.cut[name]])
            return None
        return self.reply(h, method, 200, data)


class AssetsTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "production"
        self.root.mkdir()
        self.secrets = tmp / "secrets"
        self.secrets.mkdir()
        self.key = self.secrets / "store.assets"
        self.write_key(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        self.files = tmp / "files"
        self.files.mkdir()
        self.out = tmp / "out"
        self.out.mkdir()
        self.store = AssetStore(self)
        self.stores = [self.store]
        self.values = {"ASSET_STORE": self.store.url("/b"), "SECRETS_DIR": str(self.secrets)}
        self.write_settings()
        patcher = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_key(self, text, mode=0o600):
        self.key.write_text(text, encoding="utf-8")
        self.key.chmod(mode)

    def write_settings(self, **changes):
        self.values.update(changes)
        (self.root / "production.env").write_text(f"ASSET_STORE={self.values['ASSET_STORE']}\n", encoding="utf-8")
        (self.root / "production.local.env").write_text(f"SECRETS_DIR={self.values['SECRETS_DIR']}\n",
                                                         encoding="utf-8")

    def file(self, name="a.bin", data=BIN):
        path = self.files / name
        path.write_bytes(data)
        return str(path)

    def assets(self, *argv):
        """``(exit, stdout, stderr)`` of `factory assets --root <production> <argv>`; nothing secret printed."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_assets.main(["--root", str(self.root), *argv])
        printed = out.getvalue() + err.getvalue()
        signatures = [r["headers"]["authorization"].rsplit("=", 1)[1]
                      for s in self.stores for r in s.requests if "authorization" in r["headers"]]
        for secret in [KEY_ID, SECRET, REGION, str(self.secrets), *signatures]:
            self.assertNotIn(secret, printed)
        if self.values["ASSET_STORE"]:
            self.assertNotIn(self.values["ASSET_STORE"], printed)
        return code, out.getvalue(), err.getvalue()

    def put(self, path, apply=True):
        return self.assets("put", path, *(["--apply"] if apply else []))

    def get(self, sha256, path):
        return self.assets("get", sha256, str(path))

    def stored(self, data=BIN):
        self.store.objects[sha(data)] = data
        return sha(data)

    def assert_refused(self, result, reason):
        self.assertEqual(result, (2, "", f"assets: {reason}\n"))
        self.assertEqual(self.store.requests, [])

    def assert_failed(self, result, reason):
        self.assertEqual(result, (1, "", f"assets: {reason}\n"))

    def assert_signed(self, request, method, payload=E):
        url = self.store.url(request["path"])
        self.assertEqual(request["method"], method)
        self.assertEqual(request["headers"]["authorization"],
                         media.authorization(method, url, payload, KEY_ID, SECRET, REGION, NOW))
        self.assertEqual((request["headers"]["x-amz-date"], request["headers"]["x-amz-content-sha256"]),
                         ("20261102T090000Z", payload))


class B1Authorization(unittest.TestCase):
    def test_twin_the_two_get_vectors(self):
        self.assertEqual(sha(b"factory media vector\n"), H)
        for url, region, signature in VECTORS:
            with self.subTest(url=url):
                self.assertEqual(media.authorization("GET", url, E, KEY_ID, SECRET, region, NOW),
                                 f"AWS4-HMAC-SHA256 Credential={KEY_ID}/20261102/{region}/s3/aws4_request,"
                                 f"SignedHeaders=host;x-amz-content-sha256;x-amz-date,Signature={signature}")

    def test_defect_one_changed_character_gives_another_signature(self):
        url, region, signature = VECTORS[0]
        for secret, area, time in (("factory-test-secret-0002", region, NOW), (SECRET, "autp", NOW),
                                   (SECRET, region, "2026-11-02T09:00:01Z")):
            with self.subTest(secret=secret, region=area, time=time):
                got = media.authorization("GET", url, E, KEY_ID, secret, area, time)
                self.assertNotIn(got.rsplit("=", 1)[1], [v[2] for v in VECTORS])


class B2PutNewFile(AssetsTest):
    def test_twin_head_put_head_signed(self):
        digest = sha(BIN)
        code, out, err = self.put(self.file())
        self.assertEqual((code, out, err), (0, f"assets {digest}: STORED {len(BIN)}\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "HEAD"])
        head, put, again = self.store.requests
        self.assertEqual([r["path"] for r in self.store.requests], [f"/b/{digest}"] * 3)
        self.assert_signed(head, "HEAD")
        self.assert_signed(put, "PUT", digest)
        self.assert_signed(again, "HEAD")
        self.assertEqual(put["headers"]["content-type"], "application/octet-stream")
        self.assertEqual((put["headers"]["content-length"], put["body"]), (str(len(BIN)), BIN))
        self.assertNotIn("transfer-encoding", put["headers"])
        self.assertEqual(self.store.objects, {digest: BIN})

    def test_twin_the_module_returns_sent(self):
        key_file = str(self.key)
        self.assertEqual(assets.put(self.values, key_file, self.file()),
                         {"bytes": len(BIN), "sent": True, "sha256": sha(BIN)})
        self.assertEqual(assets.put(self.values, key_file, self.file()),
                         {"bytes": len(BIN), "sent": False, "sha256": sha(BIN)})

    def test_twin_script_ignores_a_proxy_in_the_environment(self):
        env = {**os.environ, "http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_assets.py"), "--root", str(self.root),
                               "put", self.file(), "--apply"], env=env, capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, f"assets {sha(BIN)}: STORED {len(BIN)}\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "HEAD"])

    def test_defect_empty_missing_or_directory_is_refused(self):
        for path in (self.file("empty.bin", b""), str(self.files / "missing.bin"), str(self.files)):
            with self.subTest(path=os.path.basename(path)):
                self.assert_refused(self.put(path), "file is not a non-empty regular file")


class B3PutAgain(AssetsTest):
    def test_twin_same_put_again_sends_one_head(self):
        self.put(self.file())
        self.store.requests.clear()
        self.assertEqual(self.put(self.file()), (0, f"assets {sha(BIN)}: STORED {len(BIN)} (already stored)\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD"])

    def test_defect_store_holds_another_length_under_the_hash(self):
        self.store.objects[sha(BIN)] = BIN + b"tail"
        self.assert_failed(self.put(self.file()), "store holds other bytes")
        self.assertEqual(self.store.methods(), ["HEAD"])
        self.assertEqual(self.store.objects, {sha(BIN): BIN + b"tail"})

    def test_defect_store_answers_200_to_put_and_keeps_nothing(self):
        self.store.status = {"PUT": (200, b"")}
        self.assert_failed(self.put(self.file()), "store answered 404")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "HEAD"])


class B4SizeLimit(AssetsTest):
    def test_defect_a_file_above_the_limit_is_refused_unread(self):
        with mock.patch.object(assets, "MAX_BYTES", 8), mock.patch.object(media, "digest",
                                                                          wraps=media.digest) as digest:
            self.assert_refused(self.put(self.file(data=b"123456789")), "file is larger than one PUT allows")
            self.assert_refused(self.put(self.file(data=b"123456789"), apply=False),
                                "file is larger than one PUT allows")
        digest.assert_not_called()

    def test_twin_a_file_at_the_limit_is_stored(self):
        with mock.patch.object(assets, "MAX_BYTES", 8):
            self.assertEqual(self.put(self.file(data=b"12345678")), (0, f"assets {sha(b'12345678')}: STORED 8\n", ""))
        self.assertEqual(self.store.objects, {sha(b"12345678"): b"12345678"})

    def test_defect_get_reads_no_more_than_the_limit_and_one(self):
        written, fdopen = [], os.fdopen

        class Counted:
            def __init__(self, descriptor, mode):
                self.handle = fdopen(descriptor, mode)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.handle.close()

            def write(self, piece):
                written.append(len(piece))
                return self.handle.write(piece)

        digest = self.stored(b"0123456789abcdef")
        with mock.patch.object(assets, "MAX_BYTES", 8), mock.patch.object(media, "CHUNK", 4), \
                mock.patch.object(assets.os, "fdopen", Counted):
            self.assert_failed(self.get(digest, self.out / "x.bin"), "store serves other bytes")
        self.assertEqual(written, [4, 4, 1])
        self.assertEqual(os.listdir(self.out), [])


class B5Get(AssetsTest):
    def test_twin_one_signed_get_leaves_the_bytes(self):
        digest = self.stored()
        target = self.out / "x.bin"
        self.assertEqual(self.get(digest, target), (0, f"assets {digest}: FETCHED {len(BIN)}\n", ""))
        self.assertEqual(self.store.methods(), ["GET"])
        self.assert_signed(self.store.requests[0], "GET")
        self.assertEqual(self.store.requests[0]["path"], f"/b/{digest}")
        self.assertEqual(target.read_bytes(), BIN)
        self.assertEqual(os.listdir(self.out), ["x.bin"])

    def defect(self, digest, reason):
        target = self.out / "x.bin"
        self.assert_failed(self.get(digest, target), reason)
        self.assertFalse(os.path.lexists(target))
        self.assertEqual(os.listdir(self.out), [])

    def test_defect_other_bytes_of_the_same_length(self):
        digest = self.stored()
        self.store.serve[digest] = OTHER
        self.defect(digest, "store serves other bytes")

    def test_defect_a_body_cut_short_under_a_full_length(self):
        digest = self.stored()
        self.store.cut[digest] = 5
        self.defect(digest, "store serves other bytes")

    def test_defect_unknown_hash(self):
        self.defect(sha(b"nothing stored"), "store has no such object")
        self.assertEqual(self.store.methods(), ["GET"])


class B6GetToAnExistingPath(AssetsTest):
    def test_twin_path_holds_the_bytes(self):
        digest = self.stored()
        target = self.out / "x.bin"
        target.write_bytes(BIN)
        self.assertEqual(self.get(digest, target), (0, f"assets {digest}: FETCHED {len(BIN)} (already there)\n", ""))
        self.assertEqual(self.store.requests, [])

    def test_defect_path_holds_other_bytes_or_is_a_directory(self):
        digest = self.stored()
        other = self.out / "other.bin"
        other.write_bytes(OTHER)
        empty = self.out / "empty.bin"
        empty.write_bytes(b"")
        folder = self.out / "folder"
        folder.mkdir()
        (folder / "inside").write_bytes(b"kept")
        for target in (other, empty, folder):
            with self.subTest(path=target.name):
                self.assert_refused(self.get(digest, target), "path holds other bytes")
        self.assertEqual((other.read_bytes(), empty.read_bytes()), (OTHER, b""))
        self.assertEqual(os.listdir(folder), ["inside"])
        self.assertEqual(sorted(os.listdir(self.out)), ["empty.bin", "folder", "other.bin"])

    def test_defect_path_in_a_missing_directory(self):
        self.assert_refused(self.get(self.stored(), self.out / "missing" / "x.bin"),
                            "path is not in an existing directory")
        self.assertFalse((self.out / "missing").exists())

    def test_defect_temporary_file_cannot_be_made(self):
        self.out.chmod(0o500)
        self.addCleanup(self.out.chmod, 0o700)
        if os.access(self.out, os.W_OK):
            self.skipTest("this user writes into a read-only directory")
        self.assert_refused(self.get(self.stored(), self.out / "x.bin"), "path cannot be written")


class B7WriteFails(AssetsTest):
    def test_defect_replace_fails(self):
        digest = self.stored()
        with mock.patch.object(assets.os, "replace", side_effect=OSError(errno.EACCES, "denied")):
            self.assert_failed(self.get(digest, self.out / "x.bin"), "path cannot be written")
        self.assertEqual(os.listdir(self.out), [])

    def test_defect_a_write_that_fails_is_not_the_store(self):
        class FullDisk:
            def __init__(self, descriptor, mode):
                self.descriptor = descriptor

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                os.close(self.descriptor)

            def write(self, piece):
                raise OSError(errno.ENOSPC, "no space")

        digest = self.stored()
        with mock.patch.object(assets.os, "fdopen", FullDisk):
            self.assert_failed(self.get(digest, self.out / "x.bin"), "path cannot be written")
        self.assertEqual(self.store.methods(), ["GET"])
        self.assertEqual(os.listdir(self.out), [])

    def test_twin_replace_works(self):
        digest = self.stored()
        self.assertEqual(self.get(digest, self.out / "x.bin")[0], 0)
        self.assertEqual((self.out / "x.bin").read_bytes(), BIN)


class B8KeyFile(AssetsTest):
    def test_defect_bad_key_files_are_refused(self):
        unusable = "resource store.assets is not usable: secret store.assets: "
        cases = (("missing", None, 0o600, unusable + "missing"),
                 ("empty", "", 0o600, unusable + "empty"),
                 ("0644", f"{KEY_ID}\n{SECRET}\n{REGION}\n", 0o644, unusable + "readable by others"),
                 ("two lines", f"{KEY_ID}\n{SECRET}\n", 0o600, "secret store.assets: not three lines"),
                 ("four lines", f"{KEY_ID}\n{SECRET}\n{REGION}\nmore\n", 0o600, "secret store.assets: not three lines"),
                 ("an empty line", f"{KEY_ID}\n\n{REGION}\n", 0o600, "secret store.assets: not three lines"))
        digest = self.stored()
        for name, text, mode, reason in cases:
            with self.subTest(key=name):
                if text is None:
                    self.key.unlink(missing_ok=True)
                else:
                    self.write_key(text, mode)
                self.assert_refused(self.put(self.file()), reason)
                self.assert_refused(self.get(digest, self.out / "x.bin"), reason)
                self.assert_refused(self.assets("check", digest), reason)
        self.assertEqual(os.listdir(self.out), [])

    def test_defect_another_secret_is_refused_by_the_store(self):
        digest = self.stored()
        self.write_key(f"{KEY_ID}\nfactory-test-secret-0002\n{REGION}\n")
        self.assert_failed(self.put(self.file("b.bin", OTHER)), "store refused the key")
        self.assert_failed(self.get(digest, self.out / "x.bin"), "store refused the key")
        self.assertEqual(self.assets("check", digest), (1, f"assets {digest}: FAILED store refused the key\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD", "GET", "HEAD"])
        self.assertEqual(os.listdir(self.out), [])

    def test_twin_three_lines_mode_0600(self):
        self.write_key(f"{KEY_ID}\n{SECRET}\n{REGION}", 0o600)
        self.assertEqual(self.put(self.file())[0], 0)
        self.assertEqual(self.get(sha(BIN), self.out / "x.bin")[0], 0)
        self.assertEqual(self.assets("check", sha(BIN))[0], 0)


class B9Settings(AssetsTest):
    def test_defect_bad_store_settings_are_refused(self):
        self.write_settings(ASSET_STORE="")
        self.assert_refused(self.put(self.file()), "resource store.assets is not usable: setting ASSET_STORE is empty")
        port = self.store.port
        for url in ("http://assets.invalid/b", "ftp://127.0.0.1/b", f"http://127.0.0.1:{port}/b?x=1",
                    f"http://someone@127.0.0.1:{port}/b", f"http://127.0.0.1:{port}/b c", "assets.invalid/b"):
            with self.subTest(url=url):
                self.write_settings(ASSET_STORE=url)
                for result in (self.put(self.file()), self.get(sha(BIN), self.out / "x.bin"),
                               self.assets("check", sha(BIN))):
                    self.assert_refused(result, "setting ASSET_STORE is not a store URL")

    def test_twin_loopback_with_a_trailing_slash(self):
        self.write_settings(ASSET_STORE=self.store.url("/b/"))
        self.assertEqual(self.put(self.file()), (0, f"assets {sha(BIN)}: STORED {len(BIN)}\n", ""))
        self.assertEqual([r["path"] for r in self.store.requests], [f"/b/{sha(BIN)}"] * 3)


class B10NothingLeaks(AssetsTest):
    def test_defect_403_whose_body_holds_the_key(self):
        body = f"<Error>{KEY_ID} {SECRET} {REGION}</Error>".encode()
        digest = self.stored()
        for method in ("HEAD", "PUT", "GET"):
            with self.subTest(method=method):
                self.store.status = {method: (403, body)}
                self.store.requests.clear()
                if method == "GET":
                    self.assert_failed(self.get(digest, self.out / "x.bin"), "store refused the key")
                else:
                    self.assert_failed(self.put(self.file("b.bin", OTHER)), "store refused the key")
        self.store.status = {"HEAD": (403, body)}
        self.assertEqual(self.assets("check", digest), (1, f"assets {digest}: FAILED store refused the key\n", ""))

    def test_twin_a_clean_run_prints_no_secret(self):
        code, out, err = self.put(self.file())
        self.assertEqual((code, err), (0, ""))
        self.assertIn(KEY_ID, self.store.requests[0]["headers"]["authorization"])
        self.assertNotIn(KEY_ID, out)


class B11StoreAnswers(AssetsTest):
    def test_defect_307_is_not_followed(self):
        other = AssetStore(self)
        self.stores.append(other)
        digest = self.stored()
        self.store.location = other.url(f"/b/{digest}")
        self.store.status = {"PUT": (307, b"")}
        self.assert_failed(self.put(self.file("b.bin", OTHER)), "store answered 307")
        self.assertEqual(self.store.methods(), ["HEAD", "PUT"])
        self.store.status = {"GET": (307, b"")}
        self.assert_failed(self.get(digest, self.out / "x.bin"), "store answered 307")
        self.assertEqual(other.requests, [])
        self.assertEqual(os.listdir(self.out), [])

    def test_defect_closed_port(self):
        self.write_settings(ASSET_STORE=f"http://127.0.0.1:{free_port()}/b")
        self.assert_failed(self.put(self.file()), "store unreachable")
        self.assert_failed(self.get(sha(BIN), self.out / "x.bin"), "store unreachable")
        self.assertEqual(os.listdir(self.out), [])

    def test_defect_500(self):
        digest = self.stored()
        for method in ("HEAD", "PUT", "GET"):
            with self.subTest(method=method):
                self.store.status = {method: (500, b"")}
                result = self.get(digest, self.out / "x.bin") if method == "GET" else self.put(self.file("b.bin", OTHER))
                self.assert_failed(result, "store answered 500")
        self.assertEqual(os.listdir(self.out), [])

    def test_twin_any_2xx_on_put_is_success(self):
        self.store.status = {"PUT": (204, b"")}
        original = self.store.handle

        def keep(h, method):  # a 204 that keeps the object
            if method == "PUT":
                self.store.objects[sha(BIN)] = BIN
            return original(h, method)

        self.store.handle = keep
        self.assertEqual(self.put(self.file()), (0, f"assets {sha(BIN)}: STORED {len(BIN)}\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD", "PUT", "HEAD"])


class B12DryRun(AssetsTest):
    def test_twin_without_apply_sends_nothing(self):
        self.assertEqual(self.put(self.file(), apply=False), (0, f"assets {sha(BIN)}: would store {len(BIN)}\n", ""))
        self.assertEqual(self.store.requests, [])

    def test_defect_without_apply_still_refuses(self):
        self.assert_refused(self.put(self.file("empty.bin", b""), apply=False), "file is not a non-empty regular file")
        self.write_key(f"{KEY_ID}\n{SECRET}\n")
        self.assert_refused(self.put(self.file(), apply=False), "secret store.assets: not three lines")


class B13Check(AssetsTest):
    def test_twin_two_stored_hashes_sorted(self):
        first, second = sorted((self.stored(BIN), self.stored(b"a second binary")))
        lengths = {sha(BIN): len(BIN), sha(b"a second binary"): len(b"a second binary")}
        self.assertEqual(self.assets("check", second, first),
                         (0, f"assets {first}: OK {lengths[first]}\nassets {second}: OK {lengths[second]}\n", ""))
        self.assertEqual(self.store.methods(), ["HEAD", "HEAD"])
        for request in self.store.requests:
            self.assert_signed(request, "HEAD")

    def test_defect_unknown_hash_fails_alone(self):
        known, unknown = self.stored(), sha(b"never stored")
        lines = sorted([f"assets {known}: OK {len(BIN)}", f"assets {unknown}: FAILED store has no such object"])
        self.assertEqual(self.assets("check", known, unknown), (1, "\n".join(lines) + "\n", ""))

    def test_defect_not_a_hash_is_refused_before_any_request(self):
        for bad in ("abc", sha(BIN).upper(), sha(BIN) + "0"):
            with self.subTest(value=bad):
                self.assert_refused(self.assets("check", self.stored(), bad),
                                    "sha256 must be 64 lower-case hex characters")
                self.assert_refused(self.get(bad, self.out / "x.bin"), "sha256 must be 64 lower-case hex characters")


class B14Register(unittest.TestCase):
    def setUp(self):
        register, problems = resources.load(resources.REGISTER)
        self.assertEqual(problems, [])
        self.register = register
        self.resource = next(r for r in register["resources"] if r["id"] == "store.assets")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.values = {"ASSET_STORE": "https://assets.invalid/b", "SECRETS_DIR": str(self.tmp)}

    def check(self, values):
        return resources.check(self.resource, values, self.register)

    def test_defect_three_states(self):
        self.assertEqual(self.check({}), (False, ["setting ASSET_STORE is empty", "setting SECRETS_DIR is empty"]))
        self.assertEqual(self.check(self.values), (False, ["secret store.assets: missing"]))
        key = self.tmp / "store.assets"
        key.write_text("one\n", encoding="utf-8")
        key.chmod(0o644)
        self.assertEqual(self.check(self.values), (False, ["secret store.assets: readable by others"]))

    def test_twin_mode_0600_passes_whatever_the_url(self):
        key = self.tmp / "store.assets"
        key.write_text("one\n", encoding="utf-8")
        key.chmod(0o600)
        self.assertEqual(self.check(self.values), (True, []))
        self.assertEqual(self.check({**self.values, "ASSET_STORE": "not a url"}), (True, []))


class EntryPoint(unittest.TestCase):
    def test_assets_is_a_command_that_imports_only_the_standard_library(self):
        listed = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "--help"], capture_output=True,
                                text=True)
        self.assertIn("  assets\n", listed.stdout)
        own = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_assets.py"), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(own.returncode, 0)
        for name in ("assets.py", "cmd_assets.py"):
            text = (ROOT / "kit" / "lib" / name).read_text(encoding="utf-8")
            imports = [line.split()[1] for line in text.splitlines() if line.startswith(("import ", "from "))]
            self.assertTrue(all(i.split(".")[0] in sys.stdlib_module_names or i == "kit.lib" for i in imports), imports)
        self.assertNotRegex((ROOT / "kit" / "lib" / "assets.py").read_text(encoding="utf-8"), "build_opener|urlopen")


if __name__ == "__main__":
    unittest.main()
