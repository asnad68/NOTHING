-- NOTHING PostgreSQL production schema v14:
-- durable detached cryptographic proof envelopes.

CREATE TABLE IF NOT EXISTS cryptographic_proofs (
    envelope_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_hash TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    recorded_at TIMESTAMPTZ NOT NULL,
    recorded_by TEXT NOT NULL,
    CHECK (version = '0.1'),
    CHECK (resource_type IN ('identity', 'evidence', 'verification_event')),
    CHECK (resource_id <> ''),
    CHECK (resource_hash ~ '^[0-9a-f]{64}$'),
    CHECK (issuer_id <> ''),
    CHECK (key_id <> '')
);

CREATE INDEX IF NOT EXISTS idx_proofs_resource
    ON cryptographic_proofs(resource_type, resource_id, envelope_id);

REVOKE ALL ON cryptographic_proofs FROM PUBLIC;
GRANT SELECT, INSERT ON cryptographic_proofs TO nothing_app;
REVOKE UPDATE, DELETE ON cryptographic_proofs FROM nothing_app;

ALTER TABLE cryptographic_proofs OWNER TO nothing_migrator;

CREATE OR REPLACE FUNCTION nothing_immutable_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS cryptographic_proofs_no_update ON cryptographic_proofs;
DROP TRIGGER IF EXISTS cryptographic_proofs_no_delete ON cryptographic_proofs;

CREATE TRIGGER cryptographic_proofs_no_update
BEFORE UPDATE ON cryptographic_proofs
FOR EACH ROW EXECUTE FUNCTION nothing_immutable_guard();

CREATE TRIGGER cryptographic_proofs_no_delete
BEFORE DELETE ON cryptographic_proofs
FOR EACH ROW EXECUTE FUNCTION nothing_immutable_guard();
