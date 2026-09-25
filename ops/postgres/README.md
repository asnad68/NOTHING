# PostgreSQL operational boundary

## Roles

Production must use separate database identities:

- `nothing_migrator`: owns schema objects and runs migrations
- `nothing_app`: runtime role used by API instances
- managed backup/administration identity: outside the application runtime

Do not give `nothing_app` superuser, CREATEDB, CREATEROLE or BYPASSRLS privileges.

Create the roles through the managed database IAM/secret system or an administrator-controlled bootstrap process. Never commit passwords.

The migration role is required before migration `004_runtime_grants.sql` is applied.

## TLS

The application DSN must require TLS (for example, `sslmode=require`; use certificate verification when the managed provider supports and requires it).

## Backup and restore boundary

Production backup configuration belongs to the managed PostgreSQL service, not the application container.

Required controls:

1. encrypted automated backups
2. point-in-time recovery where supported
3. retention policy documented per environment
4. backup failure alert
5. restore drill before production launch and periodically thereafter
6. restore verification against the application health/readiness checks
7. documented RPO/RTO approved by the operator

Never store production backup artifacts in this repository.

## Migration boundary

Only the deployment pipeline or a controlled operator identity runs migrations. API pods use `nothing_app` and do not receive schema-administration credentials.

Migrations must be backward-compatible with the currently running application during rolling deployment.

## Tenant boundary

The current production deployment is explicitly **single-tenant**. The API refuses a multi-tenant deployment mode because the v0.1 relational model does not yet carry a tenant ownership boundary for every public object.

Do not deploy the current schema as a multi-tenant service.

A future multi-tenant phase must add a tenant ownership model and database-enforced isolation (for PostgreSQL, Row-Level Security is the appropriate database primitive), then test cross-tenant read/write denial before enabling multi-tenant mode. PostgreSQL documents that RLS is default-deny when enabled without a matching policy and that table owners normally bypass it, so the runtime role must not own the protected tables. 
