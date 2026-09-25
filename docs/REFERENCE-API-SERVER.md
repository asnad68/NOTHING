# NOTHING Reference API Server — v1

## Purpose

The reference server is the first executable implementation of the API contract in `api/openapi.json`.
It uses only the Python standard library, delegates cross-record verification semantics to `src/nothing_protocol.py`, and can consume either the demo filesystem backend or the durable SQLite reference backend.

## Start locally

```bash
python -m src.nothing_api
```

Default address: `http://127.0.0.1:8080`

Custom host/port:

```bash
python -m src.nothing_api --host 127.0.0.1 --port 8080

SQLite-backed local/staging mode:

```bash
python -m src.nothing_store --db-path data/nothing.db --import-root .
python -m src.nothing_api --storage-backend sqlite --db-path data/nothing.db
```
```

Environment variables supported by the reference implementation:
`NOTHING_API_HOST`, `NOTHING_API_PORT`, `NOTHING_DATA_ROOT`, `NOTHING_RATE_WINDOW_SECONDS`, `NOTHING_RATE_LIMIT_MAX_REQUESTS`.

## Endpoints

```text
GET /v1/identity/{nothing_id}
GET /v1/evidence/{evidence_id}
GET /v1/verification-events/{event_id}
GET /v1/procedures/{procedure_id}/{version}
GET /healthz
GET /readyz
```

## Resolution path

HTTP request → resource loader → structural validation → protocol resolver → API representation.

The identity endpoint resolves Identity → Claim → Evidence → Verification Event → Procedure + Version.

## HTTP behavior

Successful responses use `application/json`; errors use `application/problem+json`.
The reference server implements ETag, If-None-Match, 304 Not Modified, Last-Modified, cache headers, Retry-After rate limiting, CORS for GET/OPTIONS, and explicit rejection of write methods.

## Boundary

The example dataset is synthetic. The reference server is not a production internet-facing service. The SQLite backend provides durable local/staging persistence, but it is not the intended multi-instance production database.

Production deployment must preserve the same dependency direction: HTTP transport → resource representation → storage port → protocol resolver → data records. Production still requires TLS/gateway controls, authenticated ingestion for future writes, distributed rate limiting, observability, governed PostgreSQL-compatible persistence and security/privacy/legal review.

## Tests

`tests/test_api.py` performs real HTTP requests against an ephemeral server and checks identity resolution, resource endpoints, problem responses and conditional requests.
`tests/test_openapi_contract.py` checks the API document and its internal references.

## Production work still required

The repository now defines the production persistence/deployment boundary and provides a durable SQLite reference implementation. Actual production use still requires PostgreSQL implementation, managed credentials, TLS/gateway controls, distributed abuse controls, structured observability, restore-tested backups, origin-specific CORS, authenticated write ingestion and a security/privacy/legal review.

## Authenticated ingestion

\`POST /v1/ingestion/bundles\` is the only mutation route in the reference server.

It requires:

- Bearer authentication
- \`Content-Type: application/json\`
- \`Idempotency-Key\`

It accepts Identity, Evidence and Verification Event records. The server resolves the affected graph before committing the write set.

The filesystem backend returns \`503 WRITE_INGESTION_UNAVAILABLE\`. The durable SQLite backend provides the transactional implementation and persistent idempotency records.

Procedure versions are not writable through the endpoint.

The public GET API and Verify Web remain read-only.

