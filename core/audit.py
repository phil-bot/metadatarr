"""Append-only Auditprotokoll ohne Geheimnisse oder vollständige Anfragekörper."""
import json
import secrets
import sqlite3
import time

from config import DB_PATH
from core.migrations import DB_LOCK
from net.redaction import REDACTOR


def record(action, object_kind, object_id="", *, before=None, after=None, request_id=None, db_path=None):
    """Hängt ein redigiertes Audit-Ereignis an und gibt seine lokale ID zurück."""
    safe_before = REDACTOR.redact_value(before) if before is not None else None
    safe_after = REDACTOR.redact_value(after) if after is not None else None
    before_json = json.dumps(safe_before, ensure_ascii=False, separators=(",", ":")) if safe_before is not None else None
    after_json = json.dumps(safe_after, ensure_ascii=False, separators=(",", ":")) if safe_after is not None else None
    safe_action = REDACTOR.redact_text(action)[:120]
    safe_kind = REDACTOR.redact_text(object_kind)[:80]
    safe_id = REDACTOR.redact_text(object_id)[:160]
    rid = REDACTOR.redact_text(request_id or secrets.token_hex(12))[:160]
    with DB_LOCK:
        con = sqlite3.connect(db_path or DB_PATH, timeout=10)
        try:
            with con:
                cur = con.execute(
                    "INSERT INTO audit_event(occurred_at,action,object_kind,object_id,before_json,after_json,request_id) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (time.time(), safe_action, safe_kind, safe_id, before_json, after_json, rid),
                )
                return cur.lastrowid
        finally:
            con.close()
