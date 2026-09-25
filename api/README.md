# NOTHING API

The normative API contract is \`openapi.json\`.

The reference server is implemented in \`src/nothing_api.py\`:
\`python -m src.nothing_api\`

Contract versions:
- API: v1
- NOTHING protocol: v0.1

Public read endpoints:
- \`GET /v1/identity/{nothing_id}\`
- \`GET /v1/evidence/{evidence_id}\`
- \`GET /v1/verification-events/{event_id}\`
- \`GET /v1/procedures/{procedure_id}/{version}\`
- \`GET /healthz\`
- \`GET /readyz\`

Authenticated write ingestion:
- \`POST /v1/ingestion/bundles\`
- Requires \`Authorization: Bearer <credential>\`
- Requires \`Idempotency-Key\`
- Requires \`Content-Type: application/json\`
- Accepts identities, evidence and verification events only
- Uses one atomic persistence transaction
- Procedures are not writable through the endpoint

The server consumes the existing protocol resolver rather than implementing a second verification engine.

Reference configuration:
- \`NOTHING_INGESTION_TOKEN\`
- \`NOTHING_INGESTION_ACTOR\`
- \`NOTHING_INGESTION_MAX_BODY_BYTES\`
- \`NOTHING_INGESTION_MAX_RECORDS\`
- \`NOTHING_INGESTION_RATE_WINDOW_SECONDS\`
- \`NOTHING_INGESTION_RATE_LIMIT_MAX_REQUESTS\`

The bearer credential is never stored in source control. The reference server uses a configured credential; production should replace this single-token boundary with managed authentication infrastructure.
