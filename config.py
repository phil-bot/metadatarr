"""tba-tickets – Konfiguration: alle Umgebungsvariablen und feste Vorgaben."""
import os
import re
import secrets


# Schutz gegen fremde Seiten (CSRF): jede Formularanfrage braucht dieses Token. Es wird bei jedem Start neu
# erzeugt und nur in den eigenen Seiten ausgegeben, fremde Seiten können es nicht lesen.
CSRF_TOKEN = secrets.token_urlsafe(32)

SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")
METADATARR_SECRET_KEY = os.environ.get("METADATARR_SECRET_KEY", "")
SONARR_PUBLIC_URL = os.environ.get("SONARR_PUBLIC_URL", "").rstrip("/")
ORIGINAL_LANGUAGE = os.environ.get("ORIGINAL_LANGUAGE", "German")  # Legacy display only; never filters imported series.
SONARR_METADATA_LANGUAGE = os.environ.get("SONARR_METADATA_LANGUAGE", "").strip()  # Empty means language is unknown.
INCLUDE_SPECIALS = os.environ.get("INCLUDE_SPECIALS", "0") == "1"
CACHE_TTL = int(os.environ.get("CACHE_TTL", "600"))
PORT = int(os.environ.get("PORT", "8080"))
TBA_REGEX = re.compile(
    os.environ.get("TBA_REGEX", r"^\s*(tba|tbd|n/?a)?\s*$|^(episode|folge)\s+\d+$"),
    re.IGNORECASE,
)
# Anhängsel am Folgentitel, die nicht zum TVDB-Titel gehören, z. B. „- 500. Folge“ oder „- Neue Folgen“.
# Am Titelende entfernt. Über TITLE_SUFFIX_REGEX anpassbar (weitere Muster mit | ergänzen).
TITLE_SUFFIX_REGEX = re.compile(
    os.environ.get("TITLE_SUFFIX_REGEX", r"\s*[-\u2013]\s*(\d+\.\s*Folge|Neue Folgen?)\s*$"),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Schritt 2: Speicher, Mediathek, TMDB, Matching
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", "/data/tba.db")
MEDIA_API_URL = os.environ.get("MEDIA_API_URL", "https://mediathekviewweb.de/api/query")
TMDB_API_URL = os.environ.get("TMDB_API_URL", "https://api.themoviedb.org/3")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
USER_AGENT = os.environ.get("USER_AGENT", "tba-tickets/0.2 (private Nutzung, Self-Hosting)")
MEDIA_PAUSE = float(os.environ.get("MEDIA_PAUSE", "1.5"))
TMDB_PAUSE = float(os.environ.get("TMDB_PAUSE", "0.3"))
MEDIA_MAX_PAGES = int(os.environ.get("MEDIA_MAX_PAGES", "10"))
MAX_TMDB_SEASONS = int(os.environ.get("MAX_TMDB_SEASONS", "60"))
CACHE_DAYS = float(os.environ.get("CACHE_DAYS", "7"))
NIGHTLY_SEARCH = os.environ.get("NIGHTLY_SEARCH", "1") == "1"
NIGHTLY_HOUR = int(os.environ.get("NIGHTLY_HOUR", "3"))
FS_PAUSE = float(os.environ.get("FS_PAUSE", "3"))


# ---------------------------------------------------------------------------
# Schritt 3: Tickets für TheTVDB (nur Text zum Kopieren, nichts wird eingereicht)
# ---------------------------------------------------------------------------
DEEPL_API_URL = os.environ.get("DEEPL_API_URL", "https://api-free.deepl.com/v2/translate")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")


# ---------------------------------------------------------------------------
# TVDB-Support-Center: Ticket einreichen und Status lesen. Zugangsdaten nur aus der .env (nie loggen).
# ---------------------------------------------------------------------------
TVDB_BASE_URL = os.environ.get("TVDB_BASE_URL", "https://support.thetvdb.com").rstrip("/")
TVDB_USER = os.environ.get("TVDB_USER", "")
TVDB_PASSWORD = os.environ.get("TVDB_PASSWORD", "")
TVDB_TOPIC_ID = os.environ.get("TVDB_TOPIC_ID", "21")  # 21 = Hilfethema „Translations“
TVDB_PAUSE = float(os.environ.get("TVDB_PAUSE", "1"))  # Sekunden vor jeder Anfrage an TVDB
TVDB_TIMEOUT = float(os.environ.get("TVDB_TIMEOUT", "30"))
TVDB_STATUS_NIGHTLY = os.environ.get("TVDB_STATUS_NIGHTLY", "1") == "1"  # nachts Status eingereichter Tickets lesen


# ---------------------------------------------------------------------------
# Oberfläche: Poster aus Sonarr (der Server holt sie lesend und reicht sie weiter, der Key bleibt im Server)
# ---------------------------------------------------------------------------
SHOW_POSTERS = os.environ.get("SHOW_POSTERS", "1") == "1"


# ---------------------------------------------------------------------------
# Konfigurationsübersicht für die Seite „Einstellungen“ (nur Anzeige, Schlüssel werden nie ausgegeben).
# Jeder Eintrag: (Umgebungsvariable, Typ, Beschreibung). Typ: text, secret, bool, int, url.
# Für eine spätere Bearbeitung genügt es, diese Tabelle um Eingabefelder und eine Speicherung zu erweitern.
# ---------------------------------------------------------------------------
CONFIG_SECTIONS = (
    ("Sonarr", (
        ("SONARR_URL", "url", "Adresse von Sonarr (aus Sicht des Containers)."),
        ("SONARR_API_KEY", "secret", "API-Key für den lesenden Zugriff."),
        ("SONARR_PUBLIC_URL", "url", "Adresse für den Link „In Sonarr öffnen“."),
        ("ORIGINAL_LANGUAGE", "text", "Veralteter Parameter; filtert den Bestand nicht mehr."),
        ("SONARR_METADATA_LANGUAGE", "text", "Sprache der Sonarr-Texte; leer bedeutet: Sprachstatus nicht prüfbar."),
        ("INCLUDE_SPECIALS", "bool", "Staffel 0 (Specials) mitprüfen."),
        ("CACHE_TTL", "int", "Sekunden, wie lange die Sonarr-Daten im Speicher bleiben."),
        ("TBA_REGEX", "text", "Muster für Folgentitel, die als TBA gelten."),
        ("TITLE_SUFFIX_REGEX", "text", "Anhängsel am Titel, die vor dem Vergleich entfernt werden."),
    )),
    ("Quellen", (
        ("MEDIA_API_URL", "url", "Mediathek-Suche (MediathekViewWeb)."),
        ("MEDIA_PAUSE", "text", "Pause in Sekunden zwischen Mediathek-Anfragen."),
        ("MEDIA_MAX_PAGES", "int", "Höchstzahl Ergebnisseiten je Mediathek-Suche."),
        ("TMDB_API_URL", "url", "TMDB-Schnittstelle."),
        ("TMDB_API_KEY", "secret", "TMDB-Key; ohne Key entfällt die TMDB-Suche."),
        ("TMDB_PAUSE", "text", "Pause in Sekunden zwischen TMDB-Anfragen."),
        ("MAX_TMDB_SEASONS", "int", "Höchstzahl Staffeln, die bei TMDB abgefragt werden."),
        ("FS_PAUSE", "text", "Pause in Sekunden zwischen Anfragen an fernsehserien.de."),
        ("CACHE_DAYS", "text", "Tage, die Suchantworten zwischengespeichert bleiben."),
        ("USER_AGENT", "text", "Kennung bei Anfragen an externe Dienste."),
    )),
    ("Nachtlauf", (
        ("NIGHTLY_SEARCH", "bool", "Täglicher automatischer Suchlauf."),
        ("NIGHTLY_HOUR", "int", "Uhrzeit (Stunde) des Nachtlaufs."),
        ("TVDB_STATUS_NIGHTLY", "bool", "Nachts den Status eingereichter TVDB-Tickets lesen."),
    )),
    ("TVDB und Übersetzung", (
        ("TVDB_BASE_URL", "url", "Adresse des TVDB-Support-Centers."),
        ("TVDB_USER", "secret", "Benutzer für das Support-Center."),
        ("TVDB_PASSWORD", "secret", "Passwort für das Support-Center."),
        ("TVDB_TOPIC_ID", "text", "Hilfethema (21 = Translations)."),
        ("TVDB_PAUSE", "text", "Pause in Sekunden vor jeder TVDB-Anfrage."),
        ("TVDB_TIMEOUT", "text", "Zeitlimit in Sekunden für TVDB-Anfragen."),
        ("DEEPL_API_URL", "url", "DeepL-Schnittstelle."),
        ("DEEPL_API_KEY", "secret", "DeepL-Key; ohne Key keine automatische Übersetzung."),
    )),
    ("Darstellung und System", (
        ("SHOW_POSTERS", "bool", "Serienposter aus Sonarr anzeigen."),
        ("PORT", "int", "Port der Weboberfläche im Container."),
        ("DB_PATH", "text", "Pfad der SQLite-Datenbank."),
        ("METADATARR_SECRET_KEY", "secret", "Schlüssel für verschlüsselt gespeicherte Zugangsdaten; ohne Schlüssel bleibt der SecretStore gesperrt."),
    )),
)


def _strip_userinfo(value):
    """Entfernt Benutzer und Passwort aus einer Adresse, falls jemand sie dort eingetragen hat."""
    return re.sub(r"^([a-z][a-z0-9+.-]*://)[^/@\s]*@", r"\1", value or "", flags=re.IGNORECASE)


def config_overview():
    """Liefert [(Bereich, [Eintrag, ...]), ...] mit Name, Anzeigewert, Quelle und Beschreibung.

    Schlüssel und Passwörter erscheinen nur als „gesetzt“ oder „nicht gesetzt“.
    """
    current = globals()
    out = []
    for section, items in CONFIG_SECTIONS:
        rows = []
        for name, kind, desc in items:
            raw = current.get(name)
            if hasattr(raw, "pattern"):
                raw = raw.pattern
            from_env = name in os.environ and os.environ.get(name, "") != ""
            if kind == "secret":
                shown, ok = ("gesetzt" if raw else "nicht gesetzt"), bool(raw)
            elif kind == "bool":
                shown, ok = ("an" if raw else "aus"), bool(raw)
            else:
                text = "" if raw is None else str(raw)
                shown, ok = (_strip_userinfo(text) if kind == "url" else text) or "nicht gesetzt", bool(text)
            rows.append({"name": name, "kind": kind, "value": shown, "ok": ok,
                         "source": "Umgebung" if from_env else "Standard", "desc": desc})
        out.append((section, rows))
    return out
