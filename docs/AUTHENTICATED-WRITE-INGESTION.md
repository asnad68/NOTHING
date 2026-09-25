# NOTHING Authenticated Write Ingestion

## Purpose

Authenticated Write Ingestion is the controlled mutation boundary for the NOTHING data graph.

Verify Web and the public resource API remain read-only.

The reference mutation route is:

\`POST /v1/ingestion/bundles\`

It is not generic CRUD and it never accepts procedure definitions.

## Request contract

Required headers:

\`\`\`http
Authorization: Bearer <credential>
Content-Type: application/json
Idempotency-Key: <client-generated-key>
\`\`\`

Body:

\`\`\`json
{
  "identities": [],
  "evidence": [],
  "verification_events": []
}
\`\`\`

At least one protocol record must be present.

The request parser rejects duplicate JSON object properties, invalid UTF-8, unknown top-level properties, non-array collections and non-object collection members.

The protocol validators then enforce the existing v0.1 Identity, Evidence and Verification Event contracts.

## Authentication

The reference implementation uses one server-configured bearer credential:

\`NOTHING_INGESTION_TOKEN\`

The audit actor comes from:

\`NOTHING_INGESTION_ACTOR\`

The client cannot submit an actor value for the audit record.

This is a small reference authentication boundary, not an OAuth authorization server or general identity provider.

A missing server credential disables the write route with \`503 WRITE_INGESTION_UNAVAILABLE\`. A missing or invalid credential receives \`401 UNAUTHORIZED\` and a Bearer challenge.

Production deployments must protect bearer credentials with TLS and managed secret infrastructure.

## Idempotency

\`Idempotency-Key\` is mandatory.

The SQLite persistence layer stores:

- authenticated actor
- idempotency key
- canonical request SHA-256
- assigned ingestion ID
- result JSON
- original recorded timestamp

For the same actor:

\`\`\`text
same key + same request fingerprint
        -> original result is replayed

same key + different request fingerprint
        -> 409 CONFLICT
\`\`\`

The original result is never replaced by a later retry.

Idempotency records are immutable.

The reference implementation keeps them without automatic pruning. Production retention, expiry and key-namespace rules must be defined before any pruning or key reuse is introduced.

## Atomicity

The endpoint deliberately does not expose a sequence of independent write calls.

Instead:

\`\`\`text
request
  |
  +--> authentication
  +--> bounded parsing
  +--> idempotency lookup
  +--> record validation
  +--> load existing affected graph
  +--> resolve combined graph
  |
  v
single database transaction
  |
  +--> identity revisions and heads
  +--> evidence
  +--> verification events
  +--> event/evidence links
  +--> audit entries
  +--> idempotency result
  |
  v
COMMIT
\`\`\`

Any validation, reference, procedure or relationship failure occurs before commit. No partial Identity/Evidence/Event bundle becomes accepted state.

## Accepted records

The endpoint accepts:

- Identity snapshots
- Evidence records
- Verification Events

Every Verification Event must resolve against an existing exact Procedure ID + version.

Procedures are not submitted in the request. This prevents a caller from defining the verification semantics at the same time as the verification result.

## HTTP status contract

- \`200\` — accepted or idempotent replay
- \`400\` — malformed request, invalid JSON, invalid header or invalid request shape
- \`401\` — missing or invalid bearer credential
- \`409\` — idempotency conflict or immutable-record conflict
- \`413\` — request too large
- \`415\` — unsupported media type
- \`422\` — records cannot form an accepted protocol graph
- \`429\` — write rate limit exceeded
- \`503\` — write ingestion is not configured or persistence is unavailable

Error responses use \`application/problem+json\`.

## Reference limits

Defaults:

- body size: 1 MiB
- records per collection: 100
- write rate window: 60 seconds
- write requests per client per window: 30

These are deployment defaults, not protocol guarantees.

## Local example

Set the credential outside source control:

\`\`\`bash
export NOTHING_INGESTION_TOKEN='...'
python -m src.nothing_api --storage-backend sqlite
\`\`\`

Then submit a bundle:

\`\`\`bash
curl -X POST 'http://127.0.0.1:8080/v1/ingestion/bundles' \\
  -H 'Authorization: Bearer '"$NOTHING_INGESTION_TOKEN" \\
  -H 'Content-Type: application/json' \\
  -H 'Idempotency-Key: demo-ingestion-001' \\
  --data-binary @bundle.json
\`\`\`

The example is for local loopback testing. It is not a production deployment recipe.

## Production boundary

This mechanism does not establish:

- legal ownership
- business onboarding authority
- universal trust
- evidence truthfulness
- multi-tenant authorization policy
- distributed secret management
- WAF or gateway protection
- production PostgreSQL operation
- dispute-resolution governance

Those responsibilities remain outside the reference write transport and persistence implementation.

## Tests

Run:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

The integration tests cover authentication, idempotency, atomic rollback, immutable-record conflict handling, persistent replay and preservation of public read-only routes.


## Completion checklist for the reference implementation

The reference write boundary now has the following invariants:

- Public resource routes do not accept mutation methods.
- Write ingestion requires bearer authentication.
- The authenticated audit actor is server-derived.
- Every write request requires an idempotency key.
- A reused key is replayed only when its request fingerprint matches.
- Identity history is revisioned rather than overwritten.
- Evidence and verification events remain immutable.
- Exact procedure ID/version references are required.
- The affected graph is resolved before the transaction commits.
- The write set, audit records and idempotency record commit together.
- Verify Web never receives the ingestion credential.
- The durable reference backend is SQLite; production remains a managed PostgreSQL deployment concern.
