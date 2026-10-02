ALTER TABLE auth_challenges
    ADD COLUMN IF NOT EXISTS registration_consumed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_auth_challenges_registration_consumption
    ON auth_challenges(registration_consumed_at);

GRANT UPDATE (registration_consumed_at)
    ON auth_challenges
    TO nothing_app;
