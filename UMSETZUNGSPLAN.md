# Metadatarr – Bestandsaufnahme und Umsetzungsplan

**Stand:** 04.11.2026 (Sitzung 24) · **Status:** Phase 0 abgeschlossen (Befunde unten eingetragen). Phase 1 wartet auf ausdrückliche Freigabe. Es wurde kein Anwendungscode geändert.
**Grundlage:** Alle Angaben in Teil A stammen aus den Dateien im Projektordner (gelesen und ausgeführt am 02.11.2026). Angaben aus `HANDOFF.md` und `PROJEKTSTAND.md` sind nur übernommen, wenn der Code sie bestätigt. Abweichungen stehen in A.9.

Kennzeichnung: **[Code]** im Code geprüft · **[Test]** ausgeführt · **[Web]** online nachgelesen · **[offen]** nicht prüfbar, braucht Nutzer oder echte Instanz · **[Empfehlung]** mein Vorschlag, Nutzer entscheidet.

---

## A. Bestandsaufnahme (Ist-Stand)

### A.1 Dateien und Umfang

| Datei | Zeilen | Aufgabe |
|---|---|---|
| `app.py` | 20 | Start: `init_db()`, Nachtlauf-Thread, `ThreadingHTTPServer` auf `0.0.0.0:PORT` |
| `config.py` | 153 | 32 Umgebungsvariablen, `CSRF_TOKEN` (pro Prozessstart), `CONFIG_SECTIONS`, `config_overview()` |
| `sonarr.py` | 166 | Sonarr-Lesezugriff, TBA-Erkennung, Poster-Abruf und Speicher-Cache |
| `speicher.py` | 186 | SQLite, additive Migration, Cache, `store_suggestions` |
| `quellen.py` | 428 | Mediathek, TMDB, fernsehserien.de, Matching |
| `vorschlaege.py` | 183 | Suchkette, Haken/Verwerfen/Wieder öffnen, „Alle hohen markieren“ |
| `ticket.py` | 433 | Ticketbau, DeepL, TVDB-Einreichen/-Status, CSV, **und** HTML-Rendering (Schichten vermischt) |
| `tvdb.py` | 212 | Skriptgesteuerter Zugriff auf `support.thetvdb.com` (Login, Formular, Einreichen, Status) |
| `nachtlauf.py` | 55 | Täglicher Suchlauf und TVDB-Statusabfrage |
| `ui.py` | 475 | HTML/CSS/JS-Rahmen, Seitenleiste, Poster-Platzhalter |
| `web.py` | 793 | HTTP-Handler **und** HTML-Seiten |
| `test_tba.py` | 311 | Prüfsuite mit Attrappen (Sonarr, Mediathek) |
| `Dockerfile`, `docker-compose.snippet.yml` | 18 / 42 | Einstufiges Image, Stack-Eintrag |
| 3 × `metadatarr-icon*.svg` | – | Markenassets (A.8) |

Die Projektdateien liegen flach im Ordner. Der Python-Code hat zusammen 3 415 Zeilen.

### A.2 Seiten und Routen **[Code]**

Alle Seiten sind serverseitig erzeugtes HTML. Es gibt kein JSON.

| Methode | Route | Funktion |
|---|---|---|
| GET | `/health` | Healthcheck (Docker) |
| GET | `/` | Übersicht: Kacheln, Serien mit TBA, letzte Aktivitäten, offene Tickets, Nachtlauf |
| GET | `/serien[?alle=1]` | Serienraster mit Poster und Filter. `/?alle=1` leitet dorthin um |
| GET | `/series/<id>` | Serie, zeigt die erste Staffel mit TBA |
| GET | `/series/<id>/staffel[/<n>]` | Staffelansicht mit Folgen, Vorschlägen und Ticketliste |
| GET | `/series/<id>/staffel/<n>/bericht.csv` | CSV-Bericht aller Vorschläge der Staffel |
| GET | `/ticket/<id>[?format=txt]` | Ticketansicht, TXT-Download |
| GET | `/tickets` | Ticketliste |
| GET | `/einstellungen` | Konfiguration, nur Anzeige, Schlüssel als „gesetzt“ |
| GET | `/diagnose` | Sonarr-, Datenbank-, Dienststatus |
| GET | `/poster/<id>` | Poster, serverseitig aus Sonarr geholt |
| POST | `/refresh` | Sonarr-Daten neu laden |
| POST | `/series/<id>/suche[/mediathek\|tmdb]?staffel=` | Suche (Kette oder einzelne Quelle) |
| POST | `/series/<id>/uebernehmen?staffel=` | „Alle hohen markieren“ |
| POST | `/series/<id>/ticket?staffel=&lang=` | Ticket-Entwurf erstellen |
| POST | `/ticket/<id>/einreichen` | Bei TVDB einreichen |
| POST | `/ticket/<id>/tvdb-status` | TVDB-Status abfragen |
| POST | `/ticket/<id>/status` | `aktion=aufloesen`: **löscht** einen Entwurf samt Positionen |
| POST | `/vorschlag/<id>` | `aktion=toggle\|discard\|reopen` |
| POST | `/diagnose` | Sonarr-Verbindungstest |

Alle POSTs prüfen ein Formularfeld `csrf` gegen **ein** Token pro Prozess. Das Token steckt in jeder HTML-Seite.

### A.3 Integrationen, korrekt eingeordnet **[Code]**

| Integration | Im Code | Rolle | Zugriff | Bemerkung |
|---|---|---|---|---|
| **Sonarr** | `sonarr.py` | Pflichtquelle des Bestands, **nur lesend** (`GET /api/v3/series`, `/episode?seriesId=`, `/MediaCover/…`) | Header `X-Api-Key` | Kein Schreibaufruf gefunden. Siehe aber Risiko R1 |
| **MediathekViewWeb** | `quellen.media_search` | Lesequelle | `POST https://mediathekviewweb.de/api/query` (öffentlich, inoffiziell), kein Key | Vom Nutzer im Container bestätigt (197 Treffer). Zuordnung nur über „(SxxExx)“ im Titel |
| **TMDB** | `quellen.tmdb_*` | Lesequelle (DE und EN) | `api_key` als **URL-Parameter**, Antworten gecacht | Im Compose-Snippet aktiv. Der Nutzer nannte TMDB nicht, es ist aber vorhanden |
| **fernsehserien.de** | `quellen.fs_*` | Lesequelle, **HTML-Scraping** per Textzeilen-Parser | GET, Pause 3 s, nur auf Knopfdruck | Nutzungsbedingungen **nicht verifiziert** (A.7) |
| **DeepL** | `ticket.deepl_translate` | Übersetzungsdienst: sendet Beschreibungstexte an DeepL | Header `DeepL-Auth-Key` | Weder in der Nutzerliste noch Lese- oder Schreibquelle. Einordnung offen (E.4) |
| **TVDB Support-Center** | `tvdb.py`, `ticket.py` | **Aktionsintegration**: Ticket einreichen (Schreiben) und Status lesen | Skriptgesteuerter Login mit `TVDB_USER`/`TVDB_PASSWORD`, CSRF-Token, Multipart-POST | Web-Automation, keine API. Rechtslage siehe A.7 |
| **TVDB als Lesequelle** | – | **nicht vorhanden** | – | TVDB-Daten kommen nur indirekt über Sonarr |
| **TVDB-Übersetzungs-Plugin** | – | **nicht vorhanden** | – | Nur Ticketvorlage plus Serienlink `thetvdb.com/?tab=series&id=<tvdbId>` |

Weitere vorhandene Funktionen: Suchkette Mediathek → TMDB → fernsehserien.de, Sicherheitsstufen hoch/mittel/niedrig, Nachtlauf (Mediathek und TMDB, danach TVDB-Status), CSV-Bericht, TXT-Download, „Alle hohen markieren“, Haken je Folge (höchstens einer), Diagnose, Posteranzeige mit Initialen-Ersatz, Konfigurationsanzeige.

### A.4 Metadatenfelder **[Code]**

* Gelesen aus Sonarr: **nur** `title` und `airDate` der Folgen (plus `seasonNumber`, `episodeNumber`, `hasFile`, `id`). Serienfelder: `title`, `year`, `network`, `status`, `tvdbId`, `titleSlug`, `originalLanguage` (Objekt mit `name`, Sonarr 4.0.20.3014 liefert kein Textfeld; **[Test, Phase 0]**), `images`.
* **Nicht** gelesen: `overview` (Serie und Folge), Staffelobjekte, Episoden-`tvdbId`. Der Speicher hält heute nur die **TBA-Folgen** je Serie, nicht den ganzen Bestand.
* „Fehlend“ heißt heute nur: Folgentitel passt auf `TBA_REGEX` (`^\s*(tba|tbd|n/?a)?\s*$|^(episode|folge)\s+\d+$`, ohne Beachtung der Groß-/Kleinschreibung). Das ist **eine** Regel für **ein** Feld und **alle** Sprachen.
* Gefiltert wird auf `ORIGINAL_LANGUAGE` (Compose setzt `German`). Staffel 0 ist standardmäßig ausgeschlossen.
* Vorschlagsfelder (`suggestion`): `title_de`, `title_en`, `description` in **einer Zeile**. Die Sprache der Beschreibung steht nicht in der Zeile.

### A.5 Poster-/Bildbereitstellung **[Code]** **[Web]**

* Der Server liest `images[]` mit `coverType == "poster"` aus der Serienliste und akzeptiert nur Pfade der Form `/MediaCover/<id>/<name>.<jpg|jpeg|png|webp>`. Er holt zuerst `poster-250.jpg`, sonst das Original, mit Header `X-Api-Key`, ohne Weiterleitungsprüfung. Der Browser bekommt das Bild über `/poster/<id>`. Der Schlüssel verlässt den Server nicht.
* **Sonarr-Fakten aus der Dokumentation und aus Beispielantworten [Web]:** `images[].url` ist ein relativer Pfad. Er kann ein konfiguriertes URL-Base-Präfix enthalten, zum Beispiel `/sonarr/MediaCover/11/poster.jpg?lastWrite=…`. Daneben kann `remoteUrl` stehen (TVDB-Artwork); in Beispielen kommt auch `/MediaCoverProxy/<hash>/…` vor. Belege: [Sonarr API Docs](https://sonarr.tv/docs/api/), [Sonarr API Wiki](https://github.com/Sonarr/Sonarr/wiki/API), [Beispielantwort in Sonarr-Issue #4649](https://github.com/Sonarr/Sonarr/issues/4649) und [Beispielantwort in Sonarr-Issue #7438](https://github.com/Sonarr/Sonarr/issues/7438). Das sind Schema-/Beispielbelege, keine Prüfung der konkreten Nutzerinstanz.
* **Folgen für den Bestand:**
  * **Lücke L1:** Hat Sonarr ein URL-Base-Präfix, passt der Regex nicht. Es gäbe dann **keine** Poster, ohne Fehlermeldung. `SONARR_URL` mit Pfad würde den Pfad außerdem doppeln. Beim Nutzer (`http://sonarr:8989`, vermutlich ohne Präfix) tritt das nicht auf.
  * **Lücke L2:** Der Fehlerpfad ist stumm. Jede Ausnahme wird verschluckt, das Ergebnis ist immer „404 kein Poster“. Authentifizierungsfehler (401/403), Login-Weiterleitung (Forms-Auth, Reverse Proxy), Zeitüberschreitung und HTML statt Bild sind nicht unterscheidbar.
  * **Lücke L3:** Der Poster-Cache hat 400 Einträge, aber keine Bytegrenze. Im Worst Case sind das 400 × 6 MB.
  * **[Test, Phase 0]** Gemessen an der Nutzerinstanz (Sonarr 4.0.20.3014, 5 Serien, nur GET): `images[].url` ist relativ (`/MediaCover/<id>/poster.jpg`) und hat keine URL-Base. Das Bild kommt mit Key (HTTP 200, `image/jpeg`, auch die `poster-250`-Variante) und **auch ohne Key (HTTP 200)**. Die Bildroute ist in dieser Instanz also nicht durch den Key geschützt. Der Key wird trotzdem gesendet. Offen: derselbe Test über die interne Adresse `http://sonarr:8989` aus dem Container.
* `remoteUrl` (TVDB-Artwork) wird **nicht** verwendet. Gründe: Die TVDB-Nutzungsbedingungen schließen Bilder aus der API-Lizenz aus, und der Server würde externe Adressen abrufen.

### A.6 Datenbank **[Code]** **[Test]**

Datei `DB_PATH` (Standard `/data/tba.db`), kurze Verbindungen mit globalem Sperrobjekt. **Im aktuellen Python-Code** wird kein `PRAGMA journal_mode=WAL` gesetzt und es gibt keine `CREATE INDEX`-Anweisung; eine neue Datenbank bleibt daher beim SQLite-Standardjournalmodus. Das Änderungslog in `PROJEKTSTAND.md` (Sitzung 11) dokumentiert jedoch zuvor ausdrücklich WAL und Indizes auf `ticket(series_id)` sowie `ticket_item(suggestion_id)`. **Produktionsbefund (Phase 0, `db_inspect.py`, lokal ausgeführt):** Journalmodus WAL, Indizes `ix_ticket_series` auf `ticket(series_id)` und `ix_ticket_item_sug` auf `ticket_item(suggestion_id)` vorhanden, `quick_check` ok, keine `tba.db-wal`/`tba.db-shm` (sauber geschlossen), `user_version` 0. Bestand: 625 Cache-Einträge, 1931 Vorschläge (1863 unmarkiert, 68 markiert, 18 Serien), 4 Tickets, 78 Ticketpositionen, 1 eingereichtes Ticket. Backup und Migration müssen WAL und Indizes berücksichtigen. Siehe Korrektur in A.9 und die Backup-/Rollback-Regeln D.4.

| Tabelle | Spalten (Auszug) | Bemerkung |
|---|---|---|
| `cache` | `k`, `fetched`, `body` | Antworten von Mediathek, TMDB, fernsehserien.de, DeepL (dauerhaft), Schlüssel `nightly` |
| `suggestion` | `id`, `series_id`, `season`, `episode`, `source`, `source_key`, `title_de`, `title_en`, `description`, `channel`, `air_date`, `link`, `hint`, `level`, `status` (`offen`/`verworfen`), `created`, `marked`, `edited`, `orig_*` | `UNIQUE(series_id, season, episode, source, source_key)`. `store_suggestions` **löscht** bei jeder Neusuche offene, unmarkierte, nicht im Ticket stehende Vorschläge |
| `ticket` | `id`, `series_id`, `series_title`, `tvdb_id` (= **Serien**-ID), `season`, `lang`, `status` (`erstellt`/`eingereicht`/…), `body`, `body_html`, `hints`, `created`, `updated`, `tvdb_no`, `tvdb_status`, `tvdb_checked` | |
| `ticket_item` | `ticket_id`, `suggestion_id`, `season`, `episode`, `title` | |

* Die Migration in `init_db` ist **rein additiv** (`CREATE IF NOT EXISTS`, `ADD COLUMN`). Sie hat zwei Daten-Updates: alter Status `ausgewählt` wird zu `marked=1`, alter Status `eingereicht` bei Vorschlägen wird zu `verworfen`.
* **Es gibt keine Status-Verlaufstabelle.** Gespeichert ist nur der aktuelle Stand (`status`, `tvdb_status`, `tvdb_checked`). Ein „Statusverlauf“ im Sinn einer Historie existiert im Altbestand nicht und kann nicht erhalten werden. Der Plan beginnt ihn ab der Migration neu aufzuzeichnen.
* Die Spalten `edited` und `orig_*` sind vorhanden, werden aber nirgends mehr beschrieben. Handbearbeitung wurde entfernt (Test 15).
* Nicht in der DB, sondern nur im Speicher: Sonarr-Daten (`CACHE_TTL`), Poster, Meldungen, `CSRF_TOKEN`.

### A.7 Rechtliches und Nutzungsbedingungen **[Web]**

* **TVDB-API v4** (offizielle [OpenAPI-Spezifikation 4.7.10](https://github.com/thetvdb/v4-api/blob/main/docs/swagger.yml); [API-Dokumentation](https://thetvdb.github.io/v4-api/)): Die dokumentierten Schreib-POSTs sind `/login` und `/user/favorites`; ein Schreibendpunkt für Episode-Übersetzungen ist nicht dokumentiert. Episoden-Lesedaten umfassen `nameTranslations` und `overviewTranslations`; dokumentiert sind außerdem `/episodes/{id}/translations/{language}` und `/series/{id}/episodes/{season-type}/{lang}`. Sie können helfen, das Fehlen einer Zielsprache festzustellen (E.1). Vor einer Implementierung werden Spezifikation und Nutzungsbedingungen erneut geprüft.
* **TVDB-Nutzungsbedingungen** ([Terms of Service, Stand auf der Seite: 23.10.2020](https://thetvdb.com/tos)): Abschnitt 5 beschränkt den Seitenzugriff auf die bereitgestellte Schnittstelle, außer mit ausdrücklicher schriftlicher Erlaubnis; die API-Lizenz ist an das genehmigte Projekt gebunden und untersagt übermäßige Aufrufe. **Folge:** Eine automatisierte Bearbeitung von Episoden über die TVDB-Weboberfläche wird **nicht** umgesetzt. Für das getrennte Support-Center (`support.thetvdb.com`) ist die Anwendbarkeit dieser Website-Bedingungen nicht eindeutig. Das bestehende Ticket-Skript ist dennoch Web-Automation. Es bleibt auf ausdrücklichen Nutzerwunsch als optionales Aktions-Plugin erhalten und erhält einen Hinweis und die Schutzmaßnahmen aus E.3 und G.3.
* Hinweis aus den HAR-Aufnahmen im Altbestand: Die Login-Seite antwortet auch dem Browser mit HTTP 422. Das ist kein Filter gegen das Tool. Der Client behandelt das korrekt.
* **fernsehserien.de:** Die [robots.txt](https://www.fernsehserien.de/robots.txt) weist eine Sperre für den Crawler „GPTBot“ aus. Der geprüfte Pfad [`/nutzungsbedingungen`](https://www.fernsehserien.de/nutzungsbedingungen) gab in dieser Prüfung eine 404-Seite zurück. **Die Nutzungsbedingungen sind damit nicht verifiziert.** `ARBEITSANWEISUNGEN.md` (Abschnitt 6) verlangt für Quellen mit unklarem Scraping-Verbot eine ausdrückliche Freigabe des Nutzers; nach Nutzerentscheidung erfolgt das Aktivieren pro Quelle in der Oberfläche (E.2).
* **MediathekViewWeb, TMDB, DeepL:** Bedingungen von mir **nicht** erneut geprüft. TMDB, DeepL und MediathekViewWeb haben eigene Regeln. Der Plan zeigt je Quelle einen Hinweis „Bedingungen prüfen“ mit Link an.

### A.8 Markenassets **[Code]**

Alle drei Dateien liegen im Ordner-Root. SHA-256 (für den Unverändert-Test):

| Datei | Rolle | SHA-256 |
|---|---|---|
| `metadatarr-icon-light.svg` | Icon für **helle** Themes: Kachel #163653→#153653, Klammern #FFF9EF, Quadrat #0786D3 | `bc38a19a…8173` (`bc38a19adfe02448f0a55c32da16ce97fddc36755956bc9ea3c4c9eef2b78173`) |
| `metadatarr-icon-dark.svg` | Icon für **dunkle** Themes: Kachel #E8EDF7→#F4F6FB, Klammern #14243A, Quadrat #8BA1FF | `e4e2dd08…03bd` (`e4e2dd0845a0fdb3f8735be6e2ed163dbe15f2d2b8ba6c8e34934485a8ba03bd`) |
| `metadatarr-icon.svg` | Master mit beiden Varianten. Wechselt per `prefers-color-scheme` (Betriebssystem), per `data-theme` nur bei Inline-Einbindung | `e24e34b1…ce15` (`e24e34b1b147ef0cb8fdc24f925eff8a3b67cdd5b776f718ffcc7c41c7a7ce15`) |

**Zuordnung:**

* **Logo in der App (Seitenleiste, Kopf):** `-light.svg` bei hellem, `-dark.svg` bei dunklem App-Thema, gewählt über den **App-Schalter** System/Hell/Dunkel. Der Master passt dafür nicht, weil er dem Betriebssystem folgt und der App-Schalter ihn über ein `<img>` nicht erreicht.
* **Favicon und kleine Icons (Tab, App-Symbol):** `metadatarr-icon.svg` (Master). Er passt sich der Browser-Einstellung an.
* **Es gibt keine Wortmarke.** Die SVGs enthalten nur das quadratische Icon. „Logo“ ist Icon plus der Schriftzug „Metadatarr“ als Text. Eine Unterzeile gibt es **nicht**.
* Die Dateien werden **byte-identisch** nach `frontend/public/brand/` kopiert. Ein Test vergleicht die SHA-256-Werte. `<title>`/`<desc>` bleiben unverändert (nicht inline einbinden, sonst doppelte IDs).

### A.9 Abweichungen zwischen Dokumentation und Code

| Aussage in `HANDOFF.md`/`PROJEKTSTAND.md`/`ARBEITSANWEISUNGEN.md` | Befund |
|---|---|
| „Web-App ist `app.py`“, „Seiten `/`, `/series/<id>`, `POST /refresh`“ (HANDOFF) | Veraltet. 12 Module, 20 Routen (A.2) |
| Prüfsuiten „53/53“, „38/38“, Tests im Ordner `test/` | Es gibt nur `test_tba.py`. **Ausgeführt: 69/69 bestanden** (Python 3.13 hier, Image nutzt 3.12). Ordner `test/` existiert nicht |
| Vorschläge können „bearbeitet“ werden (PROJEKTSTAND 2.6) | Entfernt (Test 15). `edited`/`orig_*` ohne Wirkung |
| „Statusverläufe“ | Keine Historie vorhanden (A.6) |
| WAL und Indizes aus Änderungslog Sitzung 11 (`PROJEKTSTAND.md`) | Im aktuellen `speicher.py` fehlen `PRAGMA journal_mode=WAL` und die beiden dokumentierten `CREATE INDEX`-Anweisungen. Das kann eine Rückentwicklung gegenüber einem früheren Build sein; WAL-Modus und Indizes können in einer bestehenden SQLite-Datei weiterbestehen. **Befund (Phase 0):** Die Produktionsdatenbank hat WAL und beide Indizes. Der Code muss sie übernehmen und darf sie nicht entfernen. Die Migration soll bestehende Indizes niemals entfernen und Indexe bei bestätigtem Bedarf idempotent ergänzen; WAL nur nach Kompatibilitäts- und Parallelitätstests gezielt wieder aktivieren. Backup/Restore muss bei WAL den SQLite-Backup-Mechanismus bzw. Sidecar-Dateien sicher behandeln (D.4). |
| „Python, nur Standardbibliothek“ (HANDOFF, ARBEITSANWEISUNGEN §5) | Durch den neuen Auftrag aufgehoben. `ARBEITSANWEISUNGEN.md` §5 ist nachgezogen (Sitzung 24, begründete Abhängigkeiten erlaubt). |
| Ordnername „Deutsche Serientitel“, Projektname „TBA-Tickets“ | Ordner heißt jetzt „Matadatarr“, Projektname Metadatarr |
| „Poster kommen aus Sonarr“ (Sitzung 22) | Code vorhanden, **nicht** durch den Nutzer bestätigt (Punkt 18) |
| „Dateien flach, ohne Unterordner“ (HANDOFF §12) | Gilt für den Altbestand. Das React-Projekt braucht Unterordner (E.5) |
| TVDB als vorhandene **Lesequelle** (Nutzerangabe) | Nur als Ticket-Aktion vorhanden (A.3) |

### A.10 Umgebungsvariablen (32 + `TZ`) **[Code]**

`SONARR_URL`, `SONARR_API_KEY`🔒, `SONARR_PUBLIC_URL`, `ORIGINAL_LANGUAGE`, `INCLUDE_SPECIALS`, `CACHE_TTL`, `TBA_REGEX`, `TITLE_SUFFIX_REGEX`, `PORT`, `DB_PATH`, `MEDIA_API_URL`, `MEDIA_PAUSE`, `MEDIA_MAX_PAGES`, `TMDB_API_URL`, `TMDB_API_KEY`🔒, `TMDB_PAUSE`, `MAX_TMDB_SEASONS`, `FS_PAUSE`, `CACHE_DAYS`, `USER_AGENT`, `NIGHTLY_SEARCH`, `NIGHTLY_HOUR`, `TVDB_STATUS_NIGHTLY`, `TVDB_BASE_URL`, `TVDB_USER`🔒, `TVDB_PASSWORD`🔒, `TVDB_TOPIC_ID`, `TVDB_PAUSE`, `TVDB_TIMEOUT`, `DEEPL_API_URL`, `DEEPL_API_KEY`🔒, `SHOW_POSTERS`. Das Compose-Snippet setzt zusätzlich `TZ`. 🔒 = geheim.

### A.11 Vorhandene Risiken im Altbestand **[Code]**

| # | Befund | Folge im Plan |
|---|---|---|
| R1 | `urllib` folgt Weiterleitungen und **übernimmt dabei benutzerdefinierte Header** (u. a. `X-Api-Key`, `DeepL-Auth-Key`) | Neuer geschützter HTTP-Client (F.1), Weiterleitungen manuell und Schlüssel nur zum Ursprung |
| R2 | CSRF-Token ist **global** und steckt im HTML. Es gibt keine Prüfung von `Origin`/`Host` | Token über `GET /api/v1/session`, Header `X-CSRF-Token`, zusätzlich `Origin`-/`Host`-Prüfung (Tiefenverteidigung, **keine** Authentifizierung) |
| R3 | `Content-Length` des POST-Bodys ist unbegrenzt | Body-Limit in der neuen API |
| R4 | Poster-Cache ohne Bytegrenze (L3) | LRU mit Bytegrenze |
| R5 | `store_suggestions` löscht offene Vorschläge bei Neusuche | Kandidaten werden nicht überschrieben, sondern abgelöst (D.3) |
| R6 | `/ticket/<id>/status` löscht Entwürfe unwiderruflich | Bleibt funktional erhalten, wird protokolliert (Audit-Ereignis mit Inhalt) |
| R7 | Skript-Login bei TVDB: Absturz zwischen „TVDB hat angenommen“ und „lokal gespeichert“ erzeugt bei Wiederholung ein Doppelticket | Ausführungsprotokoll mit Zustand vor dem externen Aufruf (G.3) |

---

## B. Zielarchitektur

```
Browser ──HTTP──► Python-Prozess (ein Container, Port 8080 → Host 8083, unverändert)
                  ├─ /api/v1/*      JSON-API (nur dieser Prozess, gleiche Origin)
                  ├─ /health        unverändert
                  └─ /*             statische Vite-Dateien, SPA-Fallback auf index.html
                  Kern:  Sonarr-Client (read-only) · Missing-Resolver · Poster-Dienst · SQLite
                  Plugins: Quellen-Registry │ Aktions-Registry   (getrennt)
                  Dienste: geschützter HTTP-Client · SecretStore · Redaktion · Audit · Jobs (Nachtlauf)
```

* **Frontend:** Vite + React + TypeScript, React Router, eigenes CSS mit Design-Tokens (keine UI-Bibliothek nötig). Build erzeugt nur statische Dateien. Kein Node zur Laufzeit.
* **Backend:** Python bleibt. **Der Server bleibt `http.server` (Standardbibliothek)** mit dünnem Router. Begründung: ein Nutzer im Heimnetz, kein Bedarf für ein Framework, eine Abhängigkeit weniger. Antworten: JSON, Fehler als `{"type","title","detail","code"}` ohne Geheimnisse.
* **Dockerfile:** mehrstufig. Stufe 1 `node:<LTS>-alpine` baut `frontend/` (`npm install`, `npm run build`; **Entscheidung Nutzer, Sitzung 24:** keine `package-lock.json`, Versionen werden bei jedem Build aufgelöst, neue Versionen erscheinen als neues Release). Stufe 2 `python:3.12-slim` installiert `requirements.txt`, kopiert Python-Code und `dist/` nach `/app/static`. Nicht-Root-Benutzer, Healthcheck und `CMD ["python","app.py"]` bleiben. **Dienstname, Container-Name, Port 8080→8083, Volume `/data`, Traefik-Labels und alle vorhandenen Umgebungsvariablen bleiben unverändert.** Neu hinzu kommt höchstens `METADATARR_SECRET_KEY` (F.2). Das ist ein dokumentierter fachlicher Grund.
* **Python-Abhängigkeiten (neu, `requirements.txt`):**
  * `cryptography`: authentifizierte Verschlüsselung (AES-GCM) für gespeicherte Geheimnisse. Die Standardbibliothek hat keine. Eigene Kryptografie wäre fahrlässig. Weit verbreitet, aktiv gepflegt, Wheels für `python:3.12-slim`.
  * `beautifulsoup4` (zieht `soupsieve`): HTML-Parsing und **CSS-Selektoren** für den Quellen-Editor. Rein Python, aktiv gepflegt, Standard-Parser `html.parser` genügt (kein `lxml` nötig). Alternativen: `lxml`+`cssselect` (C-Erweiterung, mehr Aufwand im Image), `selectolax` (kleinere Gemeinschaft).
  * XML und JSON: **Standardbibliothek**. XML mit `xml.etree.ElementTree`, vorher werden Dokumente mit `<!DOCTYPE`/`<!ENTITY` abgelehnt (Schutz vor Entity-Expansion, ohne `defusedxml`). JSON-Pfade und XML-Pfade über einen eigenen kleinen, begrenzten Auswerter.
  * Versionen werden vor der Freigabe im echten Docker-Build gegen `python:3.12-slim` festgelegt. **Hier nicht prüfbar** (kein Docker).
* **Struktur im Projektordner** (Unterordner, falls zulässig, siehe E.5):

  ```
  Dockerfile  requirements.txt  .dockerignore  docker-compose.snippet.yml
  app.py config.py sonarr.py speicher.py …        (bestehende Module, ganze Dateien ersetzt, wo nötig)
  api/        Router, Handler, Schemas, Sicherheit (CSRF, Host/Origin, Header)
  core/       Missing-Resolver, Zielsprachen, Posterdienst, Jobs, Audit, Migration
  net/        geschützter HTTP-Client, Redaktion, SecretStore
  plugins/    sources/{mediathek,tmdb,fernsehserien,deepl,declarative}.py · actions/{tvdb_ticket,tvdb_translation}.py
  frontend/   Vite-Projekt (package.json, src/, public/brand/*.svg)
  tests/      Backendtests
  ```

  Bestehende Dateien, die entfallen (`ui.py`, die HTML-Teile von `web.py` und `ticket.py`), kann ich nicht aus bash löschen (Dateiregel). Ich führe sie in einer **Löschliste für den Nutzer**. Ersetzbare Dateien (`web.py`, `ticket.py`) schreibe ich als ganze Datei neu.

---

## C. JSON-API-Verträge (`/api/v1`)

Gleiche Origin, kein CORS. Alle nicht lesenden Aufrufe verlangen den Header `X-CSRF-Token` (Token aus `GET /session`) und prüfen `Origin`/`Host`. **CSRF-Schutz ist keine Authentifizierung.** Jeder Client mit Netzzugriff kann das Token holen und die API nutzen (G).

| Bereich | Endpunkte | Hinweise |
|---|---|---|
| Sitzung | `GET /session` → `{csrfToken, app:{name,version}, features}` | Ersetzt das Token im HTML |
| Übersicht | `GET /overview` | Kennzahlen, fehlende Felder nach Sprache und Ebene, offene Tickets, Nachtlauf |
| Serien | `GET /series?filter=…&language=…` · `GET /series/{id}` · `GET /series/{id}/seasons/{n}` | Serie mit Staffeln, Folgen, Feldstatus je Zielsprache, wirksamen Zielsprachen (geerbt/überschrieben) |
| Folge | `GET /episodes/{id}` | Felder × Sprachen × Status, alle Kandidaten mit Quelle, Sprache, Wert, Link, Abrufzeit |
| Fehlende Metadaten | `GET /missing?level=&field=&language=&series=&page=` | Paginiert, gleiche Regeln wie die Detailansichten |
| Poster | `GET /series/{id}/poster` | Bild oder `404`/`502` mit Kopf `X-Poster-Reason` (`missing`/`auth`/`login-redirect`/`timeout`/`not-image`/`too-large`/`disabled`). Das Frontend zeigt den Ersatz |
| Zielsprachen | `GET/PUT /settings/target-languages` · `PUT/DELETE /series/{id}/target-languages` | `DELETE` setzt auf die globale Vorgabe zurück. Audit-Ereignis je Änderung |
| Regeln | `GET/PUT /settings/placeholder-rules` | Regeln je Feld/Sprache. Aus der Umgebung gesetztes `TBA_REGEX` erscheint schreibgeschützt |
| Suche | `POST /series/{id}/search` `{sources?, season?}` | Nur aktivierte Quellen, nur auf Knopfdruck (und Nachtlauf, wie heute) |
| Kandidaten | `POST /candidates/{id}/actions` `{action: mark\|unmark\|discard\|reopen}` · `POST /series/{id}/seasons/{n}/accept-high` | Ersetzt `/vorschlag/<id>` und `/uebernehmen` |
| Tickets (lokal) | `GET /tickets` · `GET /tickets/{id}` · `POST /tickets` `{seriesId,season,lang}` · `DELETE /tickets/{id}` (nur Entwurf) · `GET /tickets/{id}/export?format=txt` · `GET /series/{id}/seasons/{n}/report.csv` | Ticketvorlage mit Sprache, Serie, Staffel, Folge, Titel, Beschreibung |
| Aktionen | `GET /actions` · `POST /actions/{plugin}/preview` · `POST /actions/{plugin}/execute` | **Zwei Schritte erzwungen** (G.2) |
| Quellen | `GET /sources` · `PUT /sources/{id}/enabled` · `GET /sources/{id}/access-profile` | Eingebaute und eigene Quellen, Zugriffsprofil |
| Eigene Quellen | `POST/GET/PUT/DELETE /sources/custom[/{id}]` · `POST /sources/custom/test` · `POST /sources/custom/preview` · `GET /sources/custom/{id}/export` · `POST /sources/custom/import` · `PUT /sources/{id}/secrets` (nur schreiben) | Test und Vorschau speichern **nichts** und lösen **keine** Aktion aus |
| Konfiguration | `GET /config` | Wirksame Konfiguration, Geheimnisse maskiert, je Eintrag Herkunft und Bearbeitbarkeit |
| Diagnose | `GET /diagnostics` · `POST /diagnostics/sonarr-test` · `POST /diagnostics/sonarr-probe` | Probe: nur lesend, meldet Antwortform und Statuscodes der Bildrouten |
| System | `GET /health` (außerhalb von `/api`, unverändert) | Docker-Healthcheck |

Alle Listen: Seitenlänge begrenzt, Fehler ohne Stacktrace und ohne Geheimnisse. Alle Textfelder aus Quellen kommen als reiner Text. Das Frontend rendert sie nie als HTML.

---

## D. Datenmodell und Migration

### D.1 Grundsätze

1. **Nur additiv.** Die vier Bestandstabellen und alle ihre Spalten bleiben unverändert und lesbar. Kein `DROP`, kein `RENAME`, kein Zurücksetzen.
2. Neue Tabellen speichern Quelle, Sprache, Wert, Link, Abrufzeit, Zustand und Herkunft. Werte werden **nicht überschrieben**. Eine Änderung erzeugt eine neue Zeile, die die alte ablöst.
3. Kein Wert aus einer Quelle ersetzt etwas in Sonarr oder in einer „gepflegten“ Angabe. Übernahme in ein Ticket oder eine externe Aktion braucht immer eine Nutzeraktion.

### D.2 Neue Tabellen (Entwurf)

| Tabelle | Zweck |
|---|---|
| `schema_migration` | Version, Zeitpunkt, Beschreibung, Prüfsumme je Schritt. Zusätzlich `PRAGMA user_version` |
| `app_setting` | Schlüssel/Wert (JSON), z. B. globale Zielsprachen, Regelwerk |
| `series_setting` | `series_id`, `target_languages` (NULL = erbt global) |
| `placeholder_rule` | `id`, `field_key`, `language` (NULL = alle), `kind` (`empty`/`exact`/`regex`), `pattern`, `enabled`, `origin` (`env-seed`/`ui`) |
| `source_config` | `id`, `plugin_key`, `kind` (`builtin`/`declarative`), `name`, `description`, `languages`, `definition` (JSON ohne Geheimnisse), `enabled`, `allow_external`, `allow_private`, `granted_hosts`, `include_in_nightly`, Zeitstempel. Zeilen für `mediathek`, `tmdb`, `fernsehserien` (und `deepl`) werden beim ersten Start angelegt |
| `action_config` | `plugin_key`, `enabled`, `settings`, Zeitstempel. **Getrennt** von `source_config` |
| `secret` | `owner_kind`, `owner_id`, `slot`, `ciphertext`, `nonce`, `key_id`, Zeitstempel. Nie im Klartext, nie in Exporten |
| `candidate_value` | **Eine Zeile je Feld, Sprache, Quelle und Abruf:** `series_id`, `season`, `episode`, `sonarr_episode_id`, `field_key`, `language`, `value`, `source_id`, `source_url`, `retrieved_at`, `match_level`, `match_hint`, `suggestion_id` (Rückverweis auf Altbestand), `superseded_by`, `created_at` |
| `candidate_state` | Zustand je Kandidat: `status` (`offen`/`verworfen`), `marked`, Zeitstempel |
| `metadata_snapshot` | Letzter gelesener Sonarr-Stand je Feld und Sprache mit Status (`present`/`missing_empty`/`missing_absent`/`placeholder`/`not_provided`), Regel, Wert-Hash, Zeitpunkt. Dient der Erkennung von Änderungen in Sonarr |
| `action_execution` | `id`, `plugin_key`, `entity_kind`, `entity_id`, `preview_hash`, `idempotency_key` (UNIQUE), `state`, `external_id`, `started_at`, `finished_at`, `result_summary` |
| `ticket_status_event` | Statusverlauf ab Migration |
| `audit_event` | Nur anhängen: Zeit, Aktion, Objekt, Vorher/Nachher (ohne Geheimnisse), Anfrage-ID |

Feldregister im Code (nicht in der DB): `series.title`, `series.overview`, `episode.title`, `episode.overview` (Stufe 1). Weitere Felder (Stufe 2, niedrige Priorität, dokumentiert): Genres, Studio/Sender, Erstausstrahlung, Laufzeit, Staffeltitel/-beschreibung, Folgenbilder.
**Staffelebene:** Sonarr liefert keine Staffeltitel oder -beschreibungen. Die Oberfläche zeigt Staffeln mit **Aggregaten** (Anzahl fehlender Folgenfelder) und markiert Staffelfelder als „von Sonarr nicht geliefert“. Es werden keine Werte erfunden.

### D.3 Backfill aus dem Altbestand (nur Kopieren)

* Jede Zeile in `suggestion` ergibt bis zu drei Zeilen in `candidate_value`: `title_de` → (`episode.title`, `de`), `title_en` → (`episode.title`, `en`), `description` → (`episode.overview`, `de`). Die Beschreibungssprache ist bei Mediathek, TMDB (de-DE) und fernsehserien.de Deutsch. Das ist **eine Annahme**, sie steht in `match_hint` („aus Altbestand“).
* `retrieved_at` = `suggestion.created` (Speicherzeitpunkt, nicht exakter Abrufzeitpunkt). Das wird in der Oberfläche als „gespeichert am“ bezeichnet.
* `marked` und `status` werden nach `candidate_state` kopiert. `ticket`, `ticket_item` und `suggestion` bleiben unberührt und führend, solange sie in Gebrauch sind. Neue Schreibvorgänge der Tickets laufen weiter über sie.
* Je Ticket ein `ticket_status_event` „Import aus Altbestand“ mit dem damaligen Stand.

### D.4 Backup und Rollback (vor der ersten Migration)

1. **Vor jeder Schemaänderung** liest der Start `PRAGMA user_version` und `PRAGMA journal_mode` und ermittelt vorhandene Indizes über `sqlite_master`. Liegt die Version unter dem Ziel, erzeugt er mit der SQLite-Backup-Schnittstelle (`Connection.backup`) eine konsistente Sicherung nach `/data/backups/tba-vor-v<N>-<Zeitstempel>.db` – auch wenn die Live-Datenbank im WAL-Modus läuft. Danach prüft die Sicherung `PRAGMA integrity_check`, Journalmodus, relevante Indizes und Zeilenzahlen der vier Altbestandstabellen. `tba.db-wal`/`tba.db-shm` werden nicht separat unkoordiniert kopiert. **Schlägt eines davon fehl, startet die App nicht und ändert nichts** (klare, redigierte Meldung im Log).
2. Jeder Migrationsschritt läuft in **einer Transaktion**. Ein Fehler rollt den Schritt zurück.
3. **Rollback:** Container stoppen und die Sicherung mit einem SQLite-sicheren Restore-Verfahren einspielen; bei einer WAL-Datenbank sicherstellen, dass keine veralteten `-wal`/`-shm`-Sidecars mit der zurückgespielten Datei kombiniert werden. Danach Journalmodus, Integrität und Zeilenzahlen prüfen, erst dann das alte Image starten. Das alte Image läuft außerdem ohne Rückspielen gegen eine rein additiv migrierte Datenbank; ein Test beweist das (J.2).
4. **Manuelle Sicherung** empfohlen vor dem ersten Start: `${DOCKER}/tba-tickets/data/tba.db` kopieren, und zwar bei gestopptem Container. Ein Hilfsskript `scripts/db_inspect.py` zeigt nur Schema und Zeilenzahlen (keine Inhalte). Mit seiner Ausgabe prüfe ich Ihre echte Datenbank, ohne Daten zu sehen.
5. **Kompatibilität:** Tests bauen Testdatenbanken in jedem Schemastand, den `init_db` kennt (ohne `hint`; ohne `marked`; ohne `body_html`; ohne `tvdb_*`), füllen sie mit Beispieldaten, migrieren und weisen nach, dass IDs, Status, Texte und Zählungen erhalten sind.

---

## E. Entscheidungen (offen oder empfohlen)

### E.1 Wie wird „fehlt in Zielsprache X“ festgestellt? **[Entscheidung nötig]**

Sonarr liefert nach dem Code und meinem Kenntnisstand je Folge **einen** Titel und **eine** Beschreibung, ohne Liste verfügbarer Übersetzungen. **[offen: Phase-0-Probe gegen die echte Instanz]** Ob dieser Text Deutsch, Englisch oder „TBA“ ist, hängt von TVDB und Sonarr ab. Bei den deutschen Serien des Nutzers steht dort heute „TBA“.

* **Vom Nutzer gewählt (02.11.2026): A.** Der Sonarr-Text bleibt ein Bestandswert, aber seine Sprache wird nicht geraten. In der Laufzeitkonfiguration wird die Sprache der Sonarr-Metadaten ausdrücklich festgelegt; solange sie nicht verifiziert/eingestellt ist, gilt der Sprachstatus als „nicht prüfbar“. **Optional** kann ein getrennt aktivierbares Plugin „TVDB (lesend, API v4)“ mit eigenem API-Schlüssel die Listen `nameTranslations`/`overviewTranslations` je Episode lesen und damit das Fehlen einer Zielsprache feststellen. Ohne das Plugin zeigt die Oberfläche für weitere Zielsprachen ehrlich „nicht prüfbar“ samt Kandidaten. Das Plugin ist eine Funktionserweiterung und wird nicht ohne die erforderliche Freigabe/API-Berechtigung aktiviert.
* **B:** Nur Sonarr; andere Zielsprachen immer „ungeprüft“.
* **C:** TVDB-v4-Leseplugin für Zielsprachen außerhalb der ausdrücklich konfigurierten Sonarr-Metadatensprache voraussetzen.

Die fachliche Wahl A ist getroffen. Die optionale TVDB-Lesequelle bleibt im Plan; konkrete API-Berechtigung und Zugriff werden vor Aktivierung geprüft.

### E.2 fernsehserien.de **[Nutzerentscheidung getroffen]**
Die Nutzungsbedingungen sind nicht verifiziert (A.7). Auf Wunsch des Nutzers bleibt die Quelle **im Quellenbereich aktivierbar/deaktivierbar**; sie startet nach Migration **deaktiviert**. Vor der Aktivierung zeigt die Oberfläche transparent den Anbieter, die unbeklärten Nutzungsbedingungen, die Zielhosts und die Folgen eines Scraping-Zugriffs. Eine Aktivierung ist eine bewusste Freigabe pro Quellenkonfiguration. Bleibt die Quelle aktiv, gilt weiter: nur manuelle Suche (nicht im Nachtlauf), Pause 3 s, Raten-/Antwortlimits und sofortiges Abschalten jederzeit in der Oberfläche. Die UI-Freigabe ersetzt keine Anbietererlaubnis; keine Schutzmaßnahme wird umgangen.

### E.3 TVDB-Ticket-Automation **[Nutzerentscheidung getroffen]**
Das bestehende Einreichungs-Plugin bleibt als getrenntes, optionales **Aktions-Plugin „TVDB-Ticket“** erhalten. Aktivierbar nur, wenn `TVDB_USER` und `TVDB_PASSWORD` konfiguriert sind; bei gesetzten Zugangsdaten bleibt es wie bisher verfügbar. Die Oberfläche weist auf die nicht abschließend geklärten Bedingungen des Support-Centers hin. Neu: serverseitig erzwungene Vorschau und ausdrückliche Bestätigung vor jedem externen Schreiben sowie Schutz vor Doppelaktionen (G). Die manuelle Ticketvorlage bleibt immer nutzbar. Nutzerentscheidung: Optional behalten und bei Zugangsdaten aktivieren.

### E.4 DeepL **[Empfehlung]**
Einordnung als **Metadatenquellen-Plugin mit abgeleiteten Kandidaten** (`derived_from` verweist auf den Ursprungswert, Kennzeichnung „maschinell übersetzt“). So bleiben zwei Plugin-Arten bestehen. Zustand nach der Migration: aktiv genau dann, wenn `DEEPL_API_KEY` gesetzt ist. Hinweis in der Oberfläche: „Beschreibungstexte werden an DeepL gesendet“.

### E.5 Projektstruktur **[Nutzerentscheidung getroffen]**
Unterordner sind ausdrücklich erlaubt. Das Projekt erhält die für den Build erforderliche Struktur, darunter `frontend/`, `api/`, `core/`, `plugins/` und `tests/`.

### E.6 Weitere Entscheidungen mit Empfehlung (gelten mit der Planfreigabe)

| # | Thema | Empfehlung |
|---|---|---|
| E.6.1 | `ORIGINAL_LANGUAGE` | **Nutzerentscheidung getroffen:** Alle Serien werden vollständig geladen und ohne feste Sprachvorauswahl angezeigt. Filter nach Originalsprache sind optional in der Serienliste verfügbar. Die alte Variable darf den Bestand nicht mehr einschränken; sie wird als Laufzeitparameter transparent als „nicht mehr filterwirksam“ angezeigt, bis sie bei einem späteren bereinigenden Release entfernt werden kann. |
| E.6.2 | Geheimnis-Schlüssel | Neue Variable `METADATARR_SECRET_KEY` (mindestens 32 Zeichen, zufällig). **Ohne** Schlüssel bleibt der Geheimnisspeicher gesperrt (Oberfläche erklärt das), Quellen ohne Zugangsdaten funktionieren weiter. Es gibt **kein** Klartext-Fallback. Vorhandene Geheimnisse aus der Umgebung (`SONARR_API_KEY` usw.) bleiben in der Umgebung |
| E.6.3 | Funktionsfarben | Die SVGs enthalten kein Rot/Grün/Gelb. Für Status (Erfolg, Warnung, Fehler) brauche ich **wenige Funktionsfarben**. Sie werden aus dem Markenblau abgesetzt, immer mit Symbol und Text (nie nur Farbe) und auf Kontrast geprüft. Alternative: nur Markenfarben plus Symbole |
| E.6.4 | Node/Lockfile | `npm ci` braucht eine `package-lock.json`. Sie wird nach lokaler Paketauflösung eingecheckt. **Kein Build ohne Lockdatei**; bis sie lokal erzeugt und geprüft ist, bleibt die Produktionsfreigabe blockiert, statt unpinned `npm install` im Docker-Build einzusetzen. |
| E.6.5 | Regex in eigenen Quellen | In Version 1 **nicht** angeboten (ReDoS-Risiko). Es gibt feste Transformationen (trimmen, Leerraum glätten, Präfix/Suffix entfernen, Datumsformat) |
| E.6.6 | Nachtlauf | Läuft nur für Serien mit fehlenden Feldern, nur über Quellen, die die Zielsprache unterstützen und `include_in_nightly` haben (Vorgabe wie heute: Mediathek und TMDB an, fernsehserien.de aus). Er schreibt **nichts** extern |
| E.6.7 | Server | `http.server` bleibt (B) |

---

## F. Plugins, Geheimnisse und Netzwerk

### F.1 Plugin-Verträge (Python, intern)

```python
class SourcePlugin:          # liest und liefert Kandidaten, kann NICHT schreiben
    key, label, languages, fields, nightly_capable, network_targets(cfg)
    def fetch(self, ctx: SourceContext, query) -> Iterable[Candidate]: ...

class ActionPlugin:          # führt externe Schreibaktionen aus, getrennt registriert
    key, label, effects, needs_secrets
    def preview(self, ctx, request) -> ActionPreview: ...           # ohne externe Wirkung
    def execute(self, ctx, preview, confirmation) -> ActionResult: ...
```

* **Getrennte Registries und getrennte Schalter** (`source_config.enabled`, `action_config.enabled`). Eine Klasse kann sich nur in **einer** Registry anmelden (Test). Die Freigabe prüft der **Dispatcher**, nicht das Plugin.
* `SourceContext` bietet nur den geschützten HTTP-Client, **lesenden** Datenzugriff und die **eigenen** Geheimnisse. Es enthält keinen Zugriff auf Aktionsgeheimnisse und keinen Schreibzugriff auf externe Dienste. Ein Lese-Plugin erhält dadurch keine Schreibrechte.
* Einordnung des Bestands: **Quellen:** Mediathek, TMDB, fernsehserien.de, DeepL (abgeleitet), später TVDB (lesend, falls freigegeben), eigene deklarative Quellen. **Aktionen:** TVDB-Ticket (Einreichen, Statusabfrage), TVDB-Übersetzung (G.4). **Kern, kein Plugin:** Sonarr, Ticketvorlage, TXT- und CSV-Export.

### F.2 Geheimnisse

* Speicherung in `secret`: AES-256-GCM, Schlüssel per HKDF aus `METADATARR_SECRET_KEY`, je Eintrag zufälliger Nonce, Eigentümer und Slot als zusätzlich authentifizierte Daten. Falscher Schlüssel führt zu einer klaren Meldung, nicht zu Datenmüll.
* **Maskierung:** API und Oberfläche liefern nie den Wert, nur `configured: true/false`. Schreiben ist ein eigener Aufruf, ein Lesen des Wertes gibt es nicht.
* **Redaktion:** Ein zentraler Redaktor kennt alle aktiven Geheimniswerte und Muster (`Authorization`, `X-Api-Key`, `api_key=`, `apikey=`, `token=`, Basic-Auth). Er läuft über Logs, Fehlermeldungen, Diagnose, Exporte, Audit und den Anfrage-Cache. URLs im Log erscheinen mit `***` an der Stelle des Schlüsselparameters. Kanarien-Test (J.1) prüft jede Ausgabe.
* **Export:** JSON ohne Geheimnisse. Er enthält `secretsRequired: [{slot, hinweis}]`. Der Import legt die Quelle **deaktiviert** an, **ohne** Netzfreigaben, und fragt Geheimnisse einzeln ab.
* **Schlüssel im URL-Parameter** (z. B. TMDB, manche Anbieter): erlaubt. Der Editor zeigt eine Warnung (höheres Offenlegungsrisiko: Proxy-Logs, Browser-Verlauf des Anbieters) und empfiehlt Header-Authentifizierung.
* **Verschlüsselung auf Datenträgerebene ist kein Ersatz** für die Netzwerkgrenze (G.1).

### F.3 Geschützter HTTP-Client (für **alle** Quellen und die Poster-Abfrage)

| Schutz | Umsetzung |
|---|---|
| Schemata | nur `http` und `https`. Benutzerinfo in der URL (`user:pass@`) wird abgelehnt (dafür gibt es Basic-Auth-Feld) |
| Freigabe | je Quelle: `allow_external` (öffentliche Ziele) und `allow_private` (lokale/private Ziele) **ausdrücklich**, dazu die freigegebenen Hostnamen. Ändert sich der Host in der URL, entfällt die Freigabe |
| Tatsächliches Ziel | Hostname wird aufgelöst, **jede** Adresse geprüft (Loopback, `10/8`, `172.16/12`, `192.168/16`, Link-Local inkl. `169.254.169.254`, `100.64/10`, IPv6 `::1`/`fc00::/7`/`fe80::/10`, IPv4-in-IPv6, Multicast, reserviert). Die Verbindung geht **an die geprüfte Adresse** (kein zweites Auflösen, Schutz gegen DNS-Rebinding), TLS-Name bleibt der Hostname |
| Weiterleitungen | manuell, höchstens 3 Sprünge, jedes Ziel wird **neu** geprüft und muss freigegeben sein. Zugangsdaten gehen **nie** an einen anderen Ursprung |
| Proxy-Umgebung | wird ignoriert (kein `HTTP_PROXY`) |
| Zeit und Größe | Verbindung 5 s, Gesamtfrist 20 s (einstellbar bis hart 60 s), Antwort höchstens 2 MB (hart 10 MB), `Accept-Encoding: identity`, Seitenzahl-Obergrenze, Datensatz-Obergrenze |
| Rate und Cache | Mindestabstand je Host (Vorgabe 1 s), eine laufende Anfrage je Quelle, Trennung nach Fehlerzahl, SQLite-Cache mit TTL je Quelle. Der Schlüssel des Caches enthält **keine** Geheimnisse |
| Transparenz | Je Quelle ein **Zugriffsprofil**: Hosts, letzter Zugriff, Anzahl Anfragen, Fehler, „Zugangsdaten konfiguriert: ja/nein“, Freigaben, Rate, Cache. Test und Vorschau zeigen vorher „Es wird jetzt auf `host` zugegriffen“ |

Sonarr nutzt denselben Client mit fester Freigabe für die konfigurierte Adresse, ohne Schlüssel an Weiterleitungsziele.

### F.4 Anzeige fremder Werte
Rendert React als Text, kein `dangerouslySetInnerHTML`. Aus HTML-Quellen wird Text extrahiert. Steuerzeichen und überlange Werte werden gekürzt. Links nur mit `http`/`https`, `rel="noopener noreferrer"`. Strenge `Content-Security-Policy` (`default-src 'self'`, `img-src 'self'`, `frame-ancestors 'none'`), `X-Content-Type-Options`, `Referrer-Policy`.

### F.5 Deklarativer Quellen-Editor (Schema Version 1)

```json
{ "schemaVersion": 1, "name": "", "description": "", "languages": ["de"],
  "request":    { "method": "GET|POST", "url": "https://…/{{series.title}}",
                  "query":   [{"name": "q", "value": "{{series.title}}"}],
                  "headers": [{"name": "Accept", "value": "application/json"}],
                  "body":    {"type": "none|json|form|text", "content": ""} },
  "auth":       { "type": "none|header|bearer|basic|query", "name": "X-Api-Key" },
  "pagination": { "type": "none|page|offset|next-url|link-header|cursor",
                  "param": "page", "start": 1, "sizeParam": "limit", "size": 50,
                  "nextPath": "$.next", "maxPages": 5 },
  "parse":      { "format": "json|xml|html", "records": "$.results[*] | //item | css:table.episodes tr" },
  "mapping":    [{ "field": "episode.title", "language": "de", "from": "$.name | css:td.title@text",
                   "transform": ["trim"] }],
  "match":      { "by": ["seasonEpisode", "airDate", "titleSimilarity"] },
  "limits":     { "timeoutSeconds": 20, "maxBytes": 2000000, "minIntervalSeconds": 1 },
  "network":    { "allowExternal": false, "allowPrivate": false, "grantedHosts": [] } }
```

* **Keine Programmierung:** Platzhalter nur aus fester Liste (`series.title`, `series.year`, `series.tvdbId`, `season`, `episode`, `airDate`, `language`). Keine Ausdrücke. Einsetzung kodiert je nach Ort (URL, JSON, Formular). Unbekannte Schlüssel im Schema werden **abgelehnt**.
* **Methoden:** `GET` und `POST` (Body nur bei `POST`). Weitere nur auf Wunsch.
* **Parser:** JSON (Pfad-Teilmenge `$.a.b[0]`, `[*]`), XML (Pfad-Teilmenge, Namensräume, DOCTYPE abgelehnt), **statisches** HTML (CSS-Selektoren mit `@text`/`@href`/`@attr`, begrenzte Trefferzahl). JavaScript-Seiten und Browser-Automation sind **nicht** Teil von Version 1.
* **Ablauf im Editor:** Schritt 1 Beschreibung und Sprachen · 2 Anfrage und Authentifizierung · 3 Paginierung und Parser · 4 Feldzuordnung mit Live-Auswahlhilfe · 5 **Verbindungstest** (zeigt nur Status, Dauer, Größe, redigierte Kopfzeilen) · 6 **Vorschau** (erste Datensätze, zugeordnete Felder, Warnungen) · 7 Speichern (deaktiviert, ohne Freigaben). Test und Vorschau speichern nichts und lösen keine externe Schreibaktion aus.
* Aktivieren, Deaktivieren, Bearbeiten, Löschen. Löschen entfernt Definition, Geheimnisse und Freigaben. Gespeicherte Kandidaten bleiben mit Quellenvermerk erhalten und werden als „Quelle gelöscht“ markiert.

---

## G. Aktionen, Netzgrenze, Einstellungen

### G.1 Netzwerkgrenze (bewusste Entscheidung des Nutzers)
Metadatarr hat **kein Login**. **Jedes Gerät mit Netzwerkzugriff erreicht die Oberfläche ohne Anmeldung.** Heute sind das: der Host-Port `8083` (im ganzen LAN, siehe `docker-compose.snippet.yml`) und der Traefik-Host `tba.grothu.net` (Middleware `internal@file`). Ich ändere diese Konfiguration nicht. Folgen, die damit verbunden bleiben:

* Wer die Oberfläche erreicht, kann eigene Quellen anlegen, also **ausgehende Anfragen** des Containers auslösen. Deshalb sind Freigaben je Quelle, Ziel-Prüfung und Limits Pflicht (F.3).
* Wer die Oberfläche erreicht, kann mit den hinterlegten TVDB-Zugangsdaten **Tickets einreichen**, sobald das Aktions-Plugin aktiv ist (heute schon so). Vorschau und Bestätigung schützen vor Versehen, **nicht** vor einem Nutzer im Netz.
* **CSRF-Schutz** hindert fremde Webseiten daran, im Browser des Nutzers Aktionen auszulösen. Er ist **keine Authentifizierung**. Ebenso die `Origin`-/`Host`-Prüfung. Beides ersetzt kein Login.
* Verschlüsselung der gespeicherten Geheimnisse schützt die Datei auf dem Datenträger. Sie ersetzt die Netzwerkgrenze nicht.
* Metadatarr gehört **nicht** ins öffentliche Internet.

### G.2 Externe Schreibaktionen: Vorschau, Bestätigung, Doppelschutz
1. `POST /actions/{plugin}/preview` erzeugt eine Vorschau (genauer Inhalt, Ziel, betroffene Episoden) und einen `preview_hash`. **Keine** externe Wirkung.
2. `POST /actions/{plugin}/execute` mit `{previewId, confirm: true, idempotencyKey}` führt nur aus, wenn: Plugin aktiv, Vorschau unverändert und nicht älter als 15 Minuten, Schlüssel noch nicht verwendet.
3. **Zustandsfolge in `action_execution`:** `previewed → confirmed → executing → succeeded | failed | unknown`. `executing` wird **vor** dem externen Aufruf geschrieben. Bleibt der Prozess dort hängen, zeigt die Oberfläche „Ergebnis unklar, bitte bei TVDB prüfen“ und wiederholt **nie** automatisch. Eine Wiederholung braucht eine neue Bestätigung mit Warnung.
4. **Keine** Hintergrund-, Nachtlauf- oder Sammelaktion schreibt extern. Der Nachtlauf liest nur (Suche, TVDB-Status).
5. Protokoll: Ergebnis, Zeit, betroffene Episode/Ticket, externe ID. **Nie** Passwörter, Tokens, Cookies oder vollständige Anfragekörper. Zwischenspeicher und Logs laufen durch den Redaktor.

### G.3 TVDB-Ticket (Aktions-Plugin)
Übernimmt `tvdb.py` und die Teile von `ticket.py` (Einreichen, Statusabfrage). Die Ticketvorlage selbst (Betreff `[GERMAN|ENGLISH] Serie - Season N`, je Folge `#Nummer`, Titel, Beschreibung, Serienlink) ist **lokale** Funktion und nicht Teil des Plugins. Die Vorschau zeigt Betreff und Inhalt **genau so, wie er gesendet wird**. Der vorhandene Schutz (`SUBMIT_LOCK`, Prüfung `tvdb_no`) bleibt zusätzlich. Fällt die Automation aus (Anmeldung, geänderte Seite, Plugin aus), zeigt die Oberfläche den Grund und die **manuelle Vorlage**.

### G.4 TVDB-Übersetzung (Aktions-Plugin, getrennt)
* **Offizielle Schnittstelle:** keine (A.7). **Weboberfläche:** Automatisierung wäre ein Zugriff „auf anderem Weg als über die bereitgestellte Schnittstelle“ (TVDB-Bedingungen §5) und hängt an Anmeldeschutz und Seitenaufbau. **Ich implementiere sie nicht.**
* **Was das Plugin stattdessen tut („manuell unterstützt“):** zeigt je Episode Sprache, Serie, Staffel, Folge, Titel und Beschreibung **zum Kopieren**, verlinkt zur Serienseite `https://thetvdb.com/?tab=series&id=<tvdbId>` (vorhanden und bereits im Gebrauch) und lässt Sie eine **manuelle Erledigung** bestätigen (Protokoll, Doppelschutz).
* **Direkte Episodenlinks:** Sonarr liefert Episoden-IDs von TVDB, aber das Muster einer **Bearbeitungsseite** kann ich ohne Anmeldung **nicht verifizieren**. Ein Direktlink wird erst eingebaut, wenn Sie ein Beispiel aus Ihrem angemeldeten Browser bestätigen (Link-Vorlage als Einstellung). Bis dahin: Serienseite.

### G.5 Einstellungen und Diagnose (erste Stufe: schreibgeschützt)
Feste Ansicht mit drei klar getrennten Blöcken:

| Block | Inhalt |
|---|---|
| **Wirksame Konfiguration (schreibgeschützt)** | Je Eintrag: Name, wirksamer Wert (Geheimnisse nur „konfiguriert“), Herkunft (`Umgebung`/`Standard`/`Oberfläche`), Beschreibung, Bearbeitbarkeit. Sonarr (Adresse ohne Benutzerinfo, Verbindungsstatus, Version, Anzahl Serien), Datenbank (Pfad, Größe, Schema-Version, Zeilenzahlen, letzte Sicherung), Laufzeit (Python, Zeit, Zeitzone, Nachtlauf, Uptime), aktive Quellen und Aktionen mit Status, Zielsprachen, Regeln |
| **In der Oberfläche bearbeitbar** (eigene Bereiche, nicht in Einstellungen) | Zielsprachen (global, je Serie), Platzhalterregeln (sofern nicht per Umgebung gesetzt), eigene Quellen, Plugin-Schalter |
| **Nur über Umgebung/Container änderbar** | `SONARR_URL`, `SONARR_API_KEY`, `SONARR_PUBLIC_URL`, `PORT`, `DB_PATH`, alle Geheimnisse, `TZ`, Pausen und Limits der eingebauten Quellen, `NIGHTLY_*`, `SHOW_POSTERS`, `METADATARR_SECRET_KEY`. Hinweis „Compose-Datei ändern und neu starten“ |

Die Konfigurationsliste kommt aus **einem** Register im Backend (`CONFIG_SECTIONS` wird dort aufgehen). Eine spätere Bearbeitung ergänzt Schreibpfade. Heute ändert die Oberfläche **keine** Umgebungsvariable.

---

## H. Oberfläche und Marke

### H.1 Bereiche (Routen im Frontend)
Übersicht · Alle Serien · Serie/Staffel/Folge (Details mit Feldstatus je Zielsprache, Kandidaten, Belege) · Fehlende Metadaten · Quellen und Quellen-Editor · Zielsprachen · Tickets (Vorlagen und Status) · TVDB-Aktionen · Einstellungen · Diagnose. Mobil: Seitenleiste als Menü, Tabellen als Karten. Desktop: Seitenleiste fest, Tabellen. Alle Texte Deutsch.

Zustände überall: Laden, leer, Fehler (mit Wiederholen), Erfolg. Tastaturbedienung, sichtbarer Fokus, `prefers-reduced-motion`, Hell/Dunkel/System (Wahl im Browser gespeichert).

**Poster** erscheinen in Serienraster, Übersicht und Serienkopf. Ohne Poster oder bei Fehler: Ersatzfläche mit Initialen und Symbol, bei Fehlerursache mit Hinweis (Tooltip und Diagnose). Navigation und Bedienung hängen nie davon ab.

### H.2 Design-Tokens aus den SVGs
Die Rollen folgen den SVG-Varianten (Hell-Icon: Kachel dunkel, Klammern warm, Quadrat blau. Dunkel-Icon: Kachel hell, Klammern dunkel, Quadrat akzent). Kontraste berechnet nach WCAG 2.x:

| Token | Hell | Dunkel | Herkunft | Kontrast (Beispiel) |
|---|---|---|---|---|
| `--ink` (Text, Primärfläche) | `#163653` | `#E8EDF7` | Kachel hell / Kachel dunkel | `#163653` auf `#F4F6FB`: **11,5:1** · `#E8EDF7` auf `#14243A`: **13,3:1** |
| `--on-ink` (Text auf Primärfläche) | `#FFF9EF` | `#14243A` | Klammern | `#FFF9EF` auf `#163653`: **11,9:1** · `#14243A` auf `#8BA1FF`: **6,4:1** |
| `--bg` | `#F4F6FB` | `#14243A` | Kachelverlauf dunkel / Klammern dunkel | `#14243A` auf `#F4F6FB`: **14,5:1** |
| `--surface-raised` | `#FFF9EF` | abgeleitet aus `#14243A` und `#E8EDF7` | Klammern hell / Mischung | – |
| `--surface-sunken` | `#E8EDF7` | abgeleitet | Kachel dunkel / Mischung | `#14243A` auf `#E8EDF7`: **13,3:1** |
| `--primary` (Schaltfläche) | `#163653` mit `--on-ink` | `#8BA1FF` mit `#14243A` | Kachel / Akzent | siehe oben |
| `--accent` (nur **nicht-Text**: Fokusring, aktive Markierung, Symbole) | `#0786D3` | `#8BA1FF` | Quadrat | `#0786D3` auf `#F4F6FB`: **3,6:1** (≥ 3:1 für Bedienelemente) · `#8BA1FF` auf `#14243A`: **6,4:1** |

**Wichtige Folge:** `#0786D3` ist als **Text** auf hellen Flächen **nicht** AA-tauglich (3,6:1, Normaltext verlangt 4,5:1). Auch heller Text auf `#0786D3` reicht nicht (3,7:1). Deshalb: Primärschaltflächen und Links im hellen Thema in `#163653` (Links unterstrichen), `#0786D3` nur für Fokusring, aktive Markierung und Symbole. Das Dunkelthema nutzt `#8BA1FF` auch für Linktext.
**Abgeleitet (nicht in den SVGs, wird als solches gekennzeichnet):** Rahmenlinien, Hover-Zustände, die dunklen Flächenstufen, die Funktionsfarben für Status (E.6.3). Keine dekorativen Verläufe. (Der Verlauf in den SVG-Kacheln ist praktisch einfarbig und wird nicht in die UI übernommen.)

---

## I. Umsetzungsphasen (intern abnehmbar, kein Zwischen-Rollout)

Jede Etappe wird mit Tests abgenommen und arbeitet auf die **einzige** React-Oberfläche hin. In Betrieb geht nur der Endstand (Phase 7). Die alte HTML-Oberfläche wird nicht separat neu ausgerollt.

| Phase | Inhalt | Abnahme |
|---|---|---|
| **0 Freigabe und Prüfung** | Entscheidungen E.1–E.6, `ARBEITSANWEISUNGEN.md` anpassen (Name, Abhängigkeiten, Ordner), `scripts/db_inspect.py` und `scripts/sonarr_probe.py` (beide nur lesend). Sie führen die Skripte gegen Ihre echte Instanz aus und geben die Ausgabe zurück: Antwortform von `images`/`overview`/`originalLanguage`, Statuscodes der Bildrouten, URL-Base, Schema und Zeilenzahlen der Datenbank | Ihre Ausgaben (ohne Geheimnisse) liegen vor |
| **1 Fundament** | Migrationsrahmen mit Backup, geschützter HTTP-Client, SecretStore, Redaktor, Audit, Registries, JSON-Router mit CSRF/Host/Origin, Body-Limit | Backendtests grün (J) |
| **2 Sonarr und Fehlt-Erkennung** | Sonarr-Client für **alle** Serien, Staffeln und Folgen mit `overview`, Regeln, Zielsprachen, Poster-Dienst (URL-Base, Fehlerursachen, Bytegrenze), lesende API | Tests für Poster, Regeln, Sprachen |
| **3 Bestand als Plugins** | Mediathek, TMDB, fernsehserien.de, DeepL als Quellen. `candidate_value` mit Backfill. Tickets-API. TVDB-Ticket als Aktion mit Vorschau/Bestätigung/Doppelschutz. Nachtlauf | Migrationstest, Aktionstests mit Attrappe |
| **4 Frontend-Grundgerüst** | Vite-Projekt, Tokens, Marke, Layout, Übersicht, Serien, Detail, Fehlende Metadaten, Tickets, Einstellungen, Diagnose | Typprüfung, Frontendtests (lokal bei Ihnen), Sichtprüfung |
| **5 Quellen-Editor** | Deklarative Engine (JSON, XML, HTML), Editor-UI, Test/Vorschau, Import/Export | Parser-, Auth-, Pagination-, Netzwerkschutz-Tests |
| **6 TVDB-Zusatz** | TVDB-Übersetzung (manuell unterstützt), Zielsprachen-UI, TVDB-Lese-Plugin (nur bei Freigabe E.1) | Tests mit Attrappen |
| **7 Cutover und Bereinigung** | Statische Auslieferung, SPA-Fallback, mehrstufiges Dockerfile, Compose prüfen, Entfernen der HTML-Schicht, Löschliste, `PROJEKTSTAND.md` | Gesamtlauf, Konfiguration, Diagnose |

**Dateien für den Docker-Build-Kontext** (mit Phase 7 abschließend aufgeführt): alle `*.py`, `api/`, `core/`, `net/`, `plugins/`, `requirements.txt`, `Dockerfile`, `.dockerignore`, `frontend/` (ohne `node_modules/`, mit `package-lock.json`, `public/brand/*.svg`).

---

## J. Tests und Abnahme

### J.1 Backend (Python, Attrappen, ohne Netz)

| Thema | Prüfung |
|---|---|
| Sonarr lesend | Nur `GET`. Attrappe zeichnet Methoden auf, jeder Nicht-GET lässt den Test scheitern |
| Poster | verfügbar · fehlt in `images` · Sonarr nicht erreichbar · 401/403 · Weiterleitung auf Login (302) · HTML statt Bild · zu groß · URL-Base-Präfix · Poster-Cache-Grenze · Header `X-Poster-Reason` |
| Migration | Altdatenbanken in allen Schemastufen, Backup erzeugt und geprüft, Abbruch bei Backup-Fehler, Rollback mit **altem** Code gegen migrierte Datenbank, Tickets, Status und Vorschläge unverändert |
| Platzhalter/Fehlt | leer, nicht vorhanden, `TBA`/`TBD`/`N/A`, `Folge 1`, `Episode 3`, Regeln je Feld und Sprache, Regel aus Umgebung schreibgeschützt |
| Zielsprachen | global, je Serie überschrieben, Rücksetzen, keine feste Originalsprache |
| Provider-HTTP | JSON, XML (inkl. DOCTYPE-Ablehnung), HTML mit CSS-Selektoren, Pagination (alle Arten), alle Auth-Arten, Fehlerstatus, Zeitlimit, Redirect-Ziele |
| Netzwerkschutz | nicht freigegebener Host · privates/lokales Ziel · Link-Local/Metadaten-Adresse · IPv6/IPv4-in-IPv6 · Weiterleitung auf nicht freigegebenes Ziel · Schlüssel nicht an fremden Ursprung · zu große und zu langsame Antwort · DNS-Wechsel zwischen Prüfung und Verbindung |
| Geheimnisse | **Kanarien-Test:** ein Marker-Geheimnis darf in Logs, Fehlern, Diagnose, Exporten, Audit, Cache und API-Antworten **nirgends** auftauchen. Export enthält keine Geheimnisse, Import fragt sie ab. Falscher oder fehlender Schlüssel |
| Aktionen (nur Attrappen) | Vorschau ohne Wirkung · Ausführung ohne Vorschau abgelehnt · ohne Bestätigung abgelehnt · veränderte Vorschau abgelehnt · CSRF · gleicher Idempotenzschlüssel zweimal · Absturz im Zustand `executing` · Rückfall auf manuelle Vorlage mit Grund · Plugin aus · Vorschau des Quellen-Editors löst nie eine Aktion aus |
| Plugin-Trennung | Eine Klasse in beiden Registries scheitert. Lese-Plugin erreicht keinen Aktionsschlüssel |
| Einstellungen | Alle nicht geheimen Einträge enthalten, Geheimnisse maskiert, Herkunft korrekt |
| Marke | SHA-256 der drei SVGs im Build gleich den Ausgangsdateien |
| Übernommen aus `test_tba.py` | Jede der 69 Prüfungen wird einer neuen Prüfung zugeordnet oder als „entfällt wegen Wegfall der HTML-Seite“ begründet. Zuordnungstabelle liegt dem Abschluss bei. Nichts wird gelöscht, nur um grün zu werden |

### J.2 Frontend und Umgebung (Grenzen dieser Arbeitsumgebung)
* **Hier nicht verfügbar:** Vite, `@vitejs/plugin-react`, Vitest, jsdom, ein Browser, Playwright, Docker-Daemon und Netz. (Im Laufzeitbaum der Plattform liegen React 19, TypeScript und esbuild, aber nicht als Projektabhängigkeit und ohne Vite.)
* **Folge:** Ich schreibe Frontend, Tests und Konfiguration vollständig. **Einen Produktionsbuild, Frontendtests, Browserprüfung (Handy/Desktop) und den Docker-Build kann ich hier nicht ausführen und behaupte sie nicht.** Wenn Typprüfung hier teilweise möglich ist, nenne ich genau, was lief.
* **Ihr Beitrag:** lokal `npm install` (für die Lockdatei) und `npm run build`/`npm test`, `docker compose build`, Sichtprüfung auf Handy und Desktop. Ergebnisse oder Screenshots liefern Sie zurück, ich korrigiere.
* Live-Schreibaktionen bei TVDB nur nach gesonderter ausdrücklicher Freigabe.

---

## K. Löschliste und nachzuziehende Dokumente (für Phase 7)
Von Ihnen im Ordner zu entfernen, **nachdem** Phase 7 abgenommen ist: `ui.py` (und weitere Module, die dann leer sind). `HANDOFF.md` bleibt als Historie. `ARBEITSANWEISUNGEN.md` wird in Phase 0 angepasst (Name, Abhängigkeitsregel, Ordnerregel, Prüfzahlen).
