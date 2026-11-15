"""Tests for kit/lib/sigv4.py: the documentation's examples, the agreement with `media`, and the refusals."""

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import media, sigv4  # noqa: E402

# The S3 documentation's presigned URL example (GET /test.txt).
S3_KEY = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1")
S3_URL = "https://examplebucket.s3.amazonaws.com/test.txt"
S3_TIME = "2013-05-24T00:00:00Z"
S3_SIGNATURE = "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"

# The Signature Version 4 documentation's IAM ListUsers GET example.
IAM_KEY = ("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1")
IAM_URL = "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08"
IAM_HEADERS = {"content-type": "application/x-www-form-urlencoded; charset=utf-8", "x-amz-date": "20150830T123600Z"}
IAM_TIME = "2015-08-30T12:36:00Z"
IAM_SIGNATURE = "5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7"
EMPTY = hashlib.sha256(b"").hexdigest()

# tests/test_media.py B1Authorization: its key, time and vectors.
NOW = "2026-11-02T09:00:00Z"
MEDIA_KEY = ("factory-test-key-id", "factory-test-secret-0001")
H = "08bf13663c2f78761258f428fb93ba07ebf3cb904fd2f692010cdb06faf51e70"
MEDIA_VECTORS = (
    ("PUT", "https://store.invalid/bucket-one/" + H, H, "auto"),
    ("HEAD", "https://store.invalid/bucket-one/" + H, EMPTY, "auto"),
    ("PUT", "http://127.0.0.1:8080/bucket-one/art/" + H, H, "us-east-1"),
)


def signature(value):
    return value.rsplit("=", 1)[1]


def iam(key=IAM_KEY):
    return sigv4.authorization("GET", IAM_URL, IAM_HEADERS, EMPTY, key, "iam", IAM_TIME)


class S3Presigned(unittest.TestCase):
    def test_twin_the_documented_signature(self):
        link = sigv4.presign("GET", S3_URL, S3_KEY, S3_TIME, 86400)
        self.assertEqual(link, S3_URL + "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
                         "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request"
                         "&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host"
                         f"&X-Amz-Signature={S3_SIGNATURE}")

    def test_defect_one_character_of_the_secret_changed(self):
        key = (S3_KEY[0], S3_KEY[1][:-1] + "X", S3_KEY[2])
        got = signature(sigv4.presign("GET", S3_URL, key, S3_TIME, 86400))
        self.assertNotEqual(got, S3_SIGNATURE)
        self.assertRegex(got, "^[0-9a-f]{64}$")


class IamListUsers(unittest.TestCase):
    def test_twin_the_documented_signature(self):
        self.assertEqual(iam(), "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request,"
                         f"SignedHeaders=content-type;host;x-amz-date,Signature={IAM_SIGNATURE}")

    def test_twin_header_names_lower_cased_and_values_trimmed(self):
        headers = {"Content-Type": "  application/x-www-form-urlencoded; charset=utf-8 ",
                   "X-Amz-Date": "20150830T123600Z"}
        self.assertEqual(sigv4.authorization("GET", IAM_URL, headers, EMPTY, IAM_KEY, "iam", IAM_TIME), iam())

    def test_twin_query_order_does_not_matter(self):
        url = "https://iam.amazonaws.com/?Version=2010-05-08&Action=ListUsers"
        self.assertEqual(sigv4.authorization("GET", url, IAM_HEADERS, EMPTY, IAM_KEY, "iam", IAM_TIME), iam())

    def test_defect_region_us_west_2(self):
        got = iam((IAM_KEY[0], IAM_KEY[1], "us-west-2"))
        self.assertIn("/us-west-2/iam/", got)
        self.assertNotEqual(signature(got), IAM_SIGNATURE)


class AgreesWithMedia(unittest.TestCase):
    def test_twin_each_vector_of_media(self):
        for method, url, payload, region in MEDIA_VECTORS:
            with self.subTest(method=method, url=url):
                headers = {"x-amz-content-sha256": payload, "x-amz-date": "20261102T090000Z"}
                self.assertEqual(sigv4.authorization(method, url, headers, payload, (*MEDIA_KEY, region), "s3", NOW),
                                 media.authorization(method, url, payload, *MEDIA_KEY, region, NOW))

    def test_defect_another_service_disagrees(self):
        method, url, payload, region = MEDIA_VECTORS[0]
        headers = {"x-amz-content-sha256": payload, "x-amz-date": "20261102T090000Z"}
        self.assertNotEqual(sigv4.authorization(method, url, headers, payload, (*MEDIA_KEY, region), "ec2", NOW),
                            media.authorization(method, url, payload, *MEDIA_KEY, region, NOW))


class PresignRefusals(unittest.TestCase):
    def test_defect_expires_out_of_range_or_not_an_integer(self):
        for expires in (0, 604801, "60", -1, 60.0, True):
            with self.subTest(expires=expires), self.assertRaises(ValueError):
                sigv4.presign("GET", S3_URL, S3_KEY, S3_TIME, expires)

    def test_defect_a_url_with_a_query_or_a_fragment(self):
        for url in (S3_URL + "?a=1", S3_URL + "#f", S3_URL + "?"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                sigv4.presign("GET", url, S3_KEY, S3_TIME, 60)

    def test_twin_seven_days_and_one_second_are_accepted(self):
        for expires in (604800, 1):
            with self.subTest(expires=expires):
                link = sigv4.presign("GET", S3_URL, S3_KEY, S3_TIME, expires)
                self.assertTrue(link.startswith(S3_URL + "?"))
                self.assertIn(f"&X-Amz-Expires={expires}&", link)


class Encoding(unittest.TestCase):
    def test_twin_a_space_and_a_tilde_in_the_path(self):
        link = sigv4.presign("GET", "https://examplebucket.s3.amazonaws.com/a b~c.txt", S3_KEY, S3_TIME, 60)
        self.assertTrue(link.startswith("https://examplebucket.s3.amazonaws.com/a%20b~c.txt?"))
        self.assertEqual(sigv4._quote("/a b~c.txt", safe="/-_.~"), "/a%20b~c.txt")

    def test_defect_the_path_is_part_of_the_signature(self):
        plain = sigv4.presign("GET", "https://examplebucket.s3.amazonaws.com/a-b~c.txt", S3_KEY, S3_TIME, 60)
        spaced = sigv4.presign("GET", "https://examplebucket.s3.amazonaws.com/a b~c.txt", S3_KEY, S3_TIME, 60)
        self.assertNotEqual(signature(plain), signature(spaced))

    def test_twin_form_sorts_and_encodes(self):
        params = {"Version": "2016-11-15", "Action": "DescribeInstances", "Filter.1.Name": "a b/~"}
        self.assertEqual(sigv4.form(params),
                         "Action=DescribeInstances&Filter.1.Name=a%20b%2F~&Version=2016-11-15")
        self.assertEqual(sigv4.form([("b", "2"), ("a", "z"), ("a", "y")]), "a=y&a=z&b=2")

    def test_twin_signing_key_chain(self):
        # The signing documentation's derived key for the IAM secret, 20120215, us-east-1, iam.
        self.assertEqual(sigv4.signing_key(IAM_KEY[1], "20120215", "us-east-1", "iam").hex(),
                         "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d")


if __name__ == "__main__":
    unittest.main()
