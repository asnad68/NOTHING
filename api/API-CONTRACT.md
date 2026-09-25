# NOTHING API Contract — v1

## Scope

The public resource surface is read-oriented, while writes use one explicitly separated authenticated ingestion endpoint. The API exposes the resolved NOTHING graph without exposing an internal database model. The API layer is storage-agnostic and is designed to run against the demo filesystem backend or a durable persistence backend.

The write boundary is deliberately not generic CRUD:

- `POST /v1/ingestion/bundles` accepts Identity, Evidence and Verification Event records only.
- Procedures are not writable through this endpoint.
- Authentication is required.
- `Idempotency-Key` is required.
- The complete affected graph is validated before persistence commits.
- A successful ingestion is one atomic transaction from the caller's perspective.

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

### POST /v1/ingestion/bundles

Accepts an authenticated write bundle containing arrays named:

- \`identities\`
- \`evidence\`
- \`verification_events\`

At least one record must be present.

The request requires:

\`\`\`http
Authorization: Bearer <credential>
Content-Type: application/json
Idempotency-Key: <client key>
\`\`\`

The reference implementation derives the audit actor from server configuration rather than trusting a client-supplied actor.

The server validates the supplied protocol records, loads the existing affected graph, resolves the combined graph using the versioned protocol resolver, and only then commits identities, evidence, verification events, audit records and the idempotency record.

A retry using the same authenticated actor, idempotency key and request fingerprint replays the original result. Reusing the key with a different request returns \`409 CONFLICT\`.

Procedure versions are not accepted by this endpoint. Procedure creation and semantic version changes remain a governed, immutable resource operation.

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

## Write authentication

The reference server uses a single server-configured bearer credential for the ingestion endpoint. It is a reference authentication boundary, not an OAuth authorization server or identity provider.

Production bearer credentials must be protected by TLS and managed secret infrastructure. Public GET resources remain unauthenticated.

## Write error/status contract

The ingestion endpoint uses:

- \`400\` for malformed JSON, invalid headers or invalid request shape
- \`401\` for missing or invalid bearer credentials
- \`409\` for idempotency or immutable-record conflicts
- \`413\` for oversized requests
- \`415\` for unsupported media type
- \`422\` for a structurally valid request that cannot form an accepted protocol graph
- \`429\` for write rate limiting, with \`Retry-After\`
- \`503\` when authenticated ingestion is not configured or persistence is unavailable

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

Authenticated ingestion is intentionally separate from Verify Web and the public GET resource surface.


## Production authentication

The production ingestion mode validates JWT access tokens from a configured identity provider.

Required validation includes:

- fixed HTTPS issuer
- fixed API audience
- fixed HTTPS JWKS URI
- explicit asymmetric signing-algorithm allow-list
- access-token type (\`at+jwt\` / \`application/at+jwt\`)
- required time and identity claims
- required authorization scope

The reference static bearer mode exists only for local/reference deployments.

Production write access should use \`NOTHING_AUTH_MODE=oidc-jwt\` and the environment settings documented in \`docs/PRODUCTION-AUTHORIZATION.md\`.
