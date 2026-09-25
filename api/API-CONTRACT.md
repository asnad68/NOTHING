# NOTHING API Contract — v1

## Scope

The first API contract is read-only. It exposes the resolved NOTHING graph without exposing an internal database model. The API layer is storage-agnostic and is designed to run against the demo filesystem backend or a durable persistence backend.

The contract is deployment-neutral: no production API hostname is assumed yet.

## Versioning

- HTTP path version: `/v1`
- NOTHING protocol version: `0.1`
- API document version: `1.0.0`

Breaking API behavior changes require a new major path version. Changes to the underlying NOTHING data protocol require a protocol version change and must not silently reinterpret historical records.

## Resources

### GET /healthz

Returns a minimal process liveness response. It does not expose storage details.

### GET /readyz

Returns whether the configured storage backend is currently reachable. A non-ready response uses HTTP 503.

### GET /v1/identity/{nothing_id}

Returns one identity and its resolved claims.

Each claim exposes:

- the recorded claim status
- the current verification event, if any
- verification scope
- exact procedure ID and version
- evidence identifiers

The response must not collapse these fields into a universal trust score.

### GET /v1/evidence/{evidence_id}

Returns one Evidence record. Evidence is a record of what was considered; it is not itself a certificate of truth.

### GET /v1/verification-events/{event_id}

Returns one immutable-style Verification Event and its exact procedure reference.

### GET /v1/procedures/{procedure_id}/{version}

Returns the exact versioned Procedure used to define a verification result.

## Content types

Successful responses use:

`application/json`

Error responses use:

`application/problem+json`

UTF-8 is assumed.

## Errors

The API uses a stable application error code in addition to HTTP status.

Initial codes include:

- `INVALID_ID`
- `NOT_FOUND`
- `RATE_LIMITED`
- `TEMPORARILY_UNAVAILABLE`

Clients should not branch on human-readable error text.

## Cache and freshness

Identity, evidence, event and procedure representations are read-oriented resources and may be cached.

The API uses:

- `ETag`
- `Last-Modified`
- conditional `If-None-Match`
- `304 Not Modified`

A deployment may publish a short public cache lifetime. The contract deliberately does not claim that cached data is permanently current.

A Verification Event is time-bound; its existence does not guarantee that the underlying real-world claim remains true forever.

## Rate limiting

The API is expected to enforce rate limits and abuse controls at deployment time.

The contract guarantees a `429 Too Many Requests` response when a limit is enforced and supports `Retry-After`.

Exact quotas remain deployment-specific and are intentionally not hard-coded into the protocol.

## Privacy and security

The public read API should expose only information intended for public verification.

Sensitive documents, private credentials, authentication secrets and unnecessary personal information are outside the public resource model.

The API must preserve the same separation used by the resolver:

`Identity → Claim → Evidence → Verification Event → Procedure`

A valid HTTP response is not an independent statement that every external source is truthful.

## Implementation status

This file and `api/openapi.json` define the contract.

The reference API server is implemented in `src/nothing_api.py`. It consumes the storage port from `src/nothing_store.py` and delegates verification semantics to `src/nothing_protocol.py`.

The production deployment target remains a stateless API tier behind TLS/WAF/API-gateway infrastructure with a PostgreSQL-compatible persistence layer.
