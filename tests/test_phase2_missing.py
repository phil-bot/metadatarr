"""Tests fuer Zielsprachen, Platzhalterregeln und ehrliche Feldstatus."""
import os
import tempfile
import unittest
from unittest.mock import patch

from core.migrations import migrate_database
from core import missing


class MissingMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        migrate_database(self.db)
        self.patch_db = patch.object(missing, "DB_PATH", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_global_and_series_target_languages_and_reset(self):
        self.assertEqual(missing.get_target_languages(), ["de", "en"])
        self.assertEqual(missing.set_target_languages(["fr", "ja-JP"]), ["fr", "ja-JP"])
        self.assertEqual(missing.set_target_languages(["de"], series_id=42), ["de"])
        self.assertTrue(missing.has_series_override(42))
        self.assertEqual(missing.set_target_languages(None, series_id=42), ["fr", "ja-JP"])
        self.assertFalse(missing.has_series_override(42))

    def test_status_distinguishes_empty_placeholder_language_and_unknown(self):
        rules = missing.list_rules()
        self.assertEqual(missing.evaluate_value("episode.title", None, "de", provided=True, rules=rules)["status"], "missing_empty")
        self.assertEqual(missing.evaluate_value("episode.title", "TBA", "de", rules=rules)["status"], "placeholder")
        self.assertEqual(missing.evaluate_value("episode.title", "A title", "de", source_language=None, rules=rules)["status"], "not_checkable")
        self.assertEqual(missing.evaluate_value("episode.title", "A title", "de", source_language="en", rules=rules)["status"], "missing_language")
        self.assertEqual(missing.evaluate_value("episode.title", "A title", "en", source_language="en-US", rules=rules)["status"], "present")
        self.assertEqual(missing.evaluate_value("series.overview", None, "de", provided=False, rules=rules)["status"], "not_provided")

    def test_custom_rules_are_separate_from_read_only_environment_seed(self):
        env_rules = [rule for rule in missing.list_rules() if rule["origin"] == "env-seed"]
        result = missing.replace_ui_rules([{"field": "episode.overview", "language": "de", "kind": "exact",
                                             "pattern": "wird ergaenzt", "enabled": True}])
        self.assertEqual([rule for rule in result if rule["origin"] == "env-seed"], env_rules)
        self.assertTrue(missing.is_placeholder("episode.overview", "  Wird Ergaenzt ", "de", result))
        self.assertFalse(missing.is_placeholder("episode.overview", "wird ergaenzt", "en", result))
        self.assertEqual(len([rule for rule in result if rule["origin"] == "ui"]), 1)
        empty_rules = missing.replace_ui_rules([{"field": "episode.overview", "kind": "empty", "pattern": ""}])
        empty_state = missing.evaluate_value("episode.overview", None, "de", rules=empty_rules)
        self.assertEqual(empty_state["status"], "placeholder")

    def test_unsafe_or_invalid_regex_is_rejected(self):
        with self.assertRaises(ValueError):
            missing.replace_ui_rules([{"field": "episode.title", "kind": "regex", "pattern": "(a+)+$"}])
        with self.assertRaises(ValueError):
            missing.replace_ui_rules([{"field": "bad.field", "kind": "exact", "pattern": "x"}])

    def test_inventory_never_guesses_sonarr_text_language(self):
        data = {"seriesTotal": 1, "series": [{"id": 8, "title": "English title", "overview": "English overview",
                                               "originalLanguage": "German", "seasons": [{"seasonNumber": 1,
                                               "episodes": [{"id": 9, "title": "Title", "overview": "Overview"}]}]}]}
        payload = missing.annotate_inventory(data, source_language=None)
        state = payload["series"][0]["fieldStatus"]["series.title"]["de"]
        self.assertEqual(state["status"], "not_checkable")
        self.assertEqual(state["sourceLanguage"], None)
        self.assertEqual(payload["series"][0]["seasons"][0]["episodes"][0]["fieldStatus"]["episode.overview"]["en"]["status"], "not_checkable")
