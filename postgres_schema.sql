-- Persistent encrypted storage for the existing SecureStorage collections.
-- Payload remains application-encrypted with the existing ENCRYPTION_KEY.
CREATE TABLE IF NOT EXISTS secure_store (
    collection TEXT PRIMARY KEY,
    payload BYTEA NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_secure_store_updated_at
    ON secure_store (updated_at);
