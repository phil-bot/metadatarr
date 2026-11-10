"""tba-tickets – Webseiten und HTTP-Handler."""
import hmac
import json
import mimetypes
import os
import platform
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from api.router import BODY_LIMIT, dispatch as dispatch_api
from config import (CSRF_TOKEN, DB_PATH, NIGHTLY_HOUR, NIGHTLY_SEARCH, SHOW_POSTERS, SONARR_API_KEY,
                    SONARR_PUBLIC_URL, SONARR_URL, TMDB_API_KEY, config_overview)
from sonarr import fetch_poster, get_data, POSTERS
from core.poster_service import PosterFetchError
from speicher import STATUSES, cache_get_raw, db_exec
from ticket import (LANG_NAME, create_ticket, render_ticket_page, report_csv, status_chip, ticket_label, ticket_list,
                    ticket_name, ticket_refresh_one, ticket_status, ticket_submit, ticket_text)
from ui import de_date, esc, icon, page, poster, render_error, set_path
from vorschlaege import accept_high, busy_both, busy_ids, clean_title, form_value, search_chain, search_series, suggestion_action, suggestion_map, ticket_links


FLASH = {}
FLASH_LOCK = threading.Lock()

LEVEL_CHIP = {"hoch": "ok", "mittel": "warn", "niedrig": ""}
LEVEL_TIP = {
    "hoch": "Sicherheit hoch: starker Treffer, zum Beispiel S/E-Angabe passt oder Datum stimmt genau.",
    "mittel": "Sicherheit mittel: Treffer über das Datum. Bitte prüfen, bevor du ein Ticket erstellst.",
    "niedrig": "Sicherheit niedrig: schwacher Treffer.",
}
DATE_STATE = {"gesendet": ("d-sent", "\u2714 "), "angekündigt": ("d-ann", "\u23f3 ")}


def flash_set(sid, msg):
    """Meldung nach einer Aktion. Sie steht im Speicher und nicht in der Adresse; sie erscheint einmal."""
    with FLASH_LOCK:
        FLASH[sid] = msg


def flash_pop(sid):
    with FLASH_LOCK:
        return FLASH.pop(sid, "")


def night_info():
    if not NIGHTLY_SEARCH:
        return "Nachtlauf ist abgeschaltet (NIGHTLY_SEARCH=0)."
    text = f"Nachtlauf {NIGHTLY_HOUR:02d}:00 (Mediathek, TMDB)"
    last = cache_get_raw("nightly")
    if last:
        text += f" · letzter Lauf {datetime.fromtimestamp(last['time']).strftime('%d.%m.%Y %H:%M')}"
    else:
        text += " · noch kein Lauf"
    return text


def sug_state(r, busy):
    """Anzeigestatus eines Vorschlags: verworfen, im Ticket, markiert oder offen."""
    if r["status"] == "verworfen":
        return "verworfen"
    if r["id"] in busy:
        return "im Ticket"
    if r["marked"]:
        return "markiert"
    return "offen"


STATE_CHIP = {"markiert": "chip ok", "im Ticket": "chip busy", "offen": "chip", "verworfen": "chip"}


def is_passt(hint):
    """„S/E passt“ wird als grünes Merkmal gezeigt, nicht als Hinweistext."""
    return (hint or "").strip().lower().startswith("s/e passt")


def status_block(s, msg=""):
    """Meldung und Zählung der Vorschläge einer Serie."""
    sid = s["id"]
    rows = db_exec("SELECT id, status, marked FROM suggestion WHERE series_id = ?", (sid,), fetch=True)
    busy = busy_ids(ticket_links(sid))
    counts = {st: 0 for st in STATUSES}
    for r in rows:
        counts[sug_state(r, busy)] += 1
    out = []
    if msg:
        out.append(f"<div class='note flashmsg' role='status'>{esc(msg)}</div>")
    out.append(
        "<div class='row' aria-label='Stand der Vorschläge'>"
        f"<span class='chip'>offen {counts['offen']}</span>"
        f"<span class='chip ok'>markiert {counts['markiert']}</span>"
        f"<span class='chip busy'>im Ticket {counts['im Ticket']}</span>"
        f"<span class='chip'>verworfen {counts['verworfen']}</span>"
        "</div>"
    )
    return "".join(out)


def sug_main(r, links, busy):
    """Sichtbarer Kern eines Vorschlags: Titel (DE und EN), Sicherheit, Status, Tickets, Quelle."""
    st = sug_state(r, busy)
    title = esc(clean_title(r["title_de"])) or "(leer)"
    title_en = esc(clean_title(r["title_en"])) or "–"
    chips = [f"<span class='chip {LEVEL_CHIP.get(r['level'], '')}' title='{esc(LEVEL_TIP.get(r['level'], ''))}'>"
             f"Sicherheit {esc(r['level'])}</span>"]
    chips.append(f"<span class='{STATE_CHIP[st]}'>{esc(st)}</span>")
    if is_passt(r["hint"]):
        chips.append("<span class='chip ok'>✔ S/E passt</span>")
    for tid, lang, _ in links.get(r["id"], []):
        chips.append(f"<a class='chip busy' href='/ticket/{tid}'>{esc(ticket_name(tid))} · {lang.upper()}</a>")
    src = esc(r["source"]) + (f" · {esc(r['channel'])}" if r["channel"] else "")
    return (
        f"<div class='ep-title'>{title}</div>"
        f"<div class='ep-sub'>EN: {title_en}</div>"
        f"<div class='ep-chips'>{''.join(chips)}</div>"
        f"<div class='ep-sub'>{src}</div>"
    )


def sug_act(r, busy):
    """Aktionen eines Vorschlags: Haken für Ticket, Verwerfen oder Wieder öffnen."""
    sid = r["id"]
    if r["status"] == "verworfen":
        return (
            f"<form method='post' action='/vorschlag/{sid}' class='ajax'>"
            "<input type='hidden' name='aktion' value='reopen'>"
            "<button class='btn sm'>↺ Wieder öffnen</button></form>"
        )
    mark = (
        f"<form method='post' id='f{sid}' action='/vorschlag/{sid}' class='ajax'>"
        "<input type='hidden' name='aktion' value='toggle'>"
        f"<label class='check'><input type='checkbox'{' checked' if r['marked'] else ''} "
        "onchange='this.form.requestSubmit()'> Für Ticket</label>"
        "<noscript><button class='btn sm'>Markieren</button></noscript></form>"
    )
    if sid in busy:
        return mark
    discard = (
        f"<form method='post' action='/vorschlag/{sid}' class='ajax'>"
        "<input type='hidden' name='aktion' value='discard'>"
        "<button class='btn quiet sm'>✕ Verwerfen</button></form>"
    )
    return mark + discard


def sug_more(r):
    """Ausklappbare Details: Beschreibung, Hinweis, Quelle."""
    desc = (r["description"] or "").strip()
    txt = esc(desc) if desc else "<span class='mut'>Keine Beschreibung aus der Quelle.</span>"
    hint = f"<div class='hint'>{esc(r['hint'])}</div>" if r["hint"] and not is_passt(r["hint"]) else ""
    link = r["link"] if r["link"].startswith(("http://", "https://")) else ""
    src = f"<a href='{esc(link)}' target='_blank' rel='noopener'>Quelle öffnen ↗</a>" if link else ""
    return f"<p class='sugtext'>{txt}</p>{hint}<p class='small'>{src}</p>"


def var_wrap(inner, i, state):
    """Ein Vorschlag innerhalb einer Folgenkarte. Sichtbar ist immer nur der gewählte (Index 0 zu Beginn)."""
    hid = " hidden" if i else ""
    return f"<div class='var' data-var='{i}' data-state='{esc(state)}'{hid}>{inner}</div>"


def episode_card(e, sugs, links, busy, sid):
    """Eine Karte je TBA-Folge. Mehrere Vorschläge wechseln über ◀ ▶, sichtbar ist immer nur einer."""
    dcls, mark = DATE_STATE.get(e["state"], ("", ""))
    n = len(sugs)
    first = sug_state(sugs[0], busy) if sugs else ""
    code = f"S{e['season']:02d}E{e['episode']:02d}"
    nav = ""
    if n > 1:
        nav = (
            "<div class='ep-nav'>"
            "<button type='button' class='btn quiet var-nav' data-dir='prev' aria-label='Vorheriger Vorschlag'>◀</button>"
            f"<span class='pos'>1 / {n}</span>"
            "<button type='button' class='btn quiet var-nav' data-dir='next' aria-label='Nächster Vorschlag'>▶</button>"
            "</div>"
        )
    main_v = "".join(var_wrap(sug_main(r, links, busy), i, sug_state(r, busy)) for i, r in enumerate(sugs))
    if not main_v:
        main_v = (f"<div class='ep-title'>{esc(e['title']) or '(kein Titel)'}</div>"
                  "<div class='ep-sub'>Kein Vorschlag gefunden</div>")
    act_v = "".join(var_wrap(sug_act(r, busy), i, sug_state(r, busy)) for i, r in enumerate(sugs))
    more_v = "".join(var_wrap(sug_more(r), i, sug_state(r, busy)) for i, r in enumerate(sugs))
    datei = "<p class='small mut'>📁 Datei vorhanden</p>" if e["hasFile"] else ""
    more = ""
    if sugs or datei:
        more = f"<details class='more'><summary>Beschreibung und Details</summary>{more_v}{datei}</details>"
    return (
        f"<article id='e{e['season']}-{e['episode']}' class='ep' data-n='{n}' data-cur='0' "
        f"data-state='{esc(first)}'>"
        f"<div class='ep-head'><span class='code'>{code}</span>"
        f"<span class='date {dcls}'>{mark}{de_date(e['airDate'])}</span></div>"
        f"<div class='ep-main'>{main_v}</div>"
        f"<div class='ep-act'>{nav}{act_v}</div>"
        f"{more}</article>"
    )


def refresh_form(data):
    return (
        "<form method='post' action='/refresh' class='row'>"
        f"<span class='small mut'>Stand {data['fetched'].strftime('%d.%m.%Y %H:%M')}</span>"
        "<button class='btn sm'>Neu laden</button></form>"
    )


def tickets_html(rows):
    items = ""
    for t in rows:
        text, cls = status_chip(t)
        items += (
            f"<a class='item' href='/ticket/{t['id']}'>"
            f"<span><span class='t'>{esc(ticket_label(t))} · {esc(t['series_title'])}</span><br>"
            f"<span class='s'>Staffel {esc(t['season'])} · {LANG_NAME.get(t['lang'], esc(t['lang']))}</span></span>"
            f"<span class='end'><span class='{cls}'>{esc(text)}</span></span></a>"
        )
    return f"<div class='list'>{items}</div>"


def stat(value, label, ico, tone=""):
    return (f"<div class='stat'><span class='si {tone}'>{icon(ico)}</span>"
            f"<span><b>{value}</b><span>{label}</span></span></div>")


def ago(ts):
    """Kurzer deutscher Zeitstempel: heute mit Uhrzeit, sonst Datum."""
    d = datetime.fromtimestamp(ts)
    if d.date() == datetime.now().date():
        return "heute " + d.strftime("%H:%M")
    return d.strftime("%d.%m.%Y %H:%M")


def activity(data):
    """Letzte Aktivitäten: neue Vorschläge je Serie und zuletzt geänderte Tickets, nach Zeit sortiert."""
    names = {s["id"]: s["title"] for s in data["series"]}
    events = []
    for r in db_exec("SELECT series_id, COUNT(*) AS n, MAX(created) AS t FROM suggestion "
                     "GROUP BY series_id ORDER BY t DESC LIMIT 8", fetch=True):
        title = names.get(r["series_id"], f"Serie {r['series_id']}")
        events.append((r["t"], "s", f"<a href='/series/{r['series_id']}'>{esc(title)}</a>",
                       f"{r['n']} Vorschläge gespeichert"))
    for r in db_exec("SELECT * FROM ticket ORDER BY updated DESC LIMIT 8", fetch=True):
        text, _ = status_chip(r)
        events.append((r["updated"], "t", f"<a href='/ticket/{r['id']}'>{esc(ticket_label(r))}</a> · {esc(r['series_title'])}",
                       f"Ticket {LANG_NAME.get(r['lang'], esc(r['lang']))}, Staffel {esc(r['season'])}, Status {esc(text)}"))
    events.sort(key=lambda e: -e[0])
    if not events:
        return "<p class='mut small'>Noch keine Aktivität. Sie erscheint nach der ersten Suche.</p>"
    rows = "".join(
        f"<li><span class='dot {kind if kind == 's' else ''}' aria-hidden='true'></span>"
        f"<span><span class='t'>{who}</span><br><span class='small mut'>{what}</span></span>"
        f"<span class='when'>{ago(ts)}</span></li>"
        for ts, kind, who, what in events[:8]
    )
    return f"<ul class='feed'>{rows}</ul>"


def series_card(s):
    fut = sum(1 for e in s["tba"] if e["state"] == "angekündigt")
    year = f" ({esc(s['year'])})" if s["year"] else ""
    chip = f"<span class='chip warn'>{len(s['tba'])} TBA</span>" if s["tba"] else "<span class='chip'>keine TBA</span>"
    fut_txt = f"<span class='chip'>{fut} angekündigt</span>" if fut else ""
    return (
        f"<a class='scard' href='/series/{s['id']}' data-name='{esc(s['title'].lower())}'>{poster(s)}"
        f"<span><span class='t'>{esc(s['title'])}{year}</span><br>"
        f"<span class='s'>{esc(s['network']) or '–'} · {s['episodeCount']} Folgen</span></span>"
        f"<span class='ep-chips'>{chip}{fut_txt}</span></a>"
    )


def render_index(data):
    """Dashboard: Kacheln, Serien mit TBA, letzte Aktivitäten, offene Tickets, Nachtlauf."""
    series = data["series"]
    with_tba = [s for s in series if s["tba"]]
    tba_total = sum(len(s["tba"]) for s in series)
    tba_future = sum(1 for s in series for e in s["tba"] if e["state"] == "angekündigt")
    marked = db_exec("SELECT COUNT(*) AS n FROM suggestion WHERE marked = 1 AND status != 'verworfen'", fetch=True)[0]["n"]
    open_tickets = db_exec(
        "SELECT * FROM ticket WHERE status = 'erstellt' OR tvdb_no IS NOT NULL "
        "ORDER BY series_title, season, lang, id",
        fetch=True,
    )

    body = [
        "<div class='pagehead'><div><h1>Übersicht</h1>"
        "<p class='meta' style='margin:0'>Alle Serien werden berücksichtigt. Die App liest nur, in Sonarr wird nichts geändert.</p></div>"
        f"{refresh_form(data)}</div>",
        "<div class='stats'>"
        + stat(len(series), "Serien", "tv")
        + stat(len(with_tba), "mit TBA", "list", "w")
        + stat(tba_total, "TBA-Folgen", "clock", "w")
        + stat(len(open_tickets), "offene Tickets", "ticket", "g")
        + "</div>",
        f"<p class='small mut'>{tba_future} TBA-Folgen sind noch angekündigt · {marked} Vorschläge für Tickets markiert · "
        f"{esc(night_info())}</p>",
    ]
    if not series:
        body.append(
            f"<div class='err'>Keine Serien verfügbar (Sonarr kennt insgesamt {data['seriesTotal']} Serien).</div>"
        )
    cols = ["<div>"]
    cols.append("<div class='card'><h2>Serien mit TBA-Folgen</h2>")
    if with_tba:
        strip = "".join(
            f"<a href='/series/{s['id']}' title='{esc(s['title'])}'>{poster(s)}<span>{esc(s['title'])}"
            f"<br><span class='chip warn'>{len(s['tba'])} TBA</span></span></a>"
            for s in with_tba[:18]
        )
        more = f"<p class='small'><a href='/serien'>Alle {len(series)} Serien anzeigen →</a></p>"
        cols.append(f"<div class='strip'>{strip}</div>{more}")
    else:
        cols.append("<p class='mut'>Keine TBA-Folgen gefunden.</p>")
    cols.append("</div></div><div style='display:grid;gap:1rem'>")
    cols.append(f"<div class='card'><h2>Letzte Aktivitäten</h2>{activity(data)}</div>")
    cols.append("<div class='card'><h2>Offene Tickets</h2>")
    cols.append(tickets_html(open_tickets[:6]) if open_tickets else "<p class='mut small'>Keine offenen Tickets.</p>")
    if len(open_tickets) > 6:
        cols.append(f"<p class='small'><a href='/tickets'>Alle {len(open_tickets)} Tickets →</a></p>")
    cols.append("</div></div>")
    body.append(f"<div class='cols'>{''.join(cols)}</div>")
    return page("Übersicht", "".join(body), "uebersicht")


def render_serien(data, show_all):
    series = data["series"]
    rows = series if show_all else [s for s in series if s["tba"]]
    cur = {True: "href='/serien?alle=1' aria-current='true'", False: "href='/serien?alle=1' aria-current='false'"}[show_all]
    cur2 = "href='/serien' aria-current='false'" if show_all else "href='/serien' aria-current='true'"
    body = [
        "<div class='pagehead'><h1>Serien</h1>"
        f"<span class='small mut'>{len(rows)} von {len(series)} Serien</span></div>",
        f"<div class='filter'><a class='chip' {cur2}>Nur mit TBA</a><a class='chip' {cur}>Alle Serien</a></div>",
        "<div class='searchbox'><label class='sr' for='serienfilter'>Serie suchen</label>"
        "<input type='text' id='serienfilter' placeholder='Serie suchen …' autocomplete='off'></div>",
    ]
    if not series:
        body.append(f"<div class='err'>Keine Serien verfügbar (Sonarr kennt insgesamt {data['seriesTotal']} Serien).</div>")
    elif not rows:
        body.append("<p class='mut'>Keine TBA-Folgen gefunden.</p>")
    else:
        body.append(f"<div class='grid'>{''.join(series_card(s) for s in rows)}</div>")
        body.append("<p id='serienleer' class='mut' hidden>Keine Serie passt zur Eingabe.</p>")
    return page("Serien", "".join(body), "serien")


def render_tickets():
    rows = db_exec("SELECT * FROM ticket ORDER BY series_title, season, lang, id", fetch=True)
    body = [f"<div class='pagehead'><h1>Tickets</h1><span class='small mut'>{len(rows)} insgesamt</span></div>"]
    if rows:
        body.append(tickets_html(rows))
    else:
        body.append("<p class='mut'>Noch keine Tickets. Folgen markieren, dann in der Serie ein Ticket erstellen.</p>")
    return page("Tickets", "".join(body), "tickets")


def render_einstellungen():
    """Aktuelle Konfiguration, nur Anzeige. Schlüssel erscheinen nie im Klartext."""
    body = [
        "<div class='pagehead'><div><h1>Einstellungen</h1>"
        "<p class='meta' style='margin:0'>Aktuelle Konfiguration, nur Anzeige. Geändert wird sie über die Umgebungsvariablen "
        "in der Compose-Datei, danach ist ein Neustart nötig.</p></div></div>",
    ]
    for section, rows in config_overview():
        trs = ""
        for r in rows:
            chip = "chip ok" if r["ok"] and r["kind"] in ("secret", "bool") else "chip"
            val = f"<span class='{chip}'>{esc(r['value'])}</span>" if r["kind"] in ("secret", "bool") else \
                f"<span class='val'>{esc(r['value'])}</span>"
            trs += (f"<tr><td><code>{esc(r['name'])}</code></td><td>{val}</td>"
                    f"<td><span class='chip'>{esc(r['source'])}</span></td><td class='mut'>{esc(r['desc'])}</td></tr>")
        body.append(
            f"<div class='card' style='margin:.8rem 0'><h2>{esc(section)}</h2>"
            "<table class='cfg'><thead><tr><th>Name</th><th>Wert</th><th>Quelle</th><th>Beschreibung</th></tr></thead>"
            f"<tbody>{trs}</tbody></table></div>"
        )
    return page("Einstellungen", "".join(body), "einstellungen")


def render_diagnose(data, err, msg=""):
    def kv(*pairs):
        return "<dl class='kv'>" + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"

    if err:
        sonarr_box = f"<span class='chip warn'>Fehler</span><p class='small'>{esc(err)}</p>"
    else:
        sonarr_box = (f"<span class='chip ok'>verbunden</span>"
                      + kv(("Serien insgesamt", data["seriesTotal"]), ("Serien eingelesen", len(data["series"])),
                           ("Stand der Daten", data["fetched"].strftime("%d.%m.%Y %H:%M:%S"))))
    try:
        size = f"{os.path.getsize(DB_PATH) / 1024:.0f} KB"
        counts = {n: db_exec(f"SELECT COUNT(*) AS n FROM {n}", fetch=True)[0]["n"] for n in ("suggestion", "ticket", "cache")}
        db_box = ("<span class='chip ok'>bereit</span>"
                  + kv(("Datei", esc(DB_PATH)), ("Größe", size), ("Vorschläge", counts["suggestion"]),
                       ("Tickets", counts["ticket"]), ("Zwischenspeicher", counts["cache"])))
    except Exception as exc:  # noqa: BLE001 - Diagnose soll Fehler anzeigen
        db_box = f"<span class='chip warn'>Fehler</span><p class='small'>{esc(type(exc).__name__)}: {esc(exc)}</p>"
    last = cache_get_raw("nightly")
    night = ("aus" if not NIGHTLY_SEARCH else
             f"täglich {NIGHTLY_HOUR:02d}:00, letzter Lauf " + (ago(last["time"]) if last else "noch keiner"))
    box = lambda title, inner: f"<div class='card'><h2>{title}</h2>{inner}</div>"  # noqa: E731
    flag = lambda ok: "<span class='chip ok'>gesetzt</span>" if ok else "<span class='chip'>nicht gesetzt</span>"  # noqa: E731
    from config import DEEPL_API_KEY, TVDB_PASSWORD, TVDB_USER
    body = [
        "<div class='pagehead'><div><h1>Diagnose</h1>"
        "<p class='meta' style='margin:0'>Zustand der Anbindungen. Es werden keine Schlüssel angezeigt.</p></div>"
        "<form method='post' action='/diagnose' class='ladeform'><button class='btn pri'>Verbindung testen</button></form></div>",
    ]
    if msg:
        body.append(f"<div class='note' role='status'>{esc(msg)}</div>")
    body.append(
        "<div class='diag'>"
        + box("Sonarr", sonarr_box + kv(("Adresse", esc(SONARR_URL or "nicht gesetzt")), ("API-Key", flag(bool(SONARR_API_KEY)))))
        + box("Datenbank", db_box)
        + box("Dienste", kv(("TMDB-Key", flag(bool(TMDB_API_KEY))), ("DeepL-Key", flag(bool(DEEPL_API_KEY))),
                            ("TVDB-Zugang", flag(bool(TVDB_USER and TVDB_PASSWORD)))))
        + box("Nachtlauf und Poster", kv(("Nachtlauf", esc(night)),
                                         ("Poster", "an" if SHOW_POSTERS else "aus"),
                                         ("Poster im Speicher", len(POSTERS))))
        + box("System", kv(("Python", esc(platform.python_version())), ("Zeit", datetime.now().strftime("%d.%m.%Y %H:%M:%S"))))
        + "</div>"
    )
    return page("Diagnose", "".join(body), "diagnose")


def season_numbers(s):
    """Staffeln mit TBA-Folgen, aufsteigend."""
    return sorted({e["season"] for e in s["tba"]})


def season_bars(sid, seasons, current, n_tba, n_hoch, n_marked):
    """Oben: Staffelauswahl. Unten (klebend): Aktionen für die Staffel."""
    opts = "".join(
        f"<option value='{n}'{' selected' if n == current else ''}>Staffel {n}</option>" for n in seasons
    )
    top = (
        f"<form method='get' action='/series/{sid}/staffel' class='seasonbar'>"
        "<label class='sr' for='stsel'>Staffel wählen</label>"
        f"<select id='stsel' name='n' onchange='this.form.submit()'>{opts}</select>"
        "<noscript><button class='btn sm'>Anzeigen</button></noscript>"
        f"<span class='chip warn'>{n_tba} TBA-Folgen</span></form>"
    )
    search = (
        f"<form method='post' action='/series/{sid}/suche?staffel={current}' class='ladeform'>"
        "<button class='btn' title='Mediathek, dann TMDB, dann fernsehserien.de (nur auf Knopfdruck). "
        "Haken und Änderungen von Hand bleiben erhalten.'>🔍 Suchen</button></form>"
    )
    mark = (
        f"<form method='post' action='/series/{sid}/uebernehmen?staffel={current}' class='ajax'>"
        f"<button class='btn' title='Je Folge den ersten Vorschlag mit Sicherheit hoch markieren. "
        f"Es entsteht noch kein Ticket.'{' disabled' if not n_hoch else ''}>⚡ Hohe markieren ({n_hoch})</button></form>"
    )
    tickets = "".join(
        f"<form method='post' action='/series/{sid}/ticket?staffel={current}&amp;lang={lang}' class='ladeform'>"
        f"<button class='btn pri' title='Aus den markierten Folgen ein Ticket {label} erstellen.'"
        f"{' disabled' if not n_marked[lang] else ''}>🎫 {label} ({n_marked[lang]})</button></form>"
        for lang, label in (("de", "Deutsch"), ("en", "Englisch"))
    )
    bottom = f"<div class='actbar' role='group' aria-label='Aktionen für diese Staffel'>{search}{mark}{tickets}</div>"
    return top, bottom


def season_list(s, season, sugg, links, busy, sid):
    eps = [e for e in s["tba"] if e["season"] == season]
    if not eps:
        return "<p>Keine TBA-Folgen in dieser Staffel.</p>"
    cards = "".join(
        episode_card(e, sorted(sugg.get((e["season"], e["episode"]), []), key=lambda r: r["status"] == "verworfen"),
                     links, busy, sid)
        for e in eps
    )
    return f"<section class='eps' aria-label='Folgen'>{cards}</section>"


def render_series(data, s, msg="", season=None):
    """Serienseite (season None: erste Staffel mit TBA) oder Seite einer einzelnen Staffel."""
    back = "<a href='/serien'>← Serien</a>" if season is None else f"<a href='/series/{s['id']}'>← {esc(s['title'])}</a>"
    body = [f"<p class='crumb'>{back}</p>"]
    year = f" ({esc(s['year'])})" if s["year"] else ""
    meta = " · ".join(x for x in [esc(s["network"]), esc(s["status"])] if x)
    if season is not None:
        meta = f"Staffel {season}" + (f" · {meta}" if meta else "")
    body.append(f"<div class='shead'>{poster(s, 'lg')}<div><h1>{esc(s['title'])}{year}</h1><p class='meta'>{meta}</p></div></div>")

    links = []
    if s["tvdbId"]:
        links.append(f"<li><a href='https://thetvdb.com/?tab=series&amp;id={esc(s['tvdbId'])}' target='_blank' rel='noopener'>"
                     f"TVDB-Seite (ID {esc(s['tvdbId'])})</a></li>")
    if SONARR_PUBLIC_URL and s["slug"]:
        links.append(f"<li><a href='{esc(SONARR_PUBLIC_URL)}/series/{esc(s['slug'])}' target='_blank' rel='noopener'>"
                     "In Sonarr öffnen</a></li>")
    body.append(
        "<details class='info'><summary>Details, Links und Symbole</summary>"
        "<ul>"
        "<li><b>Haken</b>: für ein Ticket vormerken. Lässt sich jederzeit entfernen.</li>"
        "<li><b>Verwerfen</b>: Vorschlag ausblenden, lässt sich wieder öffnen.</li>"
        "<li><b>Suchen</b>: Mediathek, dann TMDB, dann fernsehserien.de. Nur auf Knopfdruck.</li>"
        "<li><b>✔ / ⏳</b>: Folge schon gesendet / noch angekündigt.</li>"
        "</ul>"
        f"<ul>{''.join(links)}</ul>"
        f"<p class='small mut'>{esc(night_info())}</p>{refresh_form(data)}</details>"
    )
    if not s["tba"]:
        body.append("<p>Keine TBA-Folgen. 🎉</p>")
        return page(s["title"], "".join(body))

    body.append(status_block(s, msg or flash_pop(s["id"])))
    sugg = suggestion_map(s["id"])
    links = ticket_links(s["id"])
    busy = busy_ids(links)
    seasons = season_numbers(s)
    current = season if season is not None else seasons[0]
    now = [r for (sn, _), rs in sugg.items() if sn == current for r in rs]
    n_hoch = 0
    busy_b = busy_both(links)
    for (sn, _), rs in sugg.items():
        if sn == current and not any(r["marked"] or r["id"] in busy_b for r in rs) \
                and any(r["status"] == "offen" and r["level"] == "hoch" for r in rs):
            n_hoch += 1
    n_marked = {lang: sum(1 for r in now if r["marked"] and r["id"] not in busy_ids(links, lang))
                for lang in ("de", "en")}
    n_tba = len([e for e in s["tba"] if e["season"] == current])
    top, bottom = season_bars(s["id"], seasons, current, n_tba, n_hoch, n_marked)
    body.append(top)
    body.append(season_list(s, current, sugg, links, busy, s["id"]))
    body.append(bottom)
    body.append(ticket_list(s["id"], current))
    return page(s["title"], "".join(body))


def eps_of(s, season):
    return [e for e in s["tba"] if e["season"] == season]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Metadatarr/0.1.0"

    def _send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, status, raw, ctype="application/octet-stream", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._send(status, body, "application/json; charset=utf-8", {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        })

    def _serve_frontend(self, request_path):
        if request_path == "/app":
            return self._redirect("/app/")
        static_root = (Path(__file__).resolve().parent / "static").resolve()
        relative = urllib.parse.unquote(request_path[len("/app/"):])
        try:
            target = (static_root / relative).resolve()
            target.relative_to(static_root)
        except (OSError, RuntimeError, ValueError):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if relative.startswith(("assets/", "brand/")):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            target = static_root / "index.html"
        if not target.is_file():
            return self._send(503, "frontend build not available", "text/plain; charset=utf-8")
        try:
            raw = target.read_bytes()
        except OSError:
            return self._send(500, "static file could not be read", "text/plain; charset=utf-8")
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        return self._send_bytes(200, raw, content_type,
                                {"X-Content-Type-Options": "nosniff"})

    def _send_image(self, raw, ctype, headers=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _safe_data(self, force=False):
        try:
            return get_data(force), None
        except urllib.error.HTTPError as exc:
            return None, f"Sonarr antwortet mit HTTP {exc.code} ({exc.reason}). API-Key prüfen."
        except Exception as exc:  # noqa: BLE001 - Fehler soll in der Oberfläche erscheinen
            return None, f"{type(exc).__name__}: {exc}"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        set_path(parsed.path)
        if dispatch_api(self, "GET", parsed.path):
            return
        if parsed.path == "/api/v1/health":
            return self._send_json(200, {
                "status": "ok",
                "service": "metadatarr",
                "apiVersion": "v1",
            })
        if parsed.path == "/health":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if parsed.path == "/app" or parsed.path.startswith("/app/"):
            return self._serve_frontend(parsed.path)
        if parsed.path == "/einstellungen":
            return self._send(200, render_einstellungen())
        if parsed.path == "/tickets":
            return self._send(200, render_tickets())
        if parsed.path == "/diagnose":
            if not SONARR_URL or not SONARR_API_KEY:
                return self._send(200, render_diagnose(None, "SONARR_URL und/oder SONARR_API_KEY sind nicht gesetzt."))
            data, err = self._safe_data()
            return self._send(200, render_diagnose(data, err, (urllib.parse.parse_qs(parsed.query).get("meldung") or [""])[0]))
        match = re.fullmatch(r"/poster/(\d+)", parsed.path)
        if match:
            if not SHOW_POSTERS:
                return self._send(404, "kein Poster", "text/plain; charset=utf-8", {"X-Poster-Reason": "disabled"})
            if not SONARR_URL or not SONARR_API_KEY:
                return self._send(503, "Sonarr ist nicht konfiguriert.", "text/plain; charset=utf-8",
                                 {"X-Poster-Reason": "not-configured"})
            data, data_error = self._safe_data()
            if data_error:
                status = 504 if "Zeitlimit" in data_error else 502
                reason = "timeout" if status == 504 else "upstream-error"
                match_status = re.search(r"HTTP (\d+)", data_error)
                if match_status and int(match_status.group(1)) in (401, 403):
                    reason = "auth"
                return self._send(status, "Posterquelle derzeit nicht erreichbar.",
                                  "text/plain; charset=utf-8", {"X-Poster-Reason": reason})
            try:
                img = fetch_poster(int(match.group(1)))
            except PosterFetchError as exc:
                return self._send(exc.status, str(exc), "text/plain; charset=utf-8",
                                  {"X-Poster-Reason": exc.reason})
            if img is None:
                return self._send(404, "kein Poster", "text/plain; charset=utf-8", {"X-Poster-Reason": "missing"})
            return self._send_image(*img, headers={"X-Poster-Reason": "ok"})
        if not SONARR_URL or not SONARR_API_KEY:
            return self._send(500, render_error("SONARR_URL und/oder SONARR_API_KEY sind nicht gesetzt."))
        if parsed.path == "/":
            data, err = self._safe_data()
            if err:
                return self._send(502, render_error(err))
            if urllib.parse.parse_qs(parsed.query).get("alle") == ["1"]:
                return self._redirect("/serien?alle=1")
            return self._send(200, render_index(data))
        if parsed.path == "/serien":
            data, err = self._safe_data()
            if err:
                return self._send(502, render_error(err))
            show_all = urllib.parse.parse_qs(parsed.query).get("alle") == ["1"]
            return self._send(200, render_serien(data, show_all))
        match = re.fullmatch(r"/series/(\d+)/staffel/(\d+)/bericht\.csv", parsed.path)
        if match:
            return self._report(int(match.group(1)), int(match.group(2)))
        match = re.fullmatch(r"/ticket/(\d+)", parsed.path)
        if match:
            return self._ticket_page(int(match.group(1)), urllib.parse.parse_qs(parsed.query))
        match = re.fullmatch(r"/series/(\d+)/staffel(?:/(\d+))?", parsed.path)
        if match:
            sid = int(match.group(1))
            query = urllib.parse.parse_qs(parsed.query)
            if match.group(2) is None:
                n = (query.get("n") or [""])[0]
                return self._redirect(f"/series/{sid}/staffel/{int(n)}" if n.isdigit() else f"/series/{sid}")
            data, err = self._safe_data()
            if err:
                return self._send(502, render_error(err))
            series = next((x for x in data["series"] if x["id"] == sid), None)
            if series is None:
                return self._send(404, page("Nicht gefunden", "<h1>Serie nicht gefunden</h1><p><a href='/'>← Übersicht</a></p>"))
            return self._send(200, render_series(data, series, "", int(match.group(2))))
        match = re.fullmatch(r"/series/(\d+)", parsed.path)
        if match:
            data, err = self._safe_data()
            if err:
                return self._send(502, render_error(err))
            sid = int(match.group(1))
            series = next((s for s in data["series"] if s["id"] == sid), None)
            if series is None:
                return self._send(404, page("Nicht gefunden", "<h1>Serie nicht gefunden</h1><p><a href='/'>← Übersicht</a></p>"))
            return self._send(200, render_series(data, series))
        return self._send(404, page("Nicht gefunden", "<h1>Seite nicht gefunden</h1><p><a href='/'>← Übersicht</a></p>"))

    def _report(self, sid, season):
        data, err = self._safe_data()
        if err:
            return self._send(502, render_error(err))
        s = next((x for x in data["series"] if x["id"] == sid), None)
        if s is None:
            return self._send(404, page("Nicht gefunden", "<h1>Serie nicht gefunden</h1>"))
        # BOM vorn, damit Excel die Umlaute richtig liest
        return self._send(200, "\ufeff" + report_csv(s, season, suggestion_map(sid)), "text/csv; charset=utf-8",
                          {"Content-Disposition": f"attachment; filename=\"tba-s{season:02d}-vorschlaege.csv\""})

    def _ticket_page(self, tid, query):
        rows = db_exec("SELECT * FROM ticket WHERE id = ?", (tid,), fetch=True)
        if not rows:
            return self._send(404, page("Nicht gefunden", "<h1>Ticket nicht gefunden</h1><p><a href='/'>← Übersicht</a></p>"))
        t = rows[0]
        if (query.get("format") or [""])[0] == "txt":
            return self._send(200, ticket_text(t), "text/plain; charset=utf-8",
                              {"Content-Disposition": f"attachment; filename=\"ticket-{t['lang']}-s{t['season']:02d}-{tid}.txt\""})
        items = db_exec("SELECT * FROM ticket_item WHERE ticket_id = ? ORDER BY episode", (tid,), fetch=True)
        return self._send(200, render_ticket_page(t, items, (query.get("meldung") or [""])[0]))

    def _create_ticket(self, sid, query, form):
        data, err = self._safe_data()
        if err:
            return self._send(502, render_error(err))
        s = next((x for x in data["series"] if x["id"] == sid), None)
        if s is None:
            return self._send(404, page("Nicht gefunden", "<h1>Serie nicht gefunden</h1>"))
        staffel = (query.get("staffel") or [""])[0]
        lang = (query.get("lang") or [""])[0]
        if not staffel.isdigit() or lang not in LANG_NAME:
            return self._redirect(f"/series/{sid}")
        season = int(staffel)
        tid, msg = create_ticket(s, season, lang)
        if tid is None:
            flash_set(sid, msg)
            return self._redirect(f"/series/{sid}/staffel/{season}")
        return self._redirect(f"/ticket/{tid}")

    def _ticket_status(self, tid, form):
        rows = db_exec("SELECT series_id, season FROM ticket WHERE id = ?", (tid,), fetch=True)
        if not rows:
            return self._send(404, page("Nicht gefunden", "<h1>Ticket nicht gefunden</h1>"))
        sid, season = rows[0]["series_id"], rows[0]["season"]
        if ticket_status(tid, form_value(form, "aktion")):
            flash_set(sid, f"Ticket {tid} aufgelöst. Die Folgen sind wieder offen.")
            return self._redirect(f"/series/{sid}/staffel/{season}")
        return self._redirect(f"/ticket/{tid}")

    def _ticket_tvdb(self, tid, action, form):
        """TVDB-Aktionen eines Tickets: einreichen, Status abfragen, Ticketnummer eintragen."""
        if not db_exec("SELECT 1 FROM ticket WHERE id = ?", (tid,), fetch=True):
            return self._send(404, page("Nicht gefunden", "<h1>Ticket nicht gefunden</h1><p><a href='/'>← Übersicht</a></p>"))
        if action == "einreichen":
            _, msg = ticket_submit(tid)
        else:
            msg = ticket_refresh_one(tid)
        return self._redirect(f"/ticket/{tid}?" + urllib.parse.urlencode({"meldung": msg[:300]}))

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _search(self, sid, source, season=None):
        data, err = self._safe_data()
        if err:
            return self._send(502, render_error(err))
        series = next((x for x in data["series"] if x["id"] == sid), None)
        if series is None:
            return self._send(404, page("Nicht gefunden", "<h1>Serie nicht gefunden</h1>"))
        label = "Suche" if source is None else ("Mediathek" if source == "mediathek" else "TMDB")
        if season is not None:
            label += f" Staffel {season}"
        try:
            if source is None:
                counts, note = search_chain(series, season)
                msg = f"{label}: {counts or 'keine Folgen'}. {note}".strip()
            else:
                count, note = search_series(series, source, season)
                msg = f"{label}: {count} Treffer geprüft. {note}".strip()
        except Exception as exc:  # noqa: BLE001 - Meldung erscheint in der Oberfläche
            text = str(exc).replace(TMDB_API_KEY, "***") if TMDB_API_KEY else str(exc)
            msg = f"{label}-Suche fehlgeschlagen: {type(exc).__name__}: {text}"
        base = f"/series/{sid}" if season is None else f"/series/{sid}/staffel/{season}"
        flash_set(sid, msg)
        return self._redirect(base)

    def _suggestion(self, sug_id, form):
        sid = suggestion_action(sug_id, form)
        if sid is None:
            return self._send(404, page("Nicht gefunden", "<h1>Vorschlag nicht gefunden</h1>"))
        row = db_exec("SELECT season, episode FROM suggestion WHERE id = ?", (sug_id,), fetch=True)
        if not row:
            return self._redirect(f"/series/{sid}")
        season, episode = row[0]["season"], row[0]["episode"]
        # Anker auf die Folge: die Seite springt nach dem Speichern nicht nach oben
        return self._redirect(f"/series/{sid}/staffel/{season}#e{season}-{episode}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        set_path(parsed.path)
        if dispatch_api(self, "POST", parsed.path):
            return
        if self.headers.get("Transfer-Encoding"):
            return self._send(400, render_error("Chunked Request-Bodies werden nicht unterstützt."))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, render_error("Content-Length ist ungültig."))
        if length < 0:
            return self._send(400, render_error("Content-Length ist ungültig."))
        if length > BODY_LIMIT:
            return self._send(413, render_error("Der Request-Body überschreitet das erlaubte Limit."))
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            return self._send(400, render_error("Request-Body ist unvollständig."))
        form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True) if length else {}
        token = (form.get("csrf") or [""])[0]
        if not hmac.compare_digest(token.encode("utf-8"), CSRF_TOKEN.encode("utf-8")):
            # Anfrage ohne gültiges Token: nichts ändern, Hinweis zeigen
            return self._send(403, render_error("Die Anfrage konnte nicht geprüft werden. Bitte die Seite neu laden."))
        match = re.fullmatch(r"/series/(\d+)/suche(?:/(mediathek|tmdb))?", parsed.path)
        if match:
            staffel = urllib.parse.parse_qs(parsed.query).get("staffel", [""])[0]
            season = int(staffel) if staffel.isdigit() else None
            return self._search(int(match.group(1)), match.group(2), season)
        match = re.fullmatch(r"/series/(\d+)/uebernehmen", parsed.path)
        if match:
            sid = int(match.group(1))
            staffel = urllib.parse.parse_qs(parsed.query).get("staffel", [""])[0]
            if not staffel.isdigit():
                return self._redirect(f"/series/{sid}")
            season = int(staffel)
            count, skip_mark, skip_busy = accept_high(sid, season)
            hint = f" Übersprungen: {skip_mark} mit anderem Haken, {skip_busy} in einem Ticket." if (skip_mark or skip_busy) else ""
            flash_set(sid, f"Staffel {season}: {count} Folgen markiert (Sicherheit hoch).{hint} Noch kein Ticket, nichts wurde bei TVDB eingereicht.")
            return self._redirect(f"/series/{sid}/staffel/{season}")
        match = re.fullmatch(r"/series/(\d+)/ticket", parsed.path)
        if match:
            return self._create_ticket(int(match.group(1)), urllib.parse.parse_qs(parsed.query), form)
        match = re.fullmatch(r"/ticket/(\d+)/(einreichen|tvdb-status)", parsed.path)
        if match:
            return self._ticket_tvdb(int(match.group(1)), match.group(2), form)
        match = re.fullmatch(r"/ticket/(\d+)/status", parsed.path)
        if match:
            return self._ticket_status(int(match.group(1)), form)
        match = re.fullmatch(r"/vorschlag/(\d+)", parsed.path)
        if match:
            return self._suggestion(int(match.group(1)), form)
        if parsed.path == "/diagnose":
            _, err = self._safe_data(force=True) if (SONARR_URL and SONARR_API_KEY) else (None, "Sonarr ist nicht konfiguriert.")
            msg = "Verbindung zu Sonarr fehlgeschlagen." if err else "Verbindung zu Sonarr erfolgreich getestet."
            return self._redirect("/diagnose?" + urllib.parse.urlencode({"meldung": msg}))
        if parsed.path != "/refresh":
            return self._send(404, page("Nicht gefunden", "<h1>Seite nicht gefunden</h1>"))
        _, err = self._safe_data(force=True)
        if err:
            return self._send(502, render_error(err))
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _unsupported_method(self, method):
        parsed = urllib.parse.urlparse(self.path)
        set_path(parsed.path)
        if dispatch_api(self, method, parsed.path):
            return
        return self._send(405, "Methode nicht erlaubt", "text/plain; charset=utf-8", {"Allow": "GET, POST"})

    def do_PUT(self):
        return self._unsupported_method("PUT")

    def do_PATCH(self):
        return self._unsupported_method("PATCH")

    def do_DELETE(self):
        return self._unsupported_method("DELETE")

    def do_OPTIONS(self):
        return self._unsupported_method("OPTIONS")
