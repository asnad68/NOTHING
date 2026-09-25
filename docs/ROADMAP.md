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
- [ ] Build public identity lookup page
- [ ] Build claim/evidence display
- [ ] Build verification-event timeline
- [ ] Build procedure reference display
- [ ] Build revocation display
- [ ] Add human-readable Nothing Mark

## Phase 4 — API
- [ ] Define resource model from resolved protocol graph
- [ ] Implement `GET /v1/identity/{nothing_id}`
- [ ] Implement claim/evidence/event/procedure resources
- [ ] Define API error model
- [ ] Define content types and versioning
- [ ] Add rate limiting and abuse controls
- [ ] Define cache and freshness semantics

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
- [ ] Define service tiers
- [ ] Define governance
- [ ] Define operational security requirements
- [ ] Conduct legal/compliance review before launch

Roadmap status: experimental and subject to change.
