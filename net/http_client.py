"""HTTP-Client mit SSRF-Schutz, gepinntem DNS-Ziel, kontrollierten Redirects und redigierten Antwortheadern."""
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from .redaction import REDACTOR


class HTTPClientError(RuntimeError):
    """Fehler mit absichtlich redigierter, URL-freier Meldung."""


class NetworkPolicyError(HTTPClientError):
    """Die Anfrage verletzt das ausdrücklich freigegebene Netzwerkprofil."""


class ResponseTooLarge(HTTPClientError):
    """Die Antwort überschreitet das Quellenlimit."""


@dataclass(frozen=True)
class NetworkProfile:
    source_id: str
    granted_hosts: tuple
    allow_external: bool = False
    allow_private: bool = False
    timeout_seconds: float = 20.0
    connect_timeout_seconds: float = 5.0
    max_bytes: int = 2_000_000
    min_interval_seconds: float = 1.0
    max_redirects: int = 3


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict
    body: bytes
    url: str
    session_cookies: tuple = field(default=(), repr=False, compare=False)


_METADATA_IPS = tuple(ipaddress.ip_network(item) for item in (
    "169.254.0.0/16", "fd00:ec2::/64", "fd00:ec2::253/128", "fd00:ec2::254/128",
))
_CREDENTIAL_HEADER = re.compile(r"(?:authorization|proxy-authorization|cookie|set-cookie|api[-_]?key|token|secret|password)", re.I)
_FORBIDDEN_HEADERS = {"host", "connection", "proxy-connection", "proxy-authorization", "transfer-encoding", "content-length"}
_REDIRECTS = {301, 302, 303, 307, 308}


def _normalized_host(host):
    host = (host or "").rstrip(".").lower()
    if not host:
        raise NetworkPolicyError("Zielhost fehlt.")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        raise NetworkPolicyError("Zielhost ist ungültig.") from None


def _origin_parts(url):
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise NetworkPolicyError("Zieladresse enthält einen ungültigen Port.") from None
    if parsed.scheme.lower() not in ("http", "https"):
        raise NetworkPolicyError("Nur HTTP und HTTPS sind erlaubt.")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkPolicyError("Benutzerinfo in der Zieladresse ist nicht erlaubt.")
    if not parsed.hostname:
        raise NetworkPolicyError("Zielhost fehlt.")
    host = _normalized_host(parsed.hostname)
    port = port or (443 if parsed.scheme.lower() == "https" else 80)
    if not 1 <= port <= 65535:
        raise NetworkPolicyError("Zielport ist ungültig.")
    return parsed, host, port


def _safe_ip(address, profile):
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        raise NetworkPolicyError("DNS lieferte eine ungültige Zieladresse.") from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
        raise NetworkPolicyError("Das aufgelöste Ziel ist reserviert oder nicht zulässig.")
    if any(ip.version == network.version and ip in network for network in _METADATA_IPS):
        raise NetworkPolicyError("Metadaten-Endpunkte sind gesperrt.")
    if ip.is_global:
        if not profile.allow_external:
            raise NetworkPolicyError("Externe Ziele sind für diese Quelle nicht freigegeben.")
        return ip
    if ip.is_private or ip.is_loopback:
        if not profile.allow_private:
            raise NetworkPolicyError("Private oder lokale Ziele sind nicht freigegeben.")
        return ip
    raise NetworkPolicyError("Das aufgelöste Ziel ist nicht zulässig.")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self._pinned_address = address

    def connect(self):
        self.sock = socket.create_connection((self._pinned_address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self):
        raw = socket.create_connection((self._pinned_address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class SafeHTTPClient:
    """Ruft HTTP-Quellen nur über ein explizites Host- und Zielprofil ab."""

    def __init__(self, resolver=None, ssl_context=None):
        self._resolver = resolver or socket.getaddrinfo
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._registry_lock = threading.RLock()
        self._source_locks = {}
        self._last_host_request = {}
        self._failures = {}
        self._blocked_until = {}

    def _resolve(self, host, port, profile):
        try:
            literal = ipaddress.ip_address(host)
            answers = [(literal.compressed, socket.AF_INET6 if literal.version == 6 else socket.AF_INET)]
        except ValueError:
            try:
                records = self._resolver(host, port, type=socket.SOCK_STREAM)
            except OSError:
                raise HTTPClientError("Der Zielhost konnte nicht aufgelöst werden.") from None
            answers = [(record[4][0], record[0]) for record in records]
        if not answers:
            raise HTTPClientError("Der Zielhost lieferte keine Adresse.")
        checked = []
        for address, family in answers:
            ip = _safe_ip(address, profile)
            entry = (ip.compressed, family)
            if entry not in checked:
                checked.append(entry)
        return checked

    def _validate(self, url, profile):
        if not isinstance(profile, NetworkProfile) or not profile.source_id:
            raise NetworkPolicyError("Ein gültiges Netzwerkprofil ist erforderlich.")
        if any(ord(char) < 32 or ord(char) == 127 for char in url):
            raise NetworkPolicyError("Steuerzeichen in der Zieladresse sind nicht erlaubt.")
        parsed, host, port = _origin_parts(url)
        grants = {_normalized_host(item) for item in profile.granted_hosts}
        if host not in grants:
            raise NetworkPolicyError("Der Zielhost ist für diese Quelle nicht freigegeben.")
        return parsed, host, port, self._resolve(host, port, profile)

    @staticmethod
    def _origin(url):
        parsed, host, port = _origin_parts(url)
        return parsed.scheme.lower(), host, port

    @staticmethod
    def _headers(headers):
        result = {"Accept-Encoding": "identity", "User-Agent": "Metadatarr/1"}
        for name, value in (headers or {}).items():
            key, val = str(name), str(value)
            lower = key.lower()
            if lower in _FORBIDDEN_HEADERS or "\r" in key or "\n" in key or "\r" in val or "\n" in val:
                raise NetworkPolicyError("Ein nicht zulässiger HTTP-Header wurde angefordert.")
            result[key] = val
        return result

    def _rate_limit(self, host, interval):
        if interval <= 0:
            return
        with self._registry_lock:
            now = time.monotonic()
            slot = max(now, self._last_host_request.get(host, 0.0) + interval)
            self._last_host_request[host] = slot
        if slot > now:
            time.sleep(slot - now)

    def _circuit_check(self, source_id):
        until = self._blocked_until.get(source_id, 0.0)
        if time.monotonic() < until:
            raise HTTPClientError("Die Quelle ist nach wiederholten Fehlern kurzzeitig pausiert.")
        if until:
            self._blocked_until.pop(source_id, None)
            self._failures[source_id] = 0

    def _note_result(self, source_id, status=None, failed=False):
        if failed or (status is not None and status >= 500):
            count = self._failures.get(source_id, 0) + 1
            self._failures[source_id] = count
            if count >= 5:
                self._blocked_until[source_id] = time.monotonic() + 30.0
        elif status is not None and status < 500:
            self._failures[source_id] = 0
            self._blocked_until.pop(source_id, None)

    def request(self, url, *, profile, method="GET", headers=None, body=None, follow_redirects=True):
        method = str(method).upper()
        if method not in {"GET", "POST", "HEAD"}:
            raise NetworkPolicyError("Diese HTTP-Methode ist nicht freigegeben.")
        if not isinstance(url, str):
            raise NetworkPolicyError("Zieladresse muss Text sein.")
        if not isinstance(profile, NetworkProfile) or not profile.source_id:
            raise NetworkPolicyError("Ein gültiges Netzwerkprofil ist erforderlich.")
        request_headers = self._headers(headers)
        if isinstance(body, str):
            body = body.encode("utf-8")
        if body is not None and not isinstance(body, bytes):
            raise NetworkPolicyError("Der HTTP-Body muss Bytes oder Text sein.")
        if body is not None and method in {"GET", "HEAD"}:
            raise NetworkPolicyError("GET und HEAD dürfen keinen Body senden.")
        max_bytes = min(max(int(profile.max_bytes), 0), 10_000_000)
        timeout = min(max(float(profile.timeout_seconds), 0.1), 60.0)
        connect_timeout = min(max(float(profile.connect_timeout_seconds), 0.1), 5.0)
        max_redirects = min(max(int(profile.max_redirects), 0), 3)
        if body is not None and len(body) > 10_000_000:
            raise ResponseTooLarge("Anfrage-Body überschreitet das harte Größenlimit.")
        with self._registry_lock:
            lock = self._source_locks.setdefault(profile.source_id, threading.Lock())
        with lock:
            self._circuit_check(profile.source_id)
            current_url, current_method, current_body = url, method, body
            current_headers = dict(request_headers)
            deadline = time.monotonic() + timeout
            redirect_count = 0
            try:
                while True:
                    parsed, host, port, addresses = self._validate(current_url, profile)
                    self._rate_limit(host, profile.min_interval_seconds)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HTTPClientError("Zeitlimit für die HTTP-Anfrage überschritten.")
                    target = parsed.path or "/"
                    if parsed.query:
                        target += "?" + parsed.query
                    address, family = addresses[0]
                    per_connect_timeout = min(connect_timeout, remaining)
                    if parsed.scheme.lower() == "https":
                        conn = _PinnedHTTPSConnection(host, port, address, per_connect_timeout, self._ssl_context)
                    else:
                        conn = _PinnedHTTPConnection(host, port, address, per_connect_timeout)
                    try:
                        conn.request(current_method, target, body=current_body, headers=current_headers)
                        if conn.sock:
                            conn.sock.settimeout(min(connect_timeout, max(0.1, deadline - time.monotonic())))
                        response = conn.getresponse()
                        status = response.status
                        raw_header_pairs = response.getheaders()
                        response_headers = dict(raw_header_pairs)
                        session_cookies = tuple(value for key, value in raw_header_pairs if key.lower() == "set-cookie")
                        location = next((v for k, v in response_headers.items() if k.lower() == "location"), "")
                        if status in _REDIRECTS and location:
                            if not follow_redirects:
                                result = HTTPResult(status, REDACTOR.redact_headers(response_headers), b"",
                                                    REDACTOR.redact_text(current_url), session_cookies)
                                self._note_result(profile.source_id, status=status)
                                return result
                            if redirect_count >= max_redirects:
                                raise HTTPClientError("Zu viele Weiterleitungen.")
                            next_url = urljoin(current_url, location)
                            self._validate(next_url, profile)
                            if self._origin(next_url) != self._origin(current_url):
                                current_headers = {
                                    k: v for k, v in current_headers.items() if not _CREDENTIAL_HEADER.search(k)
                                }
                            if status == 303 or (status in (301, 302) and current_method == "POST"):
                                current_method, current_body = "GET", None
                                current_headers = {
                                    k: v for k, v in current_headers.items()
                                    if k.lower() not in {"content-type", "content-encoding"}
                                }
                            redirect_count += 1
                            current_url = next_url
                            continue
                        content_length = next((v for k, v in response_headers.items() if k.lower() == "content-length"), None)
                        if content_length is not None:
                            try:
                                if int(content_length) > max_bytes:
                                    raise ResponseTooLarge("Antwort überschreitet das Quellenlimit.")
                            except ValueError:
                                raise HTTPClientError("Ungültige Content-Length in der Antwort.") from None
                        chunks = []
                        total = 0
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise HTTPClientError("Zeitlimit beim Lesen der HTTP-Antwort überschritten.")
                            if conn.sock:
                                conn.sock.settimeout(min(connect_timeout, remaining))
                            chunk = response.read(min(65536, max_bytes + 1 - total))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            total += len(chunk)
                            if total > max_bytes:
                                raise ResponseTooLarge("Antwort überschreitet das Quellenlimit.")
                        result = HTTPResult(status, REDACTOR.redact_headers(response_headers), b"".join(chunks),
                                            REDACTOR.redact_text(current_url), session_cookies)
                        self._note_result(profile.source_id, status=status)
                        return result
                    finally:
                        conn.close()
            except NetworkPolicyError:
                raise
            except ResponseTooLarge:
                self._note_result(profile.source_id, failed=True)
                raise
            except HTTPClientError:
                self._note_result(profile.source_id, failed=True)
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
                self._note_result(profile.source_id, failed=True)
                raise HTTPClientError("HTTP-Aufruf fehlgeschlagen (Netzwerkfehler oder Zeitüberschreitung).") from None


HTTPClient = SafeHTTPClient


class SafeHTTPSession:
    """Small cookie session restricted to one origin and backed by SafeHTTPClient."""

    def __init__(self, client, profile, base_url):
        from http.cookies import SimpleCookie
        self.client = client
        self.profile = profile
        self.base_origin = self._origin(base_url)
        self.cookies = {}
        self._cookie_parser = SimpleCookie

    @staticmethod
    def _origin(url):
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = _normalized_host(parsed.hostname)
        port = parsed.port or (443 if scheme == "https" else 80)
        return scheme, host, port

    def request(self, url, *, method="GET", headers=None, body=None):
        from http.cookies import SimpleCookie
        request_headers = dict(headers or {})
        if self._origin(url) == self.base_origin and self.cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in sorted(self.cookies.items()))
        result = self.client.request(url, profile=self.profile, method=method, headers=request_headers,
                                     body=body, follow_redirects=False)
        if self._origin(url) == self.base_origin:
            for header in result.session_cookies:
                jar = SimpleCookie()
                try:
                    jar.load(header)
                except Exception:
                    continue
                for key, morsel in jar.items():
                    if morsel["max-age"] == "0" or not morsel.value:
                        self.cookies.pop(key, None)
                    else:
                        self.cookies[key] = morsel.value
        return result
