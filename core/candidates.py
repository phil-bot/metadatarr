"""Additive candidate values and explicit provenance for legacy and translated values."""
import time


LEGACY_FIELDS = (("title_de", "episode.title", "de"),
                 ("title_en", "episode.title", "en"),
                 ("description", "episode.overview", "de"))


def _legacy_state(status, marked):
    if status in ("verworfen", "eingereicht"):
        return "verworfen", int(bool(marked))
    return "offen", int(bool(marked) or status == "ausgew\u00e4hlt")


def sync_candidate_states(*, series_id=None, season=None, episode=None, suggestion_id=None):
    """Refresh candidate status/selection from legacy rows without changing their original status."""
    from speicher import db_exec
    filters, args = [], []
    if suggestion_id is not None:
        filters.append("s.id=?")
        args.append(int(suggestion_id))
    if series_id is not None:
        filters.append("s.series_id=?")
        args.append(int(series_id))
    if season is not None:
        filters.append("s.season=?")
        args.append(int(season))
    if episode is not None:
        filters.append("s.episode=?")
        args.append(int(episode))
    where = " AND ".join(filters) if filters else "1=1"
    db_exec(
        "UPDATE candidate_state SET "
        "status=CASE WHEN (SELECT s.status FROM candidate_value c JOIN suggestion s ON s.id=c.suggestion_id "
        "WHERE c.id=candidate_state.candidate_id) IN ('verworfen','eingereicht') THEN 'verworfen' ELSE 'offen' END, "
        "marked=CASE WHEN EXISTS(SELECT 1 FROM candidate_value c JOIN suggestion s ON s.id=c.suggestion_id "
        "WHERE c.id=candidate_state.candidate_id AND (s.marked=1 OR s.status='ausgewählt')) THEN 1 ELSE 0 END, "
        "updated_at=? WHERE candidate_id IN (SELECT c.id FROM candidate_value c JOIN suggestion s "
        "ON s.id=c.suggestion_id WHERE " + where + ")",
        (time.time(), *args),
    )


def sync_suggestion(suggestion_id):
    """Copy non-empty legacy fields into candidate_value without altering the legacy row."""
    from speicher import db_exec
    rows = db_exec("SELECT * FROM suggestion WHERE id=?", (int(suggestion_id),), fetch=True)
    if not rows:
        return 0
    row = rows[0]
    created = row["created"] or time.time()
    state, marked = _legacy_state(row["status"], row["marked"])
    copied = 0
    for column, field_key, language in LEGACY_FIELDS:
        value = (row[column] or "").strip()
        if not value:
            continue
        exists = db_exec(
            "SELECT id FROM candidate_value WHERE suggestion_id=? AND field_key=? AND language=?",
            (int(suggestion_id), field_key, language), fetch=True,
        )
        if exists:
            continue
        candidate_id = db_exec(
            "INSERT OR IGNORE INTO candidate_value(series_id,season,episode,field_key,language,value,source_id,"
            "source_url,retrieved_at,match_level,match_hint,suggestion_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "RETURNING id",
            (row["series_id"], row["season"], row["episode"], field_key, language, value, row["source"],
             row["link"] or "", created, row["level"] or "", "Aus Altbestand.", int(suggestion_id), created),
            fetch=True,
        )
        candidate_id = exists[0]["id"] if exists else (candidate_id[0]["id"] if candidate_id else None)
        if candidate_id is not None:
            db_exec(
                "INSERT OR IGNORE INTO candidate_state(candidate_id,status,marked,created_at,updated_at) VALUES(?,?,?,?,?)",
                (candidate_id, state, marked, created, time.time()),
            )
            copied += 1
    sync_candidate_states(suggestion_id=int(suggestion_id))
    return copied


def record_source_value(*, series_id, season, episode, field_key, language, value,
                        source_id, source_url="", match_level="", match_hint=""):
    """Store an original provider value once and return its candidate id."""
    from speicher import db_exec
    value = (value or "").strip()
    if not value:
        return None
    rows = db_exec(
        "SELECT id FROM candidate_value WHERE series_id=? AND season=? AND episode=? AND field_key=? "
        "AND language=? AND source_id=? AND value=? AND derived_from_id IS NULL ORDER BY id DESC LIMIT 1",
        (int(series_id), int(season), int(episode), field_key, language, source_id, value), fetch=True,
    )
    if rows:
        return rows[0]["id"]
    created = time.time()
    rows = db_exec(
        "INSERT INTO candidate_value(series_id,season,episode,field_key,language,value,source_id,source_url,"
        "retrieved_at,match_level,match_hint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (int(series_id), int(season), int(episode), field_key, language, value, source_id, source_url,
         created, match_level, match_hint, created), fetch=True,
    )
    candidate_id = rows[0]["id"]
    db_exec("INSERT OR IGNORE INTO candidate_state(candidate_id,status,marked,created_at,updated_at) VALUES(?, 'offen',0,?,?)",
            (candidate_id, created, created))
    return candidate_id


def record_translation(origin_candidate_id, value, target_language):
    """Store a DeepL value with an immutable link to the exact original candidate."""
    from speicher import db_exec
    rows = db_exec("SELECT * FROM candidate_value WHERE id=?", (int(origin_candidate_id),), fetch=True)
    if not rows:
        return None
    origin = rows[0]
    translated = (value or "").strip()
    if not translated:
        return None
    existing = db_exec(
        "SELECT id FROM candidate_value WHERE derived_from_id=? AND field_key=? AND language=? AND value=?",
        (origin["id"], origin["field_key"], target_language, translated), fetch=True,
    )
    if existing:
        return existing[0]["id"]
    created = time.time()
    rows = db_exec(
        "INSERT INTO candidate_value(series_id,season,episode,sonarr_episode_id,field_key,language,value,source_id,"
        "source_url,retrieved_at,match_level,match_hint,suggestion_id,superseded_by,created_at,machine_translated,derived_from_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,1,?) RETURNING id",
        (origin["series_id"], origin["season"], origin["episode"], origin["sonarr_episode_id"],
         origin["field_key"], target_language, translated, "deepl", origin["source_url"], created,
         origin["match_level"], "Maschinell uebersetzt; Ursprung Kandidat " + str(origin["id"]),
         created, origin["id"]), fetch=True,
    )
    candidate_id = rows[0]["id"]
    db_exec(
        "UPDATE candidate_value SET superseded_by=? WHERE derived_from_id=? AND field_key=? AND language=? "
        "AND id<>? AND superseded_by IS NULL",
        (candidate_id, origin["id"], origin["field_key"], target_language, candidate_id),
    )
    db_exec("INSERT OR IGNORE INTO candidate_state(candidate_id,status,marked,created_at,updated_at) VALUES(?, 'offen',0,?,?)",
            (candidate_id, created, created))
    return candidate_id


def record_translation_for_suggestion(suggestion_id, source_field, source_language, target_language, value):
    """Resolve a legacy field to its candidate and preserve the translation relationship."""
    sync_suggestion(suggestion_id)
    field_key = "episode.overview" if source_field == "description" else "episode.title"
    from speicher import db_exec
    rows = db_exec(
        "SELECT id FROM candidate_value WHERE suggestion_id=? AND field_key=? AND language=? ORDER BY id DESC LIMIT 1",
        (int(suggestion_id), field_key, source_language), fetch=True,
    )
    if not rows:
        return None
    return record_translation(rows[0]["id"], value, target_language)
