-- NOTHING PostgreSQL production schema v12:
-- tighten runtime update privileges and make payment-worker checkpoints
-- monotonic at the database boundary.

REVOKE UPDATE ON billing_invoices FROM nothing_app, nothing_payment;
GRANT UPDATE (status, paid_at)
    ON billing_invoices
    TO nothing_app, nothing_payment;

REVOKE UPDATE ON payment_events FROM nothing_app, nothing_payment;
GRANT UPDATE (
    block_reference,
    confirmation_count,
    finality_status,
    success,
    source,
    last_observed_at
)
    ON payment_events
    TO nothing_app, nothing_payment;

REVOKE UPDATE ON subscription_entitlements FROM nothing_app, nothing_payment;
GRANT UPDATE (status)
    ON subscription_entitlements
    TO nothing_app, nothing_payment;

REVOKE UPDATE ON payment_worker_checkpoints FROM nothing_payment;
GRANT UPDATE (last_tx_hash, last_ledger_index, updated_at)
    ON payment_worker_checkpoints
    TO nothing_payment;

CREATE OR REPLACE FUNCTION nothing_payment_checkpoint_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.worker_name <> OLD.worker_name
       OR NEW.account <> OLD.account THEN
        RAISE EXCEPTION 'payment worker checkpoint identity is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.last_ledger_index IS NOT NULL
       AND NEW.last_ledger_index IS NOT NULL
       AND NEW.last_ledger_index < OLD.last_ledger_index THEN
        RAISE EXCEPTION 'payment worker checkpoint cannot move backwards'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS payment_worker_checkpoint_guard
    ON payment_worker_checkpoints;
CREATE TRIGGER payment_worker_checkpoint_guard
BEFORE UPDATE ON payment_worker_checkpoints
FOR EACH ROW EXECUTE FUNCTION nothing_payment_checkpoint_guard();

ALTER TABLE payment_worker_checkpoints OWNER TO nothing_migrator;
