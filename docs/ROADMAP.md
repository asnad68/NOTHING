# NOTHING Roadmap

## Phase 0 — Concept
- [x] Define the core concept
- [x] Define identity and claim terminology
- [x] Establish legal/disclaimer boundaries

## Phase 1 — Identity Schema
- [x] Define `NTH-XXXXXX` identifier format
- [x] Define subject types
- [x] Define claim statuses
- [x] Define revocation structure
- [x] Add schema contract/parity test foundation

## Phase 2 — Verification Engine
- [x] Implement deterministic identity validation
- [x] Define evidence record schema
- [x] Define verification event schema
- [x] Implement evidence validation
- [x] Implement verification event validation
- [x] Add deterministic evidence/event fixtures and tests
- [x] Define evidence lifecycle and integrity boundaries
- [x] Define verification event scope and supersession
- [x] Add schema-validation parity tests against JSON Schema
- [x] Implement claim/evidence/verification relationship resolution
- [x] Implement versioned verification procedure registry
- [x] Enforce procedure result allow-lists
- [x] Enforce procedure lifecycle windows
- [x] Enforce linear, acyclic supersession in v0.1

## Phase 3 — Verify Web
- [x] Build public identity lookup page
- [x] Build claim/evidence display
- [x] Build verification-event timeline
- [x] Build procedure reference display
- [x] Build revocation display
- [x] Add human-readable Nothing Mark
- [ ] Deploy static verifier with production TLS/CDN/CSP

## Phase 4 — API
- [x] Define resource model from resolved protocol graph
- [x] Define API error model
- [x] Define content types and versioning
- [x] Define cache and freshness semantics
- [x] Define rate limiting and abuse-control baseline
- [x] Write deployment-neutral OpenAPI contract
- [x] Implement reference read-only API server
- [x] Implement `GET /v1/identity/{nothing_id}`
- [x] Implement claim/evidence/event/procedure resources
- [x] Add end-to-end HTTP tests
- [x] Define production deployment architecture
- [x] Define production persistence and governance boundary
- [x] Implement storage port and durable SQLite reference backend
- [x] Production PostgreSQL adapter and migration system
- [x] Production JWT authentication and scope authorization
- [x] Serializable transaction/retry and concurrency hardening
- [x] Production deployment boundary: OCI image, hardened Kubernetes baseline, secret contract, migration/runtime role separation, health/readiness, backup/restore runbook, observability requirements, and fail-closed single-tenant boundary

- [x] Define separate authenticated write-ingestion boundary
- [x] Require bearer authentication and idempotency for writes
- [x] Implement atomic Identity + Evidence + Verification Event ingestion
- [x] Persist idempotency records with immutable audit linkage
- [ ] Production managed authentication / authorization boundary


## Phase 5 — Pilot
- [ ] Create a controlled pilot dataset
- [ ] Test business identity onboarding
- [ ] Test authorization relationships
- [ ] Test evidence collection workflows
- [ ] Test verification lifecycle
- [ ] Test procedure lifecycle changes
- [ ] Test revocation and supersession workflows

## Phase 6 — Network
- [ ] Define interoperability boundaries
- [ ] Research W3C Verifiable Credentials
- [ ] Research OpenID for Verifiable Credentials
- [ ] Research LEI/GLEIF relationships
- [ ] Research BIMI and domain signals

## Phase 7 — Commercial Infrastructure
- [x] Define service tiers
- [x] Define initial cryptocurrency billing/entitlement data model
- [x] Define payment routing and duplicate-payment invariants
- [x] Implement receive-only chain observation adapters
- [x] Implement PostgreSQL payment allocation and entitlement activation boundary
- [x] Implement authenticated customer billing API boundary
- [x] Implement XRPL checkpointed payment worker
- [x] Implement authenticated billing price catalog/invoice/entitlement reads
- [x] Implement audited manual payment reconciliation path
- [x] Implement scheduled invoice-expiration maintenance
- [x] Define payment reconciliation/audit requirements
- [ ] Define governance
- [ ] Define operational security requirements
- [ ] Managed authentication/identity-provider production registration
- [ ] Production infrastructure deployment and secret injection
- [ ] Production backup/restore drill
- [ ] Conduct legal/compliance review before launch

Roadmap status: experimental and subject to change.
