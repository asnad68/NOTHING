-- NOTHING entitlement core schema v3.
CREATE TABLE IF NOT EXISTS entitlements (
    entitlement_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    source_payment_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
