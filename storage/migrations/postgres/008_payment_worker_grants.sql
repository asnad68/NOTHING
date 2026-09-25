-- NOTHING PostgreSQL production schema v8: least-privilege payment worker role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nothing_payment') THEN
        RAISE EXCEPTION 'required role nothing_payment does not exist';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO nothing_payment;
GRANT SELECT ON subscription_plans, billing_prices TO nothing_payment;
GRANT SELECT, UPDATE ON billing_invoices TO nothing_payment;
GRANT SELECT, INSERT, UPDATE ON payment_events TO nothing_payment;
GRANT SELECT, INSERT ON payment_allocations TO nothing_payment;
GRANT SELECT, INSERT ON subscription_entitlements TO nothing_payment;
GRANT INSERT ON billing_audit_log TO nothing_payment;

GRANT USAGE, SELECT ON SEQUENCE billing_audit_log_audit_id_seq
    TO nothing_payment;

ALTER TABLE subscription_plans OWNER TO nothing_migrator;
ALTER TABLE billing_prices OWNER TO nothing_migrator;
ALTER TABLE billing_invoices OWNER TO nothing_migrator;
ALTER TABLE payment_events OWNER TO nothing_migrator;
ALTER TABLE payment_allocations OWNER TO nothing_migrator;
ALTER TABLE subscription_entitlements OWNER TO nothing_migrator;
ALTER TABLE billing_audit_log OWNER TO nothing_migrator;
