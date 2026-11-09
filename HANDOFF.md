# Projektübergabe: TBA-Tickets (Sonarr → Mediathek/TMDB → TheTVDB-Tickets)

> **Hinweis (Stand 09.11.2026, Sitzung 28):** Diese Übergabe ist teilweise überholt. Das Repository heißt jetzt **Metadatarr**. Die Version `v0.1.0` ist laut Nutzer in GHCR veröffentlicht (`ghcr.io/phil-bot/metadatarr`, Tags `0.1.0` und `latest`), das Compose-Snippet nutzt dieses Image. Maßgeblich ist `PROJEKTSTAND.md`.

Stand: 16.10.2026 (aktualisiert gegenüber 15.10.2026, siehe Abschnitt 12). Dieses Dokument enthält alles, was ein anderer KI-Agent braucht, um nahtlos weiterzuarbeiten.
Es enthält **keine Zugangsdaten**. API-Keys liefert der Nutzer selbst.

## 1. Ziel

Der Nutzer (Phil) pflegt eine Sonarr-Instanz mit vielen deutschen Serien. In TheTVDB (der Metadatenquelle von Sonarr) haben viele deutsche Folgen keinen Titel („TBA“). Er möchte:

1. Sein lokales Sonarr nach deutschen Serien mit TBA-Folgen durchsuchen (erledigt, Schritt 1).
2. Für diese Folgen die korrekten Titel (und Beschreibungen) **auf Deutsch und Englisch** aus Online-Quellen finden (offen, Schritt 2).
3. Daraus **fertige Ticketvorlagen für TheTVDB** erzeugen, die er selbst dort einreicht (offen, Schritt 3).

Das Ganze soll als **kleine Web-App im Docker-Container** im bestehenden „arr“-Compose-Stack neben Sonarr laufen.

## 2. Umgebung des Nutzers

- Docker-Compose-Stack mit Sonarr (`lscr.io/linuxserver/sonarr`), Hostname/Container `sonarr`, Port 8989, vermutlich Sonarr v4.
- Variablen in der `.env`: `${DOCKER}`, `${SHARE}`, `${DOWNLOAD}`, `${PUID}`, `${PGID}`, `${TZ}`.
- Reverse Proxy: **Traefik** mit Labels nach diesem Muster (Router `sonarr`, `certresolver=servercow`, `entrypoints=https`, Middleware `internal@file`). Domain: `grothu.net`.
- **Zugriffsschutz läuft über den Reverse Proxy**, die App selbst braucht kein Login. Zusätzlich `ports: "8083:8080"` (bewusst gesetzt, Stand 16.10.2026). Hinweis: Dieser Port umgeht die Traefik-Middleware `internal@file`, daher muss der Host-Port separat (z. B. Firewall) abgesichert sein.
- Der Nutzer hat einen **TMDB-API-Key** (genannt, nicht übermittelt).
- Starke Technikkenntnisse (Linux, Docker, Self-Hosting).

## 3. Arbeitsweise, die der Nutzer wünscht

- Antworten **immer auf Deutsch**.
- **Klärende Fragen vor der Antwort** stellen; lieber nachfragen, statt lange zu grübeln.
- **Vor Arbeit an Code/Artefakten das Vorhaben vorstellen und auf Rückmeldung warten.**
- Code immer als **ganze Dateien** ausgeben, keine Teiländerungen.
- Wird eine Datei/Code benötigt, auf Upload/Paste warten.

## 4. Entscheidungen des Nutzers (verbindlich)

- Reverse-Proxy-Schutz statt App-Login; Einbau in den arr-Stack.
- **Kein automatisches Einreichen** bei TVDB. Tickets werden nur vorbereitet, der Nutzer reicht sie selbst ein.
- Tickets sollen **Titel plus Beschreibung aus der Mediathek** enthalten (er hat „Beschreibung aus der Mediathek“ gewählt).
- **Zukünftige Folgen werden einbezogen**: Für manche noch nicht gesendete Folgen existieren schon Namen, deshalb nicht nach Sendedatum filtern, sondern nur kennzeichnen („angekündigt“).
- Titel gesucht werden sollen **deutsch und englisch**.
- Technik: **Python, nur Standardbibliothek** (keine Zusatzpakete) wurde vorgeschlagen und nicht beanstandet.
- Auf die Frage „Was fehlt bei TVDB, nur die englische Übersetzung oder auch die deutschen Originaldaten?“ antwortete er „Weiß ich nicht genau“. Das ist **noch ungeklärt** (siehe Abschnitt 8).

## 5. Was bereits gebaut ist (Schritt 1)

Dateien im Projektordner (flach, ohne Unterordner): `app.py`, `Dockerfile`, `docker-compose.snippet.yml`. Der Projektordner-Pfad des Snippets ist `/mnt/data/docker-compose.snippet.yml`. Auf dem Host liegt der Build-Kontext `./tba-tickets` relativ zur Stack-Compose-Datei, die Daten unter `${DOCKER}/tba-tickets/data`.

| Datei | Zweck |
|---|---|
| `app.py` | Web-App (Standardbibliothek, `ThreadingHTTPServer`), liest nur aus Sonarr |
| `Dockerfile` | `python:3.12-slim`, läuft als Nicht-Root-User, Healthcheck auf `/health` |
| `docker-compose.snippet.yml` | Dienst im Stil der Sonarr-Vorlage des Nutzers, Traefik-Labels, `user: ${PUID}:${PGID}` |

Funktionen von `app.py`:

- Holt per Sonarr-API (`/api/v3/series`, `/api/v3/episode?seriesId=`) alle Serien, filtert nach Originalsprache (Feld `originalLanguage.name`, Standard `German`, `*` = alle).
- Findet TBA-Folgen per Regex (Standard: leer, `TBA`, `TBD`, `N/A`, `Episode N`, `Folge N`; über `TBA_REGEX` änderbar). Staffel 0 standardmäßig ausgeschlossen (`INCLUDE_SPECIALS=1` schaltet ein).
- Status je Folge: `gesendet`, `angekündigt` (Sendedatum in der Zukunft), `ohne Datum`.
- Seiten: `/` (Übersicht, `?alle=1` zeigt auch Serien ohne TBA), `/series/<sonarr-id>` (Folgen nach Staffel), `POST /refresh` (Neuladen), `/health`.
- Zwischenspeicher im Speicher (`CACHE_TTL`, Standard 600 s), HTML-Escaping aller Werte, deutsche Oberfläche, Hell/Dunkel nach System.
- Umgebungsvariablen: `SONARR_URL`, `SONARR_API_KEY`, `ORIGINAL_LANGUAGE`, `INCLUDE_SPECIALS`, `TBA_REGEX`, `SONARR_PUBLIC_URL`, `CACHE_TTL`, `PORT`.

**Getestet** (gegen eine selbstgebaute Sonarr-Attrappe): Filter nach Sprache, TBA-Erkennung, angekündigte Folgen, Escaping, Fehlerseite bei falschem Key, Neuladen, 404-Seiten.
**Nicht getestet:** Docker-Build, Betrieb gegen das echte Sonarr (der Nutzer hat die App laufen lassen und den Ausschnitt unten geliefert, es gab keine Fehlermeldung).

## 6. Erste Messwerte aus dem echten Sonarr

43 Serien in der Auswahl (Originalsprache Deutsch), davon 25 mit TBA-Folgen, **4583 TBA-Folgen** insgesamt, davon 12 angekündigt. Größte Fälle:

| Serie | Sender | TBA | Folgen gesamt |
|---|---|---|---|
| Between Tulle and Tears (2016) | VOX | 1598 | 1608 |
| Die Sendung mit der Maus (1971) | Das Erste | 1059 | 1543 |
| Wer weiß denn sowas? (2015) | NDR | 402 | 1477 |
| Notruf Hafenkante (2007) | ZDF | 389 | 514 |
| Ab ins Beet! Die Garten-Soap (2005) | VOX | 335 | 352 |
| Ab in die Ruine (DE) (2011) | VOX | 98 | 99 |
| Einmal Camping, immer Camping (2014) | VOX | 93 | 93 |
| The Bed-Brothers (2015) | VOX | 89 | 90 |
| Switch Reloaded (2007) | ProSieben | 83 | 114 |

## 7. Technische Fakten für Schritt 2 und 3

### TheTVDB-Ticketformat
Laut TVDB-FAQ (`https://support.thetvdb.com/kb/faq.php?id=36`, Stand der Seite: ca. 6 Jahre alt, **vor dem Bau gegenprüfen**):

- Help Topic „Translations“; **ein Ticket pro Staffel** (Specials getrennt).
- Betreff: `[SPRACHE] Serienname - Season #`.
- Text: Serienlink und Serien-ID, dann je Folge in Sendereihenfolge: `#Folgennummer`, `**Titel**`, Beschreibung in einem Codeblock.
- Serien-Link-Muster, das Sonarr selbst nutzt: `https://thetvdb.com/?tab=series&id=<tvdbId>`.

### MediathekViewWeb
- Öffentlicher Dienst mit Such-API; Quellcode: `github.com/mediathekview/mediathekviewweb`. Das README dokumentiert **keine** API, es gibt aber einen öffentlichen Beispiel-Gist und Drittanbieter-Clients.
- Nach meinem Kenntnisstand (**Details gegen die echte Antwort prüfen**): `POST https://mediathekviewweb.de/api/query`, Body als JSON, z. B.
  `{"queries":[{"fields":["topic"],"query":"Notruf Hafenkante"}],"sortBy":"timestamp","sortOrder":"desc","future":true,"offset":0,"size":50}`.
  Treffer unter `result.results[]` mit u. a. `channel`, `topic`, `title`, `description`, `timestamp`, `duration`, `url_website`. Der Gist verwendet vermutlich `Content-Type: text/plain`.
- Alternative: die offizielle Filmliste von MediathekView als Datei (groß, aber stabil).
- Dem Dienst gegenüber **rücksichtsvoll** sein: eigener User-Agent, Pausen zwischen Anfragen, Antworten zwischenspeichern, Suche nur auf Knopfdruck pro Serie, nicht für alle Serien auf einmal.

### Wichtige Einschränkungen (entscheiden über das Design)
1. **Die Mediathek deckt nur öffentlich-rechtliche Sender ab** (ARD, ZDF, 3sat, arte, KiKA, ORF, SRF usw.). Die großen Serien des Nutzers von **VOX und ProSieben sind dort nicht zu finden** (Between Tulle and Tears, Ab ins Beet!, Ab in die Ruine, Einmal Camping, The Bed-Brothers, Switch Reloaded). Nur Maus, Wer weiß denn sowas? und Notruf Hafenkante (zusammen ca. 1850 von 4583 TBA-Folgen) sind grundsätzlich abdeckbar.
2. Die Mediathek enthält nur **aktuell abrufbare** Sendungen; ältere Folgen fehlen.
3. Mediathek-Einträge haben **keine Staffel-/Folgennummern**. Zuordnung nur über Sendedatum (gleicher Tag oder ±1 Tag) plus Titelähnlichkeit; jede Zuordnung braucht eine Sicherheitsstufe (hoch/mittel/niedrig). Standard: nur hoch und mittel in Tickets, der Rest in einen Bericht.
4. **Englische Titel** liefert die Mediathek nicht. Mögliche Quelle: TMDB (`language=en-US`), sonst leer oder manuell. Eine Übersetzung durch die App ist ohne KI-Zugang im Container nicht vorgesehen.
5. **TMDB als Zweitquelle** (deutsch und englisch) deckt auch private Sender und ältere Folgen ab. TMDB- und TVDB-Nummerierung können abweichen, das Matching muss daher über das Sendedatum laufen (TVDB `airDate` ↔ TMDB `air_date`), nicht über Staffel/Folge.
6. **Urheberrecht/Regeln:** Mediathek-Beschreibungen sind Texte der Sender, TMDB-Daten stehen unter eigenen Bedingungen. Ob TVDB das Übernehmen erlaubt, ist **ungeklärt** und Sache des Nutzers. Daher eine Option `--desc`/UI-Schalter vorsehen: voll, gekürzt oder keine Beschreibung.
7. **Große Staffeln:** Staffeln mit hunderten Folgen (z. B. Between Tulle and Tears: 1608 Folgen) passen kaum in ein Ticket. Ein konfigurierbares Chunking (z. B. 50 bis 100 Folgen pro Ticket) einplanen; die Größenregeln von TVDB sind unbekannt.
8. Bei einer Serie, deren Originalsprache Deutsch ist, ist unklar, ob für fehlende **deutsche Originaldaten** überhaupt ein Ticket nötig ist oder ob man sie auf TVDB direkt eintragen kann. Das hat der Nutzer noch nicht geklärt.

## 8. Offene Fragen an den Nutzer

1. **Quelle für Serien außerhalb der Mediathek** (VOX, ProSieben …): TMDB (de-DE und en-US), nur Mediathek und den Rest manuell, oder weitere Quelle prüfen (z. B. fernsehserien.de, mit Hinweis auf deren Nutzungsbedingungen)?
2. **Englische Titel:** aus TMDB (en-US, sonst leer), nur deutsche Tickets, oder ergänzt er Englisch selbst?
3. **Ticketsprache und -art** (hängt an der ungeklärten Frage, ob auf TVDB der deutsche Reiter leer ist): `[GERMAN]`, `[ENGLISH]` oder beides?
4. Wie groß dürfen Tickets sein (Chunking)?

## 9. Plan für Schritt 2 und 3

**Schritt 2: Vorschläge finden**
- SQLite in `/data` (Volume ist im Compose-Snippet bereits eingehängt) für Zwischenspeicher der Treffer und den Status je Folge (offen, ausgewählt, eingereicht, verworfen).
- Button „In Mediathek suchen“ pro Serie: Abfragen nach `topic`, paginiert, mit Pause; Treffer speichern.
- Optional: TMDB-Abfrage pro Serie (Key per Umgebungsvariable `TMDB_API_KEY`), Matching über Sendedatum.
- Matching je TBA-Folge: Datum ±1 Tag plus Titelähnlichkeit (`difflib`), Sicherheitsstufe, Quelle und Link speichern.
- Oberfläche: je TBA-Folge Vorschlag mit Titel, Beschreibung, Sender, Datum, Link, Sicherheit; Auswahl, Bearbeiten, „verwerfen“.

**Schritt 3: Tickets**
- Pro Serie und Staffel (und ggf. Chunk) ein Ticket im TVDB-Format, Ansicht mit Kopieren-Button und Download; getrennt je Sprache.
- Bericht als CSV mit Quelle und Sicherheit zum Nachprüfen vor dem Einreichen.

## 10. Hinweise für den Agenten

- Bei Änderungen an `app.py` immer die **ganze Datei** ausgeben.
- Tests am besten mit einer kleinen Attrappe von Sonarr (und später MediathekViewWeb/TMDB) aus der Standardbibliothek; ein Docker-Daemon stand beim Bau nicht zur Verfügung.
- Nie API-Keys in Seiten ausgeben, nicht loggen.
- Die Web-App darf nur **lesend** auf Sonarr zugreifen.
- Verwandt, aber **nicht Teil dieses Pakets**: ein Bash-Skript `serien_import.sh` für den Sonarr-Import von Serien mit abweichender Folgenreihenfolge (DuckTales). Es hat mit diesem Projekt nichts zu tun.

## 11. Startprompt für den neuen Agenten (zum Einfügen)

> Du übernimmst ein bestehendes Projekt. Lies zuerst `HANDOFF.md` und die Projektdateien `app.py`, `Dockerfile` und `docker-compose.snippet.yml` vollständig. Antworte auf Deutsch. Stelle klärende Fragen, bevor du antwortest, und stelle mir dein Vorhaben vor, bevor du Code schreibst; warte auf meine Rückmeldung. Gib Code immer als ganze Dateien aus. Beginne mit den offenen Fragen aus Abschnitt 8 und baue danach Schritt 2 (Mediathek-/TMDB-Vorschläge) und Schritt 3 (Ticketvorlagen). Beachte die Einschränkungen aus Abschnitt 7.

## 12. Änderungen seit Stand 15.10.2026

- Die Projektdateien liegen flach im Projektordner, ohne Unterordner `app/`.
- Die Datei heißt `Dockerfile` (groß geschrieben).
- Der Port `ports: "8083:8080"` ist bewusst gesetzt. Der Schutz über Traefik gilt weiterhin für den Traefik-Zugang, der Host-Port braucht zusätzlich eine Absicherung.
- Die `.env`-Variable für den Sonarr-Key heißt `SONARR_APIKEY`. Das Compose-Snippet übergibt sie als `SONARR_API_KEY` an die App.
- Der Nutzer hat Schritt 1 (App und Compose-Snippet) als funktionsfähig bestätigt.
- Offen: Host-Pfad der Stack-Compose-Datei.

