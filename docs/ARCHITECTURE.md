# NOTHING Technical Architecture

## 1. Purpose

NOTHING is an experimental business-identity and verification infrastructure project.

The system is designed around a simple separation:

**Identity → Claims → Evidence → Verification → Authorization → Revocation**

NOTHING does not attempt to replace government registration, trademarks, certificates, banking credentials, or legal authority.

## 2. Repository layers

- `schema/` — machine-readable identity contracts
- `examples/` — safe example records for development and testing
- `docs/` — architecture and protocol documentation
- `src/` — future verification and application code
- `tests/` — automated validation and protocol tests
- `.github/` — future CI/CD and repository automation

## 3. Identity

A NOTHING identity uses an identifier such as `NTH-000001`.

The identifier is an application-level identifier. It is not a government registration number and has no legal effect by itself.

## 4. Claims

A claim is a statement associated with an identity.

Each claim has:

- a stable claim identifier
- a human-readable statement
- a verification status
- optional source evidence
- optional authorization information
- optional validity dates

The model deliberately avoids a single trust score.

## 5. Verification states

- `VERIFIED` — verified according to a defined NOTHING verification procedure
- `SOURCE-VERIFIED` — supported by a specified source
- `SELF-CLAIMED` — supplied by the subject without independent verification
- `REVOKED` — no longer valid

The exact evidence and verification procedure must be recorded separately from the status label.

## 6. Authorization

Identity and authorization are different concepts.

A business may exist as an identity while a particular person, account, domain, or agent may or may not be authorized to act for it.

This separation is fundamental to the architecture.

## 7. Revocation

Verification is not permanent.

A future implementation should support:

- revocation status
- revocation reason
- revocation timestamp
- audit history
- key or credential rotation where cryptographic credentials are used

## 8. Future API

A future API may expose an identity record through a route such as:

`GET /v1/identity/NTH-000001`

The API must return machine-readable data and should make the evidence/status distinction explicit.

## 9. Security direction

Future implementation should follow:

- least privilege
- secret separation
- cryptographic signing where appropriate
- key rotation
- credential revocation
- auditability
- rate limiting
- abuse prevention
- minimal data collection
- dependency and supply-chain awareness

## 10. Privacy

NOTHING follows the principle:

> Collect less. Prove more.

Only information necessary for a defined verification purpose should be collected.

## 11. Research alignment

Future protocol research may examine W3C Verifiable Credentials, OpenID for Verifiable Credentials, GLEIF/LEI, BIMI, GS1 Digital Link, and established web-security practices.

Research alignment does not imply formal compliance or certification.

## 12. Non-goals

NOTHING is not currently:

- a government identity system
- a trademark registry
- a certificate authority
- a qualified trust service provider
- a bank or payment institution
- a financial regulator
- a legal authority
- an official representative of third-party brands
- a replacement for existing corporate registration systems

## 13. Current maturity

Version 0.1 is a prototype/research stage.

The immediate engineering objective is not to create a large platform. It is to establish a small, coherent, machine-verifiable identity model that can be tested, audited, and extended without breaking its core principles.


## 14. Persistence and production API boundary

The HTTP API is storage-agnostic:

`HTTP -> storage port -> protocol resolver -> API representation`

The repository includes a durable SQLite reference implementation in `src/nothing_store.py`. It is intended for local development, deterministic tests and controlled staging.

The production target is a PostgreSQL-compatible database behind a stateless API tier. The storage port is the compatibility boundary so the HTTP layer does not become coupled to a particular database.

Historical rules:

- Identity updates create storage revisions instead of overwriting prior snapshots.
- Evidence records are immutable.
- Verification Events are immutable; corrections use supersession.
- Procedure ID + version records are immutable.
- Audit entries are append-only.
- Canonical payloads receive deterministic SHA-256 content hashes.

A storage revision is not a protocol version. Breaking protocol semantics require a new protocol version.

The public read API should never expose database credentials, internal SQL, storage administration controls or unauthenticated write operations.

## 15. Production topology

```text
Clients
  |
DNS / TLS / WAF / Gateway
  |
Stateless API instances
  |
Storage Port
  |
PostgreSQL-compatible primary
  |
Read replicas + encrypted backups
```

Evidence snapshots, when they become necessary, should be kept in encrypted object storage and referenced by Evidence metadata plus an integrity digest rather than embedded as sensitive database payloads.

Production governance must separately define onboarding authority, authorization, revocation, disputes, retention, privacy deletion/redaction, procedure approval and operator audit.
