-- NOTHING storage schema v2
-- Authenticated write-ingestion idempotency records.

CREATE TABLE IF NOT EXISTS ingestion_idempotency (
    actor TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    ingestion_id TEXT NOT NULL UNIQUE,
    status_code INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (actor, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_idempotency_recorded_at
    ON ingestion_idempotency(recorded_at);

CREATE TRIGGER IF NOT EXISTS ingestion_idempotency_no_update
BEFORE UPDATE ON ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'ingestion idempotency records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS ingestion_idempotency_no_delete
BEFORE DELETE ON ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'ingestion idempotency records are append-only');
END;
