"""Phase-2-Sonarrtests mit Attrappen; keine echten Dienste und ausschliesslich GET-Aufrufe."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import sonarr
from core.migrations import migrate_database


class SonarrInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        migrate_database(self.db)
        self.patch_db = patch("core.missing.DB_PATH", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_loads_all_languages_and_full_episode_inventory(self):
        series = [
            {"id": 1, "title": "Deutsch", "overview": "Serieninfo DE", "originalLanguage": {"name": "German"},
             "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}]},
            {"id": 2, "title": "English", "overview": "Series overview EN", "originalLanguage": {"name": "English"},
             "seasons": [{"seasonNumber": 1}]},
        ]
        episodes = {
            1: [
                {"id": 10, "seasonNumber": 0, "episodeNumber": 1, "title": "Special", "overview": "Special overview"},
                {"id": 11, "seasonNumber": 1, "episodeNumber": 1, "title": "TBA", "overview": None},
                {"id": 12, "seasonNumber": 1, "episodeNumber": 2, "title": "Benannt", "overview": "Folgeninfo"},
            ],
            2: [{"id": 21, "seasonNumber": 1, "episodeNumber": 1, "title": "Named", "overview": "English episode overview"}],
        }
        calls = []

        def fake_sonarr(path, params=None):
            calls.append((path, params))
            return series if path == "series" else episodes[params["seriesId"]]

        with patch.object(sonarr, "sonarr", side_effect=fake_sonarr), \
                patch.object(sonarr, "SONARR_METADATA_LANGUAGE", ""), \
                patch.object(sonarr, "INCLUDE_SPECIALS", False):
            result = sonarr.load_all()

        self.assertEqual(result["seriesTotal"], 2)
        by_id = {item["id"]: item for item in result["series"]}
        self.assertEqual(set(by_id), {1, 2})  # ORIGINAL_LANGUAGE German darf nicht filtern.
        self.assertEqual(by_id[2]["overview"], "Series overview EN")
        self.assertEqual(by_id[2]["seasons"][0]["episodes"][0]["overview"], "English episode overview")
        self.assertEqual(by_id[1]["seasons"][0]["seasonNumber"], 0)
        self.assertEqual(by_id[1]["seasons"][0]["episodeCount"], 1)  # Specials bleiben im Vollinventar.
        self.assertEqual(len(by_id[1]["seasons"][1]["episodes"]), 2)
        self.assertEqual(sum(1 for path, _ in calls if path == "episode"), 2)
        self.assertEqual(sum(1 for path, _ in calls if path == "series"), 1)
        con = sqlite3.connect(self.db)
        try:
            rows = con.execute("SELECT status,value_hash FROM metadata_snapshot").fetchall()
        finally:
            con.close()
        self.assertTrue(rows)
        self.assertTrue(all(value_hash is None or len(value_hash) == 64 for _status, value_hash in rows))
        self.assertNotIn("English episode overview", str(rows))

    def test_poster_path_supports_sonarr_url_base_and_rejects_remote_url(self):
        source = {"images": [
            {"coverType": "poster", "url": "/sonarr/MediaCover/3/poster.jpg?lastWrite=12",
             "remoteUrl": "https://external.invalid/poster.jpg"}
        ]}
        with patch.object(sonarr, "SONARR_URL", "http://sonarr.local:8989/sonarr"):
            self.assertEqual(sonarr.poster_path(source), "/sonarr/MediaCover/3/poster.jpg?lastWrite=12")
        source["images"] = [{"coverType": "poster", "url": "https://external.invalid/MediaCover/3/poster.jpg"}]
        with patch.object(sonarr, "SONARR_URL", "http://sonarr.local:8989"):
            self.assertEqual(sonarr.poster_path(source), "")

    def test_http_wrapper_is_get_only(self):
        class Client:
            def __init__(self):
                self.methods = []
            def request(self, url, *, profile, method, headers):
                self.methods.append(method)
                return type("Response", (), {"status": 200, "body": b"[]"})()
        client = Client()
        with patch.object(sonarr, "_HTTP", client), patch.object(sonarr, "SONARR_URL", "http://sonarr.local"), \
                patch.object(sonarr, "SONARR_API_KEY", "test-key"):
            self.assertEqual(sonarr.sonarr("series"), [])
        self.assertEqual(client.methods, ["GET"])
