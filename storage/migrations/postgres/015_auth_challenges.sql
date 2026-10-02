CREATE TABLE IF NOT EXISTS auth_challenges (
    challenge_id UUID PRIMARY KEY,
    purpose TEXT NOT NULL,
    nonce TEXT NOT NULL,
    wallet_address TEXT,
    domain TEXT NOT NULL,
    uri TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    message_sha256 TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    authorization_status TEXT NOT NULL DEFAULT 'PENDING',
    authorization_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (chain_id > 0),
    CHECK (expires_at > issued_at),
    CHECK (authorization_status IN ('PENDING', 'AUTHORIZED', 'REJECTED'))
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_expiry
    ON auth_challenges(expires_at);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_wallet
    ON auth_challenges(wallet_address);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_status
    ON auth_challenges(authorization_status, expires_at);
