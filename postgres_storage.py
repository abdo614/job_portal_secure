"""Persistent PostgreSQL backend for the existing encrypted SecureStorage API.

The application stores each logical dataset as an encrypted JSON payload in
PostgreSQL. Encryption remains Fernet-based using the existing ENCRYPTION_KEY.

When STORAGE_BACKEND=postgres, PostgreSQL is authoritative. This backend never
falls back to data/*.enc: a database/configuration failure must be visible at
startup rather than silently serving incomplete local data.
The local encrypted files are never deleted or modified by this backend.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class PostgresEncryptionManager:
    """Drop-in replacement for the file-backed EncryptionManager."""

    def __init__(self, key: bytes, ensure_schema: bool = True):
        self.key = key
        self.cipher = Fernet(key)
        self.database_url = os.environ.get("DATABASE_URL", "").strip()
        self.psycopg = None

        if not self.database_url:
            raise RuntimeError(
                "STORAGE_BACKEND=postgres but DATABASE_URL is not configured"
            )

        try:
            import psycopg
        except Exception as exc:
            raise RuntimeError(
                "STORAGE_BACKEND=postgres requires the psycopg package"
            ) from exc

        self.psycopg = psycopg
        try:
            if ensure_schema:
                self._ensure_schema()
        except Exception as exc:
            raise RuntimeError(
                "STORAGE_BACKEND=postgres could not initialize the PostgreSQL schema"
            ) from exc

        logger.info("🗄️ PostgreSQL storage initialized (authoritative backend)")

    @property
    def using_postgres(self) -> bool:
        return True

    def _connect(self):
        return self.psycopg.connect(self.database_url)

    def _ensure_schema(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS secure_store (
                        collection TEXT PRIMARY KEY,
                        payload BYTEA NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

    def schema_exists(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = 'secure_store'
                        )
                        """
                    )
                    return bool(cur.fetchone()[0])
        except Exception:
            logger.exception("❌ Failed to check PostgreSQL schema")
            return False

    def ensure_schema(self):
        self._ensure_schema()

    def encrypt_data(self, data):
        if isinstance(data, (dict, list)):
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        elif not isinstance(data, str):
            raw = str(data)
        else:
            raw = data
        return self.cipher.encrypt(raw.encode("utf-8"))

    def decrypt_data(self, encrypted_data):
        try:
            raw = self.cipher.decrypt(bytes(encrypted_data)).decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        except Exception:
            logger.exception("❌ PostgreSQL payload decryption failed")
            return None

    @staticmethod
    def _sanitize_collection(filename, data):
        """Keep collection reads resilient to malformed legacy records.

        The application expects jobs to be dictionaries. A malformed record
        must not make /api/stats fail and turn every homepage counter into 0.
        The stored PostgreSQL payload is not modified by this sanitization.
        """
        if filename == "jobs" and isinstance(data, list):
            valid = [item for item in data if isinstance(item, dict)]
            removed = len(data) - len(valid)
            if removed:
                logger.warning(
                    "⚠️ Ignored %s malformed job record(s) while reading jobs",
                    removed,
                )
            return valid
        return data

    def encrypt_file(self, filename, data):
        payload = self.encrypt_data(data)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO secure_store (collection, payload, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (collection) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (filename, payload, datetime.now(timezone.utc)),
                    )
                conn.commit()
            logger.info("✅ تم حفظ المجموعة في PostgreSQL: %s", filename)
            return True
        except Exception:
            logger.exception("❌ فشل حفظ المجموعة في PostgreSQL: %s", filename)
            return False

    def decrypt_file(self, filename):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM secure_store WHERE collection = %s",
                        (filename,),
                    )
                    row = cur.fetchone()
            if not row:
                logger.warning("⚠️ مجموعة PostgreSQL غير موجودة: %s", filename)
                return None
            data = self.decrypt_data(row[0])
            return self._sanitize_collection(filename, data)
        except Exception:
            logger.exception("❌ فشل قراءة المجموعة من PostgreSQL: %s", filename)
            return None

    def list_collections(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT collection, updated_at FROM secure_store ORDER BY collection"
                    )
                    return cur.fetchall()
        except Exception:
            logger.exception("❌ Failed to list PostgreSQL collections")
            return []
