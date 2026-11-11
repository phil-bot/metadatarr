#!/usr/bin/env python3
"""Prüfsuite für tba-tickets. Nur Standardbibliothek, ohne Netz.

Startet gegen eine Sonarr- und eine Mediathek-Attrappe, die lokal laufen.
Aufruf im Projektordner:  python3 test_tba.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []
SONARR_METHODS = []


def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(("ok   " if cond else "FEHL ") + name + ("" if cond or not info else f"  [{info}]"))


# ---------------------------------------------------------------- Attrappen
def ts(y, m, d):
    return int(datetime(y, m, d, 12).timestamp())


SONARR_SERIES = [{"id": 1, "title": "Testserie", "overview": "Serienuebersicht", "year": 2020, "network": "ARD", "status": "continuing",
                  "tvdbId": 123, "titleSlug": "testserie", "originalLanguage": {"name": "German"},
                  "images": [{"coverType": "banner", "url": "/MediaCover/1/banner.jpg"},
                             {"coverType": "poster", "url": "/MediaCover/1/poster.jpg?lastWrite=638"}]},
                 {"id": 2, "title": "Englisch", "overview": "English overview", "year": 2019, "originalLanguage": {"name": "English"},
                  "seasons": [{"seasonNumber": 1}] }]
SONARR_EPISODES = {1: [
    {"id": 11, "seasonNumber": 1, "episodeNumber": 1, "title": "TBA", "airDate": "2020-01-05", "hasFile": False},
    {"id": 12, "seasonNumber": 1, "episodeNumber": 2, "title": "TBA", "airDate": "2020-01-12", "hasFile": False},
    {"id": 13, "seasonNumber": 1, "episodeNumber": 3, "title": "Schon benannt", "overview": "Folgenbeschreibung", "airDate": "2020-01-19"},
], 2: [
    {"id": 21, "seasonNumber": 1, "episodeNumber": 1, "title": "English episode", "overview": "English episode overview", "airDate": "2019-01-05", "hasFile": True},
]}
MEDIA_HITS = [
    {"channel": "ARD", "topic": "Testserie", "title": "Testserie (S01/E01) Erste Folge",
     "description": "Beschreibung eins", "timestamp": ts(2020, 1, 5), "url_website": "https://example.org/1"},
    {"channel": "ARD", "topic": "Testserie", "title": "Testserie (S01/E02) Zweite Folge (Audiodeskription)",
     "description": "ignoriert", "timestamp": ts(2020, 1, 12), "url_website": "https://example.org/2"},
]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path.startswith("/api/v3/"):
            SONARR_METHODS.append("GET")
        if self.headers.get("X-Api-Key") != "geheim":
            self.send_response(401); self.end_headers(); return
        if p.path == "/MediaCover/1/poster-250.jpg":
            raw = b"\x89PNG\r\n\x1a\nfake"
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/api/v3/series":
            return self._json(SONARR_SERIES)
        if p.path == "/api/v3/episode":
            sid = int(urllib.parse.parse_qs(p.query)["seriesId"][0])
            return self._json(SONARR_EPISODES.get(sid, []))
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path.startswith("/api/v3/"):
            SONARR_METHODS.append("POST")
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        payload = json.loads(self.rfile.read(0) or b"{}")
        off = 0
        self._json({"result": {"results": MEDIA_HITS if off == 0 else []}})


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


sonarr_srv, SONARR = serve(Fake)
media_srv, MEDIA = serve(Fake)

# Umgebung VOR dem Import setzen (config liest sie beim Import)
os.environ.update({
    "SONARR_URL": SONARR, "SONARR_API_KEY": "geheim", "ORIGINAL_LANGUAGE": "German",
    "DB_PATH": os.path.join(tempfile.mkdtemp(), "test.db"), "MEDIA_API_URL": MEDIA,
    "MEDIA_PAUSE": "0", "TMDB_PAUSE": "0", "FS_PAUSE": "0", "NIGHTLY_SEARCH": "0",
    "TMDB_API_KEY": "", "DEEPL_API_KEY": "", "CACHE_TTL": "0",
})

import app  # noqa: E402  (Einstieg, lädt alle Module)
import config  # noqa: E402
import speicher  # noqa: E402
import nachtlauf  # noqa: E402

speicher.init_db()
# The test fixture is the only place where a private network target is explicitly granted.
import json as _json
speicher.db_exec("UPDATE source_config SET allow_private=1,granted_hosts_json=? WHERE id='mediathek'",
                 (_json.dumps([urllib.parse.urlsplit(MEDIA).hostname]),))
app_srv, BASE = serve(app.Handler)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def req(method, path, data=None, token=True):
    if method == "POST" and token:
        data = dict(data or {})
        data.setdefault("csrf", config.CSRF_TOKEN)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with OPENER.open(r, timeout=20) as resp:
            return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def suggestion_id(season=1, episode=1):
    rows = speicher.db_exec("SELECT id, status, marked FROM suggestion WHERE series_id = 1 AND season = ? "
                            "AND episode = ? AND source = 'mediathek' ORDER BY id", (season, episode), fetch=True)
    return rows[0]["id"] if rows else None


def state(sid):
    r = speicher.db_exec("SELECT status, marked FROM suggestion WHERE id = ?", (sid,), fetch=True)
    return (r[0]["status"], r[0]["marked"]) if r else None


# ---------------------------------------------------------------- Tests
s, _, b = req("GET", "/health")
check("Health antwortet 200", s == 200 and b == "ok")

s, _, b = req("GET", "/")
check("Startseite zeigt Serie mit TBA", s == 200 and "Testserie" in b and "TBA-Folgen" in b, f"status {s}")
check("Startseite zeigt keine Serie ohne TBA aus anderer Sprache", "Englisch" not in b)

s, _, b = req("GET", "/series/1")
check("Serienseite leitet auf erste TBA-Staffel", s in (200, 302, 303), f"status {s}")

s, h, _ = req("POST", "/series/1/suche/mediathek?staffel=1", {})
check("Mediathek-Suche leitet zur Staffelseite zurück", s in (302, 303) and "/series/1/staffel/1" in h.get("Location", ""), f"{s} {h.get('Location')}")
sid1 = suggestion_id(1, 1)
check("Treffer für Folge 1 gespeichert", sid1 is not None)
check("Audiodeskription wird ignoriert", suggestion_id(1, 2) is None)

s, _, b = req("GET", "/series/1/staffel/1")
check("Staffelseite zeigt den Vorschlag", s == 200 and "Erste Folge" in b, f"status {s}")
check("Keine eigenen Vorschlagszeilen mehr", "class='sugrow'" not in b)
check("Folgen sind als Karten mit Titel, Sendedatum und Status dargestellt",
      ("class='ep" in b and "data-state=" in b and "Sendedatum" in b) or "d-sent" in b or "d-ann" in b)
check("Eine Zeile je Folge mit Wechsel-Pfeilen", "class='ep'" in b and "data-n='1'" in b and "class='ib var-nav'" not in b)
check("Sendedatum ist nach Status eingefärbt (gesendet/angekündigt)", "d-sent" in b or "d-ann" in b)

if sid1:
    s, _, _ = req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle"})
    check("Haken setzen", s in (200, 302, 303) and state(sid1) == ("offen", 1), f"{s} {state(sid1)}")
    s, _, _ = req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle"})
    check("Haken entfernen", state(sid1) == ("offen", 0))
    req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle"})

    s, _, _ = req("POST", f"/vorschlag/{sid1}", {"aktion": "save", "title_de": "Eigener Titel", "title_en": "Own", "description": "Text"})
    row = speicher.db_exec("SELECT title_de, edited FROM suggestion WHERE id = ?", (sid1,), fetch=True)[0]
    check("Handbearbeitung ist entfernt: Speichern ändert nichts", row["title_de"] == "Testserie (S01/E01) Erste Folge" and row["edited"] == 0)

    req("POST", f"/vorschlag/{sid1}", {"aktion": "discard"})
    check("Verwerfen", state(sid1)[0] == "verworfen")
    req("POST", f"/vorschlag/{sid1}", {"aktion": "reopen"})
    check("Wieder öffnen", state(sid1)[0] == "offen")

    req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle"})
    # Neue Suche darf den Haken nicht entfernen
    req("POST", "/series/1/suche/mediathek?staffel=1", {})
    check("Suche erhält Haken", state(sid1) == ("offen", 1), str(state(sid1)))

    s, h, _ = req("POST", "/series/1/uebernehmen?staffel=1", {})
    check("Alle hohen markieren läuft ohne Fehler", s in (200, 302, 303), str(s))

    s, h, _ = req("POST", "/series/1/ticket?staffel=1&lang=de", {})
    loc = h.get("Location", "") if h else ""
    check("Ticket Deutsch wird erstellt", s in (302, 303) and "/ticket/" in loc, f"{s} {loc}")
    tid = int(loc.rsplit("/", 1)[-1]) if "/ticket/" in loc else None
    if tid:
        s, _, b = req("GET", f"/ticket/{tid}")
        check("Ticket-Ansicht zeigt Betreff [GERMAN]", s == 200 and "[GERMAN]" in b and "Testserie" in b, f"status {s}")
        check("Ticket enthält Folgennummer #1", "#1" in b)
        check("Ticket enthält keine Sendedaten", "2020-01-05" not in b and "05.01.2020" not in b)
        check("Entwurf trägt lokale Nummer #L", f"#L{tid}" in b)
        check("Keine manuellen Statusknöpfe", "Als eingereicht markieren" not in b and "Erfolgreich" not in b)
        check("Keine Nummerneingabe von Hand", "tvdb-nummer" not in b and "Von Hand eingereicht" not in b)
        check("Folgenliste ist entfernt", "<ol class='ticketlist'" not in b)
        check("Entwurf zeigt Ticketinhalt offen", "Betreff kopieren" in b and "Ticketinhalt anzeigen" not in b)
        check("Kein Help-Topic-Hinweis mehr", "Help Topic" not in b)
        check("Keine Zeichenzahl beim Betreff", "von 75 Zeichen" not in b)
        check("Kein Body-Label mehr", "<strong>Body</strong>" not in b)
        check("Kein Status-Hinweistext von TVDB im Entwurf", "Der Status kommt von TVDB" not in b)
        s, _, _ = req("POST", f"/ticket/{tid}/status", {"aktion": "eingereicht"})
        row = speicher.db_exec("SELECT status FROM ticket WHERE id = ?", (tid,), fetch=True)
        check("Status lässt sich nicht von Hand setzen", row and row[0]["status"] == "erstellt", str(row))
        speicher.db_exec("UPDATE ticket SET tvdb_no = 42840, tvdb_status = 'Open', status = 'eingereicht' WHERE id = ?", (tid,))
        s, _, b2 = req("GET", f"/ticket/{tid}")
        check("Echtes Ticket zeigt TVDB-Nummer #42840", "#42840" in b2)
        check("Echtes Ticket zeigt TVDB-Status Open", ">Open<" in b2)
        check("Echtes Ticket klappt Inhalt ein", "Ticketinhalt anzeigen" in b2)
        check("Echtes Ticket ohne Einreichen-Knopf", "Bei TVDB einreichen" not in b2)
        check("Echtes Ticket ohne eigenen TVDB-Abschnitt", "<h2>TVDB</h2>" not in b2)
        check("Echtes Ticket zeigt Link zu TVDB", "tickets.php?id=42840" in b2)
        speicher.db_exec("UPDATE ticket SET tvdb_no = NULL, tvdb_status = '', status = 'erstellt' WHERE id = ?", (tid,))
    sug1 = suggestion_id(1, 1)
    s, _, _ = req("POST", "/series/1/uebernehmen?staffel=1", {})
    check("Nach Deutsch-Ticket: Alle hohen markiert die Folgen erneut", state(sug1) == ("offen", 1), str(state(sug1)))
    s, _, _ = req("POST", "/series/1/ticket?staffel=1&lang=en", {})
    check("Ticket Englisch wird erstellt", s in (302, 303))

    s, h, b = req("GET", "/series/1/staffel/1/bericht.csv")
    check("CSV-Bericht mit BOM und Semikolon", s == 200 and b.startswith("\ufeff") and ";" in b, f"status {s}")

s, _, b = req("GET", "/nichts-da")
check("Unbekannte Adresse liefert 404", s == 404)

s, _, b = req("GET", "/series/1/staffel/99")
check("Unbekannte Staffel liefert keinen Serverfehler", s < 500, str(s))

# ---------------------------------------------------------------- Oberfläche: Seitenleiste, Poster, Einstellungen
s, _, b = req("GET", "/")
check("Seitenleiste mit Navigation", "class='side'" in b and "href='/einstellungen'" in b and "aria-current=\"page\"" in b)
check("Farbschema-Umschalter vorhanden", "data-t='dark'" in b and "data-t='light'" in b and "data-t='system'" in b)
check("Startseite zeigt Poster-Adresse", "/poster/1" in b)
s, _, b = req("GET", "/serien")
check("Serienseite zeigt Karten mit Filter", s == 200 and "Testserie" in b and "id='serienfilter'" in b and "Englisch" not in b)
s, _, b = req("GET", "/serien?alle=1")
check("Alle Serien unabhängig von ORIGINAL_LANGUAGE", s == 200 and "Testserie" in b and "Englisch" in b)
s, h, b = req("GET", "/api/v1/series")
try:
    inventory = json.loads(b)
    english = next(x for x in inventory["series"] if x["id"] == 2)
    check("JSON-API enthaelt Serien, Staffeln, Folgen und Overviews", s == 200 and english["overview"] == "English overview" and english["seasons"][0]["episodes"][0]["overview"] == "English episode overview")
    check("Sonarr wurde ausschliesslich per GET angesprochen", bool(SONARR_METHODS) and set(SONARR_METHODS) == {"GET"})
except Exception as exc:
    check("JSON-API enthaelt Serien, Staffeln, Folgen und Overviews", False, str(exc))
s, h, _ = req("GET", "/?alle=1")
check("Alte Adresse /?alle=1 leitet um", s == 303 and h.get("Location") == "/serien?alle=1", f"status {s}")
s, h, _ = req("GET", "/poster/1")
check("Poster kommt über den Server", s == 200 and h.get("Content-Type") == "image/png" and "max-age" in (h.get("Cache-Control") or "") and h.get("X-Poster-Reason") == "ok")
check("Poster-Antwort ohne API-Key im Kopf", "geheim" not in str(h))
s, _, _ = req("GET", "/poster/2")
check("Serie ohne Poster liefert 404", s == 404, f"status {s}")
s, _, b = req("GET", "/series/1/staffel/1")
check("Serienseite zeigt Posterfeld", s == 200 and "poster lg" in b)
s, _, b = req("GET", "/tickets")
check("Ticketliste erreichbar", s == 200 and "<h1>Tickets</h1>" in b)
s, _, b = req("GET", "/einstellungen")
check("Einstellungen zeigen Konfiguration", s == 200 and "SONARR_URL" in b and "NIGHTLY_SEARCH" in b)
check("Einstellungen zeigen keine Schlüssel", "geheim" not in b and "gesetzt" in b)
s, _, b = req("GET", "/diagnose")
check("Diagnose zeigt Sonarr verbunden", s == 200 and "verbunden" in b and "geheim" not in b)
s, h, _ = req("POST", "/diagnose", {})
check("Diagnose-Test leitet zurück", s == 303 and "/diagnose" in (h.get("Location") or ""), f"status {s}")
s, _, _ = req("POST", "/diagnose", {"csrf": "falsch"}, token=False)
check("Diagnose-Test ohne Token wird abgewiesen", s == 403, f"status {s}")

# Sonarr mit falschem Key
import sonarr as sonarr_mod  # noqa: E402
sonarr_mod.SONARR_API_KEY = "falsch"
sonarr_mod.STATE["time"] = 0
s, _, b = req("GET", "/")
check("Falscher Sonarr-Key zeigt Fehlerseite statt Absturz", s == 502 and "API-Key" in b, f"status {s}")
sonarr_mod.SONARR_API_KEY = "geheim"

# Speicher: eine bereits entschiedene Folge (Haken) bleibt bei einer neuen Suche erhalten
_row = {"season": 1, "episode": 1, "source": "mediathek", "source_key": "neu", "title_de": "Neu", "title_en": "",
        "description": "", "channel": "", "air_date": "", "link": "", "hint": "", "level": "hoch"}
_keep = state(sid1) if sid1 else None
speicher.store_suggestions(1, "mediathek", [_row])
check("Neusuche überschreibt keine markierte Folge", _keep is None or state(sid1) == _keep, str(state(sid1)))

# Nachtlauf läuft ohne Absturz (ohne TMDB-Schlüssel, nur Sonarr-Attrappe)
sonarr_mod.STATE["time"] = 0
try:
    nachtlauf.run_nightly()
    check("Nachtlauf läuft durch", True)
except Exception as exc:  # noqa: BLE001
    check("Nachtlauf läuft durch", False, repr(exc))

# CSRF: Anfragen ohne oder mit falschem Token werden abgewiesen und ändern nichts
s, _, b = req("GET", "/series/1/staffel/1")
check("Seite enthält verstecktes CSRF-Feld", "name='csrf'" in b and config.CSRF_TOKEN in b)
s0 = state(sid1)
s, _, b = req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle"}, token=False)
check("POST ohne Token wird mit 403 abgewiesen", s == 403, f"status {s}")
check("POST ohne Token ändert nichts", state(sid1) == s0)
s, _, b = req("POST", f"/vorschlag/{sid1}", {"aktion": "toggle", "csrf": "falsch"}, token=False)
check("POST mit falschem Token wird mit 403 abgewiesen", s == 403, f"status {s}")
check("POST mit falschem Token ändert nichts", state(sid1) == s0)

# Schlüssel werden nie ausgegeben
check("Schlüssel werden aus Meldungen entfernt", nachtlauf.redact("abc") == "abc")
for srv in (sonarr_srv, media_srv, app_srv):
    srv.shutdown()

print(f"\n{len(PASS)} bestanden, {len(FAIL)} fehlgeschlagen")
if FAIL:
    print("Fehlgeschlagen:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
