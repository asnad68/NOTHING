# Security Policy

## Scope

NOTHING is an experimental research project. It is not currently a production identity, financial, legal, or security authority.

## Reporting

Please do not publish sensitive vulnerability details in a public issue.

For a future production deployment, the project should establish a private vulnerability-reporting channel and a documented security response process.

## Security principles

The project follows these design goals:

- least privilege
- minimal data collection
- secret separation
- credential revocation
- key rotation
- auditability
- rate limiting
- abuse prevention
- dependency and supply-chain awareness

Never commit passwords, API keys, private keys, access tokens, or other secrets to this repository.


## Persistence safeguards

The durable reference storage layer is designed around historical integrity:

- identity revisions are append-only
- Evidence, Verification Events and Procedure versions are immutable
- write paths use transactions and referential checks
- stored payloads receive deterministic SHA-256 hashes
- the audit log is append-only

Local database files, backups and production credentials must never be committed to the repository.

Production deployment still requires managed secret storage, encrypted backups, access control, network protection, centralized audit logging, restore testing and a reviewed data-retention/deletion policy.
