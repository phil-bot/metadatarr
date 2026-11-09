"""Phase-1-Backendtests: Migrationen, Geheimnisse, Audit, Plugins, SafeHTTP, Sonarr und API-Schutz."""
import io
import ipaddress
import json
import os
import secrets as random_secrets
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from api import router
from core.audit import record
from core.migrations import MigrationError, migrate_database
from net.http_client import HTTPClientError, NetworkPolicyError, NetworkProfile, ResponseTooLarge, SafeHTTPClient, _safe_ip
from net.redaction import REDACTOR
from net.secrets import SecretStore, SecretStoreError
from plugins.registry import ActionPlugin, PluginRegistrationError, PluginRegistry, SourcePlugin


class HandlerStub:
    def __init__(self, headers=None, body=b""):
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.response = None

    def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        self.response = (status, body, content_type, headers or {})


def start_local_http_fixture():
    """Lokaler HTTP-Dienst nur für deterministische SafeHTTP-Client-Tests."""
    class FixtureHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        seen = []

        def do_GET(self):
            self.__class__.seen.append((self.path, self.headers.get("X-Api-Key"), self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://other.example:{self.server.server_port}/capture")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/blocked-redirect":
                self.send_response(302)
                self.send_header("Location", f"http://evil.example:{self.server.server_port}/capture")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/slow":
                time.sleep(1.2)
            body = b"ok"
            if self.path in {"/large", "/stream"}:
                body = b"123456"
            self.send_response(200)
            if self.path in {"/headers", "/capture"}:
                self.send_header("X-Api-Key", "response-echo-secret")
                self.send_header("Set-Cookie", "session=raw-cookie-secret")
            if self.path != "/stream":
                self.send_header("Content-Length", str(len(body)))
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, _format, *_args):
            return

    FixtureHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, FixtureHandler


def local_test_client(server):
    def resolver(_host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    return SafeHTTPClient(resolver=resolver)


def local_test_profile(*, hosts=("service.example", "other.example", "evil.example"), max_bytes=100,
                       timeout_seconds=2, max_redirects=3):
    return NetworkProfile("local-fixture", tuple(hosts), allow_private=True,
                          timeout_seconds=timeout_seconds, connect_timeout_seconds=1,
                          max_bytes=max_bytes, min_interval_seconds=0, max_redirects=max_redirects)


def make_legacy_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE cache (k TEXT PRIMARY KEY, fetched REAL NOT NULL, body TEXT NOT NULL);
        CREATE TABLE suggestion (
            id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, season INTEGER NOT NULL,
            episode INTEGER NOT NULL, source TEXT NOT NULL, source_key TEXT NOT NULL,
            title_de TEXT NOT NULL DEFAULT '', title_en TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT '', air_date TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '', hint TEXT NOT NULL DEFAULT '', level TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'offen', marked INTEGER NOT NULL DEFAULT 0,
            edited INTEGER NOT NULL DEFAULT 0, orig_title_de TEXT NOT NULL DEFAULT '',
            orig_title_en TEXT NOT NULL DEFAULT '', orig_description TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
            UNIQUE (series_id, season, episode, source, source_key)
        );
        CREATE TABLE ticket (
            id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, series_title TEXT NOT NULL DEFAULT '',
            tvdb_id INTEGER, season INTEGER NOT NULL, lang TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'erstellt',
            body TEXT NOT NULL DEFAULT '', hints TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE ticket_item (
            ticket_id INTEGER NOT NULL, suggestion_id INTEGER NOT NULL, season INTEGER NOT NULL,
            episode INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', PRIMARY KEY (ticket_id, suggestion_id)
        );
        CREATE INDEX idx_legacy_suggestion_series ON suggestion(series_id);
    """)
    con.execute(
        "INSERT INTO suggestion(series_id,season,episode,source,source_key,title_de,title_en,description,level,status,marked,created) "
        "VALUES(1,1,2,'tmdb','episode-2','Titel DE','Title EN','Beschreibung','hoch','offen',1,1000)"
    )
    con.execute(
        "INSERT INTO ticket(series_id,series_title,season,lang,status,created,updated) "
        "VALUES(1,'Serie',1,'de','eingereicht',900,1100)"
    )
    con.commit()
    con.close()


def make_legacy_stage(path, stage):
    """Erzeugt drei repräsentative Alt-Schemastände vor Version 1."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cache (k TEXT PRIMARY KEY, fetched REAL NOT NULL, body TEXT NOT NULL)")
    suggestion_columns = [
        "id INTEGER PRIMARY KEY AUTOINCREMENT", "series_id INTEGER NOT NULL", "season INTEGER NOT NULL",
        "episode INTEGER NOT NULL", "source TEXT NOT NULL", "source_key TEXT NOT NULL",
        "title_de TEXT NOT NULL DEFAULT ''", "title_en TEXT NOT NULL DEFAULT ''",
        "description TEXT NOT NULL DEFAULT ''", "channel TEXT NOT NULL DEFAULT ''",
        "air_date TEXT NOT NULL DEFAULT ''", "link TEXT NOT NULL DEFAULT ''", "level TEXT NOT NULL",
        "status TEXT NOT NULL DEFAULT 'offen'", "created REAL NOT NULL",
    ]
    if stage >= 1:
        suggestion_columns.append("hint TEXT NOT NULL DEFAULT ''")
    if stage >= 2:
        suggestion_columns.extend([
            "marked INTEGER NOT NULL DEFAULT 0", "edited INTEGER NOT NULL DEFAULT 0",
            "orig_title_de TEXT NOT NULL DEFAULT ''", "orig_title_en TEXT NOT NULL DEFAULT ''",
            "orig_description TEXT NOT NULL DEFAULT ''",
        ])
    suggestion_sql = ", ".join(suggestion_columns)
    con.execute(
        f"CREATE TABLE suggestion ({suggestion_sql}, UNIQUE(series_id,season,episode,source,source_key))"
    )
    ticket_columns = [
        "id INTEGER PRIMARY KEY AUTOINCREMENT", "series_id INTEGER NOT NULL", "series_title TEXT NOT NULL DEFAULT ''",
        "tvdb_id INTEGER", "season INTEGER NOT NULL", "lang TEXT NOT NULL",
        "status TEXT NOT NULL DEFAULT 'erstellt'", "body TEXT NOT NULL DEFAULT ''",
        "hints TEXT NOT NULL DEFAULT '[]'", "created REAL NOT NULL", "updated REAL NOT NULL",
    ]
    if stage >= 1:
        ticket_columns.append("body_html TEXT NOT NULL DEFAULT ''")
    if stage >= 2:
        ticket_columns.extend([
            "tvdb_no INTEGER", "tvdb_status TEXT NOT NULL DEFAULT ''", "tvdb_checked REAL",
        ])
    con.execute(f"CREATE TABLE ticket ({', '.join(ticket_columns)})")
    con.execute(
        "CREATE TABLE ticket_item (ticket_id INTEGER NOT NULL, suggestion_id INTEGER NOT NULL, "
        "season INTEGER NOT NULL, episode INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY(ticket_id,suggestion_id))"
    )
    con.execute(f"CREATE INDEX idx_stage_{stage}_series ON suggestion(series_id)")
    status = ("ausgewählt" if stage == 0 else "verworfen" if stage == 2 else "offen")
    con.execute(
        "INSERT INTO suggestion(series_id,season,episode,source,source_key,title_de,title_en,description,channel,air_date,link,level,status,created) "
        "VALUES(1,2,3,'legacy','legacy-1','Titel DE','Title EN','Beschreibung','Kanal','2020-01-02','https://example.test/e/3','hoch',?,1234)",
        (status,),
    )
    if stage >= 1:
        con.execute("UPDATE suggestion SET hint='alter Hinweis' WHERE id=1")
    if stage >= 2:
        con.execute("UPDATE suggestion SET orig_title_de='Alt DE',orig_title_en='Old EN',orig_description='Altbeschreibung' WHERE id=1")
    con.execute("INSERT INTO ticket(series_id,series_title,tvdb_id,season,lang,status,body,created,updated) "
                "VALUES(1,'Serie',321,2,'de','eingereicht','Ticketinhalt',1200,1300)")
    if stage >= 2:
        con.execute("UPDATE ticket SET tvdb_no=42840,tvdb_status='Open',tvdb_checked=1250 WHERE id=1")
    con.execute("INSERT INTO ticket_item(ticket_id,suggestion_id,season,episode,title) VALUES(1,1,2,3,'Titel DE')")
    con.execute("INSERT INTO cache(k,fetched,body) VALUES('legacy-cache',1000,'{}')")
    con.commit()
    con.close()


class MigrationTests(unittest.TestCase):
    def test_additive_migration_backup_and_backfill(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            make_legacy_db(path)
            self.assertEqual(migrate_database(path), 1)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM suggestion").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ticket").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM candidate_value").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ticket_status_event").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT status,marked FROM candidate_state ORDER BY id LIMIT 1").fetchone(), ("offen", 1))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM schema_migration WHERE version=1").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            con.close()
            backups = list((Path(temp) / "backups").glob("tba-vor-v1-*.db"))
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM suggestion").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM ticket").fetchone()[0], 1)
            self.assertIsNotNone(backup.execute("SELECT sql FROM sqlite_master WHERE name='idx_legacy_suggestion_series'").fetchone())
            backup.close()
            self.assertIsNone(migrate_database(path))

    def test_migration_supports_legacy_schema_matrix_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            for stage in range(3):
                with self.subTest(legacy_stage=stage):
                    stage_dir = Path(temp) / f"stage-{stage}"
                    stage_dir.mkdir()
                    path = stage_dir / "legacy.db"
                    make_legacy_stage(path, stage)
                    self.assertEqual(migrate_database(path), 1)
                    con = sqlite3.connect(path)
                    self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(con.execute("SELECT series_id,season,episode,title_de,description FROM suggestion").fetchone(),
                                     (1, 2, 3, "Titel DE", "Beschreibung"))
                    self.assertEqual(con.execute("SELECT ticket_id,suggestion_id,season,episode,title FROM ticket_item").fetchone(),
                                     (1, 1, 2, 3, "Titel DE"))
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM cache").fetchone()[0], 1)
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM candidate_value WHERE suggestion_id=1").fetchone()[0], 3)
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM ticket_status_event WHERE ticket_id=1").fetchone()[0], 1)
                    state = con.execute("SELECT status,marked FROM candidate_state ORDER BY id LIMIT 1").fetchone()
                    if stage == 0:
                        self.assertEqual(state, ("offen", 1))
                    elif stage == 1:
                        self.assertEqual(state, ("offen", 0))
                    else:
                        self.assertEqual(state, ("verworfen", 0))
                    ticket_columns = {row[1] for row in con.execute("PRAGMA table_info(ticket)")}
                    self.assertTrue({"body_html", "tvdb_no", "tvdb_status", "tvdb_checked"}.issubset(ticket_columns))
                    self.assertIsNotNone(con.execute(f"SELECT sql FROM sqlite_master WHERE name='idx_stage_{stage}_series'").fetchone())
                    self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    con.close()
                    backups = list((stage_dir / "backups").glob("tba-vor-v1-*.db"))
                    self.assertEqual(len(backups), 1)
                    backup = sqlite3.connect(backups[0])
                    self.assertEqual(backup.execute("SELECT COUNT(*) FROM suggestion").fetchone()[0], 1)
                    self.assertEqual(backup.execute("SELECT COUNT(*) FROM ticket_item").fetchone()[0], 1)
                    backup.close()
                    self.assertIsNone(migrate_database(path))
                    verify = sqlite3.connect(path)
                    self.assertEqual(verify.execute("SELECT COUNT(*) FROM candidate_value").fetchone()[0], 3)
                    verify.close()

    def test_backup_preserves_wal_commits_and_indexes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "wal-legacy.db"
            make_legacy_db(path)
            writer = sqlite3.connect(path)
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower(), "wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO cache(k,fetched,body) VALUES('wal-only',2000,'{\"saved\":true}')")
            writer.commit()
            self.assertTrue(Path(str(path) + "-wal").exists())
            self.assertEqual(migrate_database(path), 1)
            backups = list((Path(temp) / "backups").glob("tba-vor-v1-*.db"))
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            self.assertEqual(backup.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(backup.execute("SELECT body FROM cache WHERE k='wal-only'").fetchone()[0], '{"saved":true}')
            self.assertIsNotNone(backup.execute("SELECT sql FROM sqlite_master WHERE name='idx_legacy_suggestion_series'").fetchone())
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            backup.close()
            writer.close()

    def test_schema_failure_rolls_back_all_migration_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollback.db"
            make_legacy_db(path)
            with patch("core.migrations._create_foundation", side_effect=sqlite3.OperationalError("injected failure")):
                with self.assertRaises(MigrationError):
                    migrate_database(path)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM suggestion").fetchone()[0], 1)
            self.assertNotIn("candidate_value", {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertIsNotNone(con.execute("SELECT sql FROM sqlite_master WHERE name='idx_legacy_suggestion_series'").fetchone())
            con.close()
            self.assertEqual(len(list((Path(temp) / "backups").glob("tba-vor-v1-*.db"))), 1)

    def test_backup_failure_leaves_legacy_database_unmigrated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            make_legacy_db(path)

            def fail_backup(_path, _version):
                raise OSError("backup failure")

            with self.assertRaises(MigrationError):
                migrate_database(path, backup_func=fail_backup)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM suggestion").fetchone()[0], 1)
            self.assertNotIn("candidate_value", {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            con.close()


class SecretAndAuditTests(unittest.TestCase):
    def test_secret_store_encryption_scope_and_wrong_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secrets.db"
            migrate_database(path)
            key = random_secrets.token_urlsafe(48)
            secret_value = random_secrets.token_urlsafe(24)
            store = SecretStore(path, key=key)
            self.assertTrue(store.unlocked)
            store.set("source", "demo", "api_key", secret_value)
            self.assertTrue(store.configured("source", "demo", "api_key"))
            self.assertEqual(store.scoped("source", "demo").get("api_key"), secret_value)
            self.assertIsNone(store.scoped("action", "demo").get("api_key"))
            con = sqlite3.connect(path)
            ciphertext = con.execute("SELECT ciphertext FROM secret").fetchone()[0]
            con.close()
            self.assertNotIn(secret_value.encode(), ciphertext)
            with self.assertRaises(SecretStoreError):
                SecretStore(path, key=random_secrets.token_urlsafe(48)).get("source", "demo", "api_key")
            with self.assertRaises(SecretStoreError):
                SecretStore(path, key="short")
            locked = SecretStore(path, key="")
            self.assertFalse(locked.unlocked)
            with self.assertRaises(SecretStoreError):
                locked.get("source", "demo", "api_key")

    def test_redactor_handles_header_and_nested_secret_keys(self):
        result = REDACTOR.redact_value({
            "X-API-Key": "never-print-this",
            "csrfToken": "also-private",
            "nested": {"clientSecret": "hidden-too"},
            "safe": "visible",
        })
        self.assertEqual(result["X-API-Key"], "***")
        self.assertEqual(result["csrfToken"], "***")
        self.assertEqual(result["nested"]["clientSecret"], "***")
        self.assertEqual(result["safe"], "visible")
        self.assertEqual(REDACTOR.redact_headers({"Proxy-Authorization": "Bearer abc123"})["Proxy-Authorization"], "***")

    def test_registered_secret_canary_is_removed_from_text_headers_and_nested_values(self):
        canary = "phase1-canary-" + random_secrets.token_hex(16)
        REDACTOR.register(canary)
        probes = [
            f"failure: {canary}", f"https://example.invalid/?api_key={canary}",
            f"Authorization: Bearer {canary}", f"https://user:{canary}@example.invalid/path",
        ]
        for probe in probes:
            with self.subTest(probe=probe.split(":", 1)[0]):
                self.assertNotIn(canary, REDACTOR.redact_text(probe))
        structured = REDACTOR.redact_value({
            "note": "echo " + canary,
            "items": [{"accessToken": canary}, {"password": canary}],
        })
        self.assertNotIn(canary, json.dumps(structured))
        self.assertNotIn(canary, str(REDACTOR.redact_headers({"X-Api-Key": canary, "X-Note": canary})))

    def test_cache_redacts_secrets_in_keys_and_cached_values(self):
        import speicher
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cache.db"
            migrate_database(path)
            canary = "cache-canary-" + random_secrets.token_hex(16)
            REDACTOR.register(canary)
            key = "https://provider.example/search?api_key=" + canary
            payload = {"token": canary, "note": "provider echoed " + canary}
            with patch.object(speicher, "DB_PATH", str(path)):
                speicher.cache_put(key, payload)
                cached = speicher.cache_get_raw(key)
                fresh = speicher.cache_get(key)
                raw_legacy_key = "legacy-cache:" + canary
                con = sqlite3.connect(path)
                con.execute("INSERT INTO cache(k,fetched,body) VALUES(?,?,?)", (
                    raw_legacy_key, 1000, json.dumps({"message": "old echo " + canary}),
                ))
                con.commit()
                con.close()
                speicher.init_db()
            self.assertNotIn(canary, json.dumps(cached))
            self.assertNotIn(canary, json.dumps(fresh))
            con = sqlite3.connect(path)
            stored = con.execute("SELECT k,body FROM cache").fetchall()
            con.close()
            self.assertTrue(stored)
            self.assertTrue(all(canary not in key_part and canary not in body_part for key_part, body_part in stored))
            self.assertTrue(any(key_part == "legacy-cache:***" for key_part, _ in stored))
            self.assertTrue(any("***" in body_part for _key_part, body_part in stored))

    def test_config_overview_never_exposes_secret_values(self):
        import config
        canary = "config-canary-" + random_secrets.token_hex(16)
        with patch.object(config, "METADATARR_SECRET_KEY", canary):
            rows = [row for _section, items in config.config_overview() for row in items]
        item = next(row for row in rows if row["name"] == "METADATARR_SECRET_KEY")
        self.assertEqual(item["value"], "gesetzt")
        self.assertNotIn(canary, json.dumps(rows))

    def test_audit_redacts_secrets_and_is_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audit.db"
            migrate_database(path)
            row_id = record(
                "updated", "source", "demo",
                before={"apiKey": "sensitive", "name": "old"},
                after={"csrfToken": "token-value", "name": "new"},
                request_id="test-request", db_path=path,
            )
            con = sqlite3.connect(path)
            before, after = con.execute("SELECT before_json,after_json FROM audit_event WHERE id=?", (row_id,)).fetchone()
            self.assertNotIn("sensitive", before)
            self.assertNotIn("token-value", after)
            self.assertIn("***", before)
            self.assertIn("***", after)
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE audit_event SET action='tampered' WHERE id=?", (row_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("DELETE FROM audit_event WHERE id=?", (row_id,))
            con.close()

    def test_audit_redacts_canary_in_all_scalar_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audit-canary.db"
            migrate_database(path)
            canary = "audit-canary-" + random_secrets.token_hex(16)
            REDACTOR.register(canary)
            row_id = record(
                canary, "source", canary,
                before={"message": canary}, after={"message": "updated " + canary},
                request_id=canary, db_path=path,
            )
            con = sqlite3.connect(path)
            row = con.execute(
                "SELECT action,object_kind,object_id,before_json,after_json,request_id FROM audit_event WHERE id=?",
                (row_id,),
            ).fetchone()
            con.close()
            serialized = json.dumps(row)
            self.assertNotIn(canary, serialized)
            self.assertIn("***", serialized)


class RegistryAndHTTPTests(unittest.TestCase):
    def test_source_and_action_registries_are_separate(self):
        sources = PluginRegistry("source", SourcePlugin)
        actions = PluginRegistry("action", ActionPlugin)

        class ReadOnlySource(SourcePlugin):
            key = "source-test-unique"

        class WriteAction(ActionPlugin):
            key = "action-test-unique"

        sources.register(ReadOnlySource)
        actions.register(WriteAction)
        self.assertIs(sources.get("source-test-unique"), ReadOnlySource)
        self.assertIs(actions.get("action-test-unique"), WriteAction)
        with self.assertRaises(PluginRegistrationError):
            sources.register(WriteAction)
        with self.assertRaises(PluginRegistrationError):
            PluginRegistry("source", SourcePlugin).register(WriteAction)

    def test_http_client_rejects_ungranted_and_private_targets(self):
        def resolver(_host, port, type=socket.SOCK_STREAM):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        client = SafeHTTPClient(resolver=resolver)
        blocked_profile = NetworkProfile("test-source", ("service.example",), allow_private=False)
        with self.assertRaises(NetworkPolicyError):
            client._validate("http://service.example/", blocked_profile)
        mismatch = NetworkProfile("test-source-2", ("other.example",), allow_private=True)
        with self.assertRaises(NetworkPolicyError):
            client._validate("http://service.example/", mismatch)
        with self.assertRaises(NetworkPolicyError):
            client.request("http://service.example/", profile=object())
        with self.assertRaises(NetworkPolicyError):
            client.request("http://service.example/", profile=mismatch, method="DELETE")

    def test_http_checks_every_dns_answer_and_rejects_local_metadata_ips(self):
        profile = NetworkProfile("dns-matrix", ("internal.example",), allow_private=True)
        self.assertEqual(_safe_ip("127.0.0.1", profile), ipaddress.ip_address("127.0.0.1"))
        self.assertEqual(_safe_ip("::ffff:127.0.0.1", profile), ipaddress.ip_address("127.0.0.1"))
        blocked = ("::1", "169.254.169.254", "fe80::1", "fd00:ec2::1", "224.0.0.1", "0.0.0.0")
        for address in blocked:
            with self.subTest(address=address), self.assertRaises(NetworkPolicyError):
                _safe_ip(address, profile)
        no_private = NetworkProfile("no-private", ("internal.example",), allow_private=False)
        for address in ("127.0.0.1", "::1", "10.20.30.40"):
            with self.subTest(private_not_granted=address), self.assertRaises(NetworkPolicyError):
                _safe_ip(address, no_private)

        calls = []
        def mixed_resolver(host, port, type=socket.SOCK_STREAM):
            calls.append(host)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
            ]
        client = SafeHTTPClient(resolver=mixed_resolver)
        with self.assertRaises(NetworkPolicyError):
            client._validate("http://internal.example/", profile)
        self.assertEqual(calls, ["internal.example"])

    def test_http_pins_single_checked_dns_resolution(self):
        server, thread, _handler = start_local_http_fixture()
        calls = []
        def changing_resolver(host, port, type=socket.SOCK_STREAM):
            calls.append(host)
            address = "127.0.0.1" if len(calls) == 1 else "169.254.169.254"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        client = SafeHTTPClient(resolver=changing_resolver)
        try:
            with patch("net.http_client._safe_ip", side_effect=lambda address, _profile: ipaddress.ip_address(address)):
                result = client.request(f"http://service.example:{server.server_port}/ok", profile=local_test_profile())
            self.assertEqual(result.body, b"ok")
            self.assertEqual(calls, ["service.example"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_cross_origin_redirect_strips_credentials_and_redacts_headers(self):
        server, thread, handler = start_local_http_fixture()
        canary = "header-canary-" + random_secrets.token_hex(12)
        cookie_canary = "cookie-canary-" + random_secrets.token_hex(12)
        REDACTOR.register(canary)
        REDACTOR.register(cookie_canary)
        client = local_test_client(server)
        try:
            with patch("net.http_client._safe_ip", side_effect=lambda address, _profile: ipaddress.ip_address(address)):
                result = client.request(
                    f"http://service.example:{server.server_port}/redirect",
                    profile=local_test_profile(),
                    headers={"X-Api-Key": canary, "Authorization": "Bearer " + canary},
                )
            self.assertEqual(result.status, 200)
            self.assertEqual(result.body, b"ok")
            first, second = handler.seen[-2:]
            self.assertEqual(first[1], canary)
            self.assertEqual(first[2], "Bearer " + canary)
            self.assertIsNone(second[1])
            self.assertIsNone(second[2])
            self.assertNotIn("response-echo-secret", result.headers.values())
            self.assertNotIn("raw-cookie-secret", result.headers.values())
            self.assertEqual(result.headers.get("X-Api-Key"), "***")
            self.assertEqual(result.headers.get("Set-Cookie"), "***")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_rejects_ungranted_redirect_and_caps_redirects(self):
        server, thread, _handler = start_local_http_fixture()
        client = local_test_client(server)
        try:
            with patch("net.http_client._safe_ip", side_effect=lambda address, _profile: ipaddress.ip_address(address)):
                only_service = local_test_profile(hosts=("service.example",))
                with self.assertRaises(NetworkPolicyError):
                    client.request(f"http://service.example:{server.server_port}/blocked-redirect", profile=only_service)
                one_redirect = local_test_profile(max_redirects=1)
                with self.assertRaises(HTTPClientError):
                    client.request(f"http://service.example:{server.server_port}/loop", profile=one_redirect)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_enforces_streamed_size_limit_and_timeout(self):
        server, thread, _handler = start_local_http_fixture()
        client = local_test_client(server)
        try:
            with patch("net.http_client._safe_ip", side_effect=lambda address, _profile: ipaddress.ip_address(address)):
                tiny = local_test_profile(max_bytes=4)
                with self.assertRaises(ResponseTooLarge):
                    client.request(f"http://service.example:{server.server_port}/large", profile=tiny)
                with self.assertRaises(ResponseTooLarge):
                    client.request(f"http://service.example:{server.server_port}/stream", profile=tiny)
                quick = local_test_profile(timeout_seconds=0.15)
                started = time.monotonic()
                with self.assertRaises(HTTPClientError):
                    client.request(f"http://service.example:{server.server_port}/slow", profile=quick)
                self.assertLess(time.monotonic() - started, 1.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_sonarr_wrapper_uses_get_only(self):
        import sonarr

        class FakeClient:
            def __init__(self):
                self.methods = []

            def request(self, url, *, profile, method, headers):
                self.methods.append(method)
                if "/api/v3/" in url:
                    result_body, result_headers = b"[]", {}
                else:
                    result_body, result_headers = b"image", {"Content-Type": "image/jpeg"}
                return type("Result", (), {"status": 200, "body": result_body, "headers": result_headers})()

        previous_client, previous_url, previous_key = sonarr._HTTP, sonarr.SONARR_URL, sonarr.SONARR_API_KEY
        fake = FakeClient()
        sonarr._HTTP = fake
        sonarr.SONARR_URL = "http://sonarr.example"
        sonarr.SONARR_API_KEY = "test-key-not-a-real-secret"
        try:
            self.assertEqual(sonarr.sonarr("series"), [])
            self.assertEqual(sonarr._get_image("/MediaCover/1/poster.jpg"), (b"image", "image/jpeg"))
            self.assertEqual(fake.methods, ["GET", "GET"])
        finally:
            sonarr._HTTP, sonarr.SONARR_URL, sonarr.SONARR_API_KEY = previous_client, previous_url, previous_key


class APISecurityTests(unittest.TestCase):
    def _session(self):
        router._SESSIONS = router._Sessions()
        handler = HandlerStub({"X-Forwarded-Proto": "https"})
        self.assertTrue(router.dispatch(handler, "GET", "/api/v1/session"))
        status, payload, _ctype, headers = handler.response
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertTrue(data["csrfToken"])
        self.assertIn(data["csrfToken"], payload)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("; HttpOnly; SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("; Secure", headers["Set-Cookie"])
        return cookie, data["csrfToken"]

    def _post(self, cookie, token, *, origin="http://localhost:8083", host="localhost:8083", length="2", body=b"{}"):
        headers = {
            "Cookie": cookie,
            "Origin": origin,
            "Host": host,
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
            "Content-Length": length,
        }
        return HandlerStub(headers, body)

    def test_api_requires_same_origin_and_session_csrf(self):
        cookie, token = self._session()
        wrong_origin = self._post(cookie, token, origin="https://attacker.example")
        self.assertTrue(router.dispatch(wrong_origin, "POST", "/api/v1/unknown"))
        self.assertEqual(wrong_origin.response[0], 403)
        wrong_host = self._post(cookie, token, host="attacker.example")
        self.assertTrue(router.dispatch(wrong_host, "POST", "/api/v1/unknown"))
        self.assertEqual(wrong_host.response[0], 403)
        wrong_csrf = self._post(cookie, "invalid")
        router.dispatch(wrong_csrf, "POST", "/api/v1/unknown")
        self.assertEqual(wrong_csrf.response[0], 403)
        valid = self._post(cookie, token)
        router.dispatch(valid, "POST", "/api/v1/unknown")
        self.assertEqual(valid.response[0], 404)

    def test_api_enforces_body_limit_before_read(self):
        cookie, token = self._session()
        oversized = self._post(cookie, token, length="4", body=b"1234")
        with patch.object(router, "BODY_LIMIT", 3):
            router.dispatch(oversized, "POST", "/api/v1/unknown")
        self.assertEqual(oversized.response[0], 413)

    def test_api_rejects_malformed_origin_and_request_bodies(self):
        cookie, token = self._session()
        bad_origin_headers = (
            {"Origin": "null"},
            {"Origin": "http://user@localhost:8083"},
            {"Origin": "http://localhost:8083", "X-Forwarded-Proto": "https"},
        )
        for extra in bad_origin_headers:
            headers = {"Cookie": cookie, "Host": "localhost:8083", "X-CSRF-Token": token,
                       "Content-Type": "application/json", "Content-Length": "2", **extra}
            handler = HandlerStub(headers, b"{}")
            router.dispatch(handler, "POST", "/api/v1/unknown")
            self.assertEqual(handler.response[0], 403)

        cases = (
            ("bad-length", "abc", b"{}", None, 400),
            ("negative-length", "-1", b"", None, 400),
            ("incomplete-body", "4", b"{}", None, 400),
            ("invalid-json", "1", b"{", None, 400),
            ("scalar-json", "1", b"1", None, 400),
            ("wrong-content-type", "2", b"{}", "text/plain", 415),
            ("chunked", "2", b"{}", None, 400),
        )
        for label, length, body, content_type, expected in cases:
            with self.subTest(case=label):
                handler = self._post(cookie, token, length=length, body=body)
                if content_type:
                    handler.headers["Content-Type"] = content_type
                if label == "chunked":
                    handler.headers["Transfer-Encoding"] = "chunked"
                router.dispatch(handler, "POST", "/api/v1/unknown")
                self.assertEqual(handler.response[0], expected)

    def test_api_session_expires_and_rotates_tokens(self):
        sessions = router._Sessions()
        with patch("api.router.time.monotonic", return_value=10.0):
            old_sid, old_token, is_new = sessions.issue("")
        self.assertTrue(is_new)
        old_cookie = f"{router._COOKIE_NAME}={old_sid}"
        with patch("api.router.time.monotonic", return_value=10.0 + router._SESSION_TTL + 1):
            self.assertIsNone(sessions.token_for(old_cookie))
            new_sid, new_token, is_new = sessions.issue(old_cookie)
        self.assertTrue(is_new)
        self.assertNotEqual(old_sid, new_sid)
        self.assertNotEqual(old_token, new_token)

    def test_api_error_and_json_payloads_redact_canary(self):
        canary = "api-canary-" + random_secrets.token_hex(16)
        REDACTOR.register(canary)
        error_handler = HandlerStub()
        router._error(error_handler, 500, "test", "Test", "detail " + canary)
        self.assertNotIn(canary, error_handler.response[1])
        self.assertIn("***", error_handler.response[1])
        json_handler = HandlerStub()
        router._send_json(json_handler, 200, {"message": canary, "apiKey": canary})
        self.assertNotIn(canary, json_handler.response[1])


if __name__ == "__main__":
    unittest.main()
