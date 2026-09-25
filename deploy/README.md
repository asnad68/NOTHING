# NOTHING production deployment boundary

This directory contains deployment contracts, not a claim that a cloud environment has already been provisioned.

## Target topology

```text
Internet
  |
DNS
  |
Managed TLS + WAF / abuse controls
  |
Managed load balancer / API gateway
  |
+-----------------------------+
| 2+ stateless NOTHING pods  |
| /healthz   /readyz          |
+-----------------------------+
  |
Private network
  |
Managed PostgreSQL primary
  |
Encrypted backups + PITR
```

The gateway owns the public TLS certificate, request-size policy, distributed rate limiting and WAF controls. The application is not directly internet-addressable.

## Secrets

Populate `NOTHING_POSTGRES_DSN`, `NOTHING_AUTH_ISSUER`, `NOTHING_AUTH_AUDIENCE` and `NOTHING_AUTH_JWKS_URI` from the deployment platform's secret/configuration system. The API also expects the configured `nothing:billing` permission for customer billing operations.

Do not commit a Secret manifest containing credentials.

Secret lifecycle must support creation, access audit, rotation and revocation. The application should not log secret values.

## Authentication

Use the production OIDC/JWT mode only. The issuer, audience and JWKS URI are pinned by configuration. The ingestion scope is `nothing:ingest`; billing uses the separate `nothing:billing` scope.

The identity provider is an external managed dependency. Its tenant/client registration, signing-key rotation and emergency revocation procedure must be operated outside this repository.

## TLS and gateway

Terminate public TLS at the managed gateway. Only allow HTTPS from clients. Route only to the internal ClusterIP service.

Set `NOTHING_TRUST_PROXY_HEADERS=true` only when the gateway overwrites forwarding headers and the application cannot be reached directly.

The gateway must enforce:

- TLS 1.2+ according to provider policy
- request body limits consistent with the application
- distributed per-client and per-token rate limits
- WAF/abuse controls
- access logs
- upstream health checks
- no direct public database access


## Payment processing components

The payment boundary is split into three processes:

- `nothing-api`: authenticated customer billing API and public verification API
- `nothing-xrpl-worker`: receive-only XRPL observation and settlement worker
- `nothing-billing-maintenance`: scheduled invoice-expiration maintenance

The XRPL worker and maintenance job use `nothing-payment-worker-secrets`, which
must contain only the database connection required by their role. No signing
key is required because the repository never creates or sends blockchain
transactions.

## Observability

At minimum collect:

- request count/rate
- latency by route
- HTTP 4xx/5xx rate
- 429 rate
- ingestion success/conflict/failure count
- PostgreSQL pool saturation/timeouts
- database availability/readiness failures
- pod restarts
- backup success/failure
- authentication failures (without token contents)

Alert on sustained 5xx, readiness failure, database unavailability, pool exhaustion, abnormal 401/403 spikes, and failed backups.

OpenTelemetry is the intended future vendor-neutral telemetry layer; its Python implementation supports traces and metrics, with logs still evolving. 
Until an exporter is selected, stdout structured access/error logs plus platform metrics are the minimum acceptable boundary.

## Backups / restore

The database provider must provide encrypted backups and point-in-time recovery where supported. Before launch, perform a restore drill into an isolated environment and verify:

- schema migrations
- record counts/checksums
- immutable-history constraints
- `/readyz`
- representative GET verification
- authenticated ingestion
- idempotent replay

Record the observed RPO/RTO and the drill date outside the application source tree.

## Tenancy

The current production boundary is **single-tenant**. This is deliberate: the v0.1 data model does not yet attach an authorization-owned tenant identifier to every resource.

Multi-tenant production is blocked until tenant ownership and PostgreSQL RLS policies are implemented and cross-tenant denial tests pass.

## Kubernetes

The manifests provide a hardened baseline:

- restricted Pod Security labels
- non-root container
- read-only root filesystem
- dropped Linux capabilities
- RuntimeDefault seccomp
- no service-account token
- internal ClusterIP service
- disruption budget
- network policy
- readiness/liveness probes

The image tag is intentionally `CHANGE_ME`; a deployment must pin an immutable image digest before production rollout.

## Rollout gate

Do not expose the service publicly until all of these are true:

- CI is green for the exact image commit
- managed OIDC issuer/audience/JWKS are configured
- production secrets are injected and tested
- database TLS is verified
- migration role and runtime role are separated
- gateway/WAF/rate limits are active
- restore drill is passed
- observability and alerts are live
- tenancy mode remains single-tenant unless a future tenant-isolation release explicitly changes it
