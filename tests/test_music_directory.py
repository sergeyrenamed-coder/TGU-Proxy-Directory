"""Offline tests for the experimental RNMD Music Directory."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import music_directory as md


class TestMusicDirectory(unittest.TestCase):
    def test_secure_links_only(self):
        self.assertEqual(md.safe_https("https://example.org/music.mp3"), "https://example.org/music.mp3")
        for value in ["http://example.org", "javascript:alert(1)",
                      "file:///tmp/music.mp3", "https://user:pass@example.org/a", None]:
            self.assertIsNone(md.safe_https(value))

    def test_openverse_accepts_music_with_license_and_attribution(self):
        payload = {
            "id": "abc", "title": "Пример", "creator": "Автор",
            "url": "https://example.org/audio.mp3",
            "foreign_landing_url": "https://example.org/artist/123",
            "license": "by-sa", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "category": "music", "filetype": "mp3", "genres": ["indie", "rock"]
        }
        track = md.normalize_openverse(payload)
        self.assertEqual(track["id"], "openverse:abc")
        self.assertEqual(track["language"], "unknown")
        self.assertEqual(track["license"], "by-sa")
        self.assertIsNotNone(md.normalize_openverse({**payload, "filetype": "mp32"}))
        self.assertFalse(track["stream_verified"])
        self.assertFalse(track["rights_reviewed"])
        self.assertFalse(track["network_ru_verified"])

    def test_openverse_rejects_non_music_and_missing_rights(self):
        payload = {
            "id": "abc", "title": "Some Song", "creator": "Author",
            "url": "https://example.org/a.mp3",
            "foreign_landing_url": "https://example.org/landing",
            "license": "by", "category": "music", "filetype": "mp3"
        }
        for change in ({"category": "sound_effect"}, {"license": ""},
                       {"url": "http://example.org/s.mp3"},
                       {"filetype": "exe"}, {"foreign_landing_url": ""}):
            self.assertIsNone(md.normalize_openverse({**payload, **change}))

    def test_jamendo_needs_valid_track_metadata(self):
        good = {
            "id": "42", "name": "Song", "artist_name": "Musician",
            "audio": "https://example.net/stream/42",
            "shareurl": "https://www.jamendo.com/track/42",
            "licenses": {"ccurl": "https://creativecommons.org/licenses/by/4.0/"}
        }
        self.assertEqual(md.normalize_jamendo(good)["id"], "jamendo:42")
        self.assertIsNone(md.normalize_jamendo({**good, "audio": ""}))

    @patch.object(md, "collect_openverse")
    def test_duplicate_results_and_provenance(self, mock_get):
        mock_get.return_value = [{
            "id": "openverse:a", "provider": "openverse",
            "title": "Song", "artist": "Artist"
        }]
        result = md.build_candidate_index(["русская", "english"], openverse=True)
        self.assertEqual(result["track_count"], 1)
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(len(result["provider_checks"]), 4)
        self.assertFalse(result["tracks"][0].get("rights_reviewed", False))
        self.assertEqual(result["verification"]["licensing"], "unverified")

    def test_public_catalog_and_web_assets(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("providers", "tracks"):
            canonical = json.loads((root / f"data/music/{name}.json").read_text(encoding="utf-8"))
            pages = json.loads((root / f"docs/data/music/{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(canonical, pages)
            self.assertEqual(canonical["schema_version"], 1)
        providers = json.loads((root / "docs/data/music/providers.json").read_text(encoding="utf-8"))
        self.assertEqual({p["id"] for p in providers["providers"]},
                         {"openverse", "jamendo", "promodj"})
        self.assertFalse(next(p for p in providers["providers"]
                              if p["id"] == "promodj")["enabled"])
        html = (root / "docs/index.html").read_text(encoding="utf-8")
        javascript = (root / "docs/assets/app.js").read_text(encoding="utf-8")
        self.assertIn('id="music-directory"', html)
        self.assertIn('id="proxy-results"', html)
        self.assertIn('=== "music"', javascript)
        self.assertIn('$("music-directory").hidden = !active', javascript)


if __name__ == "__main__":
    unittest.main()
