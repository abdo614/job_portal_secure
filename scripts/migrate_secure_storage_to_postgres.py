"""Safely migrate encrypted SecureStorage collections to PostgreSQL.

Manual and dry-run by default. Run from a machine that has the original
encrypted data directory. Source .enc files are never deleted or modified.

Required environment variables:
  SOURCE_DATA_DIR  Directory containing the original *.enc files.
  ENCRYPTION_KEY   Same Fernet key used by the source data.
  DATABASE_URL     Target Render PostgreSQL connection string.

Usage:
  python scripts/migrate_secure_storage_to_postgres.py
  python scripts/migrate_secure_storage_to_postgres.py --apply

Dry-run performs only SELECT/read operations against PostgreSQL. The target
schema is created only with --apply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write missing collections to PostgreSQL")
    args = parser.parse_args()

    for name in ("SOURCE_DATA_DIR", "ENCRYPTION_KEY", "DATABASE_URL"):
        if not os.environ.get(name, "").strip():
            print(f"ERROR: {name} is required")
            return 2

    source_path = Path(os.environ["SOURCE_DATA_DIR"]).expanduser().resolve()
    if not source_path.is_dir():
        print(f"ERROR: source directory does not exist: {source_path}")
        return 2

    os.environ["DATA_DIR"] = str(source_path)

    from encryption import EncryptionManager
    from postgres_storage import PostgresEncryptionManager

    source = EncryptionManager()
    # Never create the target schema during dry-run.
    target = PostgresEncryptionManager(source.key, ensure_schema=args.apply)

    files = sorted(p for p in source_path.glob("*.enc") if p.is_file())
    if not files:
        print("ERROR: no encrypted source collections were found")
        return 4

    print(f"SOURCE: {source_path}")
    print(f"COLLECTIONS FOUND: {len(files)}")
    print(f"MODE: {'APPLY' if args.apply else 'DRY-RUN (READ ONLY)'}")
    print("Source files will never be deleted or modified.")
    print(f"TARGET SCHEMA EXISTS: {'YES' if target.schema_exists() else 'NO'}")

    conflicts = []
    pending = []
    skipped = []

    for file_path in files:
        collection = file_path.stem
        data = source.decrypt_file(collection)
        if data is None:
            print(f"ERROR: could not decrypt {collection}.enc")
            return 5

        source_hash = digest(data)
        existing = target.decrypt_file(collection)

        if existing is None:
            pending.append((collection, data, source_hash))
            print(f"NEW      {collection:30} sha256={source_hash}")
            continue

        target_hash = digest(existing)
        if target_hash == source_hash:
            skipped.append(collection)
            print(f"MATCH    {collection:30} sha256={source_hash}")
        else:
            conflicts.append((collection, source_hash, target_hash))
            print(f"CONFLICT {collection:28} source={source_hash} target={target_hash}")

    if conflicts:
        print("ABORTED: conflicting PostgreSQL collections were found.")
        print("No conflicting collection was overwritten.")
        return 6

    print(f"PENDING: {len(pending)} | MATCHED: {len(skipped)}")
    if not args.apply:
        print("DRY-RUN COMPLETE: PostgreSQL was not modified.")
        return 0

    for collection, data, source_hash in pending:
        if not target.encrypt_file(collection, data):
            print(f"ERROR: failed to write {collection}; migration stopped")
            return 7
        verified = target.decrypt_file(collection)
        if verified is None or digest(verified) != source_hash:
            print(f"ERROR: verification failed for {collection}")
            return 8
        print(f"COPIED   {collection:30} verified sha256={source_hash}")

    print("MIGRATION COMPLETE: all copied collections were verified.")
    print("Source encrypted files remain untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
