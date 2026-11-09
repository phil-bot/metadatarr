"""AES-GCM-Speicher für Zugangsdaten; ohne METADATARR_SECRET_KEY bleibt er gesperrt."""
import hashlib
import os
import sqlite3
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.migrations import DB_LOCK
from .redaction import REDACTOR


class SecretStoreError(RuntimeError):
    """Sicherer, geheimnisfreier Fehler des SecretStore."""


class SecretStore:
    KEY_ID_PREFIX = "metadatarr-aesgcm-v1-"
    AAD_VERSION = b"metadatarr-secret-v1"

    def __init__(self, db_path=None, key=None):
        from config import DB_PATH
        self.db_path = db_path or DB_PATH
        supplied = os.environ.get("METADATARR_SECRET_KEY", "") if key is None else str(key)
        self._aes = None
        self.key_id = ""
        if supplied:
            raw = supplied.encode("utf-8")
            if len(supplied) < 32 or len(raw) < 32:
                raise SecretStoreError("METADATARR_SECRET_KEY muss mindestens 32 Zeichen lang sein.")
            derived = HKDF(
                algorithm=hashes.SHA256(), length=32,
                salt=b"metadatarr-secret-store-v1", info=b"AES-256-GCM",
            ).derive(raw)
            self._aes = AESGCM(derived)
            self.key_id = self.KEY_ID_PREFIX + hashlib.sha256(derived).hexdigest()[:16]
            REDACTOR.register(supplied)

    @property
    def unlocked(self):
        return self._aes is not None

    def _require_key(self):
        if self._aes is None:
            raise SecretStoreError("SecretStore gesperrt: METADATARR_SECRET_KEY ist nicht gesetzt.")

    @staticmethod
    def _aad(owner_kind, owner_id, slot):
        return b"\0".join((
            SecretStore.AAD_VERSION,
            str(owner_kind).encode("utf-8"),
            str(owner_id).encode("utf-8"),
            str(slot).encode("utf-8"),
        ))

    def set(self, owner_kind, owner_id, slot, value):
        self._require_key()
        owner_kind, owner_id, slot, value = map(str, (owner_kind, owner_id, slot, value))
        if not owner_kind or not owner_id or not slot:
            raise SecretStoreError("Eigentümer und Slot müssen angegeben sein.")
        raw = value.encode("utf-8")
        nonce = os.urandom(12)
        encrypted = self._aes.encrypt(nonce, raw, self._aad(owner_kind, owner_id, slot))
        now = time.time()
        with DB_LOCK:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                with con:
                    con.execute(
                        "INSERT INTO secret (owner_kind, owner_id, slot, ciphertext, nonce, key_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(owner_kind, owner_id, slot) DO UPDATE SET "
                        "ciphertext=excluded.ciphertext, nonce=excluded.nonce, key_id=excluded.key_id, updated_at=excluded.updated_at",
                        (owner_kind, owner_id, slot, encrypted, nonce, self.key_id, now, now),
                    )
            finally:
                con.close()
        REDACTOR.register(value)

    def get(self, owner_kind, owner_id, slot):
        self._require_key()
        with DB_LOCK:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                row = con.execute(
                    "SELECT ciphertext, nonce FROM secret WHERE owner_kind=? AND owner_id=? AND slot=?",
                    (str(owner_kind), str(owner_id), str(slot)),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        try:
            value = self._aes.decrypt(bytes(row[1]), bytes(row[0]), self._aad(owner_kind, owner_id, slot)).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError):
            raise SecretStoreError("Geheimnis kann nicht entschlüsselt werden: Schlüssel falsch oder Datensatz beschädigt.") from None
        REDACTOR.register(value)
        return value

    def configured(self, owner_kind, owner_id, slot):
        with DB_LOCK:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                row = con.execute(
                    "SELECT 1 FROM secret WHERE owner_kind=? AND owner_id=? AND slot=?",
                    (str(owner_kind), str(owner_id), str(slot)),
                ).fetchone()
            finally:
                con.close()
        return row is not None

    def delete(self, owner_kind, owner_id, slot):
        with DB_LOCK:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                with con:
                    con.execute(
                        "DELETE FROM secret WHERE owner_kind=? AND owner_id=? AND slot=?",
                        (str(owner_kind), str(owner_id), str(slot)),
                    )
            finally:
                con.close()

    def register_existing(self):
        """Entschlüsselt alle gespeicherten Einträge nur in den zentralen Redaktor hinein."""
        self._require_key()
        with DB_LOCK:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                rows = con.execute(
                    "SELECT owner_kind, owner_id, slot, ciphertext, nonce FROM secret"
                ).fetchall()
            finally:
                con.close()
        for owner_kind, owner_id, slot, ciphertext, nonce in rows:
            try:
                value = self._aes.decrypt(bytes(nonce), bytes(ciphertext), self._aad(owner_kind, owner_id, slot))
                REDACTOR.register(value.decode("utf-8"))
            except (InvalidTag, UnicodeDecodeError):
                raise SecretStoreError("SecretStore enthält einen nicht entschlüsselbaren Datensatz.") from None

    def scoped(self, owner_kind, owner_id):
        return SecretScope(self, owner_kind, owner_id)


class SecretScope:
    """Capability-Ansicht eines einzigen Eigentümers; enthält keinen Schreib-/DB-Zugriff."""

    def __init__(self, store, owner_kind, owner_id):
        self._store = store
        self._owner_kind = str(owner_kind)
        self._owner_id = str(owner_id)

    def get(self, slot):
        return self._store.get(self._owner_kind, self._owner_id, slot)

    def configured(self, slot):
        return self._store.configured(self._owner_kind, self._owner_id, slot)
