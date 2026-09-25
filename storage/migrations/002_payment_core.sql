-- NOTHING payment core schema v2.
-- Amounts are integers in the asset's smallest unit; XRP uses drops.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    network TEXT NOT NULL,
    asset TEXT NOT NULL,
    destination TEXT NOT NULL,
    expected_amount_minor INTEGER NOT NULL CHECK(expected_amount_minor > 0),
    required_confirmations INTEGER NOT NULL CHECK(required_confirmations >= 0),
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL UNIQUE REFERENCES invoices(invoice_id),
    tx_id TEXT UNIQUE,
    network TEXT NOT NULL,
    asset TEXT NOT NULL,
    destination TEXT NOT NULL,
    observed_amount_minor INTEGER NOT NULL,
    confirmations INTEGER NOT NULL CHECK(confirmations >= 0),
    state TEXT NOT NULL,
    rejection_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_events (
    event_id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments(payment_id),
    state TEXT NOT NULL,
    tx_id TEXT,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_tx ON payments(tx_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_payment
    ON payment_events(payment_id, occurred_at);
