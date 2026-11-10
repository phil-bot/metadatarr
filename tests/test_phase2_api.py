"""Lokale Einstellungs-API: CSRF, Same-Origin, enge PUTs und keine Sonarr-Schreibpfade."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from api import router
from core.migrations import migrate_database


class HandlerStub:
    def __init__(self, headers, body):
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.response = None
    def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        self.response = (status, body, content_type, headers or {})


class LocalSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        migrate_database(self.db)
        self.db_patch = patch("core.missing.DB_PATH", self.db)
        self.db_patch.start()
        self.audit_patch = patch("core.audit.record")
        self.audit_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.audit_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def request(self, method, path, payload, *, valid_origin=True, valid_csrf=True):
        sid, token, _new = router._SESSIONS.issue(None)
        host = "metadatarr.test:8080"
        headers = {
            "Host": host,
            "Origin": "http://" + host if valid_origin else "http://evil.test",
            "Cookie": "metadatarr_session=" + sid,
            "X-CSRF-Token": token if valid_csrf else "wrong",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        handler = HandlerStub(headers, payload)
        handled = router.dispatch(handler, method, path)
        return handled, handler

    def test_global_and_per_series_target_language_put_and_reset(self):
        body = json.dumps({"languages": ["fr", "ja-JP"]}).encode()
        handled, response = self.request("PUT", "/api/v1/target-languages", body)
        self.assertTrue(handled)
        self.assertEqual(response.response[0], 200)
        self.assertEqual(json.loads(response.response[1])["languages"], ["fr", "ja-JP"])

        body = json.dumps({"languages": ["de"]}).encode()
        _, response = self.request("PUT", "/api/v1/series/33/target-languages", body)
        self.assertEqual(response.response[0], 200)
        self.assertEqual(json.loads(response.response[1])["languages"], ["de"])

        body = b'{"languages":null}'
        _, response = self.request("PUT", "/api/v1/series/33/target-languages", body)
        self.assertEqual(response.response[0], 200)
        reset = json.loads(response.response[1])
        self.assertFalse(reset["override"])
        self.assertEqual(reset["languages"], ["fr", "ja-JP"])

    def test_put_rules_keeps_environment_seed_read_only(self):
        body = json.dumps({"rules": [{"field": "episode.overview", "language": "de", "kind": "exact",
                                       "pattern": "PLATZHALTER", "enabled": True}]}).encode()
        _, response = self.request("PUT", "/api/v1/placeholder-rules", body)
        self.assertEqual(response.response[0], 200)
        rules = json.loads(response.response[1])["rules"]
        self.assertTrue(any(rule["origin"] == "env-seed" and not rule["editable"] for rule in rules))
        self.assertTrue(any(rule["origin"] == "ui" and rule["field"] == "episode.overview" for rule in rules))

    def test_invalid_rule_rejected_without_modifying_seed(self):
        body = json.dumps({"rules": [{"field": "episode.title", "kind": "regex", "pattern": "(a+)+$"}]}).encode()
        _, response = self.request("PUT", "/api/v1/placeholder-rules", body)
        self.assertEqual(response.response[0], 400)

    def test_csrf_and_origin_protect_local_writes(self):
        body = b'{"languages":["fr"]}'
        _, bad_origin = self.request("PUT", "/api/v1/target-languages", body, valid_origin=False)
        self.assertEqual(bad_origin.response[0], 403)
        _, bad_csrf = self.request("PUT", "/api/v1/target-languages", body, valid_csrf=False)
        self.assertEqual(bad_csrf.response[0], 403)

    def test_non_put_methods_cannot_change_settings(self):
        body = b'{"languages":["fr"]}'
        _, response = self.request("POST", "/api/v1/target-languages", body)
        self.assertEqual(response.response[0], 405)
        from core.missing import get_target_languages
        self.assertEqual(get_target_languages(), ["de", "en"])
