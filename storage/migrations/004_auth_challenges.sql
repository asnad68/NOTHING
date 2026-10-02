CREATE TABLE IF NOT EXISTS auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    nonce TEXT NOT NULL,
    wallet_address TEXT,
    domain TEXT NOT NULL,
    uri TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    message_sha256 TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    authorization_status TEXT NOT NULL DEFAULT 'PENDING',
    authorization_json TEXT,
    created_at TEXT NOT NULL,
    CHECK (chain_id > 0),
    CHECK (authorization_status IN ('PENDING', 'AUTHORIZED', 'REJECTED'))
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_expiry
    ON auth_challenges(expires_at);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_wallet
    ON auth_challenges(wallet_address);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_status
    ON auth_challenges(authorization_status, expires_at);
