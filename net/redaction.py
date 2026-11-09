"""Zentrale Geheimnis-Redaktion für Logs, Antworten und Auditdaten."""
import os
import re
import threading
from collections.abc import Mapping


_SECRET_KEYS = {
    "authorization", "proxy_authorization", "x_api_key", "api_key", "apikey", "deepl_auth_key",
    "password", "passwd", "token", "access_token", "refresh_token", "cookie", "set_cookie",
    "secret", "secret_key", "client_secret", "private_key", "signing_key", "encryption_key",
    "csrf", "csrf_token", "metadatarr_secret_key",
}
_SECRET_SUFFIXES = (
    "_authorization", "_api_key", "_apikey", "_auth_token", "_access_token", "_refresh_token",
    "_token", "_password", "_passwd", "_cookie", "_secret", "_csrf",
)


def _is_secret_key(key):
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized in _SECRET_KEYS
        or compact in {"apikey", "deeplauthkey", "clientsecret", "metadatarrsecretkey", "csrftoken"}
        or any(normalized.endswith(suffix) for suffix in _SECRET_SUFFIXES)
    )
_KEY_VALUE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|x-api-key|api[-_]?key|apikey|deepl-auth-key|"
    r"password|passwd|access[-_]?token|refresh[-_]?token|csrf(?:[-_]?token)?|token|cookie|client[-_]?secret)\b"
    r"\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@")
_BASIC_AUTH = re.compile(r"(?i)(\b(?:authorization\s*[:=]\s*)?(?:basic|bearer)\s+)[A-Za-z0-9._~+/=-]+")


class Redactor:
    """Redaktiert bekannte Secretwerte sowie gängige Header- und URL-Formate."""

    def __init__(self):
        self._lock = threading.RLock()
        self._secrets = set()

    def register(self, value):
        if value is None:
            return
        text = str(value)
        if len(text) < 3:
            return
        with self._lock:
            self._secrets.add(text)

    def register_environment(self):
        """Nimmt nur Werte bekannter Secret-Variablen auf, ohne sie auszugeben."""
        for name in (
            "SONARR_API_KEY", "TMDB_API_KEY", "DEEPL_API_KEY", "TVDB_USER", "TVDB_PASSWORD",
            "METADATARR_SECRET_KEY",
        ):
            self.register(os.environ.get(name))

    def redact_text(self, value):
        if value is None:
            return ""
        text = str(value)
        text = _URL_USERINFO.sub(r"\1***@", text)
        text = _KEY_VALUE.sub(lambda m: m.group(1) + "***", text)
        text = _BASIC_AUTH.sub(r"\1***", text)
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            text = text.replace(secret, "***")
        return text

    def redact_headers(self, headers):
        out = {}
        for key, value in (headers or {}).items():
            if _is_secret_key(key):
                out[str(key)] = "***"
            else:
                out[str(key)] = self.redact_text(value)
        return out

    def redact_value(self, value):
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if _is_secret_key(key):
                    result[str(key)] = "***" if item not in (None, "") else item
                else:
                    result[str(key)] = self.redact_value(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item) for item in value]
        if isinstance(value, bytes):
            return self.redact_text(value.decode("utf-8", "replace"))
        if isinstance(value, str):
            return self.redact_text(value)
        return value


REDACTOR = Redactor()
REDACTOR.register_environment()
