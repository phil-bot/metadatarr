"""Built-in source plugins. Source contexts expose no external write capability."""
import hashlib
import json
import urllib.parse

from config import (DEEPL_API_KEY, DEEPL_API_URL, MEDIA_API_URL, TMDB_API_KEY,
                    TMDB_API_URL, USER_AGENT)
from net.http_client import SafeHTTPClient
from net.secrets import SecretStore, SecretStoreError
from net.redaction import REDACTOR
from plugins.registry import SourceContext, SourcePlugin, register_source
from quellen import (fs_rows, match_media, match_tmdb, media_search, source_network_profile,
                     tmdb_episodes, tmdb_find_series)
from speicher import cache_get_raw, cache_put, db_exec

_SOURCE_HTTP = SafeHTTPClient()
_ENV_SECRET_SLOTS = {
    ("tmdb", "api_key"): "TMDB_API_KEY",
    ("deepl", "api_key"): "DEEPL_API_KEY",
}


class ScopedSourceSecrets:
    """Capability-scoped secret reader with environment fallback for documented env settings."""
    def __init__(self, source_id):
        self.source_id = source_id
        self._scope = None
        try:
            self._scope = SecretStore().scoped("source", source_id)
        except SecretStoreError:
            pass

    def get(self, slot):
        if self._scope is not None:
            try:
                value = self._scope.get(slot)
                if value:
                    REDACTOR.register(value)
                    return value
            except SecretStoreError:
                pass
        import os
        name = _ENV_SECRET_SLOTS.get((self.source_id, slot))
        value = os.environ.get(name, "") if name else ""
        REDACTOR.register(value)
        return value

    def configured(self, slot):
        return bool(self.get(slot))


def context_for_source(plugin_key, http=None):
    return SourceContext(http=http or _SOURCE_HTTP, reader=None, secrets=ScopedSourceSecrets(plugin_key))


def source_record(source_id):
    rows = db_exec("SELECT * FROM source_config WHERE id=?", (source_id,), fetch=True)
    return rows[0] if rows else None


def source_enabled(source_id, nightly=False):
    row = source_record(source_id)
    if row is None:
        return source_id in ("mediathek", "tmdb") and not nightly
    return bool(row["enabled"]) and (not nightly or bool(row["include_in_nightly"]))


def source_credentials_configured(source_id):
    """Return whether a source's scoped credential is available without exposing its value."""
    slot = {"tmdb": "api_key", "deepl": "api_key"}.get(source_id)
    return True if slot is None else ScopedSourceSecrets(source_id).configured(slot)


def nightly_source_keys():
    rows = db_exec("SELECT plugin_key FROM source_config WHERE enabled=1 AND include_in_nightly=1 ORDER BY plugin_key", fetch=True)
    return tuple(row["plugin_key"] for row in rows)


def translate_deepl(text, source="DE", target="EN-US", *, http=None, api_key=None):
    """Translate through a bounded protected request; never cache or return credentials."""
    text = (text or "").strip()
    if not text:
        return ""
    key = api_key or DEEPL_API_KEY or ScopedSourceSecrets("deepl").get("api_key")
    if not key:
        return None
    REDACTOR.register(key)
    cache_key = "deepl:" + source[:2].lower() + ">" + target[:2].lower() + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = cache_get_raw(cache_key)
    if cached is not None:
        return cached
    body = urllib.parse.urlencode({"text": text, "source_lang": source, "target_lang": target}).encode("utf-8")
    profile = source_network_profile("deepl", DEEPL_API_URL, pause=1.0, max_bytes=1_000_000)
    client = http or _SOURCE_HTTP
    try:
        response = client.request(
            DEEPL_API_URL, profile=profile, method="POST",
            headers={"Authorization": "DeepL-Auth-Key " + key,
                     "Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
            body=body,
        )
        if response.status < 200 or response.status >= 300:
            return None
        payload = json.loads(response.body.decode("utf-8"))
        translated = payload["translations"][0]["text"]
        if not isinstance(translated, str) or not translated.strip():
            return None
    except Exception:
        return None
    cache_put(cache_key, translated)
    return translated


class MediathekSource(SourcePlugin):
    key = "mediathek"
    label = "MediathekViewWeb"
    languages = ("de",)
    fields = ("episode.title", "episode.overview")
    nightly_capable = True

    def fetch(self, ctx, query):
        series, episodes = query["series"], query["episodes"]
        hits = media_search(series["title"], bool(query.get("force")), http=ctx.http)
        return match_media(series, episodes, hits)


class TMDBSource(SourcePlugin):
    key = "tmdb"
    label = "TMDB"
    languages = ("de", "en")
    fields = ("episode.title", "episode.overview")
    nightly_capable = True

    def fetch(self, ctx, query):
        key = ctx.secrets.get("api_key")
        if not key:
            return []
        series, episodes = query["series"], query["episodes"]
        found = tmdb_find_series(series, bool(query.get("force")), http=ctx.http, api_key=key)
        if not found:
            return []
        translated = tmdb_episodes(found["id"], bool(query.get("force")), query.get("season"),
                                    http=ctx.http, api_key=key)
        return match_tmdb(episodes, translated, found["id"],
                          german=series.get("language", "").lower() == "german")


class FernseherSerienSource(SourcePlugin):
    key = "fernsehserien"
    label = "fernsehserien.de"
    languages = ("de",)
    fields = ("episode.title", "episode.overview")
    nightly_capable = False

    def fetch(self, ctx, query):
        rows, _note = fs_rows(query["series"], query["episodes"], bool(query.get("force")), http=ctx.http)
        return rows


class DeepLSource(SourcePlugin):
    key = "deepl"
    label = "DeepL"
    languages = ("de", "en")
    fields = ("episode.title", "episode.overview")
    nightly_capable = False

    def fetch(self, ctx, query):
        original = query.get("candidate") or {}
        source_language = original.get("language")
        target_language = query.get("targetLanguage")
        if source_language not in ("de", "en") or target_language not in ("de", "en") or source_language == target_language:
            return []
        text = original.get("value") or ""
        source_code, target_code = (("DE", "EN-US") if source_language == "de" else ("EN", "DE"))
        value = translate_deepl(text, source_code, target_code, http=ctx.http, api_key=ctx.secrets.get("api_key"))
        if not value:
            return []
        return [{"value": value, "field_key": original.get("field_key"), "language": target_language,
                 "source_id": "deepl", "machine_translated": True,
                 "derived_from_id": original.get("id"), "source_url": "https://www.deepl.com/translator"}]


for _plugin in (MediathekSource, TMDBSource, FernseherSerienSource, DeepLSource):
    register_source(_plugin)
