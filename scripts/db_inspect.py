#!/usr/bin/env python3
"""Metadatarr - db_inspect.py: SQLite-Datenbank NUR LESEND untersuchen.

Aufruf (lokal oder im Container):
    python3 db_inspect.py [--db /pfad/tba.db] [--json] [--immutable]

- Die Datenbank wird per URI mit mode=ro geöffnet und zusätzlich mit PRAGMA query_only=ON gesichert.
  Es gibt keine Schreibanweisung in diesem Skript. Der Pfad kommt aus --db, sonst aus DB_PATH, sonst /data/tba.db.
- Gelistet werden: Dateien (Größe, -wal/-shm), Journalmodus, Tabellen, Indizes, Schema, Zeilenzahlen,
  Statusverteilungen und Zeitspannen. Inhalte von Texten (Titel, Beschreibungen, Ticketkörper, Cache-Antworten)
  werden NICHT ausgegeben, nur Zahlen. Cache-Schlüssel erscheinen nur als Präfix mit Anzahl.
- Der Altbestand hat KEINE Statusverlaufstabelle. Das Skript zeigt deshalb den aktuellen Stand je Status
  und Zeitstempel (created/updated/tvdb_checked), keinen Verlauf.
- --immutable öffnet mit immutable=1 (keine Sperren, keine -shm/-wal-Nutzung). Nur für eine KOPIE der Datenbank
  oder wenn der Ordner schreibgeschützt ist. Bei einer laufenden Live-Datenbank nicht verwenden.
- Hinweis: Eine WAL-Datenbank im Modus mode=ro braucht Lesezugriff auf -wal/-shm. Fehlen die Rechte, meldet
  das Skript den Fehler, statt etwas zu verändern.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import quote

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|apikey|token|passw(or)?d|secret|authorization|cookie)\s*[=:]\s*\S+"),
    re.compile(r"(?i)^([a-z][a-z0-9+.-]*://)[^/@\s]*@"),
)


def redact(text):
    out = str(text)
    out = SECRET_PATTERNS[0].sub(lambda m: m.group(1) + "=***", out)
    out = SECRET_PATTERNS[1].sub(r"\1***@", out)
    return out


def qident(name):
    return '"' + name.replace('"', '""') + '"'


def connect(path, immutable=False):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Datenbankdatei nicht gefunden: {path}")
    uri = "file:" + quote(os.path.abspath(path)) + ("?immutable=1" if immutable else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    return con


def one(con, sql, args=()):
    row = con.execute(sql, args).fetchone()
    return None if row is None else row[0]


def iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def span(con, table, col):
    row = con.execute(f"SELECT MIN({qident(col)}), MAX({qident(col)}), COUNT({qident(col)}) FROM {qident(table)}").fetchone()
    return {"von": iso(row[0]), "bis": iso(row[1]), "gefüllt": row[2]}


def dist(con, table, cols, limit=50):
    cl = ", ".join(qident(c) for c in cols)
    rows = con.execute(
        f"SELECT {cl}, COUNT(*) AS n FROM {qident(table)} GROUP BY {cl} ORDER BY n DESC LIMIT {int(limit)}"
    ).fetchall()
    return [{**{c: r[c] for c in cols}, "n": r["n"]} for r in rows]


def columns(con, table):
    return [{"name": r["name"], "typ": r["type"], "notnull": bool(r["notnull"]), "default": r["dflt_value"], "pk": r["pk"]}
            for r in con.execute(f"PRAGMA table_info({qident(table)})")]


def inspect(path, immutable=False):
    rep = {"datei": {}, "pragma": {}, "tabellen": {}, "indizes": [], "auswertung": {}, "hinweise": []}
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = path + suffix
        rep["datei"][os.path.basename(path) + suffix] = os.path.getsize(p) if os.path.exists(p) else None
    con = connect(path, immutable)
    try:
        for pr in ("journal_mode", "page_size", "page_count", "freelist_count", "user_version", "encoding", "foreign_keys"):
            rep["pragma"][pr] = one(con, f"PRAGMA {pr}")
        rep["pragma"]["quick_check"] = one(con, "PRAGMA quick_check")
        rep["pragma"]["sqlite_version"] = sqlite3.sqlite_version
        master = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        for r in master:
            if r["type"] == "index":
                rep["indizes"].append({"name": r["name"], "tabelle": r["tbl_name"], "sql": redact(r["sql"]) if r["sql"] else "(automatisch)"})
        for r in master:
            if r["type"] != "table":
                continue
            t = r["name"]
            rep["tabellen"][t] = {
                "zeilen": one(con, f"SELECT COUNT(*) FROM {qident(t)}"),
                "spalten": columns(con, t),
                "schema": redact(r["sql"]),
            }
        tabs = rep["tabellen"]
        a = rep["auswertung"]
        erwartet = {"cache", "suggestion", "ticket", "ticket_item"}
        rep["hinweise"].append("Erwartete Tabellen des Altbestands: " + ", ".join(sorted(erwartet))
                               + ("; fehlend: " + ", ".join(sorted(erwartet - set(tabs))) if erwartet - set(tabs) else "; alle vorhanden"))
        unbekannt = sorted(set(tabs) - erwartet)
        if unbekannt:
            rep["hinweise"].append("Weitere Tabellen: " + ", ".join(unbekannt))
        if not any(i["tabelle"] in ("ticket", "ticket_item") for i in rep["indizes"]):
            rep["hinweise"].append("Keine Indizes auf ticket/ticket_item (PROJEKTSTAND Sitzung 11 nannte welche).")
        if str(rep["pragma"]["journal_mode"]).lower() != "wal":
            rep["hinweise"].append(f"Journalmodus ist '{rep['pragma']['journal_mode']}', nicht WAL (PROJEKTSTAND Sitzung 11 nannte WAL).")
        if "suggestion" in tabs:
            a["suggestion_status"] = dist(con, "suggestion", ["status", "marked"]) if any(c["name"] == "marked" for c in tabs["suggestion"]["spalten"]) else dist(con, "suggestion", ["status"])
            a["suggestion_quelle_stufe"] = dist(con, "suggestion", ["source", "level"])
            a["suggestion_angelegt"] = span(con, "suggestion", "created")
            a["suggestion_serien"] = one(con, "SELECT COUNT(DISTINCT series_id) FROM suggestion")
            names = [c["name"] for c in tabs["suggestion"]["spalten"]]
            if "edited" in names:
                a["suggestion_edited_1"] = one(con, "SELECT COUNT(*) FROM suggestion WHERE edited = 1")
            a["suggestion_ohne_titel_de"] = one(con, "SELECT COUNT(*) FROM suggestion WHERE title_de = ''")
            a["suggestion_mit_beschreibung"] = one(con, "SELECT COUNT(*) FROM suggestion WHERE description <> ''")
        if "ticket" in tabs:
            names = [c["name"] for c in tabs["ticket"]["spalten"]]
            a["ticket_status_sprache"] = dist(con, "ticket", ["status", "lang"])
            if "tvdb_status" in names:
                a["ticket_tvdb_status"] = dist(con, "ticket", ["status", "tvdb_status"])
                a["ticket_mit_tvdb_nr"] = one(con, "SELECT COUNT(*) FROM ticket WHERE tvdb_no IS NOT NULL")
                a["ticket_tvdb_checked"] = span(con, "ticket", "tvdb_checked")
            a["ticket_created"] = span(con, "ticket", "created")
            a["ticket_updated"] = span(con, "ticket", "updated")
            a["ticket_serien"] = one(con, "SELECT COUNT(DISTINCT series_id) FROM ticket")
            a["ticket_leerer_body"] = one(con, "SELECT COUNT(*) FROM ticket WHERE body = ''")
        if "ticket_item" in tabs and "ticket" in tabs and "suggestion" in tabs:
            a["verwaiste_ticket_item_ohne_ticket"] = one(con, "SELECT COUNT(*) FROM ticket_item ti LEFT JOIN ticket t ON t.id = ti.ticket_id WHERE t.id IS NULL")
            a["verwaiste_ticket_item_ohne_vorschlag"] = one(con, "SELECT COUNT(*) FROM ticket_item ti LEFT JOIN suggestion s ON s.id = ti.suggestion_id WHERE s.id IS NULL")
            a["ticket_item_je_ticket_max"] = one(con, "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM ticket_item GROUP BY ticket_id)")
        if "cache" in tabs:
            rows = con.execute("SELECT k, fetched, LENGTH(body) AS len FROM cache").fetchall()
            pref = {}
            for r in rows:
                p = r["k"].split(":", 1)[0] if ":" in r["k"] else ("(ohne Präfix)" if r["k"] != "nightly" else "nightly")
                e = pref.setdefault(p, {"n": 0, "bytes": 0})
                e["n"] += 1
                e["bytes"] += r["len"] or 0
            a["cache_nach_praefix"] = pref
            a["cache_geholt"] = span(con, "cache", "fetched")
            a["hinweis_cache"] = "Cache-Schlüssel und -Inhalte werden nicht ausgegeben."
        a["hinweis_verlauf"] = ("Es gibt keine Statusverlaufstabelle. Gezeigt wird der aktuelle Stand je Status "
                                "plus Zeitstempel, kein Verlauf.")
    finally:
        con.close()
    return rep


def render(rep):
    L = []
    add = L.append
    add("== Dateien (Bytes) ==")
    for k, v in rep["datei"].items():
        add(f"  {k}: {'nicht vorhanden' if v is None else v}")
    add("== PRAGMA ==")
    for k, v in rep["pragma"].items():
        add(f"  {k}: {v}")
    add("== Tabellen ==")
    for t, info in rep["tabellen"].items():
        add(f"-- {t}: {info['zeilen']} Zeilen")
        add("   Spalten: " + ", ".join(f"{c['name']} {c['typ']}" for c in info["spalten"]))
        for line in info["schema"].splitlines():
            add("   | " + line)
    add("== Indizes ==")
    if not rep["indizes"]:
        add("  (keine)")
    for i in rep["indizes"]:
        add(f"  {i['name']} auf {i['tabelle']}: {i['sql']}")
    add("== Auswertung ==")
    for k, v in rep["auswertung"].items():
        add(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    add("== Hinweise ==")
    for h in rep["hinweise"]:
        add("  - " + h)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Metadatarr: SQLite nur lesend untersuchen.")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "/data/tba.db"), help="Pfad der Datenbank (Standard: DB_PATH oder /data/tba.db)")
    ap.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    ap.add_argument("--immutable", action="store_true", help="nur für eine Kopie: immutable=1 statt mode=ro")
    args = ap.parse_args(argv)
    try:
        rep = inspect(args.db, args.immutable)
    except (sqlite3.Error, OSError) as exc:
        print(redact(f"Fehler: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 2
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
