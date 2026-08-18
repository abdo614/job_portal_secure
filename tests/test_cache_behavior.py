from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from encryption import SecureStorage


def test_cache_is_used_for_repeated_loads():
    storage = SecureStorage()
    storage._cache = {}
    storage._cache_ttl = 2

    storage.encryption.encrypt_file('users', [{'id': 1, 'email': 'a@test.com'}])

    first = storage.load_users()
    second = storage.load_users()

    assert first == second
    assert storage._cache.get('users') is not None
