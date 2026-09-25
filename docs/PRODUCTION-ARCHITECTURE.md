# NOTHING Production API Architecture

## 1. Objective

Phase 4 established the protocol, OpenAPI contract and a reference read-only HTTP server.

This document defines the production boundary so the prototype does not accidentally become a production system merely by exposing the reference server to the internet.

The core rule is:

> The API is stateless. Verification semantics stay in the protocol resolver. Persistence is behind a storage port.

## 2. Target deployment

Users / Software / AI Agents
            |
            v
      DNS + TLS + WAF
            |
            v
   Load Balancer / API Gateway
            |
       +----+----+
       |         |
       v         v
   API instance  API instance
       |         |
       +----+----+
            |
       Storage Port
            |
            v
   PostgreSQL-compatible DB
            |
       +----+----------------+
       |                     |
       v                     v
  Read replicas         Encrypted backups

Large evidence snapshots, when the product eventually needs to retain them, should live in encrypted object storage rather than inside the relational row. The Evidence record keeps the reference and integrity digest.

## 3. What is established

The repository now contains:

- a versioned /v1 API contract
- deterministic structural validation
- deterministic cross-record relationship resolution
- a read-only HTTP server
- a storage abstraction consumed by the API
- a durable SQLite implementation for local/staging use
- a production PostgreSQL adapter with connection pooling and migration support
- JWT access-token authentication and scope authorization for production ingestion
- identity revision history
- immutable Evidence, Verification Event and Procedure records
- append-only audit records
- deterministic content hashes
- transactional referential checks
- startup database migration support
- a JSON-to-SQLite import path
- persistent API integration tests

These features establish the architecture and executable persistence behavior. They are not a claim of production certification.

## 4. Persistence model

### Identity

nothing_id is the stable application identifier.

Identity changes do not overwrite history. They append an internal storage revision:

NTH-000001
  revision 1 -> content hash A
  revision 2 -> content hash B
  revision 3 -> content hash C
                       ^
                    current

The revision is storage metadata. It is not the protocol version.

### Evidence

Evidence records are immutable.

Changing an Evidence record requires a new EVD-XXXXXX identifier. This prevents a historical Verification Event from silently pointing at changed evidence.

### Verification Events

Verification Events are immutable.

A correction is represented as a new VER-XXXXXX event that supersedes the prior event, subject to the existing v0.1 resolver rules.

### Procedures

A procedure_id + version pair is immutable.

A new semantic procedure is published under a new version. Historical events continue to resolve against the exact version they reference.

### Audit log

Every persistence write appends an audit entry containing actor, action, resource, revision where relevant, timestamp and content hash.

The audit log is append-only inside the reference database. It is an application audit trail, not a cryptographic public ledger.

## 5. Integrity controls

The storage layer uses several independent controls:

1. Canonical JSON serialization for stable content hashing.
2. SHA-256 digest of stored protocol payloads.
3. SQLite foreign-key enforcement.
4. Append-only triggers for historical records and audit records.
5. Write transactions with BEGIN IMMEDIATE.
6. WAL mode and a busy timeout for concurrent API threads.
7. synchronous=FULL in the SQLite reference backend.
8. Exact procedure ID + version resolution.
9. Protocol resolver execution before accepting a new Verification Event.

The content hash proves consistency of the stored representation. It does not prove the truth of an external claim.

## 6. Versioning policy

There are four different version concepts and they must not be mixed:

| Version concept | Meaning |
| --- | --- |
| /v1 | HTTP API compatibility boundary |
| 0.1 on protocol records | NOTHING protocol data-contract version |
| Procedure id + version | Semantics of a verification method |
| Identity storage revision | Internal historical snapshot number |

Breaking API changes use a new major API path.

Breaking protocol changes use a new protocol version.

A change in verification semantics uses a new procedure version.

An ordinary identity update appends a storage revision.

## 7. Transaction rules

The intended write ordering is:

Procedure
   |
Identity
   |
Evidence
   |
Verification Event

A Verification Event cannot be stored unless its:

- subject identity exists
- exact procedure version exists
- referenced Evidence records exist
- superseded event exists, when specified
- complete bundle passes the protocol resolver

This prevents partially valid graph fragments from becoming accepted state.

## 8. Production database target

### Prototype / zero-cost

SQLite is appropriate for:

- local development
- deterministic tests
- single-node demonstrations
- low-volume staging

The repository provides this implementation without adding a runtime database dependency.

### Production

PostgreSQL is now implemented as the production-oriented adapter. A PostgreSQL-compatible database is the primary store because production deployment needs:

- multiple API instances
- transactional concurrency
- point-in-time recovery
- managed backups
- access control
- replication/read scaling
- migration tooling

The storage port keeps this transition explicit rather than coupling the HTTP layer to SQLite.

## 9. API and database boundaries

The public API must never expose:

- database credentials
- internal SQL
- direct database connections
- storage administration endpoints
- write access without an authenticated ingestion boundary

Write operations are isolated behind the separate authenticated ingestion path rather than turning public GET endpoints into generic CRUD.

## 10. Read scaling

The first production topology can use:

API instances -> primary database

When read volume justifies it:

API instances -> read pool -> read replicas
                    |
                    +---- primary for write/consistency-sensitive reads

Identity verification pages may tolerate ordinary read-after-write behavior according to the published consistency policy. A future write API must define whether a client receives primary reads after a mutation.

## 11. Backups and recovery

A production database must have:

- encrypted backups
- tested restore procedures
- point-in-time recovery where supported
- a defined retention policy
- access logging
- a documented recovery objective

The repository should never contain production backup files.

## 12. Evidence storage boundary

The current Evidence schema stores metadata and references, not private documents.

If future deployments capture source snapshots:

Public API
    |
Evidence metadata row
    |
Encrypted object storage
    |
SHA-256 digest

The API should disclose only the public evidence fields approved for the verification surface.

## 13. Deployment security

The reference server intentionally does not claim to provide the full production perimeter.

A production deployment should place the service behind infrastructure providing:

- TLS termination and certificate management
- WAF / abuse controls
- distributed rate limiting
- service identity and secret management
- centralized structured logs
- metrics and tracing
- alerting
- network segmentation
- managed database controls

CORS should be restricted to known application origins instead of the reference server's permissive *.

## 14. Governance boundary

Persistence alone does not create governance.

Before commercial deployment, NOTHING still needs explicit rules for:

- who may create or update an identity
- how ownership/control is established
- who may authorize representatives
- how disputes are handled
- who may revoke an identity or claim
- how long evidence is retained
- when personal data must be removed
- how procedure versions are approved
- how operators are independently audited

The storage model is designed to preserve those decisions once governance rules exist; it does not invent them.

## 15. Migration path

Current JSON prototype:

examples/*.json
procedures/registry.json
        |
        v
python -m src.nothing_store --import-root .
        |
        v
SQLite persistence
        |
        v
storage port remains unchanged
        |
        v
Future PostgreSQL implementation

The import is idempotent for identical content. Immutable records reject silent replacement.

## 16. Current boundary

The public resource API is read-only, while authenticated write ingestion is implemented as a separate mutation boundary.

The production architecture is now executable through the authenticated ingestion path and PostgreSQL adapter. Actual internet-facing production operation still requires an audited deployment, managed credentials, TLS/gateway configuration, distributed abuse controls, observability, restore-tested backups and jurisdiction-specific legal/privacy review.

## Authenticated write ingestion

The reference write path is:

\`\`\`text
Client
  |
  | Authorization: Bearer ...
  | Idempotency-Key: ...
  v
POST /v1/ingestion/bundles
  |
  +--> bounded JSON parser
  +--> request fingerprint
  +--> idempotency lookup
  +--> protocol validation
  +--> affected-graph resolution
  |
  v
BEGIN IMMEDIATE / database transaction
  |
  +--> identity revisions + heads
  +--> evidence
  +--> verification events + evidence links
  +--> audit record
  +--> idempotency record
  |
  COMMIT
\`\`\`

The endpoint accepts only Identity, Evidence and Verification Event records. Procedures remain an operator/governance-controlled immutable resource.

The reference authentication uses one configured bearer credential and a server-derived audit actor. Production should replace this with managed service credentials or a proper authorization boundary appropriate to the deployment.

The write route has a separate rate limiter and does not inherit the public GET CORS wildcard.

## Idempotency retention

The reference database keeps idempotency records without automatic pruning. This is conservative: a retained key cannot silently become associated with a different request.

Production must define an explicit retention policy, namespace strategy and operational rules for expired idempotency records before pruning or key reuse is introduced.


## Production authentication boundary

The production API uses NOTHING_AUTH_MODE=oidc-jwt.

Access tokens are validated against a fixed HTTPS JWKS endpoint with explicit algorithm, issuer, audience and access-token type checks. Authorization requires the nothing:ingest scope.

The static bearer mode remains a local/reference convenience and is not required by the PostgreSQL production path.

## PostgreSQL concurrency boundary

The PostgreSQL adapter uses:

- bounded synchronous connection pooling
- explicit transaction contexts
- SERIALIZABLE isolation for mutations
- transaction-scoped advisory locks
- deterministic lock ordering
- row locks on mutable identity heads
- retry of the complete mutation on serialization/deadlock failure
- immutable database triggers
- migration locking

The database is the final concurrency authority rather than the Python process.
