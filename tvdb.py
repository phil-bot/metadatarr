"""Protected TVDB Support Center transport. Writes are called only by the guarded action dispatcher."""
import html as htmllib
import json
import re
import threading
import time
import urllib.parse
import uuid

from config import TVDB_BASE_URL, TVDB_PASSWORD, TVDB_PAUSE, TVDB_TIMEOUT, TVDB_TOPIC_ID, TVDB_USER, USER_AGENT
from net.http_client import NetworkProfile, SafeHTTPClient, SafeHTTPSession
from net.redaction import REDACTOR

TVDB_LOCK = threading.Lock()
REDIRECT_CODES = (301, 302, 303, 307, 308)


class TvdbError(Exception):
    """Sanitized provider error without credentials or response body secrets."""


def _client_session(http_client=None, base_url=None):
    base = (base_url or TVDB_BASE_URL).rstrip("/")
    host = urllib.parse.urlsplit(base).hostname or ""
    if not host or urllib.parse.urlsplit(base).scheme not in ("https", "http"):
        raise TvdbError("TVDB-Zieladresse ist ungueltig.")
    profile = NetworkProfile(source_id="tvdb-ticket", granted_hosts=(host,), allow_external=True,
                             allow_private=False, timeout_seconds=min(max(TVDB_TIMEOUT, 1), 60),
                             connect_timeout_seconds=5, max_bytes=2_000_000,
                             min_interval_seconds=max(0, TVDB_PAUSE), max_redirects=3)
    return SafeHTTPSession(http_client or SafeHTTPClient(), profile, base), base


def _text(fragment):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def _snippet(text, limit=200):
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.S | re.I)
    return REDACTOR.redact_text(re.sub(r"\s+", " ", _text(text))[:limit])


def _page_title(text):
    match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.S | re.I)
    return _snippet(match.group(1), 120) if match else ""


def _request(session, url, data=None, headers=None, schritt="Abfrage", erwartet=()):
    method = "POST" if data is not None else "GET"
    try:
        response = session.request(url, method=method,
                                  headers={"User-Agent": USER_AGENT, **(headers or {})}, body=data)
    except Exception:
        raise TvdbError("TVDB ist nicht erreichbar oder die Netzwerkfreigabe wurde abgewiesen.") from None
    status = response.status
    location = next((value for key, value in response.headers.items() if key.lower() == "location"), "")
    text = response.body.decode("utf-8", "replace")
    if status >= 400 and status not in erwartet:
        title = _page_title(text)
        snippet = _snippet(text)
        details = " ".join(part for part in [f"Seite: {title}." if title else "",
                                               f"Antwort: {snippet}" if snippet else ""] if part)
        raise TvdbError(f"TVDB antwortet im Schritt {schritt} mit HTTP {status}. {details}".strip())
    return status, location, text


def _csrf(text):
    match = re.search(r'<input[^>]*name="__CSRFToken__"[^>]*value="([^"]+)"', text)
    match = match or re.search(r'<input[^>]*value="([^"]+)"[^>]*name="__CSRFToken__"', text)
    if not match:
        raise TvdbError("TVDB-Seite ohne Sicherheits-Token; das Formular hat sich geaendert.")
    return match.group(1)


def _same_origin_target(base, location):
    target = urllib.parse.urljoin(base + "/", location)
    left, right = urllib.parse.urlsplit(base), urllib.parse.urlsplit(target)
    left_port = left.port or (443 if left.scheme == "https" else 80)
    right_port = right.port or (443 if right.scheme == "https" else 80)
    if (left.scheme.lower(), left.hostname.lower(), left_port) != (right.scheme.lower(), right.hostname.lower(), right_port):
        raise TvdbError("TVDB leitete auf einen fremden Ursprung um; Vorgang abgebrochen.")
    return right


def _login(session, base, user, password):
    if not user or not password:
        raise TvdbError("TVDB-Zugangsdaten sind nicht konfiguriert.")
    _, _, page = _request(session, base + "/login.php", schritt="Anmeldeseite laden", erwartet=(422,))
    body = urllib.parse.urlencode({"__CSRFToken__": _csrf(page), "luser": user, "lpasswd": password}).encode("utf-8")
    status, location, _ = _request(session, base + "/login.php", data=body,
                                   headers={"Content-Type": "application/x-www-form-urlencoded",
                                            "Referer": base + "/login.php"}, schritt="Anmeldung senden")
    if status not in REDIRECT_CODES or not location:
        raise TvdbError("Anmeldung bei TVDB fehlgeschlagen.")
    _same_origin_target(base, location)
    if "login.php" in urllib.parse.urlsplit(urllib.parse.urljoin(base + "/", location)).path:
        raise TvdbError("Anmeldung bei TVDB fehlgeschlagen.")


def _multipart(fields):
    boundary = "----metadatarr" + uuid.uuid4().hex
    chunks = []
    for name, value in fields:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", str(name))
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{safe_name}\"\r\n\r\n{value}\r\n")
    chunks.append(f"--{boundary}--\r\n")
    return "".join(chunks).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _form_error(text):
    match = re.search(r'<div[^>]*class="[^"]*(?:error|warning)[^"]*"[^>]*>(.*?)</div>', text, re.S | re.I)
    return _snippet(match.group(1), 200) if match else ""


def _topic_fields(session, base, topic_id):
    _, _, page = _request(session, base + "/open.php", schritt="Formular oeffnen")
    token = _csrf(page)
    _, _, raw = _request(session, f"{base}/ajax.php/form/help-topic/{topic_id}",
                         headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                                  "Referer": base + "/open.php"}, schritt="Hilfethema laden")
    try:
        form_html = json.loads(raw)["html"]
    except (ValueError, KeyError, TypeError):
        raise TvdbError("Formular fuer das Hilfethema konnte nicht geladen werden.") from None
    match = re.search(r"Issue Summary.*?<input[^>]*?\sname=\"([^\"]+)\"", form_html, re.S)
    if not match or 'name="message"' not in form_html:
        raise TvdbError("Betreff- oder Textfeld im TVDB-Formular nicht gefunden.")
    return token, match.group(1)


def submit_ticket(subject, html_body, *, user=None, password=None, http_client=None,
                  base_url=None, topic_id=None):
    """Submit once through SafeHTTPClient; called only after preview/confirmation/idempotency checks."""
    user = TVDB_USER if user is None else user
    password = TVDB_PASSWORD if password is None else password
    base = (base_url or TVDB_BASE_URL).rstrip("/")
    topic = str(topic_id or TVDB_TOPIC_ID)
    REDACTOR.register(user)
    REDACTOR.register(password)
    with TVDB_LOCK:
        session, base = _client_session(http_client, base)
        _login(session, base, user, password)
        token, subject_field = _topic_fields(session, base, topic)
        body, content_type = _multipart([
            ("__CSRFToken__", token), ("a", "open"), ("topicId", topic),
            (subject_field, subject), ("message", html_body), ("draft_id", ""),
        ])
        status, location, response_text = _request(
            session, base + "/open.php", data=body,
            headers={"Content-Type": content_type, "Referer": base + "/open.php"},
            schritt="Ticket einreichen")
        if status in REDIRECT_CODES and location:
            target = _same_origin_target(base, location)
            if target.path.endswith("/tickets.php"):
                match = re.search(r"(?:^|&)id=(\d+)(?:&|$)", target.query)
                if match:
                    return int(match.group(1))
        raise TvdbError(_form_error(response_text) or "TVDB hat das Ticket nicht angenommen.")


def _read_status(session, base, number):
    status, _, text = _request(session, f"{base}/tickets.php?id={int(number)}", schritt="Ticketstatus lesen")
    if status in REDIRECT_CODES:
        raise TvdbError("Ticket-Seite nicht lesbar; Anmeldung abgelaufen oder Weiterleitung.")
    match = re.search(r"Ticket Status:?</th>\s*<td[^>]*>(.*?)</td>", text, re.S | re.I)
    if not match:
        title = _page_title(text)
        suffix = f" Seite: {title}." if title else ""
        raise TvdbError(f"Status fuer Ticket {number} nicht gefunden.{suffix}")
    return _text(match.group(1))[:40]


def statuses(numbers, *, user=None, password=None, http_client=None, base_url=None):
    """Read ticket states in one protected session; never submits or mutates a remote ticket."""
    user = TVDB_USER if user is None else user
    password = TVDB_PASSWORD if password is None else password
    if not user or not password:
        raise TvdbError("TVDB-Zugangsdaten sind nicht konfiguriert.")
    REDACTOR.register(user)
    REDACTOR.register(password)
    base = (base_url or TVDB_BASE_URL).rstrip("/")
    out = {}
    with TVDB_LOCK:
        session, base = _client_session(http_client, base)
        _login(session, base, user, password)
        for number in numbers:
            try:
                out[number] = _read_status(session, base, number)
            except TvdbError:
                out[number] = None
    return out
