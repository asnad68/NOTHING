REVOKE UPDATE ON auth_challenges FROM nothing_app, nothing_payment;

GRANT SELECT, INSERT ON auth_challenges
    TO nothing_app;

GRANT UPDATE (consumed_at, authorization_status, authorization_json)
    ON auth_challenges
    TO nothing_app;

ALTER TABLE auth_challenges OWNER TO nothing_migrator;
