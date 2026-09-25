-- NOTHING PostgreSQL production schema v5: subscription billing boundary.

CREATE TABLE IF NOT EXISTS subscription_plans (
    plan_code TEXT PRIMARY KEY,
    duration_seconds BIGINT NOT NULL CHECK (duration_seconds > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS billing_prices (
    price_id UUID PRIMARY KEY,
    plan_code TEXT NOT NULL REFERENCES subscription_plans(plan_code)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    asset_code TEXT NOT NULL,
    network TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    asset_contract TEXT,
    amount_atomic NUMERIC(78, 0) NOT NULL CHECK (amount_atomic > 0),
    asset_decimals SMALLINT NOT NULL CHECK (asset_decimals BETWEEN 0 AND 36),
    destination TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        plan_code, asset_code, network, asset_kind,
        asset_contract, amount_atomic, destination
    )
);

CREATE TABLE IF NOT EXISTS billing_invoices (
    invoice_id UUID PRIMARY KEY,
    customer_ref TEXT NOT NULL,
    plan_code TEXT NOT NULL REFERENCES subscription_plans(plan_code)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    asset_code TEXT NOT NULL,
    network TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    asset_contract TEXT,
    amount_atomic NUMERIC(78, 0) NOT NULL CHECK (amount_atomic > 0),
    asset_decimals SMALLINT NOT NULL CHECK (asset_decimals BETWEEN 0 AND 36),
    destination TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'open', 'confirming', 'underpaid', 'paid', 'overpaid',
            'expired', 'canceled', 'review_required'
        )
    ),
    client_idempotency_key TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    quote_json TEXT NOT NULL,
    UNIQUE (customer_ref, client_idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_billing_invoices_customer_status
    ON billing_invoices(customer_ref, status, created_at DESC);

CREATE TABLE IF NOT EXISTS payment_events (
    payment_event_id UUID PRIMARY KEY,
    network TEXT NOT NULL,
    asset_code TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    asset_contract TEXT,
    destination TEXT NOT NULL,
    amount_atomic NUMERIC(78, 0) NOT NULL CHECK (amount_atomic > 0),
    chain_event_key TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block_reference TEXT,
    confirmation_count INTEGER NOT NULL DEFAULT 0
        CHECK (confirmation_count >= 0),
    finality_status TEXT NOT NULL
        CHECK (finality_status IN ('pending', 'confirmed', 'final', 'orphaned')),
    success BOOLEAN NOT NULL,
    source TEXT NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (network, chain_event_key)
);

CREATE INDEX IF NOT EXISTS idx_payment_events_tx_hash
    ON payment_events(network, tx_hash);

CREATE TABLE IF NOT EXISTS payment_allocations (
    payment_event_id UUID PRIMARY KEY
        REFERENCES payment_events(payment_event_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    invoice_id UUID NOT NULL
        REFERENCES billing_invoices(invoice_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    allocated_atomic NUMERIC(78, 0) NOT NULL CHECK (allocated_atomic > 0),
    allocated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (invoice_id, payment_event_id)
);

CREATE INDEX IF NOT EXISTS idx_payment_allocations_invoice
    ON payment_allocations(invoice_id, allocated_at);

CREATE TABLE IF NOT EXISTS subscription_entitlements (
    entitlement_id UUID PRIMARY KEY,
    invoice_id UUID NOT NULL UNIQUE
        REFERENCES billing_invoices(invoice_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    customer_ref TEXT NOT NULL,
    plan_code TEXT NOT NULL REFERENCES subscription_plans(plan_code)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'active', 'expired', 'revoked')
    ),
    starts_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entitlements_customer_active
    ON subscription_entitlements(customer_ref, status, expires_at DESC);

CREATE TABLE IF NOT EXISTS billing_audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    details_json TEXT
);

CREATE OR REPLACE FUNCTION nothing_billing_immutable_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS payment_allocations_no_update ON payment_allocations;
DROP TRIGGER IF EXISTS payment_allocations_no_delete ON payment_allocations;
CREATE TRIGGER payment_allocations_no_update
BEFORE UPDATE ON payment_allocations
FOR EACH ROW EXECUTE FUNCTION nothing_billing_immutable_guard();
CREATE TRIGGER payment_allocations_no_delete
BEFORE DELETE ON payment_allocations
FOR EACH ROW EXECUTE FUNCTION nothing_billing_immutable_guard();

DROP TRIGGER IF EXISTS billing_audit_log_no_update ON billing_audit_log;
DROP TRIGGER IF EXISTS billing_audit_log_no_delete ON billing_audit_log;
CREATE TRIGGER billing_audit_log_no_update
BEFORE UPDATE ON billing_audit_log
FOR EACH ROW EXECUTE FUNCTION nothing_billing_immutable_guard();
CREATE TRIGGER billing_audit_log_no_delete
BEFORE DELETE ON billing_audit_log
FOR EACH ROW EXECUTE FUNCTION nothing_billing_immutable_guard();


CREATE OR REPLACE FUNCTION nothing_billing_invoice_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $
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
       OR NEW.client_idempotency_key <> OLD.client_idempotency_key
       OR NEW.expires_at <> OLD.expires_at
       OR NEW.quote_json <> OLD.quote_json THEN
        RAISE EXCEPTION 'billing invoice quote fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$;

DROP TRIGGER IF EXISTS billing_invoices_quote_guard ON billing_invoices;
CREATE TRIGGER billing_invoices_quote_guard
BEFORE UPDATE ON billing_invoices
FOR EACH ROW EXECUTE FUNCTION nothing_billing_invoice_guard();

CREATE OR REPLACE FUNCTION nothing_billing_payment_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $
BEGIN
    IF NEW.network <> OLD.network
       OR NEW.chain_event_key <> OLD.chain_event_key
       OR NEW.asset_code <> OLD.asset_code
       OR NEW.asset_kind <> OLD.asset_kind
       OR NEW.asset_contract IS DISTINCT FROM OLD.asset_contract
       OR NEW.destination <> OLD.destination
       OR NEW.amount_atomic <> OLD.amount_atomic
       OR NEW.tx_hash <> OLD.tx_hash
       OR NEW.first_observed_at <> OLD.first_observed_at THEN
        RAISE EXCEPTION 'payment event identity fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$;

DROP TRIGGER IF EXISTS payment_events_identity_guard ON payment_events;
CREATE TRIGGER payment_events_identity_guard
BEFORE UPDATE ON payment_events
FOR EACH ROW EXECUTE FUNCTION nothing_billing_payment_guard();

CREATE OR REPLACE FUNCTION nothing_billing_entitlement_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $
BEGIN
    IF NEW.invoice_id <> OLD.invoice_id
       OR NEW.customer_ref <> OLD.customer_ref
       OR NEW.plan_code <> OLD.plan_code
       OR NEW.starts_at <> OLD.starts_at
       OR NEW.expires_at <> OLD.expires_at THEN
        RAISE EXCEPTION 'subscription entitlement identity fields are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$;

DROP TRIGGER IF EXISTS subscription_entitlements_identity_guard ON subscription_entitlements;
CREATE TRIGGER subscription_entitlements_identity_guard
BEFORE UPDATE ON subscription_entitlements
FOR EACH ROW EXECUTE FUNCTION nothing_billing_entitlement_guard();

-- The runtime role may operate billing state, but never alter plan structure.
GRANT SELECT ON subscription_plans, billing_prices TO nothing_app;
GRANT SELECT, INSERT, UPDATE ON billing_invoices TO nothing_app;
GRANT SELECT, INSERT, UPDATE ON payment_events TO nothing_app;
GRANT SELECT, INSERT ON payment_allocations TO nothing_app;
GRANT SELECT, INSERT, UPDATE ON subscription_entitlements TO nothing_app;
GRANT INSERT ON billing_audit_log TO nothing_app;

ALTER TABLE subscription_plans OWNER TO nothing_migrator;
ALTER TABLE billing_prices OWNER TO nothing_migrator;
ALTER TABLE billing_invoices OWNER TO nothing_migrator;
ALTER TABLE payment_events OWNER TO nothing_migrator;
ALTER TABLE payment_allocations OWNER TO nothing_migrator;
ALTER TABLE subscription_entitlements OWNER TO nothing_migrator;
ALTER TABLE billing_audit_log OWNER TO nothing_migrator;
