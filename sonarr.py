"""tba-tickets – Sonarr-Anbindung: liest Serien und TBA-Folgen (nur lesend)."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import urllib.error
import urllib.parse
import re
from urllib.parse import urlsplit
from config import CACHE_TTL, INCLUDE_SPECIALS, ORIGINAL_LANGUAGE, SONARR_API_KEY, SONARR_URL, TBA_REGEX
from net.http_client import NetworkProfile, SafeHTTPClient


STATE = {"data": None, "time": 0.0}
LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Sonarr
# ---------------------------------------------------------------------------
_HTTP = SafeHTTPClient()


def _sonarr_profile(max_bytes=10_000_000):
    host = urlsplit(SONARR_URL).hostname or ""
    return NetworkProfile(
        source_id="sonarr",
        granted_hosts=(host,) if host else (),
        allow_external=True,
        allow_private=True,
        timeout_seconds=30,
        connect_timeout_seconds=5,
        max_bytes=max_bytes,
        min_interval_seconds=0,
        max_redirects=3,
    )


def sonarr(path, params=None):
    """Liest Sonarr ausschließlich per GET über den geschützten HTTP-Client."""
    url = f"{SONARR_URL}/api/v3/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    response = _HTTP.request(
        url,
        profile=_sonarr_profile(),
        method="GET",
        headers={
            "X-Api-Key": SONARR_API_KEY,
            "Accept": "application/json",
            "User-Agent": "Metadatarr/1",
        },
    )
    if response.status >= 400:
        raise urllib.error.HTTPError(url, response.status, "Sonarr HTTP error", None, None)
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Sonarr hat ungültiges JSON geliefert.") from None


def poster_path(s):
    """Relativer Poster-Pfad aus der Sonarr-Serie (z. B. /MediaCover/1/poster.jpg?lastWrite=...) oder leer."""
    for img in s.get("images") or []:
        if img.get("coverType") == "poster":
            url = img.get("url") or ""
            if re.match(r"^/MediaCover/\d+/[\w.-]+\.(jpg|jpeg|png|webp)(\?[\w=&.:%+-]*)?$", url, re.IGNORECASE):
                return url
    return ""


POSTERS = {}  # series_id -> (Bytes, Content-Type); kleiner Zwischenspeicher, damit Sonarr nicht dauernd gefragt wird
POSTER_MAX = 400
POSTER_BYTES = 6_000_000


def _get_image(path):
    """Liest ein Bild aus Sonarr mit Body-Limit und ohne Weitergabe des Keys an Fremdhosts."""
    url = SONARR_URL + path
    response = _HTTP.request(
        url,
        profile=_sonarr_profile(max_bytes=POSTER_BYTES),
        method="GET",
        headers={"X-Api-Key": SONARR_API_KEY, "User-Agent": "Metadatarr/1", "Accept": "image/*"},
    )
    if response.status >= 400:
        raise urllib.error.HTTPError(url, response.status, "Sonarr poster HTTP error", None, None)
    ctype = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "")
    ctype = ctype.split(";", 1)[0].strip().lower()
    if not ctype.startswith("image/"):
        raise ValueError("keine Bilddatei")
    return response.body, ctype


def fetch_poster(series_id):
    """Holt das Poster einer Serie lesend aus Sonarr. Gibt (Bytes, Typ) oder None zurück.

    Es wird nur der in der Serienliste genannte Pfad unter /MediaCover/ abgerufen. Zuerst die kleine Fassung
    (poster-250.jpg), sonst das Original. Der API-Key verlässt den Server nicht.
    """
    if series_id in POSTERS:
        return POSTERS[series_id]
    data = STATE["data"]
    series = next((s for s in (data or {}).get("series", []) if s["id"] == series_id), None)
    path = (series or {}).get("poster") or ""
    if not path:
        return None
    base, _, query = path.partition("?")
    small = re.sub(r"(\.\w+)$", r"-250\1", base)
    result = None
    for candidate in (small + ("?" + query if query else ""), path):
        try:
            result = _get_image(candidate)
            break
        except Exception:  # noqa: BLE001 - fehlendes Poster ist kein Fehler, die Oberfläche zeigt einen Platzhalter
            continue
    if result is not None:
        if len(POSTERS) >= POSTER_MAX:
            POSTERS.pop(next(iter(POSTERS)))
        POSTERS[series_id] = result
    return result


def load_all():
    """Holt Serien und Folgen aus Sonarr und sucht die TBA-Folgen."""
    all_series = sonarr("series")
    wanted = ORIGINAL_LANGUAGE.strip().lower()
    selected = [
        s
        for s in all_series
        if wanted == "*"
        or ((s.get("originalLanguage") or {}).get("name", "").lower() == wanted)
    ]
    today = date.today().isoformat()

    def one(s):
        episodes = sonarr("episode", {"seriesId": s["id"]})
        tba = []
        for e in episodes:
            season = e.get("seasonNumber", 0)
            if season == 0 and not INCLUDE_SPECIALS:
                continue
            if not TBA_REGEX.match(e.get("title") or ""):
                continue
            air = e.get("airDate") or ""
            if not air:
                state = "ohne Datum"
            elif air > today:
                state = "angekündigt"
            else:
                state = "gesendet"
            tba.append(
                {
                    "id": e["id"],
                    "season": season,
                    "episode": e.get("episodeNumber", 0),
                    "title": e.get("title") or "",
                    "airDate": air,
                    "state": state,
                    "hasFile": bool(e.get("hasFile")),
                }
            )
        tba.sort(key=lambda x: (x["season"], x["episode"]))
        return {
            "id": s["id"],
            "title": s.get("title", "?"),
            "year": s.get("year"),
            "network": s.get("network") or "",
            "status": s.get("status") or "",
            "tvdbId": s.get("tvdbId"),
            "slug": s.get("titleSlug") or "",
            "poster": poster_path(s),
            "language": (s.get("originalLanguage") or {}).get("name", ""),
            "episodeCount": len(episodes),
            "tba": tba,
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        series = list(pool.map(one, selected))
    series.sort(key=lambda x: (-len(x["tba"]), x["title"].lower()))
    return {
        "series": series,
        "seriesTotal": len(all_series),
        "fetched": datetime.now(),
    }


def get_data(force=False):
    with LOCK:
        fresh = STATE["data"] is not None and (time.time() - STATE["time"]) < CACHE_TTL
        if force or not fresh:
            STATE["data"] = load_all()
            STATE["time"] = time.time()
        return STATE["data"]
