-- NOTHING PostgreSQL production schema v3: concurrency/index hardening.

CREATE INDEX IF NOT EXISTS idx_identity_heads_revision
    ON identity_heads(revision);

CREATE INDEX IF NOT EXISTS idx_ingestion_actor_key
    ON ingestion_idempotency(actor, idempotency_key);

COMMENT ON TABLE ingestion_idempotency IS
'Immutable binding between an authenticated actor, idempotency key and committed ingestion result.';

COMMENT ON TABLE audit_log IS
'Append-only application audit trail. It is not a public cryptographic ledger.';

COMMENT ON COLUMN identity_heads.revision IS
'Current storage revision selected by the successful serializable write transaction.';
