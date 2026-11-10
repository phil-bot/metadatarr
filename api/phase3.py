"""Phase-3 JSON handlers for source configuration, local tickets, and guarded actions."""
import hashlib
import json
import secrets
import sqlite3
import time
import re
from urllib.parse import urlsplit

import speicher
from core.migrations import DB_LOCK
from net.redaction import REDACTOR


BUILTIN_SOURCES = {
    "mediathek": {"name": "MediathekViewWeb", "hosts": ["mediathekviewweb.de"], "nightly": True},
    "tmdb": {"name": "TMDB", "hosts": ["api.themoviedb.org"], "nightly": True},
    "fernsehserien": {"name": "fernsehserien.de", "hosts": ["www.fernsehserien.de"], "nightly": False},
    "deepl": {"name": "DeepL", "hosts": ["api-free.deepl.com"], "nightly": False},
}


def _reply(handler, status, payload):
    safe = REDACTOR.redact_value(payload)
    raw = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    handler._send(status, raw, "application/json; charset=utf-8", {
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    })
    return True


def _error(handler, status, code, detail):
    return _reply(handler, status, {"type": "about:blank", "title": "Anfrage nicht verarbeitet",
                                   "code": code, "detail": REDACTOR.redact_text(detail)})


def _db_rows(sql, args=()):
    return speicher.db_exec(sql, args, fetch=True)


def _env_configured(name):
    """Report credential availability from the scoped vault or documented environment fallback."""
    if name in {"deepl", "tmdb"}:
        from plugins.sources import source_credentials_configured
        return source_credentials_configured(name)
    if name == "tvdb-ticket":
        from plugins.actions import ScopedActionSecrets
        secrets_store = ScopedActionSecrets(name)
        return bool(secrets_store.get("user") and secrets_store.get("password"))
    return True


def _enabled(plugin_key):
    rows = _db_rows("SELECT enabled FROM action_config WHERE plugin_key=?", (plugin_key,))
    return bool(rows and rows[0]["enabled"])


def _read_ticket(ticket_id):
    rows = _db_rows("SELECT * FROM ticket WHERE id=?", (ticket_id,))
    return rows[0] if rows else None


def _ticket_list():
    rows = _db_rows("SELECT t.*, (SELECT COUNT(*) FROM ticket_item i WHERE i.ticket_id=t.id) AS item_count "
                    "FROM ticket t ORDER BY t.created DESC,t.id DESC")
    return [{"id": row["id"], "seriesId": row["series_id"], "seriesTitle": row["series_title"],
             "season": row["season"], "language": row["lang"], "status": row["status"],
             "tvdbNumber": row["tvdb_no"], "tvdbStatus": row["tvdb_status"],
             "itemCount": row["item_count"], "created": row["created"], "updated": row["updated"]}
            for row in rows]


def _ticket_detail(ticket):
    items = _db_rows("SELECT * FROM ticket_item WHERE ticket_id=? ORDER BY episode", (ticket["id"],))
    return {"id": ticket["id"], "seriesId": ticket["series_id"], "seriesTitle": ticket["series_title"],
            "tvdbId": ticket["tvdb_id"], "season": ticket["season"], "language": ticket["lang"],
            "status": ticket["status"], "subject": __import__("ticket").ticket_summary(
                ticket["series_title"], ticket["season"], ticket["lang"])[0],
            "body": ticket["body"], "bodyHtml": ticket["body_html"],
            "tvdbNumber": ticket["tvdb_no"], "tvdbStatus": ticket["tvdb_status"],
            "items": [{"suggestionId": row["suggestion_id"], "season": row["season"],
                       "episode": row["episode"], "title": row["title"]} for row in items]}


def _canonical_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _action_preview(handler, plugin_key, body):
    if plugin_key != "tvdb-ticket" or not _enabled(plugin_key) or not _env_configured(plugin_key):
        return _error(handler, 403, "action_disabled", "Die TVDB-Ticket-Aktion ist deaktiviert oder nicht konfiguriert.")
    if set(body) != {"ticketId"} or not isinstance(body["ticketId"], int) or isinstance(body["ticketId"], bool):
        return _error(handler, 400, "invalid_request", "Erwartet wird genau {ticketId: integer}.")
    ticket = _read_ticket(body["ticketId"])
    if not ticket:
        return _error(handler, 404, "ticket_not_found", "Ticket nicht gefunden.")
    try:
        from plugins.actions import TVDBTicketAction, action_context
        from quellen import SAFE_HTTP
        plugin = TVDBTicketAction()
        preview = plugin.preview(action_context(SAFE_HTTP, _db_rows), {"ticketId": body["ticketId"]})
    except ValueError as exc:
        return _error(handler, 409, "preview_rejected", str(exc))
    preview_hash = _canonical_hash(preview)
    preview_id = secrets.token_urlsafe(24)
    now = time.time()
    with DB_LOCK:
        con = sqlite3.connect(speicher.DB_PATH, timeout=10)
        try:
            with con:
                con.execute("INSERT INTO action_preview(id,plugin_key,entity_kind,entity_id,preview_hash,snapshot_json,created_at,expires_at,consumed_at) "
                            "VALUES(?,?,?,?,?,?,?,?,NULL)",
                            (preview_id, plugin_key, "ticket", str(ticket["id"]), preview_hash,
                             json.dumps(preview, ensure_ascii=False, separators=(",", ":")), now, now + 900))
        finally:
            con.close()
    return _reply(handler, 200, {"previewId": preview_id, "previewHash": preview_hash,
                                  "expiresAt": now + 900, "confirmationRequired": True,
                                  "effect": "TVDB-Support-Center: Ticket erstellen",
                                  "preview": preview})


def _action_execute(handler, plugin_key, body):
    if plugin_key != "tvdb-ticket" or not _enabled(plugin_key) or not _env_configured(plugin_key):
        return _error(handler, 403, "action_disabled", "Die TVDB-Ticket-Aktion ist deaktiviert oder nicht konfiguriert.")
    if set(body) != {"previewId", "confirm", "idempotencyKey"}:
        return _error(handler, 400, "invalid_request", "Vorschau-ID, confirm und Idempotenzschluessel sind Pflicht.")
    if body["confirm"] is not True:
        return _error(handler, 400, "confirmation_required", "Externe Einreichung braucht confirm=true.")
    idem = body["idempotencyKey"]
    if not isinstance(idem, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", idem):
        return _error(handler, 400, "invalid_idempotency_key", "Idempotenzschluessel muss 16 bis 128 sichere Zeichen haben.")
    preview_id = body["previewId"]
    if not isinstance(preview_id, str) or len(preview_id) > 128:
        return _error(handler, 400, "invalid_preview", "Vorschau-ID ist ungueltig.")
    now = time.time()
    with DB_LOCK:
        con = sqlite3.connect(speicher.DB_PATH, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM action_execution WHERE idempotency_key=?", (idem,)).fetchone():
                con.rollback()
                return _error(handler, 409, "idempotency_reused", "Dieser Idempotenzschluessel wurde bereits verwendet.")
            row = con.execute("SELECT * FROM action_preview WHERE id=? AND plugin_key=?", (preview_id, plugin_key)).fetchone()
            if not row or row["consumed_at"] is not None or row["expires_at"] < now:
                con.rollback()
                return _error(handler, 409, "preview_expired", "Vorschau fehlt, ist abgelaufen oder wurde bereits verwendet.")
            snapshot = json.loads(row["snapshot_json"])
            ticket = con.execute("SELECT * FROM ticket WHERE id=?", (int(row["entity_id"]),)).fetchone()
            if not ticket or ticket["status"] != "erstellt" or ticket["tvdb_no"]:
                con.rollback()
                return _error(handler, 409, "ticket_changed", "Ticketstatus hat sich seit der Vorschau geaendert.")
            from plugins.actions import TVDBTicketAction, action_context
            from quellen import SAFE_HTTP
            current = TVDBTicketAction().preview(action_context(SAFE_HTTP, _db_rows), {"ticketId": ticket["id"]})
            current_hash = _canonical_hash(current)
            if current_hash != row["preview_hash"] or current_hash != body.get("previewHash", current_hash):
                con.rollback()
                return _error(handler, 409, "preview_changed", "Ticketinhalt oder Aktionsziel hat sich seit der Vorschau geaendert.")
            execution_id = secrets.token_urlsafe(18)
            con.execute("INSERT INTO action_execution(id,plugin_key,entity_kind,entity_id,preview_hash,idempotency_key,state,started_at,result_summary) "
                        "VALUES(?,?,?,?,?,?, 'executing', ?, '{}')",
                        (execution_id, plugin_key, "ticket", str(ticket["id"]), current_hash, idem, now))
            con.execute("UPDATE action_preview SET consumed_at=? WHERE id=?", (now, preview_id))
            con.commit()
            subject = current["subject"]
            ticket_body_html = current["bodyHtml"]
            ticket_id = ticket["id"]
        except Exception:
            con.rollback()
            con.close()
            raise
        finally:
            try:
                con.close()
            except Exception:
                pass
    try:
        from plugins.actions import TVDBTicketAction, action_context
        from quellen import SAFE_HTTP
        from net.redaction import REDACTOR
        from config import TVDB_USER, TVDB_PASSWORD
        REDACTOR.register(TVDB_USER)
        REDACTOR.register(TVDB_PASSWORD)
        action = TVDBTicketAction()
        ctx = action_context(SAFE_HTTP, _db_rows)
        result = action.execute(ctx, current, True)
        number = int(result["externalId"])
        finished = time.time()
        with DB_LOCK:
            con = sqlite3.connect(speicher.DB_PATH, timeout=10)
            try:
                with con:
                    con.execute("UPDATE ticket SET status='eingereicht',tvdb_no=?,tvdb_status='Open',tvdb_checked=?,updated=? WHERE id=?",
                                (number, finished, finished, ticket_id))
                    con.execute("UPDATE action_execution SET state='succeeded',external_id=?,finished_at=?,result_summary=? WHERE id=?",
                                (str(number), finished, json.dumps({"tvdbNumber": number}), execution_id))
                    con.execute("INSERT INTO ticket_status_event(ticket_id,status,occurred_at,event_source,details_json) VALUES(?,?,?,?,?)",
                                (ticket_id, "eingereicht", finished, "tvdb-ticket-action", json.dumps({"tvdbNumber": number})))
            finally:
                con.close()
        return _reply(handler, 200, {"state": "succeeded", "ticketId": ticket_id, "tvdbNumber": number})
    except Exception:
        finished = time.time()
        with DB_LOCK:
            con = sqlite3.connect(speicher.DB_PATH, timeout=10)
            try:
                with con:
                    con.execute("UPDATE action_execution SET state='unknown',finished_at=?,result_summary=? WHERE id=?",
                                (finished, json.dumps({"message": "External result unknown"}), execution_id))
            finally:
                con.close()
        return _error(handler, 502, "action_result_unknown", "Ausfuehrungsergebnis unklar; bitte Ticket beim Anbieter pruefen. Es wird nicht automatisch wiederholt.")


def dispatch_phase3(handler, method, path, body=None):
    method = method.upper()
    if method == "GET":
        if path == "/api/v1/tickets":
            return _reply(handler, 200, {"items": _ticket_list()})
        match = re.fullmatch(r"/api/v1/tickets/(\d+)", path)
        if match:
            ticket = _read_ticket(int(match.group(1)))
            if not ticket:
                return _error(handler, 404, "ticket_not_found", "Ticket nicht gefunden.")
            return _reply(handler, 200, {"item": _ticket_detail(ticket)})
        match = re.fullmatch(r"/api/v1/tickets/(\d+)/export", path)
        if match:
            ticket = _read_ticket(int(match.group(1)))
            if not ticket:
                return _error(handler, 404, "ticket_not_found", "Ticket nicht gefunden.")
            handler._send(200, ticket["body"] or "", "text/plain; charset=utf-8", {"X-Content-Type-Options": "nosniff"})
            return True
        profile_match = re.fullmatch(r"/api/v1/sources/([a-z0-9-]+)/access-profile", path)
        if profile_match:
            source_id = profile_match.group(1)
            rows = _db_rows("SELECT * FROM source_config WHERE id=?", (source_id,))
            if not rows:
                return _error(handler, 404, "source_not_found", "Quelle nicht gefunden.")
            row = rows[0]
            return _reply(handler, 200, {"id": source_id, "enabled": bool(row["enabled"]),
                "hosts": json.loads(row["granted_hosts_json"] or "[]"),
                "allowExternal": bool(row["allow_external"]), "allowPrivate": bool(row["allow_private"]),
                "includeInNightly": bool(row["include_in_nightly"]),
                "credentialsConfigured": _env_configured(source_id), "writesExternal": False})
        if path == "/api/v1/sources":
            rows = _db_rows("SELECT * FROM source_config ORDER BY id")
            return _reply(handler, 200, {"items": [{"id": row["id"], "name": row["name"],
                "enabled": bool(row["enabled"]), "languages": json.loads(row["languages_json"] or "[]"),
                "includeInNightly": bool(row["include_in_nightly"]), "hosts": json.loads(row["granted_hosts_json"] or "[]"),
                "credentialsConfigured": _env_configured(row["id"]), "nightlyCapable": BUILTIN_SOURCES.get(row["id"], {}).get("nightly", False)}
                for row in rows]})
        if path == "/api/v1/actions":
            rows = _db_rows("SELECT plugin_key,enabled FROM action_config ORDER BY plugin_key")
            return _reply(handler, 200, {"items": [{"id": row["plugin_key"], "enabled": bool(row["enabled"] and _env_configured(row["plugin_key"])),
                "credentialsConfigured": _env_configured(row["plugin_key"]), "requiresPreview": True,
                "requiresConfirmation": True, "idempotencyProtected": True} for row in rows]})
        return False
    if method in {"POST", "PUT", "DELETE"}:
        search_match = re.fullmatch(r"/api/v1/series/(\d+)/search", path)
        if search_match and method == "POST":
            if not isinstance(body, dict) or set(body) - {"sources", "season"}:
                return _error(handler, 400, "invalid_request", "Erwartet wird sources? und season?.")
            sources = body.get("sources")
            if sources is not None and (not isinstance(sources, list) or any(item not in BUILTIN_SOURCES for item in sources)):
                return _error(handler, 400, "invalid_sources", "sources muss eine Liste bekannter Quell-IDs sein.")
            season = body.get("season")
            if season is not None and (not isinstance(season, int) or isinstance(season, bool) or season < 0):
                return _error(handler, 400, "invalid_season", "season muss eine nichtnegative Ganzzahl sein.")
            try:
                from sonarr import get_data
                from vorschlaege import search_chain, search_series
                series_id = int(search_match.group(1))
                series = next((item for item in get_data().get("series", []) if item.get("id") == series_id), None)
                if not series:
                    return _error(handler, 404, "series_not_found", "Serie nicht im Sonarr-Bestand gefunden.")
                selected = sources if sources is not None else [item["id"] for item in _db_rows(
                    "SELECT id FROM source_config WHERE enabled=1 ORDER BY id")
                    if item["id"] in BUILTIN_SOURCES and item["id"] != "deepl"]
                if "deepl" in selected:
                    return _error(handler, 400, "derived_source_only", "DeepL ist eine abgeleitete Quelle und wird nur aus Kandidaten gespeist.")
                if set(selected) == set(("mediathek", "tmdb", "fernsehserien")):
                    ordered = tuple(key for key in ("mediathek", "tmdb", "fernsehserien") if key in selected)
                    counts, note = search_chain(series, season, stages=ordered)
                    result = {"count": counts, "note": note}
                else:
                    result = {"sources": []}
                    for source_id in selected:
                        count, note = search_series(series, source_id, season)
                        result["sources"].append({"id": source_id, "count": count, "note": note})
                return _reply(handler, 200, {"seriesId": series_id, **result})
            except Exception:
                return _error(handler, 502, "search_failed", "Quellensuche fehlgeschlagen; Details wurden redigiert.")
        if path == "/api/v1/tickets" and method == "POST":
            if not isinstance(body, dict) or set(body) != {"seriesId", "season", "lang"}:
                return _error(handler, 400, "invalid_request", "Erwartet wird genau {seriesId, season, lang}.")
            if not all(isinstance(body[k], int) and not isinstance(body[k], bool) for k in ("seriesId", "season")) or body["lang"] not in ("de", "en"):
                return _error(handler, 400, "invalid_request", "Serien-ID/Staffel oder Sprache ist ungueltig.")
            try:
                from sonarr import get_data
                series_items = get_data().get("series", [])
                series = next((item for item in series_items if item.get("id") == body["seriesId"]), None)
                if not series:
                    return _error(handler, 404, "series_not_found", "Serie nicht im Sonarr-Bestand gefunden.")
                from ticket import create_ticket
                ticket_id, message = create_ticket(series, body["season"], body["lang"])
                if ticket_id is None:
                    return _error(handler, 409, "ticket_not_created", message)
                return _reply(handler, 201, {"item": _ticket_detail(_read_ticket(ticket_id))})
            except Exception:
                return _error(handler, 502, "ticket_creation_failed", "Lokale Ticketvorlage konnte nicht erstellt werden.")
        match = re.fullmatch(r"/api/v1/tickets/(\d+)", path)
        if match and method == "DELETE":
            ticket_id = int(match.group(1))
            ticket = _read_ticket(ticket_id)
            if not ticket:
                return _error(handler, 404, "ticket_not_found", "Ticket nicht gefunden.")
            if ticket["status"] != "erstellt" or ticket["tvdb_no"]:
                return _error(handler, 409, "ticket_not_draft", "Nur ein lokaler Entwurf kann geloescht werden.")
            with DB_LOCK:
                con = sqlite3.connect(speicher.DB_PATH, timeout=10)
                try:
                    with con:
                        con.execute("DELETE FROM ticket_item WHERE ticket_id=?", (ticket_id,))
                        con.execute("DELETE FROM ticket WHERE id=?", (ticket_id,))
                finally:
                    con.close()
            return _reply(handler, 200, {"deleted": True, "ticketId": ticket_id})
        match = re.fullmatch(r"/api/v1/sources/([a-z0-9-]+)/enabled", path)
        if match and method == "PUT":
            source_id = match.group(1)
            if source_id not in BUILTIN_SOURCES or not isinstance(body, dict) or set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
                return _error(handler, 400, "invalid_source", "Unbekannte Quelle oder erwartet wird {enabled: boolean}.")
            if body["enabled"] and not _env_configured(source_id):
                return _error(handler, 409, "source_credentials_missing", "Fuer diese Quelle fehlen erforderliche Zugangsdaten.")
            with DB_LOCK:
                con = sqlite3.connect(speicher.DB_PATH, timeout=10)
                try:
                    with con:
                        con.execute("UPDATE source_config SET enabled=?,updated_at=? WHERE id=?",
                                    (int(body["enabled"]), time.time(), source_id))
                finally:
                    con.close()
            return _reply(handler, 200, {"id": source_id, "enabled": body["enabled"]})
        match = re.fullmatch(r"/api/v1/actions/([a-z0-9-]+)/preview", path)
        if match and method == "POST":
            return _action_preview(handler, match.group(1), body)
        match = re.fullmatch(r"/api/v1/actions/([a-z0-9-]+)/execute", path)
        if match and method == "POST":
            return _action_execute(handler, match.group(1), body)
    return False
