-- NOTHING PostgreSQL production schema v10:
-- freeze purchased duration and strengthen payment accounting invariants.

ALTER TABLE billing_invoices
    ADD COLUMN IF NOT EXISTS plan_duration_seconds BIGINT;

UPDATE billing_invoices bi
SET plan_duration_seconds = sp.duration_seconds
FROM subscription_plans sp
WHERE bi.plan_duration_seconds IS NULL
  AND bi.plan_code = sp.plan_code;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM billing_invoices
        WHERE plan_duration_seconds IS NULL
    ) THEN
        RAISE EXCEPTION
            'cannot complete billing schema v10: invoice duration snapshot is missing';
    END IF;
END $$;

ALTER TABLE billing_invoices
    ALTER COLUMN plan_duration_seconds SET NOT NULL;

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_plan_duration_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_plan_duration_check
    CHECK (plan_duration_seconds > 0);

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_asset_kind_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_asset_kind_check
    CHECK (asset_kind IN ('native', 'erc20', 'xrp', 'btc_utxo'));

ALTER TABLE billing_prices
    DROP CONSTRAINT IF EXISTS billing_prices_asset_kind_check;
ALTER TABLE billing_prices
    ADD CONSTRAINT billing_prices_asset_kind_check
    CHECK (asset_kind IN ('native', 'erc20', 'xrp', 'btc_utxo'));

ALTER TABLE payment_events
    DROP CONSTRAINT IF EXISTS payment_events_asset_kind_check;
ALTER TABLE payment_events
    ADD CONSTRAINT payment_events_asset_kind_check
    CHECK (asset_kind IN ('native', 'erc20', 'xrp', 'btc_utxo'));

ALTER TABLE billing_prices
    DROP CONSTRAINT IF EXISTS billing_prices_xrp_routing_check;
ALTER TABLE billing_prices
    ADD CONSTRAINT billing_prices_xrp_routing_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND asset_code = 'XRP'
            AND asset_kind = 'xrp'
        )
    );

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_xrp_asset_routing_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_xrp_asset_routing_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND asset_code = 'XRP'
            AND asset_kind = 'xrp'
            AND routing_reference IS NOT NULL
            AND routing_reference ~ '^[1-9][0-9]{0,9}$'
            AND routing_reference::numeric <= 4294967295
        )
    );

ALTER TABLE payment_events
    DROP CONSTRAINT IF EXISTS payment_events_xrp_asset_routing_check;
ALTER TABLE payment_events
    ADD CONSTRAINT payment_events_xrp_asset_routing_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND asset_code = 'XRP'
            AND asset_kind = 'xrp'
            AND routing_reference IS NOT NULL
            AND routing_reference ~ '^[1-9][0-9]{0,9}$'
            AND routing_reference::numeric <= 4294967295
        )
    );

CREATE OR REPLACE FUNCTION nothing_billing_allocation_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    payment_amount NUMERIC(78,0);
BEGIN
    SELECT amount_atomic
    INTO payment_amount
    FROM payment_events
    WHERE payment_event_id = NEW.payment_event_id;

    IF payment_amount IS NULL THEN
        RAISE EXCEPTION 'payment event does not exist';
    END IF;

    IF NEW.allocated_atomic <> payment_amount THEN
        RAISE EXCEPTION
            'payment allocation must equal the immutable payment event amount'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS payment_allocations_amount_guard ON payment_allocations;
CREATE TRIGGER payment_allocations_amount_guard
BEFORE INSERT ON payment_allocations
FOR EACH ROW EXECUTE FUNCTION nothing_billing_allocation_guard();

CREATE OR REPLACE FUNCTION nothing_billing_invoice_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.customer_ref <> OLD.customer_ref
       OR NEW.plan_code <> OLD.plan_code
       OR NEW.asset_code <> OLD.asset_code
       OR NEW.network <> OLD.network
       OR NEW.asset_kind <> OLD.asset_kind
       OR NEW.asset_contract IS DISTINCT FROM OLD.asset_contract
       OR NEW.amount_atomic <> OLD.amount_atomic
       OR NEW.asset_decimals <> OLD.asset_decimals
       OR NEW.destination <> OLD.destination
       OR NEW.routing_mode <> OLD.routing_mode
       OR NEW.routing_reference IS DISTINCT FROM OLD.routing_reference
       OR NEW.plan_duration_seconds <> OLD.plan_duration_seconds
       OR NEW.client_idempotency_key <> OLD.client_idempotency_key
       OR NEW.expires_at <> OLD.expires_at
       OR NEW.quote_json <> OLD.quote_json THEN
        RAISE EXCEPTION 'billing invoice quote fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE billing_invoices OWNER TO nothing_migrator;
ALTER TABLE billing_prices OWNER TO nothing_migrator;
ALTER TABLE payment_events OWNER TO nothing_migrator;
ALTER TABLE payment_allocations OWNER TO nothing_migrator;
