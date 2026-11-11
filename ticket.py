"""tba-tickets – TVDB-Tickets: Text, Übersetzung (DeepL), Einreichen bei TVDB, Status, Bericht."""
import csv
import html as htmllib
import io
import json
import threading
import time
from config import TVDB_BASE_URL, TVDB_PASSWORD, TVDB_USER
from quellen import PLACEHOLDER_EN, tmdb_get
from speicher import cache_get_raw, cache_put, db_exec, db_insert
from tvdb import TvdbError, statuses, submit_ticket
from ui import esc, page
from vorschlaege import busy_ids, clean_title, suggestion_map, ticket_links

SUMMARY_MAX = 75  # Feldlimit „Issue Summary“ im TVDB-Formular
SUBMIT_LOCK = threading.Lock()  # verhindert doppeltes Einreichen durch Doppelklick


def deepl_translate(text, source="DE", target="EN-US"):
    """Use DeepL through its protected source-plugin transport."""
    from plugins.sources import translate_deepl
    return translate_deepl(text, source=source, target=target)


LANG_NAME = {"de": "Deutsch", "en": "Englisch"}
TICKET_ACTIONS = {
    "erstellt": (("aufloesen", "Entwurf auflösen"),),
}


def ticket_label(t):
    """Anzeige-Nummer: echte Tickets zeigen die TVDB-Nummer (#42840), Entwürfe eine lokale Nummer (#L12)."""
    return f"#{t['tvdb_no']}" if t["tvdb_no"] else f"#L{t['id']}"


def ticket_name(tid):
    """Anzeige-Nummer zu einer internen Ticket-ID (für Verweise an Vorschlägen)."""
    rows = db_exec("SELECT * FROM ticket WHERE id = ?", (tid,), fetch=True)
    return ticket_label(rows[0]) if rows else f"#L{tid}"


def status_chip(t):
    """Statustext und Farbe. Entwurf grau, Open gelb, anderer TVDB-Status neutral."""
    if not t["tvdb_no"]:
        return "Entwurf", "chip"
    text = t["tvdb_status"] or "unbekannt"
    if text.lower() == "open":
        return text, "chip open"
    return text, "chip"


def ticket_summary(series_title, season, lang):
    """Betreff für das Feld „Issue Summary“, z. B. „[GERMAN] Notruf Hafenkante - Season 21“.

    Passt der Betreff nicht in das Feld (75 Zeichen), wird der Serientitel gekürzt. Gibt (Text, gekürzt) zurück.
    """
    tag = "ENGLISH" if lang == "en" else "GERMAN"
    head = f"[{tag}] "
    tail = f" - Season {season}"
    room = SUMMARY_MAX - len(head) - len(tail)
    title = series_title or ""
    cut = len(title) > room
    if cut:
        title = title[:max(room - 1, 0)].rstrip() + "…"
    return f"{head}{title}{tail}", cut


def create_ticket(s, season, lang):
    """Erstellt aus den markierten Folgen der Staffel ein Ticket für eine Sprache. Folgen, die schon in einem
    aktiven Ticket dieser Sprache stehen, werden übersprungen. Die übernommenen Folgen verlieren ihren Haken.
    Gibt (Ticket-ID oder None, Meldung) zurück."""
    sid = s["id"]
    busy = busy_ids(ticket_links(sid), lang)
    picked = []
    for (sn, ep), rs in sorted(suggestion_map(sid).items()):
        if sn != season:
            continue
        for r in rs:
            if r["marked"] and r["id"] not in busy:
                picked.append((ep, r))
                break
    if not picked:
        return None, f"Keine markierten Folgen für ein Ticket {LANG_NAME[lang]} in Staffel {season}."
    text, html, hints = ticket_build(s, season, picked, lang)
    now = time.time()
    tid = db_insert(
        "INSERT INTO ticket (series_id, series_title, tvdb_id, season, lang, status, body, body_html, hints, "
        "created, updated) VALUES (?, ?, ?, ?, ?, 'erstellt', ?, ?, ?, ?, ?)",
        (sid, s["title"], s.get("tvdbId") or None, season, lang, text, html,
         json.dumps([list(h) for h in hints], ensure_ascii=False), now, now),
    )
    for ep, r in picked:
        title = clean_title(r["title_de"] if lang == "de" else r["title_en"]) or clean_title(r["title_de"])
        if lang == "en" and title == clean_title(r["title_de"]):
            title = ""  # Anzeige in der Folgenliste: deutscher Titel ist kein englischer Titel
        db_exec(
            "INSERT INTO ticket_item (ticket_id, suggestion_id, season, episode, title) VALUES (?, ?, ?, ?, ?)",
            (tid, r["id"], season, ep, title),
        )
        db_exec("UPDATE suggestion SET marked = 0 WHERE id = ?", (r["id"],))
    from core.candidates import sync_candidate_states
    sync_candidate_states(series_id=sid, season=season)
    return tid, f"Entwurf {ticket_label({'id': tid, 'tvdb_no': None})} ({LANG_NAME[lang]}) mit {len(picked)} Folge(n) erstellt."


def ticket_status(tid, aktion):
    """Nur noch „aufloesen“: verwirft einen Entwurf (noch nicht bei TVDB). Der Status echter Tickets kommt
    ausschließlich von TVDB. Gibt True zurück, wenn der Entwurf gelöscht wurde."""
    if aktion != "aufloesen":
        return False
    rows = db_exec("SELECT status, tvdb_no FROM ticket WHERE id = ?", (tid,), fetch=True)
    if not rows or rows[0]["status"] != "erstellt" or rows[0]["tvdb_no"]:
        return False
    db_exec("DELETE FROM ticket_item WHERE ticket_id = ?", (tid,))
    db_exec("DELETE FROM ticket WHERE id = ?", (tid,))
    return True


def ticket_submit(tid):
    """Legacy HTML route is non-writing; external submission uses the guarded JSON action API."""
    if not db_exec("SELECT id FROM ticket WHERE id=?", (tid,), fetch=True):
        return False, "Ticket nicht gefunden."
    return False, ("Externe Einreichung ist gesperrt. Verwenden Sie die serverseitige Vorschau, "
                   "ausdrueckliche Bestaetigung und Idempotenz-API.")


def ticket_refresh_one(tid):
    """Liest den TVDB-Status eines Tickets und speichert ihn. Gibt eine Meldung zurück."""
    rows = db_exec("SELECT tvdb_no FROM ticket WHERE id = ?", (tid,), fetch=True)
    if not rows or not rows[0]["tvdb_no"]:
        return "Keine TVDB-Ticketnummer hinterlegt."
    no = rows[0]["tvdb_no"]
    try:
        result = statuses([no])
    except TvdbError as exc:
        return str(exc)
    st = result.get(no)
    if not st:
        return f"Status von Ticket {no} konnte nicht gelesen werden."
    db_exec("UPDATE ticket SET tvdb_status = ?, tvdb_checked = ? WHERE id = ?", (st, time.time(), tid))
    return f"TVDB-Status von Ticket {no}: {st}."


def refresh_tvdb_statuses():
    """Liest den Status aller Tickets mit TVDB-Nummer in einer Sitzung (für den Nachtlauf). Gibt eine Meldung zurück."""
    rows = db_exec("SELECT id, tvdb_no FROM ticket WHERE tvdb_no IS NOT NULL ORDER BY id", fetch=True)
    if not rows:
        return "TVDB-Status: keine Tickets mit Nummer."
    try:
        result = statuses([r["tvdb_no"] for r in rows])
    except TvdbError as exc:
        return f"TVDB-Status nicht abgefragt: {exc}"
    now, ok = time.time(), 0
    for r in rows:
        st = result.get(r["tvdb_no"])
        if st:
            db_exec("UPDATE ticket SET tvdb_status = ?, tvdb_checked = ? WHERE id = ?", (st, now, r["id"]))
            ok += 1
    return f"TVDB-Status aktualisiert: {ok} von {len(rows)} Tickets."


def ticket_list(sid, season):
    """Tickets dieser Staffel: Nummer (Entwurf oder TVDB), Sprache, Folgenzahl und Status."""
    head = "<h2>Tickets dieser Staffel</h2>"
    rows = db_exec(
        "SELECT t.*, (SELECT COUNT(*) FROM ticket_item ti WHERE ti.ticket_id = t.id) AS n "
        "FROM ticket t WHERE t.series_id = ? AND t.season = ? ORDER BY t.id",
        (sid, season), fetch=True,
    )
    if not rows:
        return (f"<section>{head}<p class='small mut'>Noch keine Tickets. "
                "Folgen markieren, dann Ticket erstellen.</p></section>")
    items = ""
    for r in rows:
        text, cls = status_chip(r)
        items += (
            f"<a class='item' href='/ticket/{r['id']}'>"
            f"<span><span class='t'>{esc(ticket_label(r))} · {LANG_NAME.get(r['lang'], esc(r['lang']))}</span><br>"
            f"<span class='s'>{r['n']} Folge(n)</span></span>"
            f"<span class='end'><span class='{cls}'>{esc(text)}</span></span></a>"
        )
    return f"<section>{head}<div class='list'>{items}</div></section>"


def tmdb_overview_en(r):
    """Englische TMDB-Overview der Folge (en-US). Nur für TMDB-Vorschläge, sonst leer. Nutzt den Zwischenspeicher."""
    if r["source"] != "tmdb":
        return ""
    try:
        tmdb_id, season_no, episode_no = (int(x) for x in r["source_key"].split(":"))
        data = tmdb_get(f"/tv/{tmdb_id}/season/{season_no}", {}, "en-US")
    except (ValueError, RuntimeError):
        return ""
    for x in data.get("episodes") or []:
        if x.get("episode_number") == episode_no:
            return (x.get("overview") or "").strip()
    return ""


def html_text(text):
    """Klartext als HTML-Absatz-Inhalt: Zeichen escapen, Zeilenumbrüche als <br>."""
    return htmllib.escape(text or "").replace("\n", "<br>")


def _record_translation(r, source_field, source_language, target_language, value):
    if not value:
        return
    try:
        suggestion_id = r["id"]
    except (KeyError, IndexError, TypeError):
        return
    from core.candidates import record_translation_for_suggestion
    record_translation_for_suggestion(suggestion_id, source_field, source_language, target_language, value)


def _record_external_translation_origin(series_id, season, episode, row, value):
    from core.candidates import record_source_value
    return record_source_value(series_id=series_id, season=season, episode=episode,
                               field_key="episode.overview", language="en", value=value,
                               source_id="tmdb", source_url=row["link"] or "",
                               match_hint="TMDB en-US Originalwert fuer DeepL-Uebersetzung")


def ticket_build(s, season, rows, lang):
    """Ticket für TheTVDB nach der Vorlage „Translations“: Betreff, Body mit „Many thanks in advance“, dem
    Serienlink und je Folge „#Nummer“, Titel und Overview, in Sendereihenfolge. Ohne Sendedatum.

    Gibt (Text, HTML, Hinweise) zurück. Der Text ist die Klartextfassung (TXT-Download), das HTML der Inhalt
    für den TVDB-Editor (Fett, Absätze). Hinweise sind Paare (Stufe, Meldung) für die Anzeige und stehen nie im
    Ticketinhalt. Fehlt eine Overview, bleibt sie einfach weg (kein Hinweis).
    Englische Titel und Overviews übersetzt DeepL. Fehlt die englische Overview, nimmt sie die TMDB-Folge.
    """
    summary, cut = ticket_summary(s["title"], season, lang)
    hints = []
    if cut:
        hints.append(("info", f"Betreff auf {SUMMARY_MAX} Zeichen gekürzt (Feldlimit im TVDB-Formular). Bitte prüfen."))
    text = ["Many thanks in advance", ""]
    html = ["<p>Many thanks in advance</p>", "<p>&nbsp;</p>"]
    if s.get("tvdbId"):
        link = f"https://thetvdb.com/?tab=series&id={s['tvdbId']}"
        text += [f"Series: {link}", ""]
        html += [f"<p>Series: <a href='{htmllib.escape(link)}'>{htmllib.escape(link)}</a></p>", "<p>&nbsp;</p>"]
    for ep, r in rows:
        if lang == "en":
            de_title = clean_title(r["title_de"])
            raw = "" if PLACEHOLDER_EN.match(r["title_en"] or "") else (r["title_en"] or "")
            en_clean = clean_title(raw)
            if en_clean and en_clean == de_title:
                en_clean = ""  # Deutscher Titel als EN-Platzhalter (alte Suchregel): nicht übernehmen
            if en_clean:
                title = en_clean
            else:
                translated_title = deepl_translate(de_title)
                title = clean_title(translated_title or "")
                if translated_title:
                    _record_translation(r, "title_de", "de", "en", translated_title)
            source_overview = (r["description"] or "").strip()
            over = deepl_translate(source_overview) if source_overview else ""
            if over and source_overview:
                _record_translation(r, "description", "de", "en", over)
            over = over or tmdb_overview_en(r)
            if not over and (r["description"] or "").strip():
                hints.append(("warn", f"#{ep}: Englische Overview nicht möglich (DeepL nicht verfügbar)."))
        else:
            title = clean_title(r["title_de"])
            over = (r["description"] or "").strip()
            if not over:
                en_text = tmdb_overview_en(r)
                if en_text:
                    over = deepl_translate(en_text, source="EN", target="DE") or ""
                    if over:
                        origin_id = _record_external_translation_origin(s.get("id", r["series_id"]), season, ep, r, en_text)
                        if origin_id:
                            from core.candidates import record_translation
                            record_translation(origin_id, over, "de")
                        hints.append(("info", f"#{ep}: Overview aus der englischen TMDB-Fassung maschinell übersetzt. Bitte prüfen."))
                    else:
                        hints.append(("warn", f"#{ep}: Nur englische Overview vorhanden, DeepL-Übersetzung nicht möglich."))
        if not title:
            hints.append(("warn", f"#{ep}: Kein Titel."))
        title = title or "(no title)"
        text += [f"#{ep}", "", title] + (["", over] if over else []) + [""]
        html += [f"<p>#{ep}</p>", f"<p><strong>{htmllib.escape(title)}</strong></p>"]
        if over:
            html += [f"<pre>{htmllib.escape(over)}</pre>"]
        html += ["<p>&nbsp;</p>"]
    return "\n".join(text).rstrip() + "\n", "\n".join(html) + "\n", hints


def ticket_text(t):
    """Nur der gespeicherte Ticket-Text, für den TXT-Download."""
    return t["body"]


def csv_safe(value):
    """Verhindert, dass Tabellenprogramme Zellen mit Formeln auswerten."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def report_csv(s, season, sugg):
    """CSV-Bericht zum Nachprüfen: alle Vorschläge der Staffel mit Quelle, Sicherheit und Status. Trennzeichen ;"""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Serie", "Staffel", "Folge", "Quelle", "Sicherheit", "Status",
                "Titel (de)", "Titel (en)", "Datum Quelle", "Link"])
    for (sn, ep), rs in sorted(sugg.items()):
        if sn != season:
            continue
        for r in rs:
            w.writerow([csv_safe(v) for v in (
                s["title"], season, ep, r["source"], r["level"], r["status"],
                clean_title(r["title_de"]), clean_title(r["title_en"]), r["air_date"], r["link"],
            )])
    return buf.getvalue()


def hint_html(hints):
    """Hinweise zur Ansicht, nie im kopierbaren Text. Warnungen als roter Kasten, Hinweise als Notiz."""
    return "".join(
        f"<div class='{'err' if level == 'warn' else 'note'}'>{esc(msg)}</div>" for level, msg in hints
    )


def ticket_block(lang, label, text, html_body, download, hints=(), offen=True):
    """Ein Ticket: Body als formatierte Vorschau mit Kopieren-Knopf. Der Betreff steht im Kopf der Seite.
    Entwürfe zeigen den Inhalt offen, echte Tickets klappen ihn ein."""
    bid = f"b-{lang}"
    inhalt = (
        "<div class='box'>"
        "<div class='body-wrap'>"
        f"<div id='{bid}' class='body-preview'>{html_body}</div>"
        f"<button type='button' class='inbtn' aria-label='Body kopieren' title='Body kopieren' onclick=\"copyRich('{bid}',this)\">⧉</button>"
        "</div>"
        f"<p class='small'><a href='{esc(download)}' download>Als TXT herunterladen</a></p>"
        "</div>"
    )
    if offen:
        abschnitt = inhalt
    else:
        abschnitt = f"<details><summary class='btn block'>Ticketinhalt anzeigen</summary>{inhalt}</details>"
    return f"<section><h2>{esc(label)}</h2>{hint_html(hints)}{abschnitt}</section>"


def tvdb_block(t):
    """Abschnitt nur für Entwürfe: bei TVDB einreichen, wenn der Zugang eingerichtet ist.
    Echte Tickets haben keinen Abschnitt, ihr Status und Stand stehen im Kopf der Seite."""
    if t["tvdb_no"]:
        return ""
    if TVDB_USER and TVDB_PASSWORD:
        inhalt = ("<p class='small mut'>Externe Einreichung nur ueber die JSON-API mit serverseitiger Vorschau, "
                  "ausdruecklicher Bestaetigung und Idempotenzschutz. Die manuelle Ticketvorlage bleibt nutzbar.</p>")
    else:
        inhalt = ("<p class='small mut'>Automatisches Einreichen ist nicht eingerichtet: "
                  "TVDB_USER und TVDB_PASSWORD fehlen in der .env.</p>")
    return f"<section><div class='box'>{inhalt}</div></section>"


def render_ticket_page(t, items, meldung=""):
    """Ticket-Seite: Status mit Aktionen, TVDB-Abschnitt, Folgenliste, Betreff und Body zum Kopieren."""
    sid, season, lang = t["series_id"], t["season"], t["lang"]
    crumb = (f"<p class='crumb'><a href='/series/{sid}/staffel/{season}'>← Staffel {season}</a> · "
             f"<a href='/series/{sid}'>Serie</a></p>")
    forms = ""
    for i, (value, label) in enumerate(TICKET_ACTIONS.get(t["status"], ())):
        confirm = (" onsubmit=\"return confirm('Ticket auflösen? Die Folgen werden wieder offen.')\""
                   if value == "aufloesen" else "")
        cls = "btn pri" if i == 0 else "btn"
        forms += (f"<form method='post' action='/ticket/{t['id']}/status' class='row'{confirm}>"
                  f"<input type='hidden' name='aktion' value='{value}'><button class='{cls}'>{label}</button></form>")
    entwurf = not t["tvdb_no"]
    chip_text, chip_cls = status_chip(t)
    summary, _ = ticket_summary(t["series_title"], season, lang)
    copy_btn = (f"<button type='button' class='ico' aria-label='Betreff kopieren' title='Betreff kopieren' "
                f"data-copy='{esc(summary)}' onclick='copyVal(this)'>⧉</button>")
    titel = f"<h1>{esc(ticket_label(t))} - {esc(summary)} {copy_btn}</h1>"
    if entwurf:
        kopf = f"{titel}<div class='row'><span class='{chip_cls}'>{esc(chip_text)}</span></div>"
        hinweis = "<p class='small mut'>Noch nicht bei TVDB. Einreichen unten oder Entwurf auflösen.</p>"
    else:
        link = (f"<a class='chip' href='{esc(TVDB_BASE_URL)}/tickets.php?id={t['tvdb_no']}' "
                "target='_blank' rel='noopener'>TVDB ↗</a>")
        kopf = f"{titel}<div class='row'><span class='{chip_cls}'>{esc(chip_text)}</span>{link}</div>"
        checked = (time.strftime("%d.%m.%Y %H:%M", time.localtime(t["tvdb_checked"]))
                   if t["tvdb_checked"] else "noch nicht abgefragt")
        hinweis = (f"<div class='row small mut'><span>Stand: {esc(checked)}</span>"
                   f"<form method='post' action='/ticket/{t['id']}/tvdb-status' class='ico-form'>"
                   "<button class='ico' aria-label='Status jetzt abfragen' title='Status jetzt abfragen'>↻</button>"
                   "</form></div>")
    summary, _ = ticket_summary(t["series_title"], season, lang)
    html_body = t["body_html"] or html_text(t["body"])
    body = [
        crumb,
        kopf,
        f"<p class='meta'>{esc(t['series_title'])} · Staffel {season} · {LANG_NAME.get(lang, esc(lang))} · {len(items)} Folge(n)</p>",
        hinweis,
        f"<div class='note' role='status'>{esc(meldung)}</div>" if meldung else "",
        f"<div class='row'>{forms}</div>" if forms else "",
        tvdb_block(t),
        ticket_block(lang, f"Ticket {LANG_NAME.get(lang, lang)} [{'GERMAN' if lang == 'de' else 'ENGLISH'}]",
                     t["body"], html_body, f"/ticket/{t['id']}?format=txt",
                     json.loads(t["hints"] or "[]"), offen=entwurf),
        "<script>function copyVal(b){var v=b.getAttribute('data-copy');"
        "var ok=function(){b.textContent='✓';setTimeout(function(){b.textContent='⧉';},1500);};"
        "var fb=function(){var t=document.createElement('textarea');t.value=v;t.setAttribute('readonly','');"
        "t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();"
        "try{if(document.execCommand('copy'))ok();}catch(e){}document.body.removeChild(t);};"
        "if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(v).then(ok,fb);}"
        "else{fb();}}"
        "function copyRich(id,b){var el=document.getElementById(id);var r=document.createRange();"
        "r.selectNodeContents(el);var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
        "var ok=false;try{ok=document.execCommand('copy');}catch(e){}s.removeAllRanges();"
        "if(ok){b.textContent='✓';setTimeout(function(){b.textContent='⧉';},1500);}}</script>",
    ]
    return page(f"Ticket {ticket_label(t)} {t['series_title']} Staffel {season}", "".join(body))
