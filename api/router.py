"""Kleiner JSON-Router mit Sitzungs-CSRF, Same-Origin-Prüfung und Body-Limit."""
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from net.redaction import REDACTOR


HARD_BODY_LIMIT = 10_000_000
try:
    BODY_LIMIT = min(max(int(os.environ.get("METADATARR_MAX_BODY_BYTES", "1048576")), 1), HARD_BODY_LIMIT)
except ValueError:
    BODY_LIMIT = 1_048_576
_SESSION_TTL = 8 * 60 * 60
_SESSION_MAX = 4096
_COOKIE_NAME = "metadatarr_session"


class _Sessions:
    def __init__(self):
        self._lock = threading.RLock()
        self._items = OrderedDict()

    def issue(self, cookie_header):
        now = time.monotonic()
        sid = ""
        if cookie_header:
            try:
                jar = SimpleCookie()
                jar.load(cookie_header)
                morsel = jar.get(_COOKIE_NAME)
                sid = morsel.value if morsel else ""
            except Exception:
                sid = ""
        with self._lock:
            entry = self._items.get(sid) if sid else None
            if entry and now - entry[1] <= _SESSION_TTL:
                self._items.move_to_end(sid)
                self._items[sid] = (entry[0], now)
                return sid, entry[0], False
            sid = secrets.token_urlsafe(24)
            token = secrets.token_urlsafe(32)
            self._items[sid] = (token, now)
            self._items.move_to_end(sid)
            while len(self._items) > _SESSION_MAX:
                self._items.popitem(last=False)
            return sid, token, True

    def token_for(self, cookie_header):
        if not cookie_header:
            return None
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(_COOKIE_NAME)
            sid = morsel.value if morsel else ""
        except Exception:
            return None
        with self._lock:
            entry = self._items.get(sid)
            if not entry or time.monotonic() - entry[1] > _SESSION_TTL:
                self._items.pop(sid, None)
                return None
            self._items.move_to_end(sid)
            self._items[sid] = (entry[0], time.monotonic())
            return entry[0]


_SESSIONS = _Sessions()


def _error(handler, status, code, title, detail):
    payload = {
        "type": "about:blank",
        "title": title,
        "detail": REDACTOR.redact_text(detail),
        "code": code,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    handler._send(status, raw, "application/json; charset=utf-8", {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    })


def _send_json(handler, status, payload, extra_headers=None, preserve_fields=()):
    safe = REDACTOR.redact_value(payload)
    # A CSRF token is intentionally delivered only by the session bootstrap endpoint.
    # Keep it in that browser response, while audit/log redaction remains unchanged.
    for field in preserve_fields:
        if field in payload:
            safe[field] = payload[field]
    raw = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    }
    if extra_headers:
        headers.update(extra_headers)
    handler._send(status, raw, "application/json; charset=utf-8", headers)


def _authority(authority, scheme):
    if not authority or any(char in authority for char in "\r\n,\\"):
        return None
    try:
        parsed = urlsplit("//" + authority)
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host, port or (443 if scheme == "https" else 80)


def _same_origin_request(handler):
    origin = handler.headers.get("Origin", "")
    host = handler.headers.get("Host", "")
    if not origin or origin == "null":
        return False
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None):
            return False
        origin_authority = _authority(parsed.netloc, parsed.scheme.lower())
        host_authority = _authority(host, parsed.scheme.lower())
    except ValueError:
        return False
    if not origin_authority or origin_authority != host_authority:
        return False
    forwarded = (handler.headers.get("X-Forwarded-Proto", "").split(",", 1)[0]).strip().lower()
    if forwarded in ("http", "https") and forwarded != parsed.scheme.lower():
        return False
    return True


def _check_csrf(handler):
    expected = _SESSIONS.token_for(handler.headers.get("Cookie", ""))
    supplied = handler.headers.get("X-CSRF-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _read_json_body(handler):
    transfer = handler.headers.get("Transfer-Encoding", "").strip().lower()
    if transfer:
        _error(handler, 400, "chunked_body_unsupported", "Ungültige Anfrage", "Chunked Request-Bodies werden nicht unterstützt.")
        return None, False
    length_text = handler.headers.get("Content-Length", "0")
    try:
        length = int(length_text)
    except (TypeError, ValueError):
        _error(handler, 400, "invalid_content_length", "Ungültige Anfrage", "Content-Length ist ungültig.")
        return None, False
    if length < 0:
        _error(handler, 400, "invalid_content_length", "Ungültige Anfrage", "Content-Length ist ungültig.")
        return None, False
    if length > BODY_LIMIT:
        _error(handler, 413, "request_body_too_large", "Anfrage zu groß", "Der Request-Body überschreitet das erlaubte Limit.")
        return None, False
    content_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        _error(handler, 415, "json_required", "JSON erwartet", "Schreibende API-Aufrufe müssen application/json verwenden.")
        return None, False
    try:
        raw = handler.rfile.read(length) if length else b"{}"
    except OSError:
        _error(handler, 400, "body_read_failed", "Ungültige Anfrage", "Request-Body konnte nicht gelesen werden.")
        return None, False
    if len(raw) != length:
        _error(handler, 400, "body_incomplete", "Ungültige Anfrage", "Request-Body ist unvollständig.")
        return None, False
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _error(handler, 400, "invalid_json", "Ungültiges JSON", "Request-Body enthält kein gültiges UTF-8-JSON.")
        return None, False
    if not isinstance(body, (dict, list)):
        _error(handler, 400, "invalid_json_shape", "Ungültiges JSON", "Der JSON-Body muss ein Objekt oder Array sein.")
        return None, False
    return body, True


def _read_inventory(handler, series_id=None):
    from config import SONARR_API_KEY, SONARR_METADATA_LANGUAGE, SONARR_URL
    from core.missing import annotate_inventory
    from sonarr import get_data

    if not SONARR_URL or not SONARR_API_KEY:
        _error(handler, 503, "sonarr_not_configured", "Sonarr nicht konfiguriert", "SONARR_URL und SONARR_API_KEY muessen gesetzt sein.")
        return True
    try:
        payload = annotate_inventory(get_data(), SONARR_METADATA_LANGUAGE or None)
        fetched = payload.get("fetched")
        if hasattr(fetched, "isoformat"):
            payload["fetched"] = fetched.isoformat()
    except Exception:
        _error(handler, 502, "sonarr_unavailable", "Sonarr nicht erreichbar", "Der lesende Sonarr-Bestand konnte nicht geladen werden.")
        return True
    if series_id is not None:
        item = next((series for series in payload.get("series", []) if series.get("id") == series_id), None)
        if item is None:
            _error(handler, 404, "series_not_found", "Serie nicht gefunden", "Diese Serie ist nicht im geladenen Sonarr-Bestand.")
            return True
        _send_json(handler, 200, {"item": item, "fetched": payload.get("fetched")})
        return True
    _send_json(handler, 200, payload)
    return True


def _valid_object(body, fields):
    return isinstance(body, dict) and set(body) == set(fields)


def _refresh_metadata_snapshot():
    """Reberechnet Snapshots aus dem bereits geladenen Bestand; loest keinen Sonarr-Aufruf aus."""
    try:
        from config import SONARR_METADATA_LANGUAGE
        from core.missing import persist_metadata_snapshots
        from sonarr import STATE
        if STATE.get("data") is not None:
            persist_metadata_snapshots(STATE["data"], SONARR_METADATA_LANGUAGE or None)
    except Exception:
        pass


def _record_local_setting(action, kind, object_id, before, after, handler):
    try:
        from core.audit import record
        record(action, kind, object_id, before=before, after=after,
               request_id=handler.headers.get("X-Request-ID", ""))
    except Exception:
        # Auditfehler werden nicht an den API-Aufrufer durchgereicht.
        pass


def dispatch(handler, method, path):
    """JSON-API v1. Schreibpfade veraendern ausschliesslich lokale Metadatarr-Einstellungen."""
    if not (path == "/api/v1" or path.startswith("/api/v1/")):
        return False
    method = method.upper()
    if method == "GET" and path == "/api/v1/health":
        return False
    if method == "GET" and path == "/api/v1/session":
        sid, token, is_new = _SESSIONS.issue(handler.headers.get("Cookie", ""))
        secure = (handler.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower() == "https")
        cookie = f"{_COOKIE_NAME}={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_TTL}"
        if secure:
            cookie += "; Secure"
        headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
        if is_new:
            headers["Set-Cookie"] = cookie
        _send_json(handler, 200, {
            "csrfToken": token,
            "app": {"name": "Metadatarr", "version": "0.1.0"},
            "features": ["json-api-v1", "read-only-sonarr-inventory", "local-target-languages", "local-placeholder-rules"],
        }, headers, preserve_fields=("csrfToken",))
        return True
    if method == "GET":
        from api.phase3 import dispatch_phase3
        if dispatch_phase3(handler, method, path):
            return True
        if path == "/api/v1/series":
            return _read_inventory(handler)
        if path == "/api/v1/target-languages":
            from core.missing import get_target_languages
            _send_json(handler, 200, {"languages": get_target_languages()})
            return True
        if path == "/api/v1/placeholder-rules":
            from core.missing import list_rules
            _send_json(handler, 200, {"rules": list_rules()})
            return True
        match = re.fullmatch(r"/api/v1/series/(\d+)", path)
        if match:
            return _read_inventory(handler, int(match.group(1)))
        _error(handler, 404, "not_found", "Nicht gefunden", "Dieser GET-Endpunkt ist nicht verfuegbar.")
        return True
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _same_origin_request(handler):
            _error(handler, 403, "origin_rejected", "Anfrage abgewiesen", "Host und Origin stimmen nicht ueberein.")
            return True
        if not _check_csrf(handler):
            _error(handler, 403, "csrf_rejected", "Anfrage abgewiesen", "CSRF-Token fehlt oder ist ungueltig; Sitzung neu laden.")
            return True
        body, ok = _read_json_body(handler)
        if not ok:
            return True
        from api.phase3 import dispatch_phase3
        if dispatch_phase3(handler, method, path, body):
            return True
        if method != "PUT":
            known = (path in {"/api/v1/target-languages", "/api/v1/placeholder-rules"} or
                     bool(re.fullmatch(r"/api/v1/series/\d+/target-languages", path)))
            status = 405 if known else 404
            code = "method_not_allowed" if known else "not_found"
            title = "Methode nicht erlaubt" if known else "Nicht gefunden"
            detail = "Fuer diese lokalen Einstellungen ist ausschliesslich PUT freigegeben." if known else "Dieser API-Endpunkt ist nicht verfuegbar."
            _error(handler, status, code, title, detail)
            return True
        try:
            if path == "/api/v1/target-languages":
                if not _valid_object(body, ("languages",)):
                    _error(handler, 400, "invalid_request", "Ungueltige Anfrage", "Erwartet wird genau {languages: [...]}.")
                    return True
                from core.missing import get_target_languages, set_target_languages
                before = get_target_languages()
                after = set_target_languages(body["languages"])
                _refresh_metadata_snapshot()
                _record_local_setting("target-languages.updated", "app-setting", "target_languages", before, after, handler)
                _send_json(handler, 200, {"languages": after, "scope": "global"})
                return True
            if path == "/api/v1/placeholder-rules":
                if not _valid_object(body, ("rules",)):
                    _error(handler, 400, "invalid_request", "Ungueltige Anfrage", "Erwartet wird genau {rules: [...]}.")
                    return True
                from core.missing import list_rules, replace_ui_rules
                before_count = sum(1 for rule in list_rules() if rule["origin"] == "ui")
                rules = replace_ui_rules(body["rules"])
                _refresh_metadata_snapshot()
                after_count = sum(1 for rule in rules if rule["origin"] == "ui")
                _record_local_setting("placeholder-rules.replaced", "placeholder-rules", "ui",
                                      {"uiRuleCount": before_count}, {"uiRuleCount": after_count}, handler)
                _send_json(handler, 200, {"rules": rules})
                return True
            match = re.fullmatch(r"/api/v1/series/(\d+)/target-languages", path)
            if match:
                if not _valid_object(body, ("languages",)):
                    _error(handler, 400, "invalid_request", "Ungueltige Anfrage", "Erwartet wird genau {languages: [...]} oder {languages: null} zum Zuruecksetzen.")
                    return True
                from core.missing import get_target_languages, has_series_override, set_target_languages
                sid = int(match.group(1))
                before = {"languages": get_target_languages(sid), "override": has_series_override(sid)}
                languages = set_target_languages(body["languages"], series_id=sid)
                after = {"languages": languages, "override": has_series_override(sid)}
                _refresh_metadata_snapshot()
                _record_local_setting("series-target-languages.updated", "series-setting", str(sid), before, after, handler)
                _send_json(handler, 200, {"seriesId": sid, **after})
                return True
        except (ValueError, TypeError) as exc:
            _error(handler, 400, "invalid_setting", "Einstellung ungueltig", str(exc))
            return True
        _error(handler, 404, "not_found", "Nicht gefunden", "Dieser lokale Einstellungs-Endpunkt ist nicht verfuegbar.")
        return True
    if method == "OPTIONS":
        _error(handler, 405, "method_not_allowed", "Methode nicht erlaubt", "CORS ist deaktiviert; nutze dieselbe Origin.")
        return True
    _error(handler, 405, "method_not_allowed", "Methode nicht erlaubt", "Diese API-Methode ist nicht erlaubt.")
    return True
