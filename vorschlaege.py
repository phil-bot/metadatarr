"""tba-tickets – Suchkette und Aktionen auf Vorschläge (Haken, Verwerfen, Bearbeiten)."""
import re
from config import TITLE_SUFFIX_REGEX
from plugins.registry import SOURCES
from plugins.sources import context_for_source, source_credentials_configured, source_enabled
from speicher import BUSY_SQL, db_exec, store_suggestions


SE_IN_TITLE = re.compile(r"\s*\(S\d+\s*/\s*E\d+\)", re.IGNORECASE)


def clean_title(text):
    """Entfernt die S/E-Angabe „(SxxExx)“ aus Titeln. Sie gehört nicht in den TVDB-Titel."""
    text = SE_IN_TITLE.sub("", text or "")
    return TITLE_SUFFIX_REGEX.sub("", text).strip()


def _source_rows(source, series, eps, season=None, force=False):
    plugin = SOURCES.get(source)
    if plugin is None:
        raise RuntimeError("Die konfigurierte Quelle ist nicht registriert.")
    if isinstance(plugin, type):
        plugin = plugin()
    context = context_for_source(source)
    return plugin.fetch(context, {"series": series, "episodes": eps, "season": season, "force": force})


def search_series(series, source, season=None, force=False):
    """Run one enabled source plugin and persist its suggestions and candidate values."""
    eps = [e for e in series["tba"] if e.get("airDate") and (season is None or e["season"] == season)]
    if not eps:
        where = f" in Staffel {season}" if season is not None else ""
        return 0, f"Keine TBA-Folge mit Sendedatum{where}, daher kein Abgleich."
    if not source_enabled(source):
        return 0, f"Quelle {source} ist deaktiviert."
    if source == "tmdb" and not source_credentials_configured("tmdb"):
        return 0, "TMDB: kein Schlüssel gesetzt."
    rows = _source_rows(source, series, eps, season=season, force=force)
    store_suggestions(series["id"], source, rows, season)
    return len(rows), ""


CHAIN_STAGES = ("mediathek", "tmdb", "fernsehserien")
NIGHTLY_STAGES = ("mediathek", "tmdb")
STAGE_LABEL = {"mediathek": "Mediathek", "tmdb": "TMDB", "fernsehserien": "fernsehserien.de"}


def search_chain(series, season=None, force=False, stages=CHAIN_STAGES):
    """Search only enabled source plugins. No action plugin can be reached from this path."""
    eps = [e for e in series["tba"] if e.get("airDate") and (season is None or e["season"] == season)]
    if not eps:
        where = f" in Staffel {season}" if season is not None else ""
        return "", f"Keine TBA-Folge mit Sendedatum{where}, daher kein Abgleich."
    rest, counts, notes = eps, [], []
    for stage in stages:
        if not source_enabled(stage, nightly=(stage in NIGHTLY_STAGES and force)):
            continue
        if stage == "tmdb" and not source_credentials_configured("tmdb"):
            notes.append("TMDB: kein Schlüssel gesetzt.")
            continue
        rows = _source_rows(stage, series, rest, season=season, force=force) if rest else []
        store_suggestions(series["id"], stage, rows, season)
        found_eps = {(r["season"], r["episode"]) for r in rows if r["level"] != "niedrig"}
        rest = [e for e in rest if (e["season"], e["episode"]) not in found_eps]
        counts.append(f"{STAGE_LABEL[stage]} {len(rows)}")
    if not counts and not notes:
        notes.append("Keine Quelle ist fuer diese Suche aktiviert.")
    return " · ".join(counts), " ".join(notes)


def ticket_links(sid):
    """Tickets je Vorschlag einer Serie: {Vorschlags-ID: [(Ticket-ID, Sprache, Status), ...]}."""
    rows = db_exec(
        "SELECT ti.suggestion_id AS sug, t.id AS tid, t.lang AS lang, t.status AS status "
        "FROM ticket_item ti JOIN ticket t ON t.id = ti.ticket_id WHERE t.series_id = ? ORDER BY t.id",
        (sid,), fetch=True,
    )
    out = {}
    for r in rows:
        out.setdefault(r["sug"], []).append((r["tid"], r["lang"], r["status"]))
    return out


def busy_ids(links, lang=None):
    """Vorschläge, die in einem aktiven Ticket stehen (optional nur für eine Sprache)."""
    return {
        sug for sug, items in links.items()
        if any(st != "nicht erfolgreich" and (lang is None or lg == lang) for _, lg, st in items)
    }


def busy_both(links):
    """Vorschläge, die schon in einem Ticket für Deutsch UND in einem für Englisch stehen. Nur diese sind für
    „Alle hohen markieren“ gesperrt, damit das zweite Sprachticket dieselben Folgen wieder markieren kann."""
    return busy_ids(links, "de") & busy_ids(links, "en")


def accept_high(sid, season):
    """Setzt je Folge der Staffel den Haken beim ersten Vorschlag mit Sicherheit hoch. Die übrigen offenen
    Vorschläge dieser Folge werden verworfen. Folgen mit Haken oder in Tickets für beide Sprachen bleiben
    unberührt. Es entsteht noch kein Ticket. Gibt die Anzahl markierter Folgen zurück."""
    busy = busy_both(ticket_links(sid))
    rows = db_exec(
        "SELECT id, episode, status, level, marked FROM suggestion WHERE series_id = ? AND season = ? "
        "ORDER BY episode, id",
        (sid, season), fetch=True,
    )
    by_ep = {}
    for r in rows:
        by_ep.setdefault(r["episode"], []).append(r)
    count, skip_mark, skip_busy = 0, 0, 0
    for ep, rs in by_ep.items():
        pick = next((r for r in rs if r["status"] == "offen" and r["level"] == "hoch"), None)
        if pick is None:
            continue
        if any(r["id"] in busy for r in rs):
            skip_busy += 1
            continue
        marked = [r for r in rs if r["marked"]]
        if marked:
            # Schon ein Haken auf dieser Folge: gemeldet wird nur, wenn der Haken nicht schon der hohe Vorschlag ist.
            if marked[0]["level"] != "hoch":
                skip_mark += 1
            continue
        db_exec("UPDATE suggestion SET marked = 1 WHERE id = ?", (pick["id"],))
        db_exec(
            "UPDATE suggestion SET status = 'verworfen' WHERE series_id = ? AND season = ? AND episode = ? "
            "AND status = 'offen' AND id <> ? AND id NOT IN (" + BUSY_SQL + ")",
            (sid, season, ep, pick["id"]),
        )
        count += 1
    from core.candidates import sync_candidate_states
    sync_candidate_states(series_id=sid, season=season)
    return count, skip_mark, skip_busy


def form_value(form, key):
    return (form.get(key) or [""])[0].strip()


def suggestion_action(sug_id, form):
    """Haken, Verwerfen, Bearbeiten und Zurücksetzen eines Vorschlags. Gibt die Serien-ID zurück, None wenn es
    den Vorschlag nicht gibt. Der Status „eingereicht“ gehört nur zu Tickets, nicht zu Vorschlägen."""
    rows = db_exec("SELECT * FROM suggestion WHERE id = ?", (sug_id,), fetch=True)
    if not rows:
        return None
    r = rows[0]
    action = form_value(form, "aktion")
    sid, season, episode = r["series_id"], r["season"], r["episode"]
    busy = sug_id in busy_ids(ticket_links(sid))
    if action == "toggle" and r["status"] == "offen":
        if r["marked"]:
            db_exec("UPDATE suggestion SET marked = 0 WHERE id = ?", (sug_id,))
        else:
            # Je Folge höchstens ein Haken; andere offene Vorschläge der Folge werden verworfen.
            db_exec(
                "UPDATE suggestion SET marked = 0, status = 'verworfen' WHERE series_id = ? AND season = ? "
                "AND episode = ? AND id <> ? AND marked = 0 AND status = 'offen' AND id NOT IN (" + BUSY_SQL + ")",
                (sid, season, episode, sug_id),
            )
            db_exec("UPDATE suggestion SET marked = 1 WHERE id = ?", (sug_id,))
    elif action == "discard" and r["status"] == "offen" and not busy:
        db_exec("UPDATE suggestion SET status = 'verworfen', marked = 0 WHERE id = ?", (sug_id,))
    elif action == "reopen" and r["status"] == "verworfen":
        db_exec("UPDATE suggestion SET status = 'offen' WHERE id = ?", (sug_id,))
    from core.candidates import sync_candidate_states
    sync_candidate_states(series_id=sid, season=season, episode=episode)
    return sid


def suggestion_map(sid):
    """Vorschläge einer Serie, gruppiert nach (Staffel, Folge)."""
    rows = db_exec("SELECT * FROM suggestion WHERE series_id = ? ORDER BY source, id", (sid,), fetch=True)
    out = {}
    for r in rows:
        out.setdefault((r["season"], r["episode"]), []).append(r)
    return out
