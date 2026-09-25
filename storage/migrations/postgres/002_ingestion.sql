-- NOTHING PostgreSQL production schema v2.

CREATE TABLE IF NOT EXISTS ingestion_idempotency (
    actor TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    ingestion_id UUID NOT NULL UNIQUE,
    status_code INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (actor, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_idempotency_recorded_at
    ON ingestion_idempotency(recorded_at);

DROP TRIGGER IF EXISTS ingestion_idempotency_no_update
    ON ingestion_idempotency;
DROP TRIGGER IF EXISTS ingestion_idempotency_no_delete
    ON ingestion_idempotency;

CREATE TRIGGER ingestion_idempotency_no_update
BEFORE UPDATE ON ingestion_idempotency
FOR EACH ROW EXECUTE FUNCTION nothing_append_only_guard();

CREATE TRIGGER ingestion_idempotency_no_delete
BEFORE DELETE ON ingestion_idempotency
FOR EACH ROW EXECUTE FUNCTION nothing_append_only_guard();
