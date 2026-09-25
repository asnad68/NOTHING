# NOTHING PostgreSQL Production Adapter

## Target

The production storage target is PostgreSQL 18 or another supported PostgreSQL major version with current security updates.

As of September 2026, PostgreSQL 18.6 is the current 18.x maintenance release. Production should keep the chosen major version on a supported maintenance release rather than pinning to an obsolete minor. citeturn592827search0turn592827search2

## Driver and pool

The adapter uses Psycopg 3 and its connection pool.

Current repository lock:

\`psycopg[binary,pool]==3.3.6\`

The pool is configured for multiple API threads and bounded connection counts.

The adapter uses explicit connection transactions rather than keeping long-lived idle transactions. Psycopg documents that ordinary operations can start transactions and that transaction contexts should be used deliberately for atomic work. citeturn974786search0turn204347view0

## Database connection

Required:

\`NOTHING_POSTGRES_DSN\`

Example shape only:

\`\`\`text
postgresql://app_user:SECRET@db.example/nothing?sslmode=require
\`\`\`

The actual credential must come from managed secret infrastructure and must never be committed to source control.

Recommended operational settings:

- TLS required
- application-specific database role
- least-privilege grants
- connection pool max sized to the database connection budget
- statement timeout
- lock timeout
- centralized database logs

## Migration system

The adapter applies ordered PostgreSQL migrations:

- \`001_initial.sql\`
- \`002_ingestion.sql\`
- \`003_concurrency.sql\`

Migration execution is protected by a PostgreSQL advisory transaction lock so multiple API instances cannot attempt the same migration concurrently.

The migration table is the source of truth for the applied schema version.

## Transaction model

Writes run at:

\`SERIALIZABLE\`

The adapter also uses transaction-scoped advisory locks.

Lock order is deterministic:

1. idempotency namespace
2. affected identity subjects in lexical order

This matters because inconsistent lock acquisition order is a common source of deadlocks.

Serialization failures and deadlocks are retried by repeating the complete mutation method from the beginning. The retry is outside the transaction context so the complete operation is re-evaluated after the database aborts the prior transaction.

Psycopg's documentation explicitly notes that SERIALIZABLE workloads must be prepared for serialization failures and retry the operation that caused the failure. citeturn974786search0

## Idempotency concurrency

For the same authenticated actor and idempotency key:

- the transaction acquires an advisory lock derived from the pair
- the existing idempotency row is checked under \`FOR UPDATE\`
- a successful write stores the result in the same transaction
- subsequent callers receive the stored result

This prevents the classic race:

\`\`\`text
request A: SELECT -> no row
request B: SELECT -> no row
request A: INSERT
request B: INSERT
\`\`\`

The application no longer depends solely on a unique constraint to discover this race after doing all the work.

## Identity concurrency

Identity heads are locked under the same subject-scoped advisory key and with \`FOR UPDATE\`.

The new identity revision is computed from the locked current head.

Therefore two concurrent updates cannot both decide that the next revision is, for example, revision 2.

A concurrent update sequence becomes:

\`\`\`text
writer A -> revision 2 -> commit
writer B -> observes revision 2 -> revision 3 -> commit
\`\`\`

The exact ordering depends on lock acquisition, but no revision is silently lost.

## Immutable history

PostgreSQL triggers reject UPDATE and DELETE on:

- identity revisions
- evidence
- procedures
- verification events
- event/evidence links
- audit log
- idempotency records

The application therefore has two lines of defense:

1. application-level immutable-record checks
2. database-level immutable triggers

## Production database roles

Do not run the API as a PostgreSQL superuser.

At minimum, production should use separate credentials/roles for:

- migration/deployment operations
- application runtime

The runtime role should have only the permissions needed by the application tables and sequences.

The migration role should be the only role allowed to create/alter schema objects.

## Backups

The production database still requires:

- encrypted backup storage
- point-in-time recovery where supported
- restore drills
- retention policy
- monitoring for failed backups
- documented recovery objectives

## Read replicas

Public verification reads can eventually use read replicas.

Consistency-sensitive reads immediately following ingestion should use the primary or an explicitly defined session-consistency strategy.

The current adapter is safe as a primary-database store. It does not silently route writes to replicas.

## PostgreSQL 18 note

PostgreSQL 18 is the current major release line as of this repository update, and PostgreSQL 18.6 is the current listed 18.x maintenance release. citeturn592827search0turn592827search5

The application should still verify the exact managed-service version and support policy before deployment.
