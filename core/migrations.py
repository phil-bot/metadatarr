"""Additive SQLite-Migrationen mit geprüfter, WAL-sicherer Vorab-Sicherung."""
import hashlib
import json
import os
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path


DB_LOCK = threading.RLock()
TARGET_VERSION = 1
_LEGACY_TABLES = ("cache", "suggestion", "ticket", "ticket_item")
_MIGRATION_TEXT = b"metadatarr schema v1: additive foundation, secure storage, registries, audit, candidate backfill"
_MIGRATION_CHECKSUM = hashlib.sha256(_MIGRATION_TEXT).hexdigest()


class MigrationError(RuntimeError):
    """Migration wurde sicher abgebrochen; bestehende Daten bleiben unangetastet."""


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con, table):
    return {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}


def _legacy_counts(con):
    existing = _tables(con)
    result = {}
    for table in _LEGACY_TABLES:
        result[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in existing else None
    return result


def _named_indexes(con):
    return sorted(
        (row[0], row[1], row[2])
        for row in con.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    )


def _create_backup(db_path, version):
    """Erstellt via SQLite-Backup-API eine konsistente Sicherung und verifiziert sie."""
    source = sqlite3.connect(db_path, timeout=10)
    destination = None
    backup_path = None
    try:
        source_mode = source.execute("PRAGMA journal_mode").fetchone()[0].lower()
        source_counts = _legacy_counts(source)
        source_indexes = _named_indexes(source)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_dir = Path(db_path).resolve().parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"tba-vor-v{version + 1}-{stamp}.db"
        destination = sqlite3.connect(str(backup_path), timeout=10)
        source.backup(destination)
        destination.commit()
        if source_mode not in {"delete", "wal", "truncate", "persist", "memory", "off"}:
            raise MigrationError("Unbekannter Journalmodus der Quelldatenbank; Sicherung abgebrochen.")
        if source_mode == "memory":
            raise MigrationError("In-Memory-Journalmodus ist für eine geprüfte Sicherung nicht zulässig.")
        current_mode = destination.execute(f"PRAGMA journal_mode={source_mode.upper()}").fetchone()[0].lower()
        if current_mode != source_mode:
            raise MigrationError("Journalmodus der Sicherung stimmt nicht mit der Quelle überein.")
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError("Integritätsprüfung der Datenbanksicherung fehlgeschlagen.")
        if _legacy_counts(destination) != source_counts:
            raise MigrationError("Zeilenzahlen der Sicherung stimmen nicht mit der Datenbank überein.")
        if _named_indexes(destination) != source_indexes:
            raise MigrationError("Indizes der Sicherung stimmen nicht mit der Datenbank überein.")
        return str(backup_path)
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("Vorab-Sicherung fehlgeschlagen; Datenbank wurde nicht migriert.") from None
    finally:
        if destination is not None:
            destination.close()
        source.close()


def _add_column(con, table, name, declaration):
    if name not in _columns(con, table):
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}')


def _ensure_legacy_schema(con):
    """Erhält die vier Altbestandstabellen und ergänzt nur fehlende Spalten."""
    con.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, fetched REAL NOT NULL, body TEXT NOT NULL)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS suggestion ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, season INTEGER NOT NULL, "
        "episode INTEGER NOT NULL, source TEXT NOT NULL, source_key TEXT NOT NULL, "
        "title_de TEXT NOT NULL DEFAULT '', title_en TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
        "channel TEXT NOT NULL DEFAULT '', air_date TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', "
        "level TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'offen', created REAL NOT NULL, "
        "UNIQUE (series_id, season, episode, source, source_key))"
    )
    _add_column(con, "suggestion", "hint", "TEXT NOT NULL DEFAULT ''")
    # Altbestand-Statuswerte werden nicht umgeschrieben. Die Projektion auf den
    # engeren candidate_state erfolgt separat; der Originalstatus bleibt erhalten.
    _add_column(con, "suggestion", "marked", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "suggestion", "edited", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "suggestion", "orig_title_de", "TEXT NOT NULL DEFAULT ''")
    _add_column(con, "suggestion", "orig_title_en", "TEXT NOT NULL DEFAULT ''")
    _add_column(con, "suggestion", "orig_description", "TEXT NOT NULL DEFAULT ''")

    con.execute(
        "CREATE TABLE IF NOT EXISTS ticket ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, series_title TEXT NOT NULL DEFAULT '', "
        "tvdb_id INTEGER, season INTEGER NOT NULL, lang TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'erstellt', "
        "body TEXT NOT NULL DEFAULT '', hints TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL)"
    )
    for name, declaration in (
        ("body_html", "TEXT NOT NULL DEFAULT ''"), ("tvdb_no", "INTEGER"),
        ("tvdb_status", "TEXT NOT NULL DEFAULT ''"), ("tvdb_checked", "REAL"),
    ):
        _add_column(con, "ticket", name, declaration)
    con.execute(
        "CREATE TABLE IF NOT EXISTS ticket_item (ticket_id INTEGER NOT NULL, suggestion_id INTEGER NOT NULL, "
        "season INTEGER NOT NULL, episode INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (ticket_id, suggestion_id))"
    )


def _create_foundation(con):
    statements = (
        "CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL, description TEXT NOT NULL, checksum TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS series_setting (series_id INTEGER PRIMARY KEY, target_languages_json TEXT, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS placeholder_rule (id TEXT PRIMARY KEY, field_key TEXT NOT NULL, language TEXT, kind TEXT NOT NULL CHECK(kind IN ('empty','exact','regex')), pattern TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL CHECK(origin IN ('env-seed','ui')), created_at REAL NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS source_config (id TEXT PRIMARY KEY, plugin_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('builtin','declarative')), name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', languages_json TEXT NOT NULL DEFAULT '[]', definition_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 0, allow_external INTEGER NOT NULL DEFAULT 0, allow_private INTEGER NOT NULL DEFAULT 0, granted_hosts_json TEXT NOT NULL DEFAULT '[]', include_in_nightly INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS action_config (plugin_key TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, settings_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS secret (owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, slot TEXT NOT NULL, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, key_id TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(owner_kind, owner_id, slot))",
        "CREATE TABLE IF NOT EXISTS candidate_value (id INTEGER PRIMARY KEY AUTOINCREMENT, series_id INTEGER NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL, sonarr_episode_id INTEGER, field_key TEXT NOT NULL, language TEXT NOT NULL, value TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', retrieved_at REAL, match_level TEXT NOT NULL DEFAULT '', match_hint TEXT NOT NULL DEFAULT '', suggestion_id INTEGER, superseded_by INTEGER, created_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS candidate_state (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'offen' CHECK(status IN ('offen','verworfen')), marked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS metadata_snapshot (series_id INTEGER NOT NULL, season INTEGER NOT NULL DEFAULT -1, episode INTEGER NOT NULL DEFAULT -1, sonarr_episode_id INTEGER, field_key TEXT NOT NULL, language TEXT NOT NULL, status TEXT NOT NULL, rule_id TEXT, value_hash TEXT, observed_at REAL NOT NULL, PRIMARY KEY(series_id,season,episode,field_key,language))",
        "CREATE TABLE IF NOT EXISTS action_execution (id TEXT PRIMARY KEY, plugin_key TEXT NOT NULL, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, preview_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, external_id TEXT, started_at REAL, finished_at REAL, result_summary TEXT NOT NULL DEFAULT '{}')",
        "CREATE TABLE IF NOT EXISTS ticket_status_event (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, status TEXT NOT NULL, occurred_at REAL NOT NULL, event_source TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}')",
        "CREATE TABLE IF NOT EXISTS audit_event (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL, action TEXT NOT NULL, object_kind TEXT NOT NULL, object_id TEXT NOT NULL DEFAULT '', before_json TEXT, after_json TEXT, request_id TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS ix_candidate_episode ON candidate_value(series_id,season,episode,field_key,language)",
        "CREATE INDEX IF NOT EXISTS ix_candidate_source ON candidate_value(source_id,retrieved_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_candidate_legacy_source ON candidate_value(suggestion_id,field_key,language) WHERE suggestion_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_ticket_status_event_ticket ON ticket_status_event(ticket_id,occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_event_time ON audit_event(occurred_at)",
        "CREATE TRIGGER IF NOT EXISTS audit_event_immutable_update BEFORE UPDATE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS audit_event_immutable_delete BEFORE DELETE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END",
    )
    for statement in statements:
        con.execute(statement)


def _seed_defaults(con, now):
    con.execute(
        "INSERT OR IGNORE INTO app_setting(key,value_json,updated_at) VALUES('target_languages','[\"de\",\"en\"]',?)",
        (now,),
    )
    pattern = os.environ.get("TBA_REGEX", r"^\s*(tba|tbd|n/?a)?\s*$|^(episode|folge)\s+\d+$")
    con.execute(
        "INSERT OR IGNORE INTO placeholder_rule(id,field_key,language,kind,pattern,enabled,origin,created_at,updated_at) "
        "VALUES('env:TBA_REGEX:episode.title','episode.title',NULL,'regex',?,1,'env-seed',?,?)",
        (pattern, now, now),
    )
    entries = (
        ("mediathek", "MediathekViewWeb", ["de"], 1, 1),
        ("tmdb", "TMDB", ["de", "en"], 1, 1),
        ("fernsehserien", "fernsehserien.de", ["de"], 0, 0),
        ("deepl", "DeepL", ["de", "en"], bool(os.environ.get("DEEPL_API_KEY")), 0),
    )
    for plugin_key, label, languages, enabled, nightly in entries:
        con.execute(
            "INSERT OR IGNORE INTO source_config(id,plugin_key,kind,name,languages_json,enabled,include_in_nightly,created_at,updated_at) "
            "VALUES(?,?,'builtin',?,?,?, ?,?,?)",
            (plugin_key, plugin_key, label, json.dumps(languages), int(enabled), nightly, now, now),
        )
    tvdb_ready = bool(os.environ.get("TVDB_USER")) and bool(os.environ.get("TVDB_PASSWORD"))
    con.execute(
        "INSERT OR IGNORE INTO action_config(plugin_key,enabled,settings_json,created_at,updated_at) VALUES('tvdb-ticket',?,'{}',?,?)",
        (int(tvdb_ready), now, now),
    )


def _backfill_legacy(con, now):
    suggestions = con.execute(
        "SELECT id,series_id,season,episode,source,title_de,title_en,description,link,level,hint,status,marked,created "
        "FROM suggestion ORDER BY id"
    ).fetchall()
    fields = (("title_de", "episode.title", "de"), ("title_en", "episode.title", "en"),
              ("description", "episode.overview", "de"))
    for row in suggestions:
        values = dict(row)
        for column, field_key, language in fields:
            value = (values.get(column) or "").strip()
            if not value:
                continue
            hint = "Aus Altbestand; Beschreibungssprache Deutsch angenommen." if column == "description" else "Aus Altbestand."
            if values.get("hint"):
                hint += " " + str(values["hint"])
            cur = con.execute(
                "INSERT INTO candidate_value(series_id,season,episode,field_key,language,value,source_id,source_url,retrieved_at,match_level,match_hint,suggestion_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (values["series_id"], values["season"], values["episode"], field_key, language, value,
                 values["source"], values.get("link") or "", values.get("created"), values.get("level") or "",
                 hint, values["id"], values.get("created") or now),
            )
            legacy_status = values.get("status") or ""
            status = "verworfen" if legacy_status in ("verworfen", "eingereicht") else "offen"
            marked = bool(values.get("marked")) or legacy_status == "ausgewählt"
            con.execute(
                "INSERT INTO candidate_state(candidate_id,status,marked,created_at,updated_at) VALUES(?,?,?,?,?)",
                (cur.lastrowid, status, int(marked), values.get("created") or now, now),
            )
    tickets = con.execute("SELECT id,status,created,updated,tvdb_no,tvdb_status FROM ticket ORDER BY id").fetchall()
    for row in tickets:
        values = dict(row)
        con.execute(
            "INSERT INTO ticket_status_event(ticket_id,status,occurred_at,event_source,details_json) VALUES(?,?,?,?,?)",
            (values["id"], values.get("status") or "erstellt", values.get("updated") or values.get("created") or now,
             "legacy-backfill", json.dumps({"ticketNo": values.get("tvdb_no"), "tvdbStatus": values.get("tvdb_status") or ""}, separators=(",", ":"))),
        )


def migrate_database(db_path, backup_func=None):
    """Wendet die Basismigration an und verifiziert vorhandene Versionen 1 und 2."""
    db_path = os.path.abspath(os.fspath(db_path))
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with DB_LOCK:
        existed = os.path.isfile(db_path) and os.path.getsize(db_path) > 0
        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise MigrationError("Datenbankversion ist neuer als diese Anwendung; Start abgebrochen.")
            if version in (1, 2):
                if "schema_migration" not in _tables(con):
                    raise MigrationError("Datenbankversion und Migrationsprotokoll widersprechen sich.")
                recorded = con.execute("SELECT checksum FROM schema_migration WHERE version=1").fetchone()
                if recorded is None or recorded[0] != _MIGRATION_CHECKSUM:
                    raise MigrationError("Migrationsprotokoll fehlt oder Prüfsumme stimmt nicht.")
                if version == 2:
                    phase3 = con.execute("SELECT checksum FROM schema_migration WHERE version=2").fetchone()
                    if phase3 is None or phase3[0] != _PHASE3_CHECKSUM:
                        raise MigrationError("Phase-3-Migrationsprotokoll fehlt oder Prüfsumme stimmt nicht.")
                return None
            if existed and _tables(con):
                creator = backup_func or _create_backup
                try:
                    creator(db_path, version)
                except Exception:
                    raise MigrationError("Vorab-Sicherung fehlgeschlagen; Datenbank wurde nicht migriert.") from None
            try:
                con.execute("BEGIN IMMEDIATE")
                _ensure_legacy_schema(con)
                _create_foundation(con)
                now = time.time()
                _seed_defaults(con, now)
                _backfill_legacy(con, now)
                con.execute(
                    "INSERT INTO schema_migration(version,applied_at,description,checksum) VALUES(?,?,?,?)",
                    (TARGET_VERSION, now, "Additive Backend-Grundlage", _MIGRATION_CHECKSUM),
                )
                con.execute(f"PRAGMA user_version={TARGET_VERSION}")
                con.commit()
            except Exception as exc:
                con.rollback()
                if isinstance(exc, MigrationError):
                    raise
                raise MigrationError("Migration fehlgeschlagen; Änderungen wurden zurückgerollt.") from None
            return TARGET_VERSION
        finally:
            con.close()


_PHASE3_TEXT = b"metadatarr schema v2: candidates provenance, source/action defaults, preview ledger"
_PHASE3_CHECKSUM = hashlib.sha256(_PHASE3_TEXT).hexdigest()


def _configured_host(url, fallback):
    try:
        return (urlsplit(url).hostname or fallback).lower().rstrip(".")
    except (TypeError, ValueError):
        return fallback


def _backfill_phase3_candidates(con, now):
    """Idempotently copy suggestions added after the base migration into candidates."""
    fields = (("title_de", "episode.title", "de"), ("title_en", "episode.title", "en"),
              ("description", "episode.overview", "de"))
    rows = con.execute("SELECT * FROM suggestion ORDER BY id").fetchall()
    for row in rows:
        values = dict(row)
        for column, field_key, language in fields:
            value = (values.get(column) or "").strip()
            if not value:
                continue
            hint = "Aus Altbestand; Beschreibungssprache Deutsch angenommen." if column == "description" else "Aus Altbestand."
            if values.get("hint"):
                hint += " " + str(values["hint"])
            con.execute(
                "INSERT OR IGNORE INTO candidate_value(series_id,season,episode,field_key,language,value,source_id,"
                "source_url,retrieved_at,match_level,match_hint,suggestion_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (values["series_id"], values["season"], values["episode"], field_key, language, value,
                 values["source"], values.get("link") or "", values.get("created"), values.get("level") or "",
                 hint, values["id"], values.get("created") or now),
            )
            candidate = con.execute(
                "SELECT id FROM candidate_value WHERE suggestion_id=? AND field_key=? AND language=?",
                (values["id"], field_key, language),
            ).fetchone()
            if candidate:
                legacy_status = values.get("status") or ""
                normalized = "verworfen" if legacy_status in ("verworfen", "eingereicht") else "offen"
                marked = int(bool(values.get("marked")) or legacy_status == "ausgew\u00e4hlt")
                con.execute(
                    "INSERT OR IGNORE INTO candidate_state(candidate_id,status,marked,created_at,updated_at,legacy_status) "
                    "VALUES(?,?,?,?,?,?)",
                    (candidate[0], normalized, marked, values.get("created") or now, now, legacy_status),
                )
                con.execute("UPDATE candidate_state SET legacy_status=? WHERE candidate_id=? AND legacy_status=''",
                            (legacy_status, candidate[0]))
    tickets = con.execute("SELECT id,status,created,updated,tvdb_no,tvdb_status FROM ticket ORDER BY id").fetchall()
    for ticket in tickets:
        if con.execute("SELECT 1 FROM ticket_status_event WHERE ticket_id=? LIMIT 1", (ticket["id"],)).fetchone():
            continue
        detail = json.dumps({"tvdbNumber": ticket["tvdb_no"], "tvdbStatus": ticket["tvdb_status"] or ""}, separators=(",", ":"))
        con.execute("INSERT INTO ticket_status_event(ticket_id,status,occurred_at,event_source,details_json) VALUES(?,?,?,?,?)",
                    (ticket["id"], ticket["status"] or "erstellt", ticket["updated"] or ticket["created"] or now,
                     "phase3-backfill", detail))


def _apply_phase3_schema(con, now):
    """Additive Phase-3-Erweiterungen; keine Altbestandstabelle wird umgebaut."""
    _add_column(con, "candidate_value", "machine_translated", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "candidate_value", "derived_from_id", "INTEGER")
    _add_column(con, "candidate_state", "legacy_status", "TEXT NOT NULL DEFAULT ''")
    con.execute(
        "CREATE TABLE IF NOT EXISTS action_preview ("
        "id TEXT PRIMARY KEY, plugin_key TEXT NOT NULL, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, "
        "preview_hash TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, consumed_at REAL)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_action_preview_expiry ON action_preview(expires_at,consumed_at)"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_candidate_derived_origin "
        "ON candidate_value(derived_from_id,field_key,language,value) WHERE derived_from_id IS NOT NULL"
    )
    # Save the exact source status separately from the normalized candidate state.
    _backfill_phase3_candidates(con, now)
    con.execute(
        "UPDATE candidate_state SET legacy_status=COALESCE((SELECT status FROM suggestion s "
        "JOIN candidate_value c ON c.suggestion_id=s.id WHERE c.id=candidate_state.candidate_id), '') "
        "WHERE legacy_status=''"
    )
    from config import DEEPL_API_KEY, DEEPL_API_URL, MEDIA_API_URL, TMDB_API_URL, TVDB_USER, TVDB_PASSWORD
    from config import TVDB_BASE_URL
    defaults = {
        "mediathek": (_configured_host(MEDIA_API_URL, "mediathekviewweb.de"),),
        "tmdb": (_configured_host(TMDB_API_URL, "api.themoviedb.org"),),
        "fernsehserien": ("www.fernsehserien.de",),
        "deepl": (_configured_host(DEEPL_API_URL, "api-free.deepl.com"),),
    }
    for source_id, hosts in defaults.items():
        row = con.execute("SELECT granted_hosts_json FROM source_config WHERE id=?", (source_id,)).fetchone()
        if row is None:
            continue
        try:
            current = json.loads(row[0] or "[]")
        except (TypeError, ValueError):
            current = []
        if not current:
            con.execute(
                "UPDATE source_config SET granted_hosts_json=?,allow_external=1,updated_at=? WHERE id=?",
                (json.dumps(list(hosts)), now, source_id),
            )
    con.execute("UPDATE source_config SET enabled=0,include_in_nightly=0,updated_at=? WHERE id='fernsehserien'", (now,))
    if DEEPL_API_KEY:
        con.execute("UPDATE source_config SET enabled=1,include_in_nightly=0,updated_at=? WHERE id='deepl'", (now,))
    else:
        con.execute("UPDATE source_config SET enabled=0,include_in_nightly=0,updated_at=? WHERE id='deepl'", (now,))
    ready = int(bool(TVDB_USER and TVDB_PASSWORD))
    con.execute("UPDATE action_config SET enabled=?,updated_at=? WHERE plugin_key='tvdb-ticket'", (ready, now))
    con.execute(
        "INSERT INTO schema_migration(version,applied_at,description,checksum) VALUES(2,?,?,?)",
        (now, "Phase-3-Kandidatenherkunft und Aktionsvorschauen", _PHASE3_CHECKSUM),
    )
    con.execute("PRAGMA user_version=2")


def migrate_phase3_database(db_path, backup_func=None):
    """Migriert v1->v2 mit verifizierter Vorab-Sicherung, Transaktion und wiederholbarer Pruefung."""
    db_path = os.path.abspath(os.fspath(db_path))
    with DB_LOCK:
        if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
            raise MigrationError("Phase-3-Migration benoetigt zuerst die Basis-Migration.")
        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                row = con.execute("SELECT checksum FROM schema_migration WHERE version=2").fetchone()
                if row is None or row[0] != _PHASE3_CHECKSUM:
                    raise MigrationError("Phase-3-Migrationsprotokoll fehlt oder Pruefsumme stimmt nicht.")
                return None
            if version != 1:
                raise MigrationError("Unerwartete Datenbankversion fuer Phase 3.")
            creator = backup_func or _create_backup
            try:
                creator(db_path, version)
            except Exception:
                raise MigrationError("Vorab-Sicherung fehlgeschlagen; Phase-3-Migration abgebrochen.") from None
            try:
                con.execute("BEGIN IMMEDIATE")
                _apply_phase3_schema(con, time.time())
                con.commit()
            except Exception:
                con.rollback()
                raise MigrationError("Phase-3-Migration fehlgeschlagen; Aenderungen wurden zurueckgerollt.") from None
            return 2
        finally:
            con.close()
