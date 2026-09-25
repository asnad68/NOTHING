-- NOTHING PostgreSQL production schema v11:
-- grant the maintenance/payment worker the minimum entitlement update needed
-- for scheduled entitlement expiration.

GRANT SELECT, INSERT, UPDATE ON subscription_entitlements TO nothing_payment;

ALTER TABLE subscription_entitlements OWNER TO nothing_migrator;
