"""tba-tickets – Externe Quellen (Mediathek, TMDB, fernsehserien.de) und das Zuordnen der Treffer."""
import difflib
import html
import json
import re
import threading
import time
import unicodedata
from datetime import date, datetime
import urllib.parse
from urllib.parse import urlsplit
from config import FS_PAUSE, MAX_TMDB_SEASONS, MEDIA_API_URL, MEDIA_MAX_PAGES, MEDIA_PAUSE, TMDB_API_KEY, TMDB_API_URL, TMDB_PAUSE, USER_AGENT
from net.http_client import HTTPClientError, NetworkProfile, SafeHTTPClient
from speicher import cache_get, cache_put


MEDIA_PAGE_SIZE = 50
FS_BASE = "https://www.fernsehserien.de"
AUDIO_DESC = re.compile(r"audiodeskription", re.IGNORECASE)
SE_TITLE = re.compile(r"\(S(\d+)\s*/\s*E(\d+)\)", re.IGNORECASE)
NET = {"lock": threading.Lock(), "last": 0.0}
SAFE_HTTP = SafeHTTPClient()
DEFAULT_SOURCE_HOSTS = {
    "mediathek": ("mediathekviewweb.de",),
    "tmdb": ("api.themoviedb.org",),
    "fernsehserien": ("www.fernsehserien.de",),
    "deepl": ("api-free.deepl.com", "api.deepl.com"),
}


class SourceHTTPError(HTTPClientError):
    def __init__(self, status):
        self.status = int(status)
        super().__init__(f"Quellenserver antwortet mit HTTP {self.status}.")


def source_network_profile(source_id, url, pause=0.0, max_bytes=2_000_000):
    """Construct a strict profile from the source's persisted host grants."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    hosts = DEFAULT_SOURCE_HOSTS.get(source_id, ())
    allow_external, allow_private = True, False
    try:
        from speicher import db_exec
        rows = db_exec("SELECT granted_hosts_json,allow_external,allow_private FROM source_config WHERE id=?",
                       (source_id,), fetch=True)
        if rows:
            import json as _json
            saved = _json.loads(rows[0]["granted_hosts_json"] or "[]")
            hosts = tuple(saved)
            allow_external = bool(rows[0]["allow_external"])
            allow_private = bool(rows[0]["allow_private"])
    except Exception:
        pass
    return NetworkProfile(source_id=source_id, granted_hosts=tuple(hosts), allow_external=allow_external,
                          allow_private=allow_private, timeout_seconds=20, connect_timeout_seconds=5,
                          max_bytes=max_bytes, min_interval_seconds=max(0.0, float(pause)), max_redirects=3)


def polite_wait(pause):
    """Legacy no-op: SafeHTTPClient enforces the per-host rate interval."""
    return None


def http_json(url, data=None, headers=None, pause=0.0, source_id="mediathek", http=None):
    client = http or SAFE_HTTP
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    profile = source_network_profile(source_id, url, pause=pause)
    result = client.request(url, profile=profile, method="POST" if data is not None else "GET",
                            headers=hdrs, body=data)
    if result.status < 200 or result.status >= 300:
        raise SourceHTTPError(result.status)
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RuntimeError("Quellenantwort enthaelt kein gueltiges JSON.") from None


def media_search(title, force=False, http=None):
    """Sucht in MediathekViewWeb nach dem Sendungsnamen (Feld topic). Antwort wird zwischengespeichert.

    Hinweis: Das API-Format stammt aus Erinnerung und ist nicht verifiziert (HANDOFF, Punkt 2.2).
    """
    key = "media:" + title.lower()
    cached = None if force else cache_get(key)
    if cached is not None:
        return cached
    results = []
    offset = 0
    for _ in range(MEDIA_MAX_PAGES):
        payload = {
            "queries": [{"fields": ["topic"], "query": title}],
            "sortBy": "timestamp",
            "sortOrder": "desc",
            "future": True,
            "offset": offset,
            "size": MEDIA_PAGE_SIZE,
        }
        resp = http_json(
            MEDIA_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            pause=MEDIA_PAUSE, source_id="mediathek", http=http,
        )
        items = ((resp or {}).get("result") or {}).get("results") or []
        results.extend(items)
        if len(items) < MEDIA_PAGE_SIZE:
            break
        offset += MEDIA_PAGE_SIZE
    slim = [
        {
            "channel": h.get("channel") or "",
            "topic": h.get("topic") or "",
            "title": h.get("title") or "",
            "description": h.get("description") or "",
            "timestamp": h.get("timestamp"),
            "url": h.get("url_website") or "",
        }
        for h in results
    ]
    cache_put(key, slim)
    return slim


def tmdb_get(path, params, lang, force=False, http=None, api_key=None):
    """TMDB-Abfrage mit Zwischenspeicher. Der API-Key erscheint nie in Fehlermeldungen."""
    ckey = f"tmdb:{lang}:{path}:{urllib.parse.urlencode(sorted(params.items()))}"
    cached = None if force else cache_get(ckey)
    if cached is not None:
        return cached
    key = api_key or TMDB_API_KEY
    if not key:
        raise RuntimeError("TMDB_API_KEY ist nicht gesetzt.")
    query = dict(params, language=lang, api_key=key)
    url = f"{TMDB_API_URL}{path}?{urllib.parse.urlencode(query)}"
    try:
        body = http_json(url, pause=TMDB_PAUSE, source_id="tmdb", http=http)
    except Exception as exc:  # noqa: BLE001 - Schlüssel aus der Meldung entfernen
        text = str(exc).replace(key, "***")
        raise RuntimeError(f"TMDB-Fehler: {type(exc).__name__}: {text}") from None
    cache_put(ckey, body)
    return body


def norm(text):
    return re.sub(r"[^0-9a-zäöüß]+", " ", (text or "").lower()).strip()


def similar(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def parse_day(text):
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def tmdb_find_series(series, force=False, http=None, api_key=None):
    res = tmdb_get("/search/tv", {"query": series["title"]}, "de-DE", force, http, api_key)
    best, best_score = None, 0.0
    for r in (res.get("results") or [])[:10]:
        score = max(similar(series["title"], r.get("name")), similar(series["title"], r.get("original_name")))
        if series.get("year") and str(series["year"]) == (r.get("first_air_date") or "")[:4]:
            score += 0.05
        if score > best_score:
            best, best_score = r, score
    return best if best is not None and best_score >= 0.8 else None


def tmdb_episodes(tmdb_id, force=False, only_season=None, http=None, api_key=None):
    show = tmdb_get(f"/tv/{tmdb_id}", {}, "de-DE", force, http, api_key)
    seasons = min(int(show.get("number_of_seasons") or 0), MAX_TMDB_SEASONS)
    numbers = [only_season] if only_season is not None else range(1, seasons + 1)
    episodes = []
    for n in numbers:
        try:
            de = tmdb_get(f"/tv/{tmdb_id}/season/{n}", {}, "de-DE", force, http, api_key)
            en = tmdb_get(f"/tv/{tmdb_id}/season/{n}", {}, "en-US", force, http, api_key)
        except RuntimeError as exc:
            if "404" in str(exc):
                continue
            raise
        en_names = {x.get("episode_number"): x.get("name") or "" for x in en.get("episodes") or []}
        for x in de.get("episodes") or []:
            episodes.append(
                {
                    "season": n,
                    "episode": x.get("episode_number") or 0,
                    "air_date": x.get("air_date") or "",
                    "name_de": x.get("name") or "",
                    "name_en": en_names.get(x.get("episode_number")) or "",
                    "overview": x.get("overview") or "",
                }
            )
    return episodes


def make_row(e, source, source_key, title_de, title_en, desc, channel, air, link, level, hint=""):
    return {
        "season": e["season"],
        "episode": e["episode"],
        "source": source,
        "source_key": source_key,
        "title_de": title_de or "",
        "title_en": title_en or "",
        "description": desc or "",
        "channel": channel or "",
        "air_date": air or "",
        "link": link or "",
        "level": level,
        "hint": hint or "",
    }


def media_key(h):
    """Eindeutiger Schlüssel eines Mediathek-Treffers: Adresse, sonst Kanal, Zeitstempel und Titel."""
    return h["url"] or f"{h['channel']}|{h['timestamp']}|{h['title']}"


def se_rows(series, eps, parsed):
    """Ordnet Mediathek-Treffer mit S/E-Angabe im Titel zuerst über die Nummer zu.

    Die Nummer im Titel ist die einzige Zuordnung. Ein Datum wird nur als Hinweis angezeigt, wenn der
    Abstand zum TVDB-Sendedatum ungewöhnlich ist. Treffer ohne passende TBA-Folge entstehen nicht.
    Gibt (Vorschlagszeilen, Schlüssel der zugeordneten Treffer) zurück.
    """
    tba_keys = {(x["season"], x["episode"]): x for x in series["tba"]}
    scope = {(x["season"], x["episode"]) for x in eps}
    rows, claimed = [], set()
    for day, _, h in parsed:
        m = SE_TITLE.search(h["title"] or "")
        key = (int(m.group(1)), int(m.group(2))) if m else None
        if key is None or key not in tba_keys or key not in scope:
            continue
        e = tba_keys[key]
        air = parse_day(e["airDate"])
        hint = "S/E passt."
        if air and not MEDIA_LEAD_MIN <= (air - day).days <= MEDIA_LEAD_MAX:
            hint += f" Mediathek {day.strftime('%d.%m.%Y')}, TVDB {air.strftime('%d.%m.%Y')}."
        rows.append(make_row(e, "mediathek", media_key(h), h["title"], h["title"], h["description"],
                             h["channel"], air.isoformat() if air else "", h["url"], "hoch", hint))
        claimed.add(media_key(h))
    return rows, claimed


# Abstand Veröffentlichung bis Sendung, ab dem der Datumshinweis erscheint. Das Datum ordnet nichts zu.
MEDIA_LEAD_MIN, MEDIA_LEAD_MAX = 0, 14


def match_media(series, eps, hits):
    """Ordnet Mediathek-Treffer nur über die S/E-Angabe im Titel zu (Titel-Ähnlichkeit auf den Sendungsnamen).

    Ein Datum ordnet nichts zu. Die Mediathek liefert das Veröffentlichungsdatum, das bei wöchentlichen
    Serien zu viele Folgen gleichzeitig träfe. Titel ohne passende S/E-Angabe (oder mit einer Folge, die
    in TVDB nicht als TBA geführt wird) werden verworfen. Titel mit "(Audiodeskription)" werden übersprungen.
    """
    parsed = []
    for h in hits:
        if not h.get("timestamp") or AUDIO_DESC.search(h.get("title") or ""):
            continue
        day = datetime.fromtimestamp(h["timestamp"]).date()
        sim = similar(series["title"], h["topic"])
        if sim >= 0.6:
            parsed.append((day, sim, h))
    rows, _claimed = se_rows(series, eps, parsed)
    return rows


PLACEHOLDER_EN = re.compile(r"^\s*(episode|folge)\s*\d+\s*$", re.IGNORECASE)


def en_title(t, german):
    """EN-Titel für TMDB-Vorschläge. Platzhalter wie „Episode 4“ gelten als leer.

    Bei deutschen Originalserien ist der deutsche Titel der EN-Titel (wie bei der Mediathek).
    """
    en = t["name_en"] or ""
    if en and not PLACEHOLDER_EN.match(en):
        return en
    return t["name_de"] if german else ""


def match_tmdb(eps, tmdb_eps, tmdb_id, german=False):
    dated = [(parse_day(t["air_date"]), t) for t in tmdb_eps]
    dated = [(d, t) for d, t in dated if d]
    rows = []
    for e in eps:
        air = parse_day(e["airDate"])
        if air is None:
            continue
        same = [t for d, t in dated if d == air]
        if same:
            for t in same[:3]:
                level = "hoch" if len(same) == 1 else "mittel"
                link = f"https://www.themoviedb.org/tv/{tmdb_id}/season/{t['season']}/episode/{t['episode']}?language=de-DE"
                rows.append(make_row(e, "tmdb", f"{tmdb_id}:{t['season']}:{t['episode']}",
                                     t["name_de"], en_title(t, german), t["overview"], "TMDB",
                                     air.isoformat(), link, level))
        else:
            near = [t for d, t in dated if abs((d - air).days) == 1]
            for t in near[:2]:
                link = f"https://www.themoviedb.org/tv/{tmdb_id}/season/{t['season']}/episode/{t['episode']}?language=de-DE"
                rows.append(make_row(e, "tmdb", f"{tmdb_id}:{t['season']}:{t['episode']}",
                                     t["name_de"], en_title(t, german), t["overview"], "TMDB",
                                     air.isoformat(), link, "niedrig"))
    return rows


def http_text(url, pause=0.0, http=None, source_id="fernsehserien"):
    client = http or SAFE_HTTP
    profile = source_network_profile(source_id, url, pause=pause, max_bytes=2_000_000)
    result = client.request(url, profile=profile, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    if result.status < 200 or result.status >= 300:
        raise SourceHTTPError(result.status)
    return result.body.decode("utf-8", "replace")


def slugify(title):
    """Name zu fernsehserien.de-Adresse: 'Notruf Hafenkante' -> 'notruf-hafenkante'."""
    text = (title or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def html_to_lines(raw):
    """Wandelt HTML in Textzeilen um. Blockelemente erzeugen Zeilenumbrüche."""
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|tr|td|section|article)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.split("\n"))
    return [line for line in lines if line]


FS_EPISODE = re.compile(r"^Staffel (\d+), Folge (\d+)\s*(\(|$)")
FS_TITLE = re.compile(r"^\d+\.\s+(\S.*)$")
FS_TV_PREMIERE = re.compile(r"Deutsche TV-Premiere\s*\S*\s*(\d{2}\.\d{2}\.\d{4})\s*(\S+)?")
FS_END = ("Erhalte Neuigkeiten", "Alle Neuigkeiten", "Highlights", "Auch interessant", "Weiterführende")


def parse_fs_episodes(raw):
    """Liest Folgen aus einer fernsehserien.de-Staffelseite: Nummer, Titel, deutsches TV-Datum, Sender, Beschreibung."""
    records = []
    cur, title = None, ""
    for line in html_to_lines(raw):
        if line.startswith(FS_END):
            break
        m = FS_EPISODE.match(line)
        if m:
            cur = {"season": int(m.group(1)), "episode": int(m.group(2)), "title": title,
                   "air_date": "", "channel": "", "desc": ""}
            records.append(cur)
            continue
        t = FS_TITLE.match(line)
        if t and len(line) < 120:
            title = t.group(1)
            continue
        if cur is None:
            continue
        p = FS_TV_PREMIERE.search(line)
        if p:
            try:
                cur["air_date"] = datetime.strptime(p.group(1), "%d.%m.%Y").date().isoformat()
            except ValueError:
                pass
            cur["channel"] = p.group(2) or ""
            continue
        if len(line) > 60 and len(line) > len(cur["desc"]) and not line.startswith(("Deutsche", "Staffel")):
            cur["desc"] = line
    unique = {}
    for r in records:
        key = (r["season"], r["episode"])
        if key not in unique or (r["air_date"] and not unique[key]["air_date"]):
            unique[key] = r
    return list(unique.values())


def fs_season_ids(series, force=False, http=None):
    """Liest die Staffel-Kennungen aus der Episodenübersicht. Gibt (Kennungen, Hinweis) zurück."""
    slug = slugify(series["title"])
    key = f"fs:index:{slug}"
    cached = None if force else cache_get(key)
    if cached is not None:
        return {int(n): int(sid) for n, sid in cached}, ""
    try:
        raw = http_text(f"{FS_BASE}/{slug}/episodenguide", pause=FS_PAUSE, http=http)
    except SourceHTTPError as exc:
        if exc.status == 404:
            return {}, f"fernsehserien.de: keine Seite unter „{slug}“ (404). Bitte von Hand prüfen."
        raise
    if norm(series["title"]) not in norm(" ".join(html_to_lines(raw))):
        return {}, "fernsehserien.de: Seite gefunden, aber der Serientitel passt nicht. Bitte von Hand prüfen."
    ids = sorted({(int(n), int(sid)) for n, sid in
                  re.findall(rf"/{re.escape(slug)}/episodenguide/staffel-(\d+)/(\d+)", raw)})
    if not ids:
        return {}, "fernsehserien.de: keine Staffeln gelesen (Seitenaufbau geändert?)."
    cache_put(key, [list(p) for p in ids])
    return dict(ids), ""


def fs_season_records(slug, n, sid, force=False, http=None):
    key = f"fs:{slug}:{n}:{sid}"
    cached = None if force else cache_get(key)
    if cached is not None:
        return cached
    records = parse_fs_episodes(http_text(f"{FS_BASE}/{slug}/episodenguide/staffel-{n}/{sid}", pause=FS_PAUSE, http=http))
    if records:
        cache_put(key, records)
    return records


def dated_row(e, t, level, source, link_of, air):
    """Eine Zeile aus einem Treffer mit Datum. Weicht die Nummer ab, höchstens mittel mit Hinweis."""
    hint = ""
    if (t["season"], t["episode"]) != (e["season"], e["episode"]):
        if level == "hoch":
            level = "mittel"
        hint = (f"Nummer bei {source}: S{t['season']:02d}E{t['episode']:02d}, "
                f"TVDB: S{e['season']:02d}E{e['episode']:02d}. Bitte prüfen.")
    return make_row(e, source, f"{t['season']}:{t['episode']}", t["name_de"], t["name_en"],
                    t["overview"], t["channel"], air.isoformat(), link_of(t), level, hint)


def match_dated(eps, items, source, link_of):
    """Ordnet Treffer mit Datum über das Sendedatum zu, wie bei TMDB, mit S/E-Prüfung."""
    dated = [(parse_day(t.get("air_date")), t) for t in items]
    dated = [(d, t) for d, t in dated if d]
    rows = []
    for e in eps:
        air = parse_day(e["airDate"])
        if air is None:
            continue
        same = [t for d, t in dated if d == air]
        for t in same[:3]:
            rows.append(dated_row(e, t, "hoch" if len(same) == 1 else "mittel", source, link_of, air))
        if not same:
            for t in [t for d, t in dated if abs((d - air).days) == 1][:2]:
                rows.append(dated_row(e, t, "niedrig", source, link_of, air))
    return rows


def fs_rows(series, eps, force=False, http=None):
    """Vorschläge von fernsehserien.de. Gibt (Zeilen, Hinweis) zurück. Fehler werden zum Hinweis, nicht zum Abbruch."""
    slug = slugify(series["title"])
    try:
        ids, note = fs_season_ids(series, force, http)
        if note:
            return [], note
        items = []
        for n in sorted({e["season"] for e in eps}):
            if n not in ids:
                continue
            for r in fs_season_records(slug, n, ids[n], force, http):
                items.append({
                    "season": r["season"], "episode": r["episode"], "air_date": r["air_date"],
                    "name_de": r["title"], "name_en": "", "overview": r["desc"],
                    "channel": r["channel"] or "fernsehserien.de",
                })
        if not items:
            return [], "fernsehserien.de: keine Folgen mit deutschem Sendedatum gelesen."
    except (HTTPClientError, OSError) as exc:
        return [], f"fernsehserien.de nicht erreichbar: {type(exc).__name__}."
    rows = match_dated(eps, items, "fernsehserien",
                       lambda t: f"{FS_BASE}/{slug}/episodenguide/staffel-{t['season']}/{ids[t['season']]}")
    return rows, ""
