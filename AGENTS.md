# AGENTS

## Purpose
This file helps AI coding agents understand the primary structure, conventions, and run/test workflow for this Flask-based job portal project.

## Key project facts
- Main application: `app.py`
- Secure storage: `encryption.py` and `data/*.enc`
- Templates: `templates/`
- Encrypted data key: `data/.key`
- Default admin account is created or updated automatically at startup.

## Run / install
- Install dependencies: `python -m pip install -r requirements.txt`
- Start the app: `python app.py`
- Syntax check: `python scripts/check_syntax.py`

## Important conventions
- All persistent site data is stored encrypted in `data/*.enc` using AES-256.
- Keep `data/.key` intact. Do not delete or replace it if you need to preserve existing encrypted data.
- User passwords are hashed with bcrypt when `SecureStorage.save_users` saves users.
- The app loads configuration from environment variables for email and Flask security settings:
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`
  - `FLASK_SECRET_KEY`, `FLASK_HTTPS`
- The admin login guard uses `admin_required` and `is_admin` in `app.py`.

## Useful files
- `app.py`: Flask routes, authentication, admin logic, email sending, and CMS behavior.
- `encryption.py`: `EncryptionManager`, `PasswordManager`, `SecureStorage`, file caching, and AES encryption.
- `scripts/check_syntax.py`: static syntax verification for all Python files.
- `tests/test_cache_behavior.py`: example of expected storage/caching behavior.
- `README_FIXED_AR.md`: repository-specific usage notes and important warnings.

## Agent guidance
- Preserve encryption key and encrypted data when changing storage logic.
- Avoid assuming a database backend: the app uses encrypted JSON files, not SQL.
- Prefer modifying route behavior in `app.py` and storage behavior in `encryption.py`.
- Use `scripts/check_syntax.py` to validate Python syntax after changes.
