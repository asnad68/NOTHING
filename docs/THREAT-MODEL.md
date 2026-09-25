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


### Unauthorized write attempts

An attacker may try to create or alter protocol records through the public API.

**Control:** write access exists only at \`POST /v1/ingestion/bundles\`, requires a bearer credential, derives the audit actor server-side and rejects write methods on public resource routes.

### Credential disclosure

A bearer credential may be leaked through source control, logs or unsafe deployment configuration.

**Control:** no credential is stored in the repository; the reference server reads it from environment configuration. Production requires managed secrets, TLS and access controls.

### Replay / duplicate submission

A client or intermediary may retry a write after a timeout and the same logical mutation could otherwise be applied twice.

**Control:** required \`Idempotency-Key\`, request fingerprinting, immutable idempotency records and replay of the original accepted result.

### Idempotency-key misuse

A client may reuse one key for a different payload.

**Control:** the stored request fingerprint is compared and a different payload receives \`409 CONFLICT\`.

### Partial bundle persistence

A multi-record submission may fail after only part of the bundle has been written.

**Control:** the reference store resolves the complete affected graph before entering one transaction and persists the write set plus idempotency record in the same commit.

### Oversized or malformed input

An attacker may use large bodies, duplicate JSON keys or malformed payloads to stress parsers or create ambiguous interpretation.

**Control:** bounded request size, per-collection record limits, UTF-8 enforcement, duplicate-key rejection and strict top-level request shape.

### Credential brute force

An attacker may repeatedly submit invalid bearer credentials.

**Control direction:** separate write rate limiting at the reference layer plus gateway/WAF controls in production.

