-- NOTHING PostgreSQL production schema v6: per-invoice payment routing.

ALTER TABLE billing_prices
    ADD COLUMN IF NOT EXISTS routing_mode TEXT NOT NULL DEFAULT 'manual_shared';

ALTER TABLE billing_invoices
    ADD COLUMN IF NOT EXISTS routing_mode TEXT NOT NULL DEFAULT 'manual_shared',
    ADD COLUMN IF NOT EXISTS routing_reference TEXT;

ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS routing_mode TEXT NOT NULL DEFAULT 'manual_shared',
    ADD COLUMN IF NOT EXISTS routing_reference TEXT;

ALTER TABLE billing_prices
    DROP CONSTRAINT IF EXISTS billing_prices_routing_mode_check;
ALTER TABLE billing_prices
    ADD CONSTRAINT billing_prices_routing_mode_check
    CHECK (routing_mode IN (
        'unique_destination', 'xrp_destination_tag', 'manual_shared'
    ));

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_routing_mode_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_routing_mode_check
    CHECK (routing_mode IN (
        'unique_destination', 'xrp_destination_tag', 'manual_shared'
    ));

ALTER TABLE payment_events
    DROP CONSTRAINT IF EXISTS payment_events_routing_mode_check;
ALTER TABLE payment_events
    ADD CONSTRAINT payment_events_routing_mode_check
    CHECK (routing_mode IN (
        'unique_destination', 'xrp_destination_tag', 'manual_shared'
    ));

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_xrp_routing_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_xrp_routing_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND asset_code = 'XRP'
            AND routing_reference IS NOT NULL
            AND routing_reference ~ '^[1-9][0-9]*$'
            AND routing_reference::numeric BETWEEN 1 AND 4294967295
        )
    );

ALTER TABLE payment_events
    DROP CONSTRAINT IF EXISTS payment_events_xrp_routing_check;
ALTER TABLE payment_events
    ADD CONSTRAINT payment_events_xrp_routing_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND asset_code = 'XRP'
            AND routing_reference IS NOT NULL
            AND routing_reference ~ '^[1-9][0-9]*$'
            AND routing_reference::numeric BETWEEN 1 AND 4294967295
        )
    );

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_unique_route_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_unique_route_check
    CHECK (
        routing_mode <> 'unique_destination'
        OR routing_reference IS NULL
    );

ALTER TABLE payment_events
    DROP CONSTRAINT IF EXISTS payment_events_unique_route_check;
ALTER TABLE payment_events
    ADD CONSTRAINT payment_events_unique_route_check
    CHECK (
        routing_mode <> 'unique_destination'
        OR routing_reference IS NULL
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_invoices_xrp_destination_tag
    ON billing_invoices(network, destination, routing_reference)
    WHERE routing_mode = 'xrp_destination_tag';

CREATE INDEX IF NOT EXISTS idx_payment_events_route
    ON payment_events(network, routing_mode, routing_reference);

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
       OR NEW.client_idempotency_key <> OLD.client_idempotency_key
       OR NEW.expires_at <> OLD.expires_at
       OR NEW.quote_json <> OLD.quote_json THEN
        RAISE EXCEPTION 'billing invoice quote fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION nothing_billing_payment_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.network <> OLD.network
       OR NEW.chain_event_key <> OLD.chain_event_key
       OR NEW.asset_code <> OLD.asset_code
       OR NEW.asset_kind <> OLD.asset_kind
       OR NEW.asset_contract IS DISTINCT FROM OLD.asset_contract
       OR NEW.destination <> OLD.destination
       OR NEW.routing_mode <> OLD.routing_mode
       OR NEW.routing_reference IS DISTINCT FROM OLD.routing_reference
       OR NEW.amount_atomic <> OLD.amount_atomic
       OR NEW.tx_hash <> OLD.tx_hash
       OR NEW.first_observed_at <> OLD.first_observed_at THEN
        RAISE EXCEPTION 'payment event identity fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE billing_prices OWNER TO nothing_migrator;
ALTER TABLE billing_invoices OWNER TO nothing_migrator;
ALTER TABLE payment_events OWNER TO nothing_migrator;
