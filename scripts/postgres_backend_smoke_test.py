"""Read-only smoke test for the authoritative PostgreSQL SecureStorage backend."""
from __future__ import annotations

import os


def main() -> int:
    if os.environ.get("STORAGE_BACKEND", "").strip().lower() != "postgres":
        print("ERROR: set STORAGE_BACKEND=postgres before running this test")
        return 2

    from encryption import secure_storage

    backend = secure_storage.encryption
    if not getattr(backend, "using_postgres", False):
        print("ERROR: SecureStorage is not using PostgreSQL")
        return 3

    if not backend.schema_exists():
        print("ERROR: secure_store schema is unavailable")
        return 4

    collections = backend.list_collections()
    print(f"POSTGRES BACKEND OK; collections={len(collections)}")
    for name in ("users", "jobs", "news"):
        data = backend.decrypt_file(name)
        print(f"{name}: {len(data) if hasattr(data, '__len__') else 0}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
