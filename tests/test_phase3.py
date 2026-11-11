"""Phase-3 migration, provider, ticket API, plugin boundary, and action safety tests."""
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from api import router
from core.migrations import MigrationError, migrate_database, migrate_phase3_database
from net.http_client import HTTPResult
from net.redaction import REDACTOR


class HandlerStub:
    def __init__(self, headers=None, body=b""):
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.response = None

    def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        self.response = (status, body, content_type, headers or {})


class FakeHTTP:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def request(self, url, *, profile, method="GET", headers=None, body=None, follow_redirects=True):
        call = {"url": url, "profile": profile, "method": method, "headers": dict(headers or {}),
                "body": body, "follow_redirects": follow_redirects}
        self.calls.append(call)
        return self.responder(call)


def response(status=200, payload=None, headers=None, cookies=(), url="https://fixture.invalid/"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload or {}).encode("utf-8")
    return HTTPResult(status, headers or {"Content-Type": "application/json"}, body, url, tuple(cookies))


class Phase3DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "phase3.db")
        migrate_database(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_legacy_rows(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        with con:
            con.execute("INSERT INTO suggestion(id,series_id,season,episode,source,source_key,title_de,title_en,description,link,level,status,created,marked) "
                        "VALUES(51,7,2,3,'tmdb','66:3','Deutscher Titel','English Title','Beschreibung','https://source.invalid/ep','hoch','ausgew\u00e4hlt',1234,0)")
            con.execute("INSERT INTO suggestion(id,series_id,season,episode,source,source_key,title_de,title_en,description,link,level,status,created,marked) "
                        "VALUES(52,7,2,4,'mediathek','media-4','Noch ein Titel','','','https://source.invalid/ep4','mittel','eingereicht',1235,0)")
            con.execute("INSERT INTO ticket(id,series_id,series_title,tvdb_id,season,lang,status,body,body_html,hints,created,updated,tvdb_no,tvdb_status) "
                        "VALUES(91,7,'Serie',777,2,'de','eingereicht','Tickettext','<p>Text</p>','[]',1200,1300,42840,'Open')")
            con.execute("INSERT INTO ticket_item(ticket_id,suggestion_id,season,episode,title) VALUES(91,51,2,3,'Deutscher Titel')")
            con.execute("INSERT INTO cache(k,fetched,body) VALUES('legacy-key',1200,'{}')")
        before = {}
        for table in ("cache", "suggestion", "ticket", "ticket_item"):
            before[table] = con.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        columns = {table: [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
                   for table in ("cache", "suggestion", "ticket", "ticket_item")}
        con.close()
        return before, columns

    def test_phase3_backfill_preserves_ids_statuses_and_relationships(self):
        before, columns = self._seed_legacy_rows()
        self.assertEqual(migrate_phase3_database(self.db), 2)
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        for table in ("cache", "suggestion", "ticket", "ticket_item"):
            self.assertEqual(columns[table], [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')])
            self.assertEqual([tuple(row) for row in before[table]],
                             [tuple(row) for row in con.execute(f'SELECT * FROM "{table}" ORDER BY 1')])
        candidates = con.execute("SELECT suggestion_id,field_key,language,value FROM candidate_value WHERE suggestion_id IN (51,52) ORDER BY suggestion_id,field_key,language").fetchall()
        self.assertEqual(len(candidates), 4)
        self.assertEqual({row[0] for row in candidates}, {51, 52})
        state = con.execute("SELECT cs.status,cs.marked,cs.legacy_status FROM candidate_state cs JOIN candidate_value cv ON cv.id=cs.candidate_id WHERE cv.suggestion_id=51 AND cv.field_key='episode.title' AND cv.language='de'").fetchone()
        self.assertEqual(tuple(state), ("offen", 1, "ausgew\u00e4hlt"))
        state2 = con.execute("SELECT cs.legacy_status FROM candidate_state cs JOIN candidate_value cv ON cv.id=cs.candidate_id WHERE cv.suggestion_id=52").fetchone()
        self.assertEqual(state2[0], "eingereicht")
        self.assertEqual(con.execute("SELECT suggestion_id FROM ticket_item WHERE ticket_id=91").fetchone()[0], 51)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM ticket_status_event WHERE ticket_id=91").fetchone()[0], 1)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertIsNotNone(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_preview'").fetchone())
        con.close()
        self.assertIsNone(migrate_phase3_database(self.db))
        self.assertIsNone(migrate_database(self.db))
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM candidate_value WHERE suggestion_id IN (51,52)").fetchone()[0], 4)
        con.close()
        backups = list((Path(self.temp.name) / "backups").glob("tba-vor-v2-*.db"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertIsNone(backup.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_preview'").fetchone())
        backup.close()

    def test_phase3_failure_rolls_back_ddl_and_keeps_verified_backup(self):
        original = __import__("core.migrations", fromlist=["_apply_phase3_schema"])._apply_phase3_schema
        def apply_then_fail(con, now):
            original(con, now)
            raise sqlite3.OperationalError("injected migration failure")
        with patch("core.migrations._apply_phase3_schema", side_effect=apply_then_fail):
            with self.assertRaises(MigrationError):
                migrate_phase3_database(self.db)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertNotIn("action_preview", {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertNotIn("machine_translated", {row[1] for row in con.execute("PRAGMA table_info(candidate_value)")})
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        self.assertEqual(len(list((Path(self.temp.name) / "backups").glob("tba-vor-v2-*.db"))), 1)

    def test_v2_database_verifier_checks_phase3_checksum(self):
        migrate_phase3_database(self.db)
        con = sqlite3.connect(self.db)
        with con:
            con.execute("UPDATE schema_migration SET checksum='corrupt' WHERE version=2")
        con.close()
        with self.assertRaises(MigrationError):
            migrate_database(self.db)

    def test_phase3_backup_failure_leaves_v1_database_untouched(self):
        def fail_backup(_path, _version):
            raise OSError("backup failure")
        with self.assertRaises(MigrationError):
            migrate_phase3_database(self.db, backup_func=fail_backup)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertNotIn("action_preview", {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        con.close()


class ProviderAndCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "providers.db")
        migrate_database(self.db)
        migrate_phase3_database(self.db)
        import speicher
        self.db_patch = patch.object(speicher, "DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def series():
        return {"id": 7, "title": "Test Serie", "year": 2020, "language": "German", "tvdbId": 999,
                "tba": [{"id": 7001, "season": 1, "episode": 1, "airDate": "2020-01-01"}]}

    def test_mediathek_provider_maps_local_fixture(self):
        from plugins.sources import MediathekSource
        fake = FakeHTTP(lambda call: response(payload={"result": {"results": [
            {"channel": "ARD", "topic": "Test Serie", "title": "Test Serie (S01/E01) Pilot",
             "description": "Mediathek Beschreibung", "timestamp": 1577880000,
             "url_website": "https://example.invalid/item"}]}}))
        context = __import__("plugins.sources", fromlist=["context_for_source"]).context_for_source("mediathek", fake)
        rows = MediathekSource().fetch(context, {"series": self.series(), "episodes": self.series()["tba"], "force": True})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "mediathek")
        self.assertEqual(rows[0]["title_de"], "Test Serie (S01/E01) Pilot")
        self.assertEqual(fake.calls[0]["method"], "POST")
        self.assertEqual(fake.calls[0]["profile"].source_id, "mediathek")

    def test_tmdb_search_accepts_scoped_secret_instead_of_environment_only(self):
        import vorschlaege
        with patch("vorschlaege.source_credentials_configured", return_value=True), \
             patch.object(vorschlaege, "_source_rows", return_value=[]) as fetch, \
             patch.object(vorschlaege, "store_suggestions"):
            count, note = vorschlaege.search_series(self.series(), "tmdb")
        self.assertEqual((count, note), (0, ""))
        fetch.assert_called_once()

    def test_tmdb_provider_uses_mocked_json_and_matches_episode(self):
        from plugins.sources import TMDBSource
        class Secrets:
            def get(self, _slot):
                return "test-tmdb-key-not-real"
        def responder(call):
            path = urlsplit(call["url"]).path
            if path.endswith("/search/tv"):
                return response(payload={"results": [{"id": 77, "name": "Test Serie", "original_name": "Test Serie", "first_air_date": "2020-01-01"}]}, url=call["url"])
            if path.endswith("/tv/77"):
                return response(payload={"number_of_seasons": 1}, url=call["url"])
            if path.endswith("/season/1") and "en-US" in call["url"]:
                return response(payload={"episodes": [{"episode_number": 1, "name": "Pilot EN"}]}, url=call["url"])
            if path.endswith("/season/1"):
                return response(payload={"episodes": [{"episode_number": 1, "name": "Pilot DE", "overview": "Originale Beschreibung", "air_date": "2020-01-01"}]}, url=call["url"])
            return response(payload={})
        fake = FakeHTTP(responder)
        from plugins.registry import SourceContext
        rows = TMDBSource().fetch(SourceContext(fake, None, Secrets()),
                                  {"series": self.series(), "episodes": self.series()["tba"], "force": True})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "tmdb")
        self.assertEqual(rows[0]["title_en"], "Pilot EN")
        self.assertEqual(len(fake.calls), 4)
        self.assertTrue(all(call["profile"].source_id == "tmdb" for call in fake.calls))

    def test_fernsehserien_html_fixture_parser(self):
        from plugins.sources import FernseherSerienSource
        index = (b'<h1>Test Serie</h1><a href="/test-serie/episodenguide/staffel-1/77">Staffel</a>')
        season = (b'<p>1. Pilot</p><p>Staffel 1, Folge 1 (Folgentitel)</p>'
                  b'<p>Deutsche TV-Premiere: 01.01.2020 ARD</p>'
                  b'<p>Dies ist eine ausreichend lange Beschreibung der ersten Testfolge fuer den Parser.</p>')
        def responder(call):
            payload = index if call["url"].endswith("/episodenguide") else season
            return response(payload=payload, headers={"Content-Type": "text/html"}, url=call["url"])
        fake = FakeHTTP(responder)
        from plugins.registry import SourceContext
        rows = FernseherSerienSource().fetch(SourceContext(fake, None, None),
            {"series": self.series(), "episodes": self.series()["tba"], "force": True})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title_de"], "Pilot")
        self.assertEqual(rows[0]["description"].startswith("Dies ist"), True)
        self.assertEqual([call["profile"].source_id for call in fake.calls], ["fernsehserien", "fernsehserien"])

    def test_deepl_is_derived_and_machine_translation_is_traceable(self):
        import speicher
        from core.candidates import record_translation, sync_suggestion
        from plugins.sources import DeepLSource
        from plugins.registry import SourceContext
        now = time.time()
        with patch.object(speicher, "DB_PATH", self.db):
            sid = speicher.db_insert(
                "INSERT INTO suggestion(series_id,season,episode,source,source_key,title_de,title_en,description,level,status,created) "
                "VALUES(7,1,1,'tmdb','77:1','Titel DE','','Beschreibung','hoch','offen',?)", (now,))
            sync_suggestion(sid)
            origin = speicher.db_exec("SELECT id FROM candidate_value WHERE suggestion_id=? AND field_key='episode.title' AND language='de'", (sid,), fetch=True)[0]["id"]
            candidate = speicher.db_exec("SELECT * FROM candidate_value WHERE id=?", (origin,), fetch=True)[0]
            class Secrets:
                def get(self, _slot):
                    return "test-deepl-key-not-real"
            fake = FakeHTTP(lambda call: response(payload={"translations": [{"text": "Translated title"}]}, url=call["url"]))
            derived = DeepLSource().fetch(SourceContext(fake, None, Secrets()),
                {"candidate": dict(candidate), "targetLanguage": "en"})
            self.assertEqual(derived[0]["source_id"], "deepl")
            self.assertTrue(derived[0]["machine_translated"])
            self.assertEqual(derived[0]["derived_from_id"], origin)
            translated_id = record_translation(origin, derived[0]["value"], "en")
            stored = speicher.db_exec("SELECT source_id,machine_translated,derived_from_id,value FROM candidate_value WHERE id=?", (translated_id,), fetch=True)[0]
            self.assertEqual(tuple(stored), ("deepl", 1, origin, "Translated title"))
            self.assertEqual(fake.calls[0]["method"], "POST")
            self.assertNotIn("test-deepl-key-not-real", REDACTOR.redact_text("DeepL-Auth-Key test-deepl-key-not-real"))

    def test_candidate_state_tracks_legacy_selection_changes(self):
        import speicher
        con = sqlite3.connect(self.db)
        with con:
            con.execute("INSERT INTO suggestion(id,series_id,season,episode,source,source_key,title_de,title_en,description,link,level,status,created,marked) "
                        "VALUES(61,7,1,1,'tmdb','77:1','Pilot','Pilot EN','Beschreibung','https://source.invalid/1','hoch','offen',?,0)",
                        (time.time(),))
        con.close()
        from core.candidates import sync_suggestion
        sync_suggestion(61)
        from vorschlaege import suggestion_action
        suggestion_action(61, {"aktion": ["toggle"]})
        state = speicher.db_exec("SELECT cs.status,cs.marked FROM candidate_state cs JOIN candidate_value cv ON cv.id=cs.candidate_id WHERE cv.suggestion_id=61 AND cv.field_key='episode.title' AND cv.language='de'", fetch=True)[0]
        self.assertEqual((state["status"], state["marked"]), ("offen", 1))
        suggestion_action(61, {"aktion": ["discard"]})
        state = speicher.db_exec("SELECT cs.status,cs.marked FROM candidate_state cs JOIN candidate_value cv ON cv.id=cs.candidate_id WHERE cv.suggestion_id=61 AND cv.field_key='episode.title' AND cv.language='de'", fetch=True)[0]
        self.assertEqual((state["status"], state["marked"]), ("verworfen", 0))
        suggestion_action(61, {"aktion": ["reopen"]})
        state = speicher.db_exec("SELECT cs.status,cs.marked FROM candidate_state cs JOIN candidate_value cv ON cv.id=cs.candidate_id WHERE cv.suggestion_id=61 AND cv.field_key='episode.title' AND cv.language='de'", fetch=True)[0]
        self.assertEqual((state["status"], state["marked"]), ("offen", 0))

    def test_nightly_sources_follow_enabled_allowlist_and_run_is_read_only(self):
        import speicher
        from plugins.sources import nightly_source_keys
        self.assertEqual(nightly_source_keys(), ("mediathek", "tmdb"))
        speicher.db_exec("UPDATE source_config SET include_in_nightly=0 WHERE id='tmdb'")
        self.assertEqual(nightly_source_keys(), ("mediathek",))
        import nachtlauf
        series = {"id": 7, "title": "Test Serie", "tba": [{"season": 1, "episode": 1, "airDate": "2020-01-01"}]}
        with patch.object(nachtlauf, "SONARR_URL", "http://sonarr.fixture"), \
             patch.object(nachtlauf, "SONARR_API_KEY", "canary-sonarr-key"), \
             patch.object(nachtlauf, "get_data", return_value={"series": [series]}), \
             patch.object(nachtlauf, "nightly_source_keys", return_value=("mediathek", "fernsehserien")), \
             patch.object(nachtlauf, "search_chain", return_value=("Mediathek 1", "")) as search, \
             patch.object(nachtlauf, "cache_put"), \
             patch.object(nachtlauf, "TVDB_STATUS_NIGHTLY", False), \
             patch("tvdb.submit_ticket") as submit:
            nachtlauf.run_nightly()
        search.assert_called_once_with(series, None, force=True, stages=("mediathek",))
        submit.assert_not_called()

    def test_source_and_action_plugin_types_remain_separate(self):
        from plugins.registry import ACTIONS, SOURCES, ActionPlugin, SourcePlugin, PluginRegistrationError
        import plugins.actions
        self.assertEqual(set(SOURCES.keys()), {"mediathek", "tmdb", "fernsehserien", "deepl"})
        self.assertEqual(set(ACTIONS.keys()), {"tvdb-ticket"})
        self.assertTrue(all(issubclass(SOURCES.get(key), SourcePlugin) for key in SOURCES.keys()))
        self.assertTrue(all(issubclass(ACTIONS.get(key), ActionPlugin) for key in ACTIONS.keys()))
        with self.assertRaises(PluginRegistrationError):
            ACTIONS.register(SOURCES.get("mediathek"))


class TicketsAndActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "api.db")
        migrate_database(self.db)
        migrate_phase3_database(self.db)
        import speicher
        self.db_patch = patch.object(speicher, "DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp.cleanup)
        con = sqlite3.connect(self.db)
        try:
            with con:
                con.execute("UPDATE action_config SET enabled=1 WHERE plugin_key='tvdb-ticket'")
                con.execute("INSERT INTO ticket(id,series_id,series_title,tvdb_id,season,lang,status,body,body_html,hints,created,updated) "
                            "VALUES(31,7,'Test Serie',777,1,'de','erstellt','Tickettext','<p>Ticket</p>','[]',?,?)",
                            (time.time(), time.time()))
                con.execute("INSERT INTO ticket_item(ticket_id,suggestion_id,season,episode,title) VALUES(31,88,1,1,'Pilot')")
        finally:
            con.close()

    def request(self, method, path, payload=None):
        if method == "GET":
            handler = HandlerStub({})
        else:
            sid, token, _ = router._SESSIONS.issue(None)
            body = json.dumps(payload or {}).encode("utf-8")
            host = "metadatarr.test:8080"
            headers = {"Host": host, "Origin": "http://" + host,
                       "Cookie": "metadatarr_session=" + sid, "X-CSRF-Token": token,
                       "Content-Type": "application/json", "Content-Length": str(len(body))}
            handler = HandlerStub(headers, body)
        self.assertTrue(router.dispatch(handler, method, path))
        return handler

    def preview(self):
        with patch("api.phase3._env_configured", return_value=True):
            result = self.request("POST", "/api/v1/actions/tvdb-ticket/preview", {"ticketId": 31})
        self.assertEqual(result.response[0], 200)
        return json.loads(result.response[1])

    def test_ticket_api_lists_and_exports_local_template(self):
        listed = self.request("GET", "/api/v1/tickets")
        self.assertEqual(listed.response[0], 200)
        self.assertEqual(json.loads(listed.response[1])["items"][0]["id"], 31)
        detail = self.request("GET", "/api/v1/tickets/31")
        self.assertEqual(json.loads(detail.response[1])["item"]["body"], "Tickettext")
        exported = self.request("GET", "/api/v1/tickets/31/export")
        self.assertEqual(exported.response[1], "Tickettext")

    def test_preview_has_no_external_effect_and_missing_confirmation_is_rejected(self):
        import tvdb
        with patch("api.phase3._env_configured", return_value=True), patch.object(tvdb, "submit_ticket") as submit:
            preview = self.preview()
            self.assertEqual(preview["confirmationRequired"], True)
            self.assertEqual(preview["preview"]["ticketId"], 31)
            self.assertIn("support.thetvdb.com", preview["preview"]["host"])
            denied = self.request("POST", "/api/v1/actions/tvdb-ticket/execute",
                {"previewId": preview["previewId"], "confirm": False, "idempotencyKey": "missing-confirm-0001"})
            self.assertEqual(denied.response[0], 400)
            self.assertEqual(json.loads(denied.response[1])["code"], "confirmation_required")
            submit.assert_not_called()
        import speicher
        self.assertEqual(speicher.db_exec("SELECT COUNT(*) AS n FROM action_execution", fetch=True)[0]["n"], 0)

    def test_changed_ticket_after_preview_is_rejected_without_submission(self):
        import tvdb
        preview = self.preview()
        import speicher
        speicher.db_exec("UPDATE ticket SET body='changed after preview' WHERE id=31")
        with patch("api.phase3._env_configured", return_value=True), patch.object(tvdb, "submit_ticket") as submit:
            denied = self.request("POST", "/api/v1/actions/tvdb-ticket/execute",
                {"previewId": preview["previewId"], "confirm": True, "idempotencyKey": "changed-preview-key-001"})
        self.assertEqual(denied.response[0], 409)
        self.assertEqual(json.loads(denied.response[1])["code"], "preview_changed")
        submit.assert_not_called()
        self.assertEqual(speicher.db_exec("SELECT COUNT(*) AS n FROM action_execution", fetch=True)[0]["n"], 0)

    def test_execute_is_idempotent_and_redacts_action_failure(self):
        import tvdb
        import plugins.actions as action_module
        preview = self.preview()
        credentials = {"user": "tvdb-user-canary", "password": "tvdb-password-canary"}
        with patch("api.phase3._env_configured", return_value=True), \
             patch.object(action_module.ScopedActionSecrets, "get", side_effect=lambda slot: credentials[slot]), \
             patch.object(tvdb, "submit_ticket", return_value=42840) as submit:
            first = self.request("POST", "/api/v1/actions/tvdb-ticket/execute",
                {"previewId": preview["previewId"], "confirm": True, "idempotencyKey": "idempotency-key-0001"})
            self.assertEqual(first.response[0], 200)
            self.assertEqual(json.loads(first.response[1])["tvdbNumber"], 42840)
            second = self.request("POST", "/api/v1/actions/tvdb-ticket/execute",
                {"previewId": preview["previewId"], "confirm": True, "idempotencyKey": "idempotency-key-0001"})
            self.assertEqual(second.response[0], 409)
            self.assertEqual(json.loads(second.response[1])["code"], "idempotency_reused")
            self.assertEqual(submit.call_count, 1)
        preview2 = self.preview_for_new_ticket()
        secret = "tvdb-failure-secret-canary"
        REDACTOR.register(secret)
        with patch("api.phase3._env_configured", return_value=True), \
             patch.object(action_module.ScopedActionSecrets, "get", side_effect=lambda slot: credentials[slot]), \
             patch.object(tvdb, "submit_ticket", side_effect=RuntimeError("password=" + secret)):
            failed = self.request("POST", "/api/v1/actions/tvdb-ticket/execute",
                {"previewId": preview2["previewId"], "confirm": True, "idempotencyKey": "idempotency-key-0002"})
            self.assertEqual(failed.response[0], 502)
            self.assertNotIn(secret, failed.response[1])
            self.assertEqual(json.loads(failed.response[1])["code"], "action_result_unknown")

    def preview_for_new_ticket(self):
        import speicher
        con = sqlite3.connect(self.db)
        try:
            with con:
                now = time.time()
                con.execute("INSERT INTO ticket(id,series_id,series_title,tvdb_id,season,lang,status,body,body_html,hints,created,updated) "
                            "VALUES(32,7,'Test Serie',777,1,'en','erstellt','English body','<p>English</p>','[]',?,?)", (now, now))
        finally:
            con.close()
        with patch("api.phase3._env_configured", return_value=True):
            result = self.request("POST", "/api/v1/actions/tvdb-ticket/preview", {"ticketId": 32})
        self.assertEqual(result.response[0], 200)
        return json.loads(result.response[1])

    def test_local_ticket_creation_api_does_not_submit(self):
        import speicher
        import sonarr
        from unittest.mock import patch as mock_patch
        now = time.time()
        suggestion_id = speicher.db_insert(
            "INSERT INTO suggestion(series_id,season,episode,source,source_key,title_de,title_en,description,link,level,status,created,marked) "
            "VALUES(7,1,1,'mediathek','local-1','Pilot','','Deutsche Beschreibung','https://example.invalid/1','hoch','offen',?,1)", (now,))
        speicher.db_exec("UPDATE suggestion SET marked=1 WHERE id=?", (suggestion_id,))
        inventory = {"series": [{"id": 7, "title": "Test Serie", "tvdbId": 777, "tba": []}]}
        with mock_patch.object(sonarr, "get_data", return_value=inventory), mock_patch("tvdb.submit_ticket") as submit:
            created = self.request("POST", "/api/v1/tickets", {"seriesId": 7, "season": 1, "lang": "de"})
            self.assertEqual(created.response[0], 201)
            item = json.loads(created.response[1])["item"]
            self.assertIn("Pilot", item["body"])
            submit.assert_not_called()


class TVDBTransportTests(unittest.TestCase):
    def test_tvdb_attrap_uses_protected_client_session_without_live_network(self):
        import tvdb
        requested = []
        def responder(call):
            requested.append(call)
            url = call["url"]
            if url.endswith("/login.php") and call["method"] == "GET":
                return response(422, b'<input name="__CSRFToken__" value="csrf-fixture">',
                                {"Content-Type": "text/html"}, ("session=fixture-cookie; Path=/; HttpOnly",), url)
            if url.endswith("/login.php") and call["method"] == "POST":
                return response(302, b"", {"Location": "/open.php"}, ("auth=fixture-auth; Path=/; HttpOnly",), url)
            if url.endswith("/open.php") and call["method"] == "GET":
                return response(200, b'<input name="__CSRFToken__" value="form-token">', {"Content-Type": "text/html"}, (), url)
            if "/ajax.php/form/help-topic/" in url:
                form = '<label>Issue Summary</label><input name="subjectField"><textarea name="message"></textarea>'
                return response(200, {"html": form}, url=url)
            if url.endswith("/open.php") and call["method"] == "POST":
                return response(302, b"", {"Location": "/tickets.php?id=9001"}, (), url)
            return response(404, {"error": "not found"}, url=url)
        fake = FakeHTTP(responder)
        number = tvdb.submit_ticket("[GERMAN] Test", "<p>Safe fixture</p>", user="mock-user",
                                    password="mock-password", base_url="https://support.thetvdb.test",
                                    http_client=fake)
        self.assertEqual(number, 9001)
        self.assertEqual([call["method"] for call in requested], ["GET", "POST", "GET", "GET", "POST"])
        self.assertTrue(all(call["follow_redirects"] is False for call in requested))
        self.assertTrue(all(call["profile"].source_id == "tvdb-ticket" for call in requested))
        self.assertIn("session=fixture-cookie", requested[1]["headers"].get("Cookie", ""))
        self.assertIn("auth=fixture-auth", requested[-1]["headers"].get("Cookie", ""))
        self.assertTrue(all(not call["profile"].allow_private for call in requested))


if __name__ == "__main__":
    unittest.main()
