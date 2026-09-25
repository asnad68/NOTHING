# NOTHING Production Release Checklist

This checklist separates repository-level implementation from external production operations. A checked code item means it is implemented and covered by the repository test suite; it does not mean a production cloud environment has already been provisioned.

## Implemented in the repository

- [x] PostgreSQL durable billing persistence
- [x] Atomic invoice/payment allocation/entitlement transaction boundary
- [x] Immutable payment-allocation and audit history
- [x] Invoice quote and purchased-duration snapshots
- [x] Receive-only XRPL transaction discovery with validated-ledger checks
- [x] XRPL delivered-amount handling for Payment transactions
- [x] Invoice-specific XRPL DestinationTag routing
- [x] Durable XRPL worker checkpoint with pagination boundary
- [x] Exact idempotency for invoice creation
- [x] Billing request fingerprint binding for API idempotency
- [x] Duplicate payment-event protection
- [x] Entitlement activation and renewal serialization
- [x] Active-entitlement enforcement boundary
- [x] Scheduled invoice/entitlement expiry
- [x] Audited manual payment reconciliation
- [x] Column-limited PostgreSQL runtime update privileges
- [x] Database-enforced checkpoint identity and monotonicity
- [x] Authenticated customer billing API
- [x] Customer-scoped invoice and entitlement reads
- [x] Separate billing scope from ingestion scope
- [x] Hardened Kubernetes manifests for API, XRPL worker and maintenance job
- [x] OCI production image build
- [x] PostgreSQL integration coverage in CI
- [x] Production image build verification in CI

## Intentionally disabled until routing is safe

- [x] XRP automatic settlement on the configured XRPL treasury account
- [x] ETH automatic settlement disabled for the currently shared receiving address
- [x] BTC automatic settlement disabled for the currently shared receiving address
- [x] USDT disabled until its exact settlement network and token contract are configured
- [x] No private-key signing code in the repository

## Required before public production launch

- [ ] Register and configure a managed OIDC identity provider
- [ ] Inject production PostgreSQL DSN using the `nothing_app` runtime role
- [ ] Inject payment-worker PostgreSQL DSN using the least-privileged `nothing_payment` role
- [ ] Provision the operator-controlled subscription plans and crypto prices
- [ ] Configure the production gateway, WAF, TLS and distributed rate limits
- [ ] Deploy the API and internal worker components behind the managed gateway
- [ ] Replace `CHANGE_ME` image references with immutable image digests
- [ ] Configure centralized logs, metrics and alerts
- [ ] Configure encrypted backups and point-in-time recovery where supported
- [ ] Perform an isolated backup/restore drill and record measured RPO/RTO
- [ ] Verify database TLS and network isolation from the public Internet
- [ ] Verify XRPL provider reliability, quotas and operational fallback
- [ ] Complete jurisdiction-specific legal, tax, sanctions, AML/CFT and virtual-asset review
- [ ] Complete privacy/security review for the actual production data flows
- [ ] Perform a controlled end-to-end payment test using a non-production amount/account

## Current deployment statement

The repository contains the production payment and entitlement code paths and their deployment contracts. This repository status is not a claim that the production service is currently live. Live operation begins only after the external deployment gates above are completed and verified.