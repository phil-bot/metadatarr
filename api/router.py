"""Kleiner JSON-Router mit Sitzungs-CSRF, Same-Origin-Prüfung und Body-Limit."""
import hmac
import json
import os
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


def dispatch(handler, method, path):
    """Behandelt `/api/v1/*`; False bedeutet, dass der bestehende HTML-Router fortfahren soll."""
    if not (path == "/api/v1" or path.startswith("/api/v1/")):
        return False
    method = method.upper()
    if method == "GET" and path == "/api/v1/health":
        return False  # bestehender JSON-Health-Endpunkt bleibt exakt erhalten
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
            "features": ["json-api-v1"],
        }, headers, preserve_fields=("csrfToken",))
        return True
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _same_origin_request(handler):
            _error(handler, 403, "origin_rejected", "Anfrage abgewiesen", "Host und Origin stimmen nicht überein.")
            return True
        if not _check_csrf(handler):
            _error(handler, 403, "csrf_rejected", "Anfrage abgewiesen", "CSRF-Token fehlt oder ist ungültig; Sitzung neu laden.")
            return True
        _, ok = _read_json_body(handler)
        if not ok:
            return True
        _error(handler, 404, "not_found", "Nicht gefunden", "Dieser API-Endpunkt ist noch nicht verfügbar.")
        return True
    if method == "OPTIONS":
        _error(handler, 405, "method_not_allowed", "Methode nicht erlaubt", "CORS ist deaktiviert; nutze dieselbe Origin.")
        return True
    _error(handler, 404, "not_found", "Nicht gefunden", "Dieser API-Endpunkt ist noch nicht verfügbar.")
    return True
