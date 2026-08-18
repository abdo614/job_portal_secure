"""Read-only PostgreSQL connectivity check.

This script never creates, updates, or deletes application data. It only runs
SELECT 1 against DATABASE_URL and reports whether the connection succeeds.
"""
import os
import sys


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not configured")
        return 2

    try:
        import psycopg
    except ImportError:
        print("psycopg is not installed")
        return 3

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                value = cur.fetchone()[0]
        if value == 1:
            print("POSTGRES OK (read-only SELECT 1)")
            return 0
        print("POSTGRES CHECK FAILED: unexpected result")
        return 1
    except Exception as exc:
        print(f"POSTGRES CHECK FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
