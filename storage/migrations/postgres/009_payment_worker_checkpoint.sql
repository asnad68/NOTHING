-- NOTHING PostgreSQL production schema v9: durable XRPL worker checkpoint.

CREATE TABLE IF NOT EXISTS payment_worker_checkpoints (
    worker_name TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    last_tx_hash TEXT,
    last_ledger_index BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE payment_worker_checkpoints
    OWNER TO nothing_migrator;

GRANT SELECT, INSERT, UPDATE
    ON payment_worker_checkpoints
    TO nothing_payment;
