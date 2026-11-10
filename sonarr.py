"""Sonarr-Lesezugriff: vollstaendiger Serien-/Staffel-/Folgenbestand ohne Sprachfilter."""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from urllib.parse import urlsplit

from config import (CACHE_TTL, INCLUDE_SPECIALS, SONARR_API_KEY, SONARR_METADATA_LANGUAGE,
                    SONARR_URL, TBA_REGEX)
from core.missing import is_placeholder
from core.poster_service import (MAX_CACHE_ITEMS, MAX_POSTER_BYTES, POSTER_CACHE,
                                 PosterFetchError, _request_image, fetch_poster as _fetch_poster)
from net.http_client import NetworkProfile, SafeHTTPClient


STATE = {"data": None, "time": 0.0}
LOCK = threading.Lock()
_HTTP = SafeHTTPClient()
# Compatibility for the existing server-side settings page; the service enforces the byte limit.
POSTERS = POSTER_CACHE.items
POSTER_MAX = MAX_CACHE_ITEMS
POSTER_BYTES = MAX_POSTER_BYTES


def _sonarr_profile(max_bytes=10_000_000):
    host = urlsplit(SONARR_URL).hostname or ""
    return NetworkProfile(source_id="sonarr", granted_hosts=(host,) if host else (),
                          allow_external=True, allow_private=True, timeout_seconds=30,
                          connect_timeout_seconds=5, max_bytes=max_bytes, min_interval_seconds=0,
                          max_redirects=3)


def sonarr(path, params=None):
    """Fuehrt ausschliesslich GET-Aufrufe gegen die konfigurierte Sonarr-API aus."""
    url = f"{SONARR_URL}/api/v3/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    response = _HTTP.request(url, profile=_sonarr_profile(), method="GET",
                             headers={"X-Api-Key": SONARR_API_KEY, "Accept": "application/json",
                                      "User-Agent": "Metadatarr/1"})
    if response.status >= 400:
        raise urllib.error.HTTPError(url, response.status, "Sonarr HTTP error", None, None)
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Sonarr hat ungueltiges JSON geliefert.") from None


def poster_path(series):
    """Liefert nur einen gueltigen lokalen MediaCover-Pfad; remoteUrl wird ignoriert."""
    from core.poster_service import normalized_media_url
    for image in series.get("images") or []:
        if not isinstance(image, dict) or image.get("coverType") != "poster":
            continue
        raw = image.get("url") or ""
        normalized = normalized_media_url(raw, SONARR_URL)
        if normalized:
            parts = urlsplit(normalized)
            return urllib.parse.urlunsplit(("", "", parts.path, parts.query, ""))
    return ""


def _get_image(path):
    """Kompatibler, streng lesender Einzelbildabruf fuer alte Tests und interne Aufrufer."""
    from core.poster_service import normalized_media_url
    url = normalized_media_url(path, SONARR_URL)
    if not url:
        raise ValueError("ungueltiger lokaler Sonarr-Posterpfad")
    return _request_image(_HTTP, url, SONARR_API_KEY, SONARR_URL)


def fetch_poster(series_id):
    """Liefert (Bytes, MIME-Typ); fehlende oder fehlerhafte Poster werfen PosterFetchError."""
    data = STATE["data"]
    series = next((item for item in (data or {}).get("series", []) if item["id"] == series_id), None)
    return _fetch_poster(series_id, series, base_url=SONARR_URL, api_key=SONARR_API_KEY,
                         client=_HTTP, cache=POSTER_CACHE)


def _episode_record(episode):
    season = episode.get("seasonNumber", 0)
    number = episode.get("episodeNumber", 0)
    return {
        "id": episode.get("id"),
        "season": season if isinstance(season, int) else 0,
        "episode": number if isinstance(number, int) else 0,
        "title": episode.get("title"),
        "overview": episode.get("overview"),
        "provided": {"title": "title" in episode, "overview": "overview" in episode},
        "airDate": episode.get("airDate"),
        "hasFile": bool(episode.get("hasFile")),
        "tvdbId": episode.get("tvdbId"),
        "monitored": episode.get("monitored"),
    }


def load_all():
    """Laedt alle Serien und Folgen per GET; uebernimmt verfuegbare Overviews unveraendert."""
    all_series = sonarr("series")
    if not isinstance(all_series, list):
        raise ValueError("Sonarr hat keine Serienliste geliefert.")
    today = date.today().isoformat()

    def one(source):
        episodes_raw = sonarr("episode", {"seriesId": source["id"]})
        if not isinstance(episodes_raw, list):
            raise ValueError("Sonarr hat keine Folgenliste geliefert.")
        episodes = [_episode_record(item) for item in episodes_raw]
        episodes.sort(key=lambda item: (item["season"], item["episode"]))
        season_numbers = {episode["season"] for episode in episodes}
        for season in source.get("seasons") or []:
            if isinstance(season, dict) and isinstance(season.get("seasonNumber"), int):
                season_numbers.add(season["seasonNumber"])
        seasons = []
        for number in sorted(season_numbers):
            season_episodes = [episode for episode in episodes if episode["season"] == number]
            # Sonarr liefert keine Staffel-Titel/Overviews; keine Werte erfinden.
            seasons.append({"seasonNumber": number, "title": None, "overview": None,
                            "titleProvided": False, "overviewProvided": False,
                            "episodeCount": len(season_episodes), "episodes": season_episodes})
        tba = []
        for episode in episodes:
            season = episode["season"]
            if season == 0 and not INCLUDE_SPECIALS:
                continue
            title = episode.get("title")
            placeholder = is_placeholder("episode.title", title, SONARR_METADATA_LANGUAGE or None)
            if not placeholder and not TBA_REGEX.match(title if isinstance(title, str) else ""):
                continue
            air = episode.get("airDate") or ""
            if not air:
                state = "ohne Datum"
            elif air > today:
                state = "angekuendigt"
            else:
                state = "gesendet"
            tba.append({"id": episode.get("id"), "season": season,
                        "episode": episode.get("episode", 0), "title": title or "",
                        "airDate": air, "state": state, "hasFile": episode["hasFile"]})
        language = source.get("originalLanguage") or {}
        return {
            "id": source["id"], "title": source.get("title", "?"), "overview": source.get("overview"),
            "provided": {"title": "title" in source, "overview": "overview" in source},
            "year": source.get("year"), "network": source.get("network") or "",
            "status": source.get("status") or "", "tvdbId": source.get("tvdbId"),
            "slug": source.get("titleSlug") or "", "poster": poster_path(source),
            "originalLanguage": language.get("name", "") if isinstance(language, dict) else "",
            "metadataLanguage": SONARR_METADATA_LANGUAGE or None,
            "seasons": seasons, "episodeCount": len(episodes), "tba": tba,
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        series = list(pool.map(one, all_series))
    series.sort(key=lambda item: (-len(item["tba"]), str(item["title"]).lower()))
    result = {"series": series, "seriesTotal": len(all_series), "fetched": datetime.now()}
    from core.missing import persist_metadata_snapshots
    persist_metadata_snapshots(result, SONARR_METADATA_LANGUAGE or None)
    return result


def get_data(force=False):
    with LOCK:
        fresh = STATE["data"] is not None and (time.time() - STATE["time"]) < CACHE_TTL
        if force or not fresh:
            STATE["data"] = load_all()
            STATE["time"] = time.time()
        return STATE["data"]
