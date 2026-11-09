"""tba-tickets – Anbindung an das TVDB-Support-Center: anmelden, Ticket einreichen, Status lesen.

Nur Standardbibliothek. Zugangsdaten kommen aus der Umgebung (.env) und werden nie ausgegeben oder gespeichert.
Die Abläufe entsprechen der Browser-Aufzeichnung (HAR, 27.10.2026):
  1. GET login.php (CSRF-Token), POST login.php mit __CSRFToken__, luser, lpasswd. Erfolg: Weiterleitung auf open.php.
  2. GET open.php (CSRF-Token). GET ajax.php/form/help-topic/<topicId> liefert das Formular als JSON (Feld „html“).
     Der Feldname des Betreffs ändert sich je Formular und wird deshalb aus dem Label „Issue Summary“ gelesen.
  3. POST open.php als multipart: __CSRFToken__, a=open, topicId, <Betreff-Feld>, message (HTML), draft_id.
     Erfolg: Weiterleitung auf tickets.php?id=<TVDB-Ticketnummer>.
  4. GET tickets.php?id=<Nummer>: Zeile „Ticket Status:“ mit dem Status (z. B. Open).
"""
import html as htmllib
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from config import TVDB_BASE_URL, TVDB_PASSWORD, TVDB_PAUSE, TVDB_TIMEOUT, TVDB_TOPIC_ID, TVDB_USER, USER_AGENT

TVDB_LOCK = threading.Lock()  # Anmeldungen und Anfragen nacheinander, nie parallel
REDIRECT_CODES = (301, 302, 303, 307, 308)


class TvdbError(Exception):
    """Fehler mit einer Meldung für die Anzeige. Enthält nie Zugangsdaten."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nicht folgen, damit die Ziel-Adresse (Ticketnummer) ausgewertet werden kann."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect())


def _snippet(text, limit=200):
    """Kurzer Klartext aus einer Fehlerantwort. Zugangsdaten werden maskiert."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.S | re.I)
    msg = re.sub(r"\s+", " ", _text(text))
    for secret in (TVDB_PASSWORD, TVDB_USER):
        if secret:
            msg = msg.replace(secret, "***")
    return msg[:limit]


def _page_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.S | re.I)
    return _snippet(m.group(1), 120) if m else ""


def _request(op, url, data=None, headers=None, schritt="Abfrage", erwartet=()):
    """Eine Anfrage mit Pause davor. Gibt (Statuscode, Location, Text) zurück.

    Status aus „erwartet“ gelten nur für diese Anfrage als gültig, z. B. 422 auf der Anmeldeseite,
    die der Browser ebenso erhält. Alle anderen Fehler nennen den Schritt, den Code und einen bereinigten Ausschnitt.
    """
    time.sleep(TVDB_PAUSE)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with op.open(req, timeout=TVDB_TIMEOUT) as resp:
            return resp.status, resp.headers.get("Location", "") or "", resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in REDIRECT_CODES:
            return exc.code, exc.headers.get("Location", "") or "", ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            body = ""
        if exc.code in erwartet:
            return exc.code, exc.headers.get("Location", "") or "", body
        title = _page_title(body)
        snippet = _snippet(body)
        details = " ".join(
            part for part in [f"Seite: „{title}“." if title else "", f"Antwort: {snippet}" if snippet else ""] if part
        )
        raise TvdbError(f"TVDB antwortet im Schritt „{schritt}“ mit HTTP {exc.code}. {details}".strip()) from None
    except (urllib.error.URLError, OSError):
        raise TvdbError("TVDB ist nicht erreichbar.") from None


def _csrf(text):
    m = re.search(r"<input[^>]*name=\"__CSRFToken__\"[^>]*value=\"([^\"]+)\"", text)
    m = m or re.search(r"<input[^>]*value=\"([^\"]+)\"[^>]*name=\"__CSRFToken__\"", text)
    if not m:
        raise TvdbError("TVDB-Seite ohne Sicherheits-Token. Die Seite hat sich vermutlich geändert.")
    return m.group(1)


def _text(fragment):
    """HTML-Schnipsel als Klartext."""
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def _login(op):
    if not TVDB_USER or not TVDB_PASSWORD:
        raise TvdbError("TVDB-Zugang fehlt: TVDB_USER und TVDB_PASSWORD in der .env setzen.")
    base = TVDB_BASE_URL
    _, _, page_text = _request(
        op, base + "/login.php", schritt="Anmeldeseite laden", erwartet=(422,)  # wie im Browser: Formular trotz 422
    )
    body = urllib.parse.urlencode(
        {"__CSRFToken__": _csrf(page_text), "luser": TVDB_USER, "lpasswd": TVDB_PASSWORD}
    ).encode("utf-8")
    status, loc, _ = _request(
        op, base + "/login.php", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": base + "/login.php"},
        schritt="Anmeldung senden",
    )
    if status not in REDIRECT_CODES or not loc or "login.php" in loc:
        raise TvdbError("Anmeldung bei TVDB fehlgeschlagen. TVDB_USER und TVDB_PASSWORD in der .env prüfen.")


def _multipart(fields):
    boundary = "----tba" + uuid.uuid4().hex
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        for name, value in fields
    ]
    chunks.append(f"--{boundary}--\r\n")
    return "".join(chunks).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _form_error(text):
    m = re.search(r"<div[^>]*class=\"[^\"]*(?:error|warning)[^\"]*\"[^>]*>(.*?)</div>", text, re.S | re.I)
    msg = _text(m.group(1)) if m else ""
    return msg[:200]


def _topic_fields(op):
    """Liest CSRF-Token und den Feldnamen des Betreffs für das Hilfethema aus dem Formular."""
    base = TVDB_BASE_URL
    _, _, page_text = _request(op, base + "/open.php", schritt="Formular öffnen")
    token = _csrf(page_text)
    _, _, raw = _request(
        op, f"{base}/ajax.php/form/help-topic/{TVDB_TOPIC_ID}",
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": base + "/open.php"},
        schritt="Formular für das Hilfethema laden",
    )
    try:
        form_html = json.loads(raw)["html"]
    except (ValueError, KeyError, TypeError):
        raise TvdbError("Formular für das Hilfethema konnte nicht geladen werden.") from None
    m = re.search(r"Issue Summary.*?<input[^>]*?\sname=\"([^\"]+)\"", form_html, re.S)
    if not m or 'name="message"' not in form_html:
        raise TvdbError("Betreff- oder Textfeld im TVDB-Formular nicht gefunden. Die Seite hat sich vermutlich geändert.")
    return token, m.group(1)


def submit_ticket(subject, html_body):
    """Reicht ein Ticket bei TVDB ein. Gibt die TVDB-Ticketnummer (int) zurück oder wirft TvdbError."""
    with TVDB_LOCK:
        op = _opener()
        _login(op)
        token, subject_field = _topic_fields(op)
        body, ctype = _multipart([
            ("__CSRFToken__", token),
            ("a", "open"),
            ("topicId", TVDB_TOPIC_ID),
            (subject_field, subject),
            ("message", html_body),
            ("draft_id", ""),
        ])
        status, loc, text = _request(
            op, TVDB_BASE_URL + "/open.php", data=body,
            headers={"Content-Type": ctype, "Referer": TVDB_BASE_URL + "/open.php"},
            schritt="Ticket einreichen",
        )
        m = re.search(r"tickets\.php\?id=(\d+)", loc)
        if status in REDIRECT_CODES and m:
            return int(m.group(1))
        raise TvdbError(_form_error(text) or "TVDB hat das Ticket nicht angenommen.")


def read_status(op, number):
    """Status eines Tickets aus tickets.php. Die Sitzung muss schon angemeldet sein."""
    status, _, text = _request(
        op, f"{TVDB_BASE_URL}/tickets.php?id={int(number)}", schritt=f"Status von Ticket {int(number)} lesen"
    )
    if status in REDIRECT_CODES:
        raise TvdbError("Ticket-Seite nicht lesbar (Anmeldung abgelaufen oder Weiterleitung).")
    m = re.search(r"Ticket Status:?</th>\s*<td[^>]*>(.*?)</td>", text, re.S | re.I)
    if not m:
        title = _page_title(text)
        hinweis = f" Seite: „{title}“." if title else ""
        raise TvdbError(f"Status für Ticket {number} nicht gefunden (Ticket fehlt oder Seite geändert).{hinweis}")
    return _text(m.group(1))[:40]


def statuses(numbers):
    """Liest mehrere Status in einer Sitzung. Gibt {Nummer: Status oder None} zurück.

    Ein fehlgeschlagenes Ticket ergibt None; Anmeldefehler werfen TvdbError.
    """
    out = {}
    with TVDB_LOCK:
        op = _opener()
        _login(op)
        for number in numbers:
            try:
                out[number] = read_status(op, number)
            except TvdbError:
                out[number] = None
    return out
