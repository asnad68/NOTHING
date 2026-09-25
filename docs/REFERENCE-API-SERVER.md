# NOTHING Reference API Server — v1

## Purpose

The reference server is the first executable implementation of the API contract in `api/openapi.json`.
It uses only the Python standard library and delegates cross-record verification semantics to `src/nothing_protocol.py`.

## Start locally

```bash
python -m src.nothing_api
```

Default address: `http://127.0.0.1:8080`

Custom host/port:

```bash
python -m src.nothing_api --host 127.0.0.1 --port 8080
```

Environment variables supported by the reference implementation:
`NOTHING_API_HOST`, `NOTHING_API_PORT`, `NOTHING_DATA_ROOT`, `NOTHING_RATE_WINDOW_SECONDS`, `NOTHING_RATE_LIMIT_MAX_REQUESTS`.

## Endpoints

```text
GET /v1/identity/{nothing_id}
GET /v1/evidence/{evidence_id}
GET /v1/verification-events/{event_id}
GET /v1/procedures/{procedure_id}/{version}
```

## Resolution path

HTTP request → resource loader → structural validation → protocol resolver → API representation.

The identity endpoint resolves Identity → Claim → Evidence → Verification Event → Procedure + Version.

## HTTP behavior

Successful responses use `application/json`; errors use `application/problem+json`.
The reference server implements ETag, If-None-Match, 304 Not Modified, Last-Modified, cache headers, Retry-After rate limiting, CORS for GET/OPTIONS, and explicit rejection of write methods.

## Boundary

The current dataset is synthetic. The reference server is not a production internet-facing service and does not provide TLS termination, authentication, persistent storage or distributed rate limiting.

Production deployment must preserve the same dependency direction: HTTP transport → resource representation → protocol resolver → data records.

## Tests

`tests/test_api.py` performs real HTTP requests against an ephemeral server and checks identity resolution, resource endpoints, problem responses and conditional requests.
`tests/test_openapi_contract.py` checks the API document and its internal references.

## Production work still required

Production use still requires TLS, governed persistent storage, stronger rate limiting, structured observability, audit controls, origin-specific CORS, and a security/privacy/legal review.