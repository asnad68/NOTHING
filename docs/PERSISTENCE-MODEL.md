# NOTHING Persistence Model

## Design principle

The storage model must preserve history instead of treating verification data like ordinary mutable application state.

NOTHING therefore uses:

- append-only identity revisions
- immutable Evidence
- immutable Verification Events
- immutable Procedure versions
- append-only audit records
- content hashes for stored protocol payloads

## Logical model

Identity
  1 -> N IdentityRevision
  |
  +-> N VerificationEvent
              |
              +-> N EventEvidence N -> 1 Evidence
              |
              +-> 1 Procedure (versioned)

AuditLog records every accepted persistence write.

## Tables

### identity_revisions

Stores every accepted identity snapshot.

Key:

(nothing_id, revision)

Important fields:

- protocol version
- canonical payload JSON
- SHA-256 content hash
- recorded timestamp
- actor
- previous content hash

There is intentionally no update/delete path for historical revisions.

### identity_heads

Stores only the current pointer:

nothing_id -> current revision + current hash

This table is mutable state, not historical evidence.

### evidence

One immutable row per evidence_id.

The database rejects an attempt to replace the same Evidence ID with different content.

### verification_events

One immutable row per event_id.

The row also stores indexed values for:

- subject
- claim
- occurrence time
- procedure ID/version
- superseded event

The full canonical event remains in payload_json.

### event_evidence

Explicit links between Verification Events and Evidence.

The current protocol permits an event to reference multiple Evidence records.

### procedures

One immutable row per procedure_id + version.

This is important because old verification events must continue to reference the exact semantics that existed when they were evaluated.

### audit_log

Append-only operator/application audit trail.

The reference implementation records:

- when
- who
- what action
- which resource
- which revision
- which content hash
- optional JSON details

## Current-state resolution

A normal identity read should not rebuild the whole database.

The reference SQLite store:

1. loads the current identity revision
2. loads Verification Events for that subject
3. collects only the Evidence IDs referenced by those events
4. loads the registered Procedure versions
5. passes the resulting bundle to the existing protocol resolver
6. constructs the public API representation

This preserves the existing semantic engine while avoiding a full-table Evidence scan per identity request.

## Historical semantics

Storage revision is deliberately distinct from protocol version.

Example:

NTH-000001 / protocol 0.1 / revision 1
NTH-000001 / protocol 0.1 / revision 2
NTH-000001 / protocol 0.1 / revision 3

All three are protocol 0.1 records. The revisions simply represent different historical snapshots.

When a protocol interpretation changes, a new protocol version is required instead of silently changing how old rows are read.

## Event correction

Do not edit VER-000010.

Instead create:

VER-000011
supersedes = VER-000010

The protocol resolver decides which event is current and validates chronology and chain integrity.

## Procedure evolution

Do not edit NOTHING-BASIC-SOURCE-CHECK@0.1.

Create a new version:

NOTHING-BASIC-SOURCE-CHECK@0.2

Historical events remain bound to 0.1.

## Deletion policy

The reference persistence layer does not provide hard delete operations for protocol history.

Production deletion rules must be decided at governance/legal/privacy level. Where removal is legally required, the production design must distinguish between:

- removing public access
- retaining a required audit trail
- legal holds
- cryptographic erasure of sensitive blobs
- redaction of personal data where lawful

Those rules should never be implemented as an ad-hoc database DELETE from the public API.

## Hashing

Canonical payloads use sorted JSON keys and compact separators before SHA-256 hashing.

This provides deterministic content fingerprints across restarts and API instances.

It does not provide non-repudiation by itself. Future signed records will need explicit key management and signature verification rules.

## SQLite operating profile

The executable reference backend uses:

- WAL journal mode
- synchronous=FULL
- foreign_keys=ON
- trusted_schema=OFF
- five-second busy timeout
- one database connection per storage operation
- transactional writes with BEGIN IMMEDIATE

This is intentionally conservative for a small reference service.

## PostgreSQL mapping

The logical model maps directly to a PostgreSQL implementation:

| SQLite reference | PostgreSQL target |
| --- | --- |
| identity_revisions | same logical table |
| identity_heads | same logical table |
| evidence | same logical table |
| verification_events | same logical table |
| event_evidence | same logical table |
| procedures | same logical table |
| audit_log | same logical table |

The future PostgreSQL adapter should preserve the storage port and protocol resolver behavior. SQL dialect details, connection pooling and migration tooling are implementation concerns of that adapter.

## Non-goals

This persistence layer does not provide:

- a public append-only blockchain
- a universal trust score
- a certificate authority
- cryptographic proof of external-source truth
- anonymous write access
- payment processing
- identity-document storage
