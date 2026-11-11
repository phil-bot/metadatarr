#!/usr/bin/env python3
"""Metadatarr - sonarr_probe.py: Sonarr NUR LESEND sondieren (ausschließlich HTTP GET).

Zugangsdaten kommen NUR aus Umgebungsvariablen, nie aus Argumenten:
    SONARR_URL      z. B. http://sonarr:8989   (ein URL-Base-Pfad wie /sonarr ist erlaubt)
    SONARR_API_KEY  der API-Key (wird nie ausgegeben; jede Ausgabe läuft durch den Redaktor)

Aufruf:
    SONARR_URL=http://sonarr:8989 SONARR_API_KEY=... python3 sonarr_probe.py [--sample 5] [--episodes 3] [--json]

Geprüft wird:
  1. Erreichbarkeit, TLS, URL-Base (aus der URL und aus /api/v3/system/status), Version.
  2. Authentifizierung: mit Key (erwartet 200) und OHNE Key (zeigt, ob Sonarr den Zugriff erzwingt: 401/403,
     Weiterleitung auf Login oder offen).
  3. /api/v3/series: Anzahl, vorhandene Felder (images, overview, seasons, statistics, originalLanguage, ...),
     Verteilung der Originalsprachen und Serienstatus.
  4. Episodenstruktur für eine Stichprobe: Felder, Staffeln, Anzahl, leere Titel/Overviews, TBA-Platzhalter
     (gleicher Regex wie der Altbestand), Specials.
  5. Poster: Form der images[]-Einträge (relativ, mit URL-Base, MediaCoverProxy, absolut), ob der Regex des
     Altbestands die URL akzeptiert, und die tatsächlichen HTTP-Status der Bildrouten (ohne Weiterleitungen
     zu folgen). Auch ohne Key, um zu sehen, ob Bilder frei oder geschützt sind.
Es wird nichts geschrieben, nichts gelöscht, kein POST/PUT/DELETE. Redirects werden NICHT befolgt, damit der Key
nirgendwohin gelangt. Bilder werden nur bis zu einer Grenze gelesen und nicht gespeichert.
"""
import argparse
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OLD_POSTER_REGEX = re.compile(r"^/MediaCover/\d+/[\w.-]+\.(jpg|jpeg|png|webp)(\?[\w=&.:%+-]*)?$", re.IGNORECASE)
OLD_TBA_REGEX = re.compile(r"^\s*(tba|tbd|n/?a)?\s*$|^(episode|folge)\s+\d+$", re.IGNORECASE)
IMAGE_MAGIC = ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"RIFF", "webp?"), (b"GIF8", "gif"))
MAX_IMAGE_BYTES = 6_000_000
ALLOWED_METHODS = ("GET",)


class Redactor:
    def __init__(self, *secrets):
        self.secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def __call__(self, text):
        out = str(text)
        for s in self.secrets:
            out = out.replace(s, "***")
            out = out.replace(urllib.parse.quote(s), "***")
        out = re.sub(r"(?i)\b(apikey|api_key|token|password|passwd)=[^&\s\"']+", r"\1=***", out)
        out = re.sub(r"(?i)(x-api-key|authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n\"',}]+", r"\1: ***", out)
        out = re.sub(r"(?i)^([a-z][a-z0-9+.-]*://)[^/@\s]*@", r"\1***@", out)
        return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Probe:
    def __init__(self, base_url, api_key, timeout=15, pause=0.2, insecure=False):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("SONARR_URL muss mit http:// oder https:// beginnen.")
        self.userinfo_removed = bool(parsed.username or parsed.password)
        netloc = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
        if ":" in parsed.hostname:
            netloc = f"[{parsed.hostname}]" + (f":{parsed.port}" if parsed.port else "")
        self.origin = f"{parsed.scheme}://{netloc}"
        self.base_path = parsed.path.rstrip("/")
        self.key = api_key
        self.timeout = timeout
        self.pause = pause
        self.redact = Redactor(api_key, parsed.username, parsed.password)
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
        self.requests = []  # (Methode, Pfad) - Beleg, dass nur GET gesendet wurde

    def get(self, path, with_key=True, read_limit=None, accept="application/json"):
        """GET ohne Weiterleitungen. Gibt dict(status, headers, body, error) zurück. Nie eine Ausnahme."""
        method = "GET"
        assert method in ALLOWED_METHODS
        time.sleep(self.pause)
        url = self.origin + path
        headers = {"User-Agent": "metadatarr-probe/0.1 (read-only)", "Accept": accept}
        if with_key and self.key:
            headers["X-Api-Key"] = self.key
        req = urllib.request.Request(url, headers=headers, method=method)
        self.requests.append((method, self.redact(urllib.parse.urlsplit(path).path)))
        result = {"status": None, "headers": {}, "body": b"", "error": None}
        try:
            resp = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            result["error"] = self.redact(f"{type(reason).__name__}: {reason}")
            return result
        try:
            result["status"] = resp.status if hasattr(resp, "status") else resp.code
            result["headers"] = {k.lower(): v for k, v in resp.headers.items()}
            limit = read_limit if read_limit is not None else 50_000_000
            result["body"] = resp.read(limit + 1)
            result["truncated"] = len(result["body"]) > limit
            if result["truncated"]:
                result["body"] = result["body"][:limit]
        except (OSError, http.client.HTTPException) as exc:
            result["error"] = self.redact(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        return result

    def json(self, path, with_key=True):
        r = self.get(path, with_key)
        data = None
        if r["status"] == 200:
            try:
                data = json.loads(r["body"].decode("utf-8", "replace"))
            except ValueError:
                r["error"] = "Antwort ist kein gültiges JSON (HTML-Seite oder Proxy?)"
        return r, data


def api(probe, rest):
    return f"{probe.base_path}/api/v3/{rest}"


def classify_image_url(url, base_path):
    if not url:
        return "leer"
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return "absolut"
    p = url.split("?", 1)[0]
    if "/MediaCoverProxy/" in p:
        return "MediaCoverProxy" + ("+URL-Base" if base_path and p.startswith(base_path + "/") else "")
    if p.startswith("/MediaCover/"):
        return "relativ /MediaCover (ohne URL-Base)"
    if base_path and p.startswith(base_path + "/MediaCover/"):
        return "relativ mit URL-Base-Präfix"
    if "/MediaCover/" in p:
        return "relativ mit fremdem Präfix"
    return "anderes Muster"


def safe_path_of(url, base_path):
    """Nur der Pfad (ohne Query und Host) für die Ausgabe; Query enthält ggf. lastWrite."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return "(absolute Adresse, Host nicht ausgegeben)"
    return url.split("?", 1)[0]


def probe_image(probe, path_with_query, with_key=True):
    r = probe.get(path_with_query, with_key=with_key, read_limit=MAX_IMAGE_BYTES, accept="image/*")
    ctype = (r["headers"].get("content-type") or "").split(";")[0].strip().lower()
    body = r["body"]
    magic = next((n for m, n in IMAGE_MAGIC if body.startswith(m)), None)
    loc = r["headers"].get("location")
    out = {"status": r["status"], "content_type": ctype or None, "bytes": len(body), "bilddaten_erkannt": magic,
           "zu_gross": bool(r.get("truncated")), "fehler": r["error"]}
    if loc:
        lp = urllib.parse.urlsplit(loc)
        out["weiterleitung_nach"] = (lp.path or "/") + (" (anderer Host)" if lp.netloc and lp.netloc != urllib.parse.urlsplit(probe.origin).netloc else "")
    if r["status"] == 200:
        if not ctype.startswith("image/"):
            out["urteil"] = "200, aber kein Bild (Content-Type " + (ctype or "fehlt") + ")" + (" - sieht nach HTML aus" if body[:1] == b"<" else "")
        elif magic is None:
            out["urteil"] = "200 image/*, aber Magic-Bytes unbekannt"
        else:
            out["urteil"] = "ok"
    elif r["status"] in (301, 302, 303, 307, 308):
        out["urteil"] = "Weiterleitung (Login oder Proxy?) - wird nicht befolgt"
    elif r["status"] in (401, 403):
        out["urteil"] = "Zugriff verweigert (Authentifizierung)"
    elif r["status"] == 404:
        out["urteil"] = "nicht gefunden"
    elif r["status"] is None:
        out["urteil"] = "keine Antwort: " + str(r["error"])
    else:
        out["urteil"] = f"HTTP {r['status']}"
    return out


def resolve_image_path(url, base_path):
    """Pfad, den der Server abrufen würde: bei relativer URL unverändert, sonst None (absolute Adressen nie abrufen)."""
    if not url or re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return None
    return url if url.startswith("/") else "/" + url


def small_variant(path):
    base, _, query = path.partition("?")
    small = re.sub(r"(\.\w+)$", r"-250\1", base)
    return small + ("?" + query if query else "")


def run(probe, sample=5, episodes=3, pause_note=True):
    rep = {"verbindung": {}, "authentifizierung": {}, "serien": {}, "episoden": [], "poster": [], "hinweise": [], "anfragen": {}}
    rd = probe.redact
    v = rep["verbindung"]
    v["ursprung"] = rd(probe.origin)
    v["url_base_in_SONARR_URL"] = probe.base_path or "(keiner)"
    if probe.userinfo_removed:
        rep["hinweise"].append("SONARR_URL enthielt Benutzer/Passwort; sie wurden ignoriert und nicht ausgegeben.")
    r, status = probe.json(api(probe, "system/status"))
    v["system_status_http"] = r["status"]
    v["fehler"] = r["error"]
    if isinstance(status, dict):
        for k in ("appName", "version", "branch", "isDocker", "urlBase", "authentication", "startupPath"):
            if k in status:
                v[k] = status[k] if k != "startupPath" else "(vorhanden)"
        ub = (status.get("urlBase") or "").rstrip("/")
        if ub and ub != probe.base_path:
            rep["hinweise"].append(f"Sonarr meldet urlBase '{ub}', SONARR_URL hat '{probe.base_path or '-'}'. Der Altbestand setzt SONARR_URL + /api/v3 und kennt keine URL-Base-Behandlung für Bilder.")
        v["url_base_laut_sonarr"] = ub or "(keiner)"
    a = rep["authentifizierung"]
    a["mit_key_http"] = r["status"]
    if r["status"] in (401, 403):
        a["urteil"] = "API-Key wird abgelehnt oder fehlt (HTTP %s). Key und URL-Base prüfen." % r["status"]
    elif r["status"] == 200 and status is not None:
        a["urteil"] = "Key akzeptiert."
    elif r["status"] in (301, 302, 303, 307, 308):
        a["urteil"] = "Weiterleitung statt API-Antwort (Reverse-Proxy/Login davor?). Ziel: " + rd(urllib.parse.urlsplit(r["headers"].get("location", "")).path or "?")
    elif r["status"] == 404:
        a["urteil"] = "404 auf /api/v3/system/status: falsche URL-Base oder kein Sonarr v3/v4."
    else:
        a["urteil"] = f"Unerwartet: HTTP {r['status']} {r['error'] or ''}".strip()
    r0 = probe.get(api(probe, "system/status"), with_key=False)
    a["ohne_key_http"] = r0["status"]
    a["ohne_key_urteil"] = ("Sonarr erzwingt den Key (gut)" if r0["status"] in (401, 403)
                            else "OFFEN: ohne Key antwortet Sonarr mit 200 (Authentifizierung für diese Adresse nicht erzwungen)" if r0["status"] == 200
                            else "Weiterleitung ohne Key (Login-Seite?)" if r0["status"] in (301, 302, 303, 307, 308)
                            else f"HTTP {r0['status']}")
    if r["status"] != 200 or not isinstance(status, dict):
        rep["hinweise"].append("Weitere Prüfungen übersprungen, weil die API-Antwort nicht in Ordnung ist.")
        rep["anfragen"] = request_summary(probe)
        return rep

    r, series = probe.json(api(probe, "series"))
    s = rep["serien"]
    s["http"] = r["status"]
    if not isinstance(series, list):
        s["fehler"] = r["error"] or "keine Liste"
        rep["anfragen"] = request_summary(probe)
        return rep
    s["anzahl"] = len(series)
    fields = {}
    for item in series:
        for k in item:
            fields[k] = fields.get(k, 0) + 1
    s["felder_anzahl_je_feld"] = dict(sorted(fields.items()))
    s["wichtige_felder_fehlen"] = [k for k in ("id", "title", "tvdbId", "titleSlug", "images", "overview", "seasons", "originalLanguage", "status", "network", "year") if fields.get(k, 0) < len(series)]
    langs, stati = {}, {}
    ol_form = set()
    for item in series:
        ol = item.get("originalLanguage")
        ol_form.add(type(ol).__name__)
        name = ol.get("name") if isinstance(ol, dict) else (ol if isinstance(ol, str) else "(fehlt)")
        langs[name or "(leer)"] = langs.get(name or "(leer)", 0) + 1
        stati[item.get("status") or "(leer)"] = stati.get(item.get("status") or "(leer)", 0) + 1
    s["originalLanguage_form"] = sorted(ol_form)
    s["originalLanguage_verteilung"] = dict(sorted(langs.items(), key=lambda kv: -kv[1]))
    s["status_verteilung"] = stati
    s["mit_overview"] = sum(1 for i in series if (i.get("overview") or "").strip())
    s["mit_seasons_feld"] = sum(1 for i in series if isinstance(i.get("seasons"), list))
    s["mit_poster_in_images"] = sum(1 for i in series if any(im.get("coverType") == "poster" for im in i.get("images") or []))
    s["mit_remoteUrl_im_poster"] = sum(1 for i in series if any(im.get("coverType") == "poster" and im.get("remoteUrl") for im in i.get("images") or []))
    cover_types = {}
    shapes = {}
    for item in series:
        for im in item.get("images") or []:
            cover_types[im.get("coverType")] = cover_types.get(im.get("coverType"), 0) + 1
            if im.get("coverType") == "poster":
                shape = classify_image_url(im.get("url") or "", probe.base_path)
                shapes[shape] = shapes.get(shape, 0) + 1
    s["coverTypes"] = cover_types
    s["poster_url_formen"] = shapes
    s["altregex_akzeptiert_poster"] = sum(
        1 for i in series for im in i.get("images") or [] if im.get("coverType") == "poster" and OLD_POSTER_REGEX.match(im.get("url") or ""))
    if shapes and not any(k.startswith("relativ /MediaCover") for k in shapes):
        rep["hinweise"].append("Keine Poster-URL hat die Form /MediaCover/...: der Regex des Altbestands würde keine Poster akzeptieren (stumm, ohne Fehlermeldung).")

    picked = pick_sample(series, sample)
    s["stichprobe_ids"] = [i["id"] for i in picked]
    for item in picked[:episodes]:
        r, eps = probe.json(api(probe, f"episode?seriesId={int(item['id'])}"))
        e = {"serie_id": item["id"], "titel": rd(item.get("title", "?")), "http": r["status"]}
        if isinstance(eps, list):
            e["folgen"] = len(eps)
            ef = {}
            for ep in eps:
                for k in ep:
                    ef[k] = ef.get(k, 0) + 1
            e["felder_anzahl_je_feld"] = dict(sorted(ef.items()))
            seasons = {}
            for ep in eps:
                seasons[ep.get("seasonNumber")] = seasons.get(ep.get("seasonNumber"), 0) + 1
            e["staffeln_folgen"] = {str(k): v for k, v in sorted(seasons.items(), key=lambda kv: (kv[0] is None, kv[0]))}
            e["staffel_0_specials"] = seasons.get(0, 0)
            e["titel_leer"] = sum(1 for ep in eps if not (ep.get("title") or "").strip())
            e["titel_altregex_tba"] = sum(1 for ep in eps if OLD_TBA_REGEX.match(ep.get("title") or ""))
            e["titel_tba_exakt"] = sum(1 for ep in eps if (ep.get("title") or "").strip().upper() in ("TBA", "TBD", "N/A"))
            e["overview_leer"] = sum(1 for ep in eps if not (ep.get("overview") or "").strip())
            e["airDate_leer"] = sum(1 for ep in eps if not ep.get("airDate"))
            e["mit_datei"] = sum(1 for ep in eps if ep.get("hasFile"))
            e["mit_tvdbId"] = sum(1 for ep in eps if ep.get("tvdbId"))
            sl = item.get("seasons")
            if isinstance(sl, list):
                e["staffeln_laut_serienobjekt"] = sorted(x.get("seasonNumber") for x in sl if isinstance(x, dict))
        else:
            e["fehler"] = r["error"]
        rep["episoden"].append(e)

    for item in picked:
        entry = {"serie_id": item["id"], "titel": rd(item.get("title", "?")), "bilder": []}
        posters = [im for im in item.get("images") or [] if im.get("coverType") == "poster"]
        if not posters:
            entry["urteil"] = "kein poster in images[]"
            rep["poster"].append(entry)
            continue
        im = posters[0]
        url = im.get("url") or ""
        entry["form"] = classify_image_url(url, probe.base_path)
        entry["pfad"] = rd(safe_path_of(url, probe.base_path))
        entry["altregex_akzeptiert"] = bool(OLD_POSTER_REGEX.match(url))
        entry["remoteUrl_vorhanden"] = bool(im.get("remoteUrl"))
        path = resolve_image_path(url, probe.base_path)
        if path is None:
            entry["urteil"] = "absolute oder leere Adresse: wird nicht abgerufen"
        else:
            tests = [("wie in images[].url, mit Key", path, True), ("-250-Variante, mit Key", small_variant(path), True), ("wie in images[].url, ohne Key", path, False)]
            if probe.base_path and not path.startswith(probe.base_path + "/"):
                tests.append(("mit vorangestellter URL-Base, mit Key", probe.base_path + path, True))
            for label, p, wk in tests:
                res = probe_image(probe, p, wk)
                res["pruefung"] = label
                entry["bilder"].append(res)
        rep["poster"].append(entry)
    ok = sum(1 for p in rep["poster"] for b in p["bilder"] if b["pruefung"] == "wie in images[].url, mit Key" and b.get("urteil") == "ok")
    rep["hinweise"].append(f"Poster mit Key über die Originalroute erfolgreich: {ok} von {len(picked)} geprüften Serien.")
    rep["anfragen"] = request_summary(probe)
    return rep


def pick_sample(series, n):
    """Erste n Serien, bevorzugt mit Poster; stabile Reihenfolge nach id."""
    ordered = sorted(series, key=lambda i: i.get("id", 0))
    with_poster = [i for i in ordered if any(im.get("coverType") == "poster" for im in i.get("images") or [])]
    without = [i for i in ordered if i not in with_poster]
    return (with_poster + without)[: max(0, n)]


def request_summary(probe):
    counts = {}
    for m, _ in probe.requests:
        counts[m] = counts.get(m, 0) + 1
    return {"nach_methode": counts, "gesamt": len(probe.requests)}


def render(rep):
    out = []
    add = out.append

    def block(title, obj, indent=2):
        add(f"== {title} ==")
        for k, v in obj.items():
            add(" " * indent + f"{k}: {json.dumps(v, ensure_ascii=False)}")

    block("Verbindung", rep["verbindung"])
    block("Authentifizierung", rep["authentifizierung"])
    if rep["serien"]:
        block("Serien", rep["serien"])
    for e in rep["episoden"]:
        block(f"Episoden (Serie {e['serie_id']})", e)
    add("== Poster ==")
    for p in rep["poster"]:
        add(f"  Serie {p['serie_id']} ({p['titel']}): " + ", ".join(f"{k}={json.dumps(p[k], ensure_ascii=False)}" for k in p if k not in ("serie_id", "titel", "bilder")))
        for b in p["bilder"]:
            add("     - " + json.dumps(b, ensure_ascii=False))
    add("== Hinweise ==")
    for h in rep["hinweise"]:
        add("  - " + h)
    block("Gesendete Anfragen (nur GET erlaubt)", rep["anfragen"])
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Metadatarr: Sonarr nur lesend sondieren. URL und Key nur über SONARR_URL/SONARR_API_KEY.")
    ap.add_argument("--sample", type=int, default=5, help="Zahl der Serien für die Posterprüfung (Standard 5)")
    ap.add_argument("--episodes", type=int, default=3, help="Zahl der Serien für die Episodenstruktur (Standard 3)")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--pause", type=float, default=0.2, help="Pause in Sekunden zwischen Anfragen")
    ap.add_argument("--insecure", action="store_true", help="TLS-Zertifikat nicht prüfen (nur für Selbstsigniert im LAN)")
    ap.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    args = ap.parse_args(argv)
    url = os.environ.get("SONARR_URL", "").strip()
    key = os.environ.get("SONARR_API_KEY", "").strip()
    if not url or not key:
        print("Fehler: SONARR_URL und SONARR_API_KEY müssen als Umgebungsvariablen gesetzt sein (nicht als Argument).", file=sys.stderr)
        return 2
    try:
        probe = Probe(url, key, args.timeout, args.pause, args.insecure)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    rep = run(probe, args.sample, args.episodes)
    text = json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render(rep)
    print(probe.redact(text))  # letzte Redaktion über die gesamte Ausgabe
    return 0


if __name__ == "__main__":
    sys.exit(main())
