-- NOTHING storage schema v1
-- Human-readable copy of the embedded migration in src/nothing_store.py.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_revisions (
    nothing_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    protocol_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    previous_content_sha256 TEXT,
    PRIMARY KEY (nothing_id, revision),
    UNIQUE (nothing_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS identity_heads (
    nothing_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    FOREIGN KEY (nothing_id, revision)
        REFERENCES identity_revisions(nothing_id, revision)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    protocol_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedures (
    procedure_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    published_at TEXT,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    PRIMARY KEY (procedure_id, version)
);

CREATE TABLE IF NOT EXISTS verification_events (
    event_id TEXT PRIMARY KEY,
    protocol_version TEXT NOT NULL,
    subject TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    procedure_id TEXT NOT NULL,
    procedure_version TEXT NOT NULL,
    supersedes_event_id TEXT,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    FOREIGN KEY (subject)
        REFERENCES identity_heads(nothing_id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,
    FOREIGN KEY (procedure_id, procedure_version)
        REFERENCES procedures(procedure_id, version)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,
    FOREIGN KEY (supersedes_event_id)
        REFERENCES verification_events(event_id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS event_evidence (
    event_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (event_id, evidence_id),
    FOREIGN KEY (event_id)
        REFERENCES verification_events(event_id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,
    FOREIGN KEY (evidence_id)
        REFERENCES evidence(evidence_id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    revision INTEGER,
    content_sha256 TEXT,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_identity_revisions_latest
    ON identity_revisions(nothing_id, revision DESC);

CREATE INDEX IF NOT EXISTS idx_events_subject_time
    ON verification_events(subject, occurred_at, event_id);

CREATE INDEX IF NOT EXISTS idx_events_claim
    ON verification_events(subject, claim_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_event_evidence_evidence
    ON event_evidence(evidence_id);

CREATE TRIGGER IF NOT EXISTS identity_revisions_no_update
BEFORE UPDATE ON identity_revisions
BEGIN
    SELECT RAISE(ABORT, 'identity revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS identity_revisions_no_delete
BEFORE DELETE ON identity_revisions
BEGIN
    SELECT RAISE(ABORT, 'identity revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS procedures_no_update
BEFORE UPDATE ON procedures
BEGIN
    SELECT RAISE(ABORT, 'procedure versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS procedures_no_delete
BEFORE DELETE ON procedures
BEGIN
    SELECT RAISE(ABORT, 'procedure versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS verification_events_no_update
BEFORE UPDATE ON verification_events
BEGIN
    SELECT RAISE(ABORT, 'verification events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS verification_events_no_delete
BEFORE DELETE ON verification_events
BEGIN
    SELECT RAISE(ABORT, 'verification events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS event_evidence_no_update
BEFORE UPDATE ON event_evidence
BEGIN
    SELECT RAISE(ABORT, 'event evidence links are immutable');
END;

CREATE TRIGGER IF NOT EXISTS event_evidence_no_delete
BEFORE DELETE ON event_evidence
BEGIN
    SELECT RAISE(ABORT, 'event evidence links are immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
