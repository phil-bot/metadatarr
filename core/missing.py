"""Zielsprachen, Platzhalterregeln und Metadatenstatus; alle Schreibvorgaenge bleiben lokal in SQLite."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from copy import deepcopy

from config import DB_PATH
from core.migrations import DB_LOCK


FIELDS = ("series.title", "series.overview", "episode.title", "episode.overview")
DEFAULT_LANGUAGES = ("de", "en")
STATUS_VALUES = ("present", "missing_empty", "missing_absent", "missing_language", "placeholder", "not_checkable", "not_provided")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def _connect(db_path=None):
    con = sqlite3.connect(db_path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def normalize_language(value):
    if not isinstance(value, str) or not _LANGUAGE_RE.fullmatch(value.strip()):
        raise ValueError("Sprachcode muss ein gueltiger BCP-47-artiger Code sein, z. B. de oder en-US.")
    parts = value.strip().split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == 2 and part.isalpha() else part.title() if len(part) == 4 and part.isalpha() else part.lower())
    return "-".join(normalized)


def _language_matches(source, target):
    if not source or not target:
        return False
    return source.casefold().split("-", 1)[0] == target.casefold().split("-", 1)[0]


def _normalize_languages(values, *, allow_empty=False):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("Sprachen muessen als Liste mit hoechstens 20 Eintraegen angegeben werden.")
    result = []
    for value in values:
        language = normalize_language(value)
        if language.casefold() not in {item.casefold() for item in result}:
            result.append(language)
    if not result and not allow_empty:
        raise ValueError("Mindestens eine Zielsprache ist erforderlich.")
    return result


def get_target_languages(series_id=None, db_path=None):
    """Liefert globale oder je Serie wirksame Zielsprachen."""
    with DB_LOCK:
        con = _connect(db_path)
        try:
            if series_id is not None:
                row = con.execute("SELECT target_languages_json FROM series_setting WHERE series_id=?", (int(series_id),)).fetchone()
                if row and row[0] is not None:
                    try:
                        return _normalize_languages(json.loads(row[0]), allow_empty=True)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            row = con.execute("SELECT value_json FROM app_setting WHERE key='target_languages'").fetchone()
            if row:
                try:
                    return _normalize_languages(json.loads(row[0]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            return list(DEFAULT_LANGUAGES)
        finally:
            con.close()


def has_series_override(series_id, db_path=None):
    with DB_LOCK:
        con = _connect(db_path)
        try:
            row = con.execute("SELECT target_languages_json FROM series_setting WHERE series_id=?", (int(series_id),)).fetchone()
            return bool(row and row[0] is not None)
        finally:
            con.close()


def set_target_languages(values, series_id=None, db_path=None):
    """Speichert nur in Metadatarr; values=None setzt eine Serienausnahme zurueck."""
    now = time.time()
    with DB_LOCK:
        con = _connect(db_path)
        try:
            with con:
                if series_id is None:
                    languages = _normalize_languages(values)
                    con.execute("INSERT INTO app_setting(key,value_json,updated_at) VALUES('target_languages',?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                                (json.dumps(languages, ensure_ascii=False), now))
                    return languages
                sid = int(series_id)
                if values is None:
                    con.execute("DELETE FROM series_setting WHERE series_id=?", (sid,))
                    row = con.execute("SELECT value_json FROM app_setting WHERE key='target_languages'").fetchone()
                    if row:
                        try:
                            return _normalize_languages(json.loads(row[0]))
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass
                    return list(DEFAULT_LANGUAGES)
                languages = _normalize_languages(values, allow_empty=True)
                con.execute("INSERT INTO series_setting(series_id,target_languages_json,updated_at) VALUES(?,?,?) "
                            "ON CONFLICT(series_id) DO UPDATE SET target_languages_json=excluded.target_languages_json,updated_at=excluded.updated_at",
                            (sid, json.dumps(languages, ensure_ascii=False), now))
                return languages
        finally:
            con.close()


def list_rules(db_path=None):
    with DB_LOCK:
        con = _connect(db_path)
        try:
            rows = con.execute("SELECT id,field_key,language,kind,pattern,enabled,origin FROM placeholder_rule ORDER BY origin,id").fetchall()
            return [{"id": row["id"], "field": row["field_key"], "language": row["language"],
                     "kind": row["kind"], "pattern": row["pattern"], "enabled": bool(row["enabled"]),
                     "origin": row["origin"], "editable": row["origin"] == "ui"} for row in rows]
        finally:
            con.close()


def _validate_rule(rule):
    if not isinstance(rule, dict):
        raise ValueError("Jede Regel muss ein Objekt sein.")
    allowed = {"field", "language", "kind", "pattern", "enabled"}
    if set(rule) - allowed:
        raise ValueError("Die Regel enthaelt unbekannte Felder.")
    field = rule.get("field")
    kind = rule.get("kind")
    pattern = rule.get("pattern", "")
    language = rule.get("language")
    if field not in FIELDS:
        raise ValueError("Unbekanntes Metadatenfeld.")
    if kind not in {"empty", "exact", "regex"}:
        raise ValueError("Regeltyp muss empty, exact oder regex sein.")
    if not isinstance(pattern, str) or len(pattern) > 256:
        raise ValueError("Regelmuster muss Text mit maximal 256 Zeichen sein.")
    if kind == "empty" and pattern:
        raise ValueError("Eine empty-Regel darf kein Muster enthalten.")
    if kind == "exact" and not pattern:
        raise ValueError("Eine exact-Regel braucht ein Muster.")
    if kind == "regex":
        if not pattern:
            raise ValueError("Eine regex-Regel braucht ein Muster.")
        # Konservative Abwehr gegen typische katastrophale Backtracking-Muster.
        quantifiers = re.findall(r"(?<!\\)(?:[*+?]|\{\d+(?:,\d*)?\})", pattern)
        if (len(quantifiers) > 1 or re.search(r"\\|\(\?", pattern) or
                re.search(r"\([^)]*[*+{][^)]*\)[*+{]", pattern) or
                re.search(r"\([^)]*\|[^)]*\)[*+{]", pattern) or
                re.search(r"(?:\.\*|\.\+){2,}", pattern)):
            raise ValueError("Das Regex-Muster enthaelt nicht erlaubte Rueckreferenzen oder verschachtelte Wiederholungen.")
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            raise ValueError("Regex-Muster ist ungueltig.") from None
    if language is not None:
        language = normalize_language(language)
    enabled = rule.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled muss true oder false sein.")
    return {"field": field, "language": language, "kind": kind, "pattern": pattern, "enabled": enabled}


def replace_ui_rules(rules, db_path=None):
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("rules muss eine Liste mit hoechstens 100 Regeln sein.")
    validated = [_validate_rule(rule) for rule in rules]
    now = time.time()
    with DB_LOCK:
        con = _connect(db_path)
        try:
            with con:
                con.execute("DELETE FROM placeholder_rule WHERE origin='ui'")
                for rule in validated:
                    con.execute("INSERT INTO placeholder_rule(id,field_key,language,kind,pattern,enabled,origin,created_at,updated_at) "
                                "VALUES(?,?,?,?,?,?,'ui',?,?)",
                                ("ui:" + uuid.uuid4().hex, rule["field"], rule["language"], rule["kind"],
                                 rule["pattern"], int(rule["enabled"]), now, now))
        finally:
            con.close()
    return list_rules(db_path)


def _rule_matches(rule, value, source_language):
    if not rule["enabled"] or rule["origin"] == "env-seed" and rule["language"] is not None:
        return False
    if rule["language"] and not _language_matches(source_language, rule["language"]):
        return False
    text = value if isinstance(value, str) else ""
    if rule["kind"] == "empty":
        return not text.strip()
    if rule["kind"] == "exact":
        return text.strip().casefold() == rule["pattern"].strip().casefold()
    try:
        return bool(re.search(rule["pattern"], text, re.IGNORECASE))
    except re.error:
        return False


def is_placeholder(field, value, source_language=None, rules=None, db_path=None):
    active_rules = rules if rules is not None else list_rules(db_path)
    return any(rule.get("field", rule.get("field_key")) == field and _rule_matches(rule, value, source_language)
               for rule in active_rules)


def evaluate_value(field, value, target_language, source_language=None, *, provided=True, rules=None, db_path=None):
    target = normalize_language(target_language)
    if not provided:
        return {"status": "not_provided", "reason": "field_not_returned_by_sonarr", "targetLanguage": target,
                "sourceLanguage": source_language or None, "ruleId": None}
    active_rules = rules if rules is not None else list_rules(db_path)
    empty_value = value is None or not str(value).strip()
    if empty_value:
        for rule in active_rules:
            if (rule.get("field", rule.get("field_key")) == field and rule.get("kind") == "empty"
                    and _rule_matches(rule, value, source_language)):
                return {"status": "placeholder", "reason": "empty_placeholder_rule", "targetLanguage": target,
                        "sourceLanguage": source_language or None, "ruleId": rule.get("id")}
        return {"status": "missing_empty", "reason": "empty_value", "targetLanguage": target,
                "sourceLanguage": source_language or None, "ruleId": None}
    for rule in active_rules:
        if rule.get("field", rule.get("field_key")) == field and _rule_matches(rule, value, source_language):
            return {"status": "placeholder", "reason": "placeholder_rule", "targetLanguage": target,
                    "sourceLanguage": source_language or None, "ruleId": rule.get("id")}
    if not source_language:
        return {"status": "not_checkable", "reason": "sonarr_metadata_language_unknown", "targetLanguage": target,
                "sourceLanguage": None, "ruleId": None}
    if not _language_matches(source_language, target):
        return {"status": "missing_language", "reason": "sonarr_text_in_different_language", "targetLanguage": target,
                "sourceLanguage": source_language, "ruleId": None}
    return {"status": "present", "reason": "value_present_in_target_language", "targetLanguage": target,
            "sourceLanguage": source_language, "ruleId": None}


def annotate_inventory(data, source_language=None, db_path=None):
    """Ergaenzt eine Sonarr-Inventur um ehrliche, je Zielsprache berechnete Feldstatus."""
    result = deepcopy(data)
    rules = list_rules(db_path)
    global_targets = get_target_languages(db_path=db_path)
    result["targetLanguages"] = global_targets
    for series in result.get("series", []):
        sid = series.get("id")
        override = has_series_override(sid, db_path) if sid is not None else False
        targets = get_target_languages(sid, db_path) if sid is not None else global_targets
        series["targetLanguages"] = targets
        series["targetLanguagesOverride"] = override
        series["fieldStatus"] = {}
        for field, key in (("series.title", "title"), ("series.overview", "overview")):
            value = series.get(key)
            series["fieldStatus"][field] = {
                lang: evaluate_value(field, value, lang, source_language,
                                     provided=series.get("provided", {}).get(key, key in series), rules=rules, db_path=db_path)
                for lang in targets
            }
        for season in series.get("seasons") or []:
            episodes = season.get("episodes") or []
            counts = {}
            for field, key in (("episode.title", "title"), ("episode.overview", "overview")):
                counts[field] = {}
                for language in targets:
                    statuses = []
                    for episode in episodes:
                        episode.setdefault("fieldStatus", {}).setdefault(field, {})
                        state = evaluate_value(field, episode.get(key), language, source_language,
                                               provided=episode.get("provided", {}).get(key, key in episode), rules=rules, db_path=db_path)
                        episode["fieldStatus"][field][language] = state
                        statuses.append(state["status"])
                    counts[field][language] = {status: statuses.count(status) for status in STATUS_VALUES if status in statuses}
            season["fieldSummary"] = counts
    return result


def persist_metadata_snapshots(data, source_language=None, db_path=None):
    """Speichert nur Hash, Status und Regelreferenz in metadata_snapshot (keine Klartextwerte)."""
    rules = list_rules(db_path)
    observed_at = time.time()
    rows = []
    current_keys = []
    for series in data.get("series", []):
        sid = int(series["id"])
        targets = get_target_languages(sid, db_path)
        fields = [(-1, -1, None, "series.title", "title", series),
                  (-1, -1, None, "series.overview", "overview", series)]
        for season in series.get("seasons") or []:
            for episode in season.get("episodes") or []:
                season_number = int(episode.get("season", season.get("seasonNumber", 0)) or 0)
                episode_number = int(episode.get("episode", 0) or 0)
                fields.append((season_number, episode_number, episode.get("id"),
                               "episode.title", "title", episode))
                fields.append((season_number, episode_number, episode.get("id"),
                               "episode.overview", "overview", episode))
        for season_number, episode_number, episode_id, field, key, owner in fields:
            value = owner.get(key)
            provided = owner.get("provided", {}).get(key, key in owner)
            value_hash = hashlib.sha256(str(value).encode("utf-8")).hexdigest() if provided and value is not None else None
            for language in targets:
                state = evaluate_value(field, value, language, source_language,
                                       provided=provided, rules=rules, db_path=db_path)
                identity = (sid, season_number, episode_number, field, language)
                current_keys.append(identity)
                rows.append((sid, season_number, episode_number, episode_id, field, language,
                             state["status"], state["ruleId"], value_hash, observed_at))
    with DB_LOCK:
        con = _connect(db_path)
        try:
            con.execute("CREATE TEMP TABLE IF NOT EXISTS phase2_current_snapshot ("
                        "series_id INTEGER, season INTEGER, episode INTEGER, field_key TEXT, language TEXT, "
                        "PRIMARY KEY(series_id,season,episode,field_key,language))")
            con.execute("DELETE FROM phase2_current_snapshot")
            with con:
                if current_keys:
                    con.executemany("INSERT INTO phase2_current_snapshot VALUES(?,?,?,?,?)", current_keys)
                    con.executemany(
                        "INSERT INTO metadata_snapshot(series_id,season,episode,sonarr_episode_id,field_key,language,status,rule_id,value_hash,observed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(series_id,season,episode,field_key,language) DO UPDATE SET "
                        "sonarr_episode_id=excluded.sonarr_episode_id,status=excluded.status,rule_id=excluded.rule_id, "
                        "value_hash=excluded.value_hash,observed_at=excluded.observed_at", rows)
                    con.execute("DELETE FROM metadata_snapshot WHERE NOT EXISTS (SELECT 1 FROM phase2_current_snapshot c "
                                "WHERE c.series_id=metadata_snapshot.series_id AND c.season=metadata_snapshot.season "
                                "AND c.episode=metadata_snapshot.episode AND c.field_key=metadata_snapshot.field_key "
                                "AND c.language=metadata_snapshot.language)")
                else:
                    con.execute("DELETE FROM metadata_snapshot")
            return len(rows)
        finally:
            con.close()
