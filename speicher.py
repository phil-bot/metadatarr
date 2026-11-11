"""SQLite-Speicher, Zwischenspeicher und Vorschläge; Cache-Schlüssel/-Werte werden redigiert."""
import json
import os
import sqlite3
import time
from config import CACHE_DAYS, DB_PATH
from core.migrations import DB_LOCK, migrate_database, migrate_phase3_database
from net.redaction import REDACTOR


STATUSES = ("offen", "markiert", "im Ticket", "verworfen")
# Vorschläge, die in einem aktiven Ticket stehen (aktiv = nicht „nicht erfolgreich“).
BUSY_SQL = ("SELECT ti.suggestion_id FROM ticket_item ti JOIN ticket t ON t.id = ti.ticket_id "
            "WHERE t.status <> 'nicht erfolgreich'")
TICKET_STATES = ("erstellt", "eingereicht", "erfolgreich", "nicht erfolgreich")


def db_exec(sql, args=(), fetch=False):
    """Führt eine SQL-Anweisung in einer kurzen Verbindung aus (serialisiert über DB_LOCK)."""
    with DB_LOCK:
        con = sqlite3.connect(DB_PATH, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                cur = con.execute(sql, args)
                return cur.fetchall() if fetch else []
        finally:
            con.close()


def db_insert(sql, args=()):
    """Fügt eine Zeile ein und gibt deren ID zurück (serialisiert über DB_LOCK)."""
    with DB_LOCK:
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            with con:
                return con.execute(sql, args).lastrowid
        finally:
            con.close()


def init_db():
    """Migriert additiv und bereinigt bekannte Geheimnisse aus bereits vorhandenen Cache-Einträgen."""
    result = migrate_database(DB_PATH)
    phase3 = migrate_phase3_database(DB_PATH)
    _redact_existing_cache()
    return phase3 or result


def _redact_existing_cache():
    """Bereinigt alte Cache-Schlüssel und -Werte, nicht nur neu geschriebene Einträge."""
    with DB_LOCK:
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            rows = con.execute("SELECT k,fetched,body FROM cache").fetchall()
            with con:
                for key, fetched, body in rows:
                    safe_key = REDACTOR.redact_text(key)
                    try:
                        value = json.loads(body)
                        safe_value = REDACTOR.redact_value(value)
                        safe_body = json.dumps(safe_value)
                    except (TypeError, ValueError):
                        safe_body = REDACTOR.redact_text(body)
                    if safe_key != key or safe_body != body:
                        if safe_key != key:
                            con.execute("DELETE FROM cache WHERE k=?", (key,))
                        con.execute(
                            "INSERT OR REPLACE INTO cache(k,fetched,body) VALUES(?,?,?)",
                            (safe_key, fetched, safe_body),
                        )
        finally:
            con.close()


def cache_get(key):
    safe_key = REDACTOR.redact_text(key)
    rows = db_exec("SELECT fetched, body FROM cache WHERE k = ?", (safe_key,), fetch=True)
    if rows and time.time() - rows[0]["fetched"] < CACHE_DAYS * 86400:
        value = json.loads(rows[0]["body"])
        safe_value = REDACTOR.redact_value(value)
        if safe_value != value:
            db_exec("UPDATE cache SET body=? WHERE k=?", (json.dumps(safe_value), safe_key))
        return safe_value
    return None


def cache_put(key, value):
    safe_key = REDACTOR.redact_text(key)
    safe_value = REDACTOR.redact_value(value)
    db_exec(
        "INSERT OR REPLACE INTO cache (k, fetched, body) VALUES (?, ?, ?)",
        (safe_key, time.time(), json.dumps(safe_value)),
    )


def store_suggestions(series_id, source, rows, season=None):
    """Ersetzt offene Vorschläge dieser Quelle, optional nur für eine Staffel.

    Markierte, bearbeitete und in Tickets stehende Vorschläge bleiben unberührt, damit Haken, Bearbeitung
    und Tickets bei jeder Suche erhalten bleiben.
    """
    keep = "marked = 0 AND edited = 0 AND id NOT IN (" + BUSY_SQL + ")"
    if season is None:
        db_exec(f"DELETE FROM suggestion WHERE series_id = ? AND source = ? AND status = 'offen' AND {keep}",
                (series_id, source))
    else:
        db_exec(f"DELETE FROM suggestion WHERE series_id = ? AND source = ? AND season = ? "
                f"AND status = 'offen' AND {keep}", (series_id, source, season))
    for r in rows:
        decided = db_exec(
            "SELECT 1 FROM suggestion WHERE series_id = ? AND season = ? AND episode = ? "
            "AND (marked = 1 OR id IN (" + BUSY_SQL + "))",
            (series_id, r["season"], r["episode"]),
            fetch=True,
        )
        if decided:
            continue
        db_exec(
            "INSERT OR IGNORE INTO suggestion (series_id, season, episode, source, source_key, title_de, "
            "title_en, description, channel, air_date, link, hint, level, status, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'offen', ?)",
            (
                series_id, r["season"], r["episode"], r["source"], r["source_key"], r["title_de"],
                r["title_en"], r["description"], r["channel"], r["air_date"], r["link"], r["hint"],
                r["level"], time.time(),
            ),
        )
        saved = db_exec(
            "SELECT id FROM suggestion WHERE series_id=? AND season=? AND episode=? AND source=? AND source_key=?",
            (series_id, r["season"], r["episode"], r["source"], r["source_key"]), fetch=True,
        )
        if saved:
            from core.candidates import sync_suggestion
            sync_suggestion(saved[0]["id"])


def cache_get_raw(key):
    """Liest einen Zwischenspeicher-Eintrag ohne Ablaufprüfung, z. B. den Zeitpunkt des letzten Nachtlaufs."""
    safe_key = REDACTOR.redact_text(key)
    rows = db_exec("SELECT body FROM cache WHERE k = ?", (safe_key,), fetch=True)
    if not rows:
        return None
    value = json.loads(rows[0]["body"])
    safe_value = REDACTOR.redact_value(value)
    if safe_value != value:
        db_exec("UPDATE cache SET body=? WHERE k=?", (json.dumps(safe_value), safe_key))
    return safe_value
