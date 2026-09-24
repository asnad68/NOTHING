# NOTHING Threat Model

## Purpose

This document records the initial security assumptions for the prototype. A verification system must not turn a validly formatted record into an assumption of truth.

## Assets

- identity records
- evidence references
- verification decisions
- authorization relationships
- revocation state
- future cryptographic keys and signing material
- audit records
- service credentials

## Threats and controls

### False claims
An actor may submit an inaccurate claim.

**Control:** distinguish SELF-CLAIMED from independently verified statuses and preserve evidence references.

### Stale verification
A previously correct claim may become incorrect.

**Control:** record check timestamps, validity periods where appropriate, and revocation state.

### Unauthorized representation
A person, account, domain or agent may claim to act for a business without authorization.

**Control:** keep authorization separate from identity and require explicit authorization evidence.

### Evidence substitution
Evidence may be replaced or misrepresented.

**Control direction:** preserve stable references, provenance and eventually cryptographic integrity mechanisms where appropriate.

### Credential compromise
Future signing keys or service credentials could be stolen.

**Control direction:** least privilege, key rotation, revocation and secret separation.

### Replay
An old verification result may be reused after circumstances change.

**Control direction:** include verification time, record version and revocation information in future responses.

### Denial of service
A public verification API could be abused.

**Control direction:** rate limiting, caching, abuse detection and monitoring.

### Privacy leakage
Verification infrastructure may collect unnecessary personal information.

**Control:** minimize data and avoid sensitive documents unless explicitly required by a future design.

## Trust boundary

The repository itself is not evidence that a third-party claim is true.

**A machine-valid record is not automatically a verified record.**
