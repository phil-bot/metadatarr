"""Poster-Service-Tests ohne Netz, mit kontrollierten HTTP-Antworten."""
import unittest
from types import SimpleNamespace

from core.poster_service import (ByteLRU, PosterFetchError, fetch_poster,
                                 normalized_media_url)
from net.http_client import HTTPClientError, ResponseTooLarge


BASE = "http://sonarr.local:8989/sonarr"
SERIES = {"poster": "/sonarr/MediaCover/7/poster.jpg?lastWrite=23"}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def request(self, url, *, profile, method, headers):
        self.calls.append((url, method, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status=200, ctype="image/jpeg", body=b"image", url="http://sonarr.local:8989/sonarr/MediaCover/7/poster-250.jpg"):
    return SimpleNamespace(status=status, headers={"Content-Type": ctype}, body=body, url=url)


class PosterServiceTests(unittest.TestCase):
    def test_url_base_is_applied_once_and_foreign_hosts_are_rejected(self):
        self.assertEqual(normalized_media_url("/MediaCover/7/poster.jpg", BASE),
                         "http://sonarr.local:8989/sonarr/MediaCover/7/poster.jpg")
        self.assertEqual(normalized_media_url("/sonarr/MediaCover/7/poster.jpg", BASE),
                         "http://sonarr.local:8989/sonarr/MediaCover/7/poster.jpg")
        self.assertEqual(normalized_media_url("/sonarr/MediaCover/7/poster.jpg", "http://sonarr.local:8989"),
                         "http://sonarr.local:8989/sonarr/MediaCover/7/poster.jpg")
        self.assertIsNone(normalized_media_url("https://evil.invalid/MediaCover/7/poster.jpg", BASE))
        self.assertIsNone(normalized_media_url("/sonarr/../login/MediaCover/7/poster.jpg", BASE))

    def test_lru_respects_total_byte_budget_and_recency(self):
        cache = ByteLRU(max_bytes=7, max_items=3)
        cache.put("a", (b"1234", "image/jpeg"))
        cache.put("b", (b"5678", "image/jpeg"))
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b")[0], b"5678")
        cache.put("c", (b"abc", "image/jpeg"))
        self.assertEqual(cache.total_bytes, 7)
        self.assertEqual(list(cache.items), ["b", "c"])

    def test_small_image_is_preferred_and_original_falls_back_on_404(self):
        client = FakeClient([response(status=404), response(body=b"original", url="http://sonarr.local:8989/sonarr/MediaCover/7/poster.jpg")])
        cache = ByteLRU(max_bytes=64)
        result = fetch_poster(7, SERIES, base_url=BASE, api_key="local-test", client=client, cache=cache)
        self.assertEqual(result[0], b"original")
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(client.calls[0][0].endswith("poster-250.jpg?lastWrite=23"))
        self.assertTrue(client.calls[1][0].endswith("poster.jpg?lastWrite=23"))
        self.assertEqual(client.calls[0][1], "GET")
        self.assertEqual(cache.total_bytes, len(b"original"))

    def test_error_causes_are_distinguishable(self):
        cases = [
            (FakeClient([response(status=401)]), "auth"),
            (FakeClient([response(ctype="text/html", body=b"login", url=BASE + "/login")]), "login-redirect"),
            (FakeClient([response(ctype="text/html", body=b"html")]), "not-image"),
            (FakeClient([ResponseTooLarge("large")]), "too-large"),
            (FakeClient([HTTPClientError("Zeitlimit ueberschritten")]), "timeout"),
        ]
        for client, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(PosterFetchError) as raised:
                    fetch_poster(7, SERIES, base_url=BASE, api_key="local-test", client=client, cache=ByteLRU(64))
                self.assertEqual(raised.exception.reason, reason)
        with self.assertRaises(PosterFetchError) as missing:
            fetch_poster(7, {}, base_url=BASE, api_key="local-test", client=FakeClient([]), cache=ByteLRU(64))
        self.assertEqual(missing.exception.reason, "missing")

    def test_missing_small_variant_falls_back_but_second_404_is_reported(self):
        client = FakeClient([response(status=404), response(status=404)])
        with self.assertRaises(PosterFetchError) as raised:
            fetch_poster(7, SERIES, base_url=BASE, api_key="local-test", client=client, cache=ByteLRU(64))
        self.assertEqual(raised.exception.reason, "missing")
