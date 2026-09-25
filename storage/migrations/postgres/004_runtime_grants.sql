-- Runtime database privileges are intentionally narrow.
-- Apply this migration with the deployment/migration role, not the API role.
--
-- The roles are expected to exist in the managed PostgreSQL environment:
--   nothing_migrator (schema owner)
--   nothing_app (runtime role)
--
-- This migration does not create passwords. Credentials belong in the
-- managed secret/identity system.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nothing_migrator') THEN
        RAISE EXCEPTION 'required role nothing_migrator does not exist';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nothing_app') THEN
        RAISE EXCEPTION 'required role nothing_app does not exist';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO nothing_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nothing_app;

-- Runtime writes are limited to the mutation tables actually used by the
-- authenticated ingestion path. Immutable history is never granted UPDATE or
-- DELETE privileges, even though database triggers also enforce immutability.
GRANT INSERT ON identity_revisions, evidence, verification_events,
    event_evidence, audit_log, ingestion_idempotency TO nothing_app;
GRANT UPDATE ON identity_heads TO nothing_app;

ALTER DEFAULT PRIVILEGES FOR ROLE nothing_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO nothing_app;

-- The application role must never own the schema objects.
ALTER TABLE schema_migrations OWNER TO nothing_migrator;
ALTER TABLE identity_revisions OWNER TO nothing_migrator;
ALTER TABLE identity_heads OWNER TO nothing_migrator;
ALTER TABLE evidence OWNER TO nothing_migrator;
ALTER TABLE procedures OWNER TO nothing_migrator;
ALTER TABLE verification_events OWNER TO nothing_migrator;
ALTER TABLE event_evidence OWNER TO nothing_migrator;
ALTER TABLE audit_log OWNER TO nothing_migrator;
ALTER TABLE ingestion_idempotency OWNER TO nothing_migrator;
