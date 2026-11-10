"""Lesender Sonarr-Posterabruf mit URL-Base-Unterstuetzung und Byte-begrenztem LRU-Cache."""
import re
import threading
from collections import OrderedDict
from urllib.parse import urlsplit, urlunsplit

from net.http_client import HTTPClientError, NetworkProfile, ResponseTooLarge


MAX_POSTER_BYTES = 6_000_000
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHE_ITEMS = 400
_MEDIA_PATH = re.compile(r"^(?P<prefix>(?:/[A-Za-z0-9._~-]+)*)/MediaCover/(?P<id>\d+)/(?P<name>[\w.-]+\.(?:jpg|jpeg|png|webp))$", re.IGNORECASE)
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


class PosterFetchError(RuntimeError):
    def __init__(self, reason, status=502, message="Poster konnte nicht geladen werden."):
        self.reason = reason
        self.status = status
        super().__init__(message)


class ByteLRU:
    """Thread-sicherer LRU-Cache, begrenzt nach Eintraegen und Gesamtbytes."""
    def __init__(self, max_bytes=MAX_CACHE_BYTES, max_items=MAX_CACHE_ITEMS):
        self.max_bytes = int(max_bytes)
        self.max_items = int(max_items)
        self.items = OrderedDict()
        self.total_bytes = 0
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            value = self.items.get(key)
            if value is None:
                return None
            self.items.move_to_end(key)
            return value

    def put(self, key, value):
        body, _ctype = value
        if len(body) > self.max_bytes or self.max_items <= 0:
            return
        with self.lock:
            old = self.items.pop(key, None)
            if old is not None:
                self.total_bytes -= len(old[0])
            self.items[key] = value
            self.total_bytes += len(body)
            while self.items and (self.total_bytes > self.max_bytes or len(self.items) > self.max_items):
                _old_key, old_value = self.items.popitem(last=False)
                self.total_bytes -= len(old_value[0])

    def clear(self):
        with self.lock:
            self.items.clear()
            self.total_bytes = 0


POSTER_CACHE = ByteLRU()


def _origin(parts):
    default_port = 443 if parts.scheme.lower() == "https" else 80
    return parts.scheme.lower(), (parts.hostname or "").lower().rstrip("."), parts.port or default_port


def normalized_media_url(raw_path, base_url):
    """Liefert eine gleiche-Origin-URL fuer MediaCover; remoteUrl und fremde Hosts sind gesperrt."""
    if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 2048:
        return None
    try:
        base = urlsplit(base_url)
        parsed = urlsplit(raw_path)
        if base.scheme.lower() not in {"http", "https"} or not base.hostname or base.username or base.password:
            return None
        if parsed.scheme or parsed.netloc:
            if parsed.scheme.lower() not in {"http", "https"} or _origin(parsed) != _origin(base):
                return None
        elif parsed.netloc:
            return None
        path = parsed.path
        if not path.startswith("/") or "\\" in path or any(segment in {".", ".."} for segment in path.split("/")):
            return None
        match = _MEDIA_PATH.fullmatch(path)
        if not match:
            return None
        base_path = (base.path or "").rstrip("/")
        prefix = match.group("prefix")
        if base_path:
            if prefix == "":
                path = base_path + path
            elif prefix != base_path:
                return None
        # Bei Basis-URL am Ursprung bleibt ein expliziter Sonarr-URL-Base-Praefix erhalten.
        query = parsed.query
        if len(query) > 1024 or any(char in query for char in "\r\n#"):
            return None
        return urlunsplit((base.scheme.lower(), base.netloc, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def _small_variant(url):
    parts = urlsplit(url)
    path = re.sub(r"(\.[A-Za-z0-9]+)$", r"-250\1", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _profile(base_url):
    host = urlsplit(base_url).hostname or ""
    return NetworkProfile(source_id="sonarr", granted_hosts=(host,) if host else (), allow_external=True,
                          allow_private=True, timeout_seconds=30, connect_timeout_seconds=5,
                          max_bytes=MAX_POSTER_BYTES, min_interval_seconds=0, max_redirects=3)


def _request_image(client, url, api_key, base_url):
    try:
        response = client.request(url, profile=_profile(base_url), method="GET",
                                  headers={"X-Api-Key": api_key, "User-Agent": "Metadatarr/1", "Accept": "image/*"})
    except ResponseTooLarge:
        raise PosterFetchError("too-large", 413, "Das Poster ueberschreitet die erlaubte Bytegrenze.") from None
    except HTTPClientError as exc:
        if "Zeitlimit" in str(exc):
            raise PosterFetchError("timeout", 504, "Zeitlimit beim Abruf des Posters ueberschritten.") from None
        if "Weiterleitung" in str(exc):
            raise PosterFetchError("login-redirect", 502, "Posterabruf wurde auf eine Anmeldeseite umgeleitet.") from None
        raise PosterFetchError("upstream-error", 502, "Sonarr war beim Posterabruf nicht erreichbar.") from None
    if response.status in (401, 403):
        raise PosterFetchError("auth", 502, "Sonarr hat den Posterabruf nicht autorisiert.")
    if response.status in (301, 302, 303, 307, 308):
        raise PosterFetchError("login-redirect", 502, "Sonarr hat auf eine andere Seite umgeleitet.")
    if response.status == 404:
        raise PosterFetchError("missing", 404, "Poster in Sonarr nicht gefunden.")
    if response.status >= 400:
        raise PosterFetchError("upstream-error", 502, "Sonarr meldet einen Fehler beim Posterabruf.")
    content_type = next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    final_path = urlsplit(getattr(response, "url", url)).path.lower()
    if content_type == "text/html" and ("login" in final_path or "signin" in final_path or "auth" in final_path):
        raise PosterFetchError("login-redirect", 502, "Sonarr hat statt des Posters eine Anmeldeseite geliefert.")
    if content_type not in _ALLOWED_TYPES:
        raise PosterFetchError("not-image", 502, "Sonarr hat keine unterstuetzte Bilddatei geliefert.")
    if not response.body:
        raise PosterFetchError("not-image", 502, "Sonarr hat ein leeres Bild geliefert.")
    if len(response.body) > MAX_POSTER_BYTES:
        raise PosterFetchError("too-large", 413, "Das Poster ueberschreitet die erlaubte Bytegrenze.")
    return response.body, content_type


def fetch_poster(series_id, series, *, base_url, api_key, client, cache=POSTER_CACHE):
    """Holt Poster nur aus dem gleichen Sonarr-Origin; Ergebnis oder erklaerter Fehler."""
    if not series:
        raise PosterFetchError("missing", 404, "Serie oder Poster ist nicht vorhanden.")
    raw_path = series.get("poster") or ""
    url = normalized_media_url(raw_path, base_url)
    if not url:
        raise PosterFetchError("missing", 404, "Fuer diese Serie ist kein gueltiger Sonarr-Posterpfad vorhanden.")
    key = (series_id, url)
    cached = cache.get(key)
    if cached is not None:
        return cached
    last_error = None
    for candidate in (_small_variant(url), url):
        try:
            result = _request_image(client, candidate, api_key, base_url)
            cache.put(key, result)
            return result
        except PosterFetchError as exc:
            last_error = exc
            # Bei fehlendem Thumbnail das Original versuchen; sonst Ursache unveraendert melden.
            if exc.reason != "missing":
                raise
    raise last_error or PosterFetchError("missing", 404, "Poster nicht gefunden.")
