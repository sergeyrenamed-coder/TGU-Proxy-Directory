"""Offline regression tests for bounded source stream validation."""
import unittest
from io import BytesIO
from unittest.mock import patch

from scripts import music_stream_check as m


class FakeResponse:
    status = 206
    headers = {"Content-Type": "audio/mpeg"}

    def __init__(self, payload=b"ID3\x03\x00\x00"):
        self.body = BytesIO(payload)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.body.read(n)
    def geturl(self):
        return "https://prod-1.storage.jamendo.com/something"


class FakeOpener:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
    def open(self, request, timeout):
        assert request.get_header("Range") == "bytes=0-1023"
        assert timeout == m.TIMEOUT
        return self.response


class Checks(unittest.TestCase):
    def setUp(self):
        self.track = {
            "id": "openverse:test", "provider": "openverse",
            "title": "Пример", "artist": "Автор", "language": "unknown",
            "genre": "rock", "license": "by-nc",
            "license_url": "https://creativecommons.org/licenses/by-nc/3.0/",
            "source_page": "https://www.jamendo.com/track/123",
            "audio_url": "https://prod-1.storage.jamendo.com/?trackid=123&format=mp32",
        }

    def test_host_allowlist(self):
        self.assertTrue(m.allowed_host(self.track["audio_url"]))
        for url in (
            "http://prod-1.storage.jamendo.com/a",
            "https://prod-1.storage.jamendo.com.evil.test/a",
            "https://127.0.0.1/test", "https://localhost/a",
            "https://user:password@prod-1.storage.jamendo.com/a",
            "https://prod-1.storage.jamendo.com:8080/a",
            "file:///etc/passwd",
        ):
            self.assertFalse(m.allowed_host(url))

    def test_id3_audio_accepted_and_marked_unreviewed(self):
        track, status = m.probe(self.track, FakeOpener())
        self.assertEqual(status["status"], "reachable")
        self.assertTrue(track["stream_verified"])
        self.assertFalse(track["rights_reviewed"])
        self.assertFalse(track["network_ru_verified"])
        self.assertEqual(track["title"], "Пример")

    def test_html_and_unsupported_license_rejected(self):
        class FakeHtml(FakeResponse):
            headers = {"Content-Type": "text/html"}
        record, status = m.probe(self.track, FakeOpener(FakeHtml()))
        self.assertIsNone(record)
        self.assertEqual(status["status"], "unavailable")
        self.assertIsNone(m.probe({**self.track, "license": "unknown"}, FakeOpener())[0])

    def test_broken_mp3_rejected(self):
        record, status = m.probe(self.track, FakeOpener(FakeResponse(b"this is not MP3")))
        self.assertIsNone(record)
        self.assertEqual(status["status"], "unavailable")

    def test_zero_results_safe(self):
        result = m.validate({"tracks": []})
        self.assertEqual(result["track_count"], 0)
        self.assertEqual(result["verification"]["russian_network"], "not_checked")


if __name__ == "__main__":
    unittest.main()
