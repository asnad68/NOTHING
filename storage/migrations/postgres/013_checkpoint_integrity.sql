-- NOTHING PostgreSQL production schema v13:
-- make worker checkpoints self-validating at the database boundary.

ALTER TABLE payment_worker_checkpoints
    DROP CONSTRAINT IF EXISTS payment_worker_checkpoint_worker_name_check;
ALTER TABLE payment_worker_checkpoints
    ADD CONSTRAINT payment_worker_checkpoint_worker_name_check
    CHECK (btrim(worker_name) <> '');

ALTER TABLE payment_worker_checkpoints
    DROP CONSTRAINT IF EXISTS payment_worker_checkpoint_account_check;
ALTER TABLE payment_worker_checkpoints
    ADD CONSTRAINT payment_worker_checkpoint_account_check
    CHECK (btrim(account) <> '');

UPDATE payment_worker_checkpoints
SET last_tx_hash = btrim(last_tx_hash)
WHERE last_tx_hash IS NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM payment_worker_checkpoints
        WHERE last_tx_hash IS NULL
    ) THEN
        RAISE EXCEPTION
            'cannot complete billing schema v13: checkpoint last_tx_hash is null';
    END IF;
END $$;

ALTER TABLE payment_worker_checkpoints
    ALTER COLUMN last_tx_hash SET NOT NULL;

ALTER TABLE payment_worker_checkpoints
    DROP CONSTRAINT IF EXISTS payment_worker_checkpoint_tx_hash_check;
ALTER TABLE payment_worker_checkpoints
    ADD CONSTRAINT payment_worker_checkpoint_tx_hash_check
    CHECK (btrim(last_tx_hash) <> '');

ALTER TABLE payment_worker_checkpoints
    DROP CONSTRAINT IF EXISTS payment_worker_checkpoint_ledger_check;
ALTER TABLE payment_worker_checkpoints
    ADD CONSTRAINT payment_worker_checkpoint_ledger_check
    CHECK (last_ledger_index IS NULL OR last_ledger_index >= 0);

ALTER TABLE payment_worker_checkpoints OWNER TO nothing_migrator;
