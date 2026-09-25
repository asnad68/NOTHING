# NOTHING API

The normative API contract is `openapi.json`.

The first executable implementation is the read-only reference server:
`python -m src.nothing_api`

Contract versions:
- API: v1
- NOTHING protocol: v0.1

Read-only endpoints:
- `GET /v1/identity/{nothing_id}`
- `GET /v1/evidence/{evidence_id}`
- `GET /v1/verification-events/{event_id}`
- `GET /v1/procedures/{procedure_id}/{version}`

The server consumes the existing protocol resolver rather than implementing a second verification engine.