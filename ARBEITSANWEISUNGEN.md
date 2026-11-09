# Arbeitsanweisungen für den KI-Agenten

Gilt für jede Sitzung in diesem Projektordner. Die Projektdaten stehen in `PROJEKTSTAND.md`, der Ausgangspunkt in `HANDOFF.md`.

---

## 1. Sitzungsstart (immer in dieser Reihenfolge)

1. Ordnerinhalt neu prüfen (`list /mnt/data`). Nichts aus dem Gedächtnis übernehmen.
2. Lesen: `ARBEITSANWEISUNGEN.md`, dann `PROJEKTSTAND.md`, dann `HANDOFF.md`. Danach die Projektdateien prüfen. Stand 09.11.2026: Alle Projektdateien liegen flach im Ordner-Root (u. a. `app.py`, `Dockerfile`); Unterordner `api/`, `core/`, `net/`, `plugins/`, `tests/`, `scripts/`, `frontend/`.
3. Abschnitt „Nächster Schritt“ in `PROJEKTSTAND.md` prüfen. Wenn der Nutzer etwas anderes will, Abweichung nennen.
4. Dateiliste auf Dubletten und fehlende Dateien prüfen. Gefundenes sofort melden.
5. Die Sitzungsnummer im Änderungslog um eins erhöhen, sobald gearbeitet wird.

## 2. Dateiregeln (verbindlich)

- **Keine Suffix-Dateien.** Niemals Dateien wie `Name (2).md`, `Name_neu.md`, `Name_v2.md`, `Name_final.md` oder `Name copy.md` anlegen.
- **Vor jedem Anlegen prüfen**, ob der Name schon existiert (`list`). Existiert er, wird die Datei geändert, nicht daneben neu angelegt.
- **Ändern:** Markdown- und Code-Dateien mit `edit` (gezielte Ersetzung). Vorher mit `read` lesen. `write` nur für eine bewusste Vollersetzung.
- **Löschen und Umbenennen:** Dateien in `/mnt/data/` werden **nicht** mit `rm` oder `mv` aus bash gelöscht oder umbenannt. Dubletten meldet der Agent dem Nutzer, der sie im Ordner entfernt.
- **Arbeitskopien** (Zwischenstände, Tests, Skripte) liegen unter `/home/user/`, nicht in `/mnt/data/`. Nur finale Dateien kommen nach `/mnt/data/`.
- **Code** wird als **ganze Datei** ausgegeben, keine Teiländerungen (Wunsch des Nutzers).
- **Keine Zugangsdaten** (API-Keys, Passwörter) in Dateien schreiben, loggen oder anzeigen.
- **Eindeutige Pfade:** Im Projektordner immer den vollständigen Pfad `/mnt/data/<Dateiname>` nennen. Host-Pfade (Server, z. B. `${DOCKER}/...`) nur verwenden, wenn der Nutzer sie bestätigt hat. Nie raten.

## 2a. Schutz vor „(2)“-Dubletten (Pflicht, Vorfall vom 16.10.2026)

**Vorfall:** Nach dem Anlegen von `PROJEKTSTAND.md` und `ARBEITSANWEISUNGEN.md` erschienen Kopien mit `(2)` im Namen, mit identischem Inhalt und derselben Anhang-ID. Am 17.10.2026 wurden beim Bearbeiten weitere Kopien dieser Art sichtbar (insgesamt 16 Paare). Der Nutzer hat diese Dubletten ausdrücklich nicht gewünscht. Jede `(2)`-Datei muss dem Nutzer mit vollständigem Namen gemeldet werden, sobald sie in der Sicht des Agenten auftaucht. Der Agent darf sie nicht löschen oder umbenennen, weil ihm dafür das Werkzeug fehlt.

**Ursache (Stand 17.10.2026):** Dateien unter `/mnt/data/` werden per `cp` aus bash angelegt, während der Zielname schon existiert. Das System legt dann eine Kopie mit `(2)` an, das Original bleibt. Ein Schreibvorgang per bash in `/mnt/data/` ist deshalb verboten. Auch Bearbeitungen von zwei Dateien in parallelen Aufrufen führen zu verlorenen Änderungen. Bearbeitungen deshalb immer nacheinander und danach prüfen.

**Quelle der Wahrheit:** Der Projektordner, abgefragt mit `list` (folderName „Metadatarr“). Die bash-Sicht unter `/mnt/data/` kann zusätzliche `(2)`-Kopien zeigen. Vor jeder Aussage über Ordnerinhalte `list` ausführen und Abweichungen zur bash-Sicht dem Nutzer nennen.

**Vor jedem Schreibvorgang:**

1. `ls -la /mnt/data` ausführen. Prüfen, ob der Zielname schon existiert, auch als `Name (2).md`.
2. Mit `list` prüfen, ob der Name im Projektordner bereits existiert.
3. **Existiert die Datei:** nur `edit`. Niemals `write` auf eine bestehende Datei, auch nicht „zum Zurücksetzen“ oder „zur Sicherheit“.
4. **Existiert sie nicht:** genau einmal `write`. Danach nicht erneut schreiben, sondern nur noch `edit`.

**Nach jedem Schreibvorgang** (ein Befund zählt nur, wenn `list` ihn im Projektordner zeigt; die bash-Sicht allein zählt nicht):

5. `ls /mnt/data | grep -E ' \(+\)'` ausführen.
6. Taucht eine `(2)`-Datei auf: **nicht** löschen, **nicht** umbenennen, **nicht** überschreiben und **keine** weitere Version anlegen. Den Dateinamen und den letzten Schreibvorgang dem Nutzer melden und auf seine Entscheidung warten.

**Ursache der „(2)“-Dateien (beobachtet 16. und 17.10.2026):** Wird eine Datei unter `/mnt/data/` mit `cp` aus bash angelegt, während ihr Name im Projektordner schon existiert, speichert das System eine Kopie mit „(2)“ im Namen (z. B. `app.py` wird zu `app (2).py`). Das Original bleibt unverändert stehen. Der Agent kann diese Dublette nicht entfernen oder umbenennen, deshalb entsteht sie dauerhaft im Ordner. Ein Schreibvorgang über bash ist deshalb in `/mnt/data/` verboten.

**Pflicht vor jedem Schreiben in `/mnt/data/` (in dieser Reihenfolge):**
1. Arbeitsstand unter `/home/user/work/` fertig machen und prüfen (Syntax, Tests). Nichts wird unfertig in den Projektordner gelegt.
2. Mit `list` prüfen, ob der Zielname im Projektordner schon existiert.
3. **Neue Datei:** einmal mit `write` anlegen, nicht per `cp`.
4. **Bestehende Datei ändern:** nur mit `edit`.
5. **Bestehende Datei vollständig ersetzen** (z. B. neue Fassung eines Moduls): vorher mit `read` lesen, dann mit `write` auf den exakten Namen schreiben. Nicht per `cp`.
6. Nach jedem Schreibvorgang mit `list` kontrollieren, dass keine „(2)“-Datei entstanden ist. Ist eine entstanden: nicht löschen, nicht umbenennen, sondern dem Nutzer mit Namen melden und auf seine Entscheidung warten.
7. Vor dem Schreiben dem Nutzer nennen, welche Dateien neu oder geändert werden. Das gehört zur Änderungsmeldung.

**Verboten:**

- `cp`, `mv` oder `tee` mit einem Ziel unter `/mnt/data/`- Dateinamen mit Klammern, Zahlen-Suffixen oder Wörtern wie `neu`, `v2`, `final`, `copy`, `alt`, `backup` oder einem Datum
- Zwischenstände in `/mnt/data/`. Arbeitsstände liegen unter `/home/user/work/`.
- Mehrfaches `write` auf dieselbe Datei
- Versuche, Dubletten mit `rm`, `mv` oder `cp` aus bash zu bereinigen

**Änderungsmeldung (Pflicht, Nutzer-Wunsch 17.10.2026):** Nach jeder Änderung an Projektdateien nennt der Agent im Chat **alle geänderten Dateien** mit vollständigem Namen, z. B. `app.py`, `quellen.py`, `PROJEKTSTAND.md`, und sagt je Datei in einem Satz, was sich geändert hat. Neue Dateien werden als „neu“ gekennzeichnet. Dateien, die der Nutzer im Docker-Stack ersetzen muss, werden zusätzlich gesondert aufgeführt.

## 3. Arbeitsweise mit dem Nutzer

- Antworten **immer auf Deutsch**.
- **Klärende Fragen vorab.** Mehrere Fragen über das Frageformular stellen, nicht als Liste im Text. Keine Fragen erfinden, die schon in `PROJEKTSTAND.md` beantwortet sind.
- **Vor Code oder Artefakten das Vorhaben vorstellen** und auf Freigabe warten.
- Bei fehlenden Dateien (z. B. `app/`) den Nutzer um Upload bitten, nichts neu erfinden.

## 4. Tracking-Mechanismus

Das Projekt läuft über viele Sitzungen. Der Stand wird nur über `PROJEKTSTAND.md` weitergegeben.

**Am Ende jeder Sitzung, auch bei Abbruch:**

1. Checkboxen aktualisieren: `[x]` nur bei verifiziertem Ergebnis, `🔄` bei begonnener Arbeit, `⛔` bei Blockade (mit Grund).
2. Abschnitt „Nächster Schritt“ neu schreiben: konkret, mit Verantwortlichem (Agent oder Nutzer).
3. Eintrag im Änderungslog ergänzen: Datum, Sitzungsnummer, was getan wurde, was offen ist.
4. Abschnitt 1 (Dateien) und Abschnitt 3 (offene Entscheidungen) bei Änderungen nachziehen.
5. Mit `edit` ändern, nicht neu schreiben. Die Datei bleibt die einzige Quelle.
6. Im Chat eine kurze Zusammenfassung geben: Stand, nächster Schritt, offene Fragen (max. 5 Zeilen).

**Bei langen Aufgaben:** In Etappen arbeiten. Nach jeder Etappe das Log aktualisieren, nicht erst am Ende.

**Abnahme:** Ein Punkt gilt erst als ✅, wenn er geprüft ist. Prüfung heißt: Syntaxcheck, Test gegen Attrappe oder Ausgabe belegt. Was nicht geprüft werden kann (z. B. Docker, weil kein Daemon vorhanden), wird als offen oder ⚠️ markiert, nicht als erledigt.

## 5. Technische Vorgaben

- Python 3. Für den Umbau zu Metadatarr (Sitzung 24, Nutzerauftrag) ist die frühere Regel „nur Standardbibliothek“ gelockert: Gepflegte Abhängigkeiten sind erlaubt, wenn sie begründet sind (z. B. HTML-Parsing für CSS-Selektoren). Jede Abhängigkeit wird mit Grund, Version und Lizenz in `requirements.txt` bzw. `PROJEKTSTAND.md` festgehalten. Die read-only Hilfsskripte unter `scripts/` bleiben bewusst reine Standardbibliothek.
- Web-App **liest nur** aus Sonarr, schreibt nie.
- Rücksicht auf MediathekViewWeb und TMDB: eigener User-Agent, Pausen zwischen Anfragen, Antworten zwischenspeichern, Suche nur auf Knopfdruck pro Serie.
- Keine Logs oder Ausgaben mit Schlüsseln.
- Tests mit Attrappen (Sonarr, Mediathek, TMDB) aus der Standardbibliothek.

## 6. Rechtliches und Quellen

- Mediathek-Beschreibungen sind Texte der Sender. TMDB hat eigene Nutzungsbedingungen.
- Ob TVDB Beschreibungen übernehmen darf, ist ungeklärt. Das entscheidet der Nutzer (Frage F). Der Agent setzt die Option, legt aber keine Entscheidung selbst fest.
- Quellen, deren Nutzungsbedingungen Scraping verbieten, werden nur nach ausdrücklicher Freigabe genutzt.

## 7. Startprompt für eine neue Sitzung

> Du übernimmst das Projekt „Metadatarr“ (vormals „TBA-Tickets für TheTVDB“) im Ordner „Metadatarr“. Lies zuerst `ARBEITSANWEISUNGEN.md`, dann `PROJEKTSTAND.md` und `HANDOFF.md`. Prüfe den Ordnerinhalt neu. Antworte auf Deutsch. Stelle klärende Fragen vorab, stelle Vorhaben vor und warte auf Freigabe, bevor du Code schreibst. Arbeite nach den Dateiregeln (keine Suffix-Dateien, keine Löschung aus bash). Aktualisiere am Ende `PROJEKTSTAND.md`.
