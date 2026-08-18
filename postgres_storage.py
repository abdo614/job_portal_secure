"""Persistent PostgreSQL backend for the existing encrypted SecureStorage API.

The application currently stores each logical dataset as an encrypted JSON file.
This backend keeps the same logical collections and Fernet encryption, but stores
one encrypted payload per collection in PostgreSQL so Render redeploys do not
remove application data.

It intentionally exposes the small interface used by encryption.py:
    encrypt_file(filename, data)
    decrypt_file(filename)

The database URL and encryption key are environment variables only.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class PostgresEncryptionManager:
    """Drop-in replacement for file-backed EncryptionManager."""

    def __init__(self, key: bytes):
        self.key = key
        self.cipher = Fernet(key)
        self.database_url = os.environ.get("DATABASE_URL", "").strip()
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL storage")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgreSQL storage") from exc
        self.psycopg = psycopg
        self._ensure_schema()
        logger.info("🗄️ PostgreSQL storage enabled")

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
                logger.warning("⚠️ PostgreSQL collection not found: %s", filename)
                return None
            return self.decrypt_data(row[0])
        except Exception:
            logger.exception("❌ فشل قراءة المجموعة من PostgreSQL: %s", filename)
            return None

    def list_collections(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT collection, updated_at FROM secure_store ORDER BY collection")
                return cur.fetchall()
