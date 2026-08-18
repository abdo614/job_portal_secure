"""Persistent PostgreSQL backend for the existing encrypted SecureStorage API.

The application stores each logical dataset as an encrypted JSON payload in
PostgreSQL. Encryption remains Fernet-based using the existing ENCRYPTION_KEY.

PostgreSQL is the preferred backend when STORAGE_BACKEND=postgres. If the
PostgreSQL dependency, URL, or initial connection/schema setup is unavailable
at startup, the manager falls back to the existing encrypted data/*.enc files.
The local files are never deleted or modified by the PostgreSQL backend.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class PostgresEncryptionManager:
    """Drop-in replacement for the file-backed EncryptionManager."""

    def __init__(self, key: bytes, ensure_schema: bool = True):
        self.key = key
        self.cipher = Fernet(key)
        self.database_url = os.environ.get("DATABASE_URL", "").strip()
        self.psycopg = None
        self._local_fallback = False
        self._local_data_dir = Path(
            os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "data"))
        ).expanduser()

        try:
            if not self.database_url:
                raise RuntimeError("DATABASE_URL is not configured")
            import psycopg

            self.psycopg = psycopg
            if ensure_schema:
                self._ensure_schema()
            logger.info("🗄️ PostgreSQL storage initialized")
        except Exception as exc:
            self._local_fallback = True
            logger.warning(
                "⚠️ PostgreSQL unavailable at startup; using encrypted local storage fallback: %s",
                exc,
            )

    @property
    def using_postgres(self) -> bool:
        return not self._local_fallback

    def _connect(self):
        if self.psycopg is None:
            raise RuntimeError("PostgreSQL backend is unavailable")
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
        if self._local_fallback:
            return False
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
        if not self._local_fallback:
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

    def _local_encrypt_file(self, filename, data):
        try:
            self._local_data_dir.mkdir(parents=True, exist_ok=True)
            target = self._local_data_dir / f"{filename}.enc"
            temp = self._local_data_dir / f".{filename}.enc.tmp"
            temp.write_bytes(self.encrypt_data(data))
            os.replace(temp, target)
            return True
        except Exception:
            logger.exception("❌ Failed to save local encrypted collection: %s", filename)
            return False

    def _local_decrypt_file(self, filename):
        try:
            encrypted = (self._local_data_dir / f"{filename}.enc").read_bytes()
            return self.decrypt_data(encrypted)
        except FileNotFoundError:
            return None
        except Exception:
            logger.exception("❌ Failed to read local encrypted collection: %s", filename)
            return None

    def encrypt_file(self, filename, data):
        if self._local_fallback:
            return self._local_encrypt_file(filename, data)

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
        if self._local_fallback:
            return self._local_decrypt_file(filename)

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
            return self.decrypt_data(row[0])
        except Exception:
            logger.exception("❌ فشل قراءة المجموعة من PostgreSQL: %s", filename)
            return None

    def list_collections(self):
        if self._local_fallback:
            return [
                (path.stem, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
                for path in sorted(self._local_data_dir.glob("*.enc"))
                if path.is_file()
            ]

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
