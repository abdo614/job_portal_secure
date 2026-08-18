"""One-way, idempotent migration from data/*.enc to PostgreSQL.

Run this ONLY while the application is still using file storage and the
original ENCRYPTION_KEY is available. The script never deletes local files.
It decrypts each existing *.enc file with the current encryption key and
stores the same logical JSON value in PostgreSQL using the new encrypted
backend.

Required environment variables:
  DATABASE_URL
  ENCRYPTION_KEY

Optional:
  DATA_DIR (defaults to ./data)

Usage:
  python migrate_to_postgres.py
"""

import os
from pathlib import Path

from encryption import EncryptionManager
from postgres_storage import PostgresEncryptionManager


def main():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not os.environ.get("ENCRYPTION_KEY", "").strip():
        raise SystemExit("ENCRYPTION_KEY is required; do not generate a new key")

    source = EncryptionManager()
    target = PostgresEncryptionManager(source.key)
    data_dir = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "data")))

    files = sorted(data_dir.glob("*.enc"))
    if not files:
        raise SystemExit(f"No encrypted data files found in {data_dir}")

    migrated = 0
    failed = []
    for path in files:
        collection = path.stem
        value = source.decrypt_file(collection)
        if value is None:
            failed.append(collection)
            continue
        if not target.encrypt_file(collection, value):
            failed.append(collection)
            continue
        migrated += 1
        print(f"MIGRATED {collection}")

    print(f"Migration finished: {migrated}/{len(files)} collections migrated")
    if failed:
        raise SystemExit("Failed collections: " + ", ".join(failed))
    print("IMPORTANT: source .enc files were NOT deleted.")


if __name__ == "__main__":
    main()
