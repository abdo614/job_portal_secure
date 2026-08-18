"""Persistent PostgreSQL backend for the existing encrypted SecureStorage API.

Each logical dataset is stored as one encrypted JSON payload in PostgreSQL.
The existing Fernet key remains the encryption key; PostgreSQL is only the
persistent storage layer.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class PostgresEncryptionManager:
    """Drop-in replacement for the file-backed EncryptionManager."""

    def __init__(self, key: bytes, ensure_schema: bool = True):
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
        self._file_cache: dict[str, dict] = {}
        self._file_cache_ttl = 2.0
        self._cache_lock = threading.RLock()

        if ensure_schema:
            self._ensure_schema()

        logger.info("🗄️ PostgreSQL storage initialized")

    def _connect(self):
        return self.psycopg.connect(self.database_url, connect_timeout=10)

    def _get_cached_file(self, filename):
        now = time.time()
        with self._cache_lock:
            entry = self._file_cache.get(filename)
            if entry and now - entry["timestamp"] < self._file_cache_ttl:
                return entry["data"]
            if entry:
                self._file_cache.pop(filename, None)
        return None

    def _set_cached_file(self, filename, data):
        with self._cache_lock:
            self._file_cache[filename] = {
                "data": data,
                "timestamp": time.time(),
            }

    def _invalidate_cached_file(self, filename):
        with self._cache_lock:
            self._file_cache.pop(filename, None)

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
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'secure_store'
                    )
                    """
                )
                return bool(cur.fetchone()[0])

    def ensure_schema(self):
        self._ensure_schema()

    def encrypt_data(self, data):
        if isinstance(data, (dict, list)):
            raw = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
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
        try:
            payload = self.encrypt_data(data)
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

            self._set_cached_file(filename, data)
            logger.info("✅ تم حفظ المجموعة في PostgreSQL: %s", filename)
            return True
        except Exception:
            self._invalidate_cached_file(filename)
            logger.exception("❌ فشل حفظ المجموعة في PostgreSQL: %s", filename)
            return False

    def decrypt_file(self, filename):
        cached = self._get_cached_file(filename)
        if cached is not None:
            return cached

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM secure_store WHERE collection = %s",
                        (filename,),
                    )
                    row = cur.fetchone()

            if not row:
                return None

            data = self.decrypt_data(row[0])
            if data is not None:
                self._set_cached_file(filename, data)
            return data
        except Exception:
            logger.exception("❌ فشل قراءة المجموعة من PostgreSQL: %s", filename)
            return None

    def list_collections(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT collection, updated_at "
                        "FROM secure_store ORDER BY collection"
                    )
                    return cur.fetchall()
        except Exception:
            logger.exception("❌ فشل قراءة مجموعات PostgreSQL")
            return []
