# NOTHING Verification Protocol — Draft v0.1

## Objective

Define a repeatable procedure for turning a claim into a verification result without implying more certainty than the evidence supports.

## Verification event

A verification event identifies:

- NOTHING ID
- Claim ID
- verifier or verification service
- exact procedure ID and procedure version
- evidence references
- occurred-at timestamp
- result
- scope and limitations
- optional superseded event

## Procedure

1. Parse and structurally validate the identity record.
2. Identify exactly which subject and claim are being evaluated.
3. Resolve the exact procedure ID and version from the Procedure Registry.
4. Confirm that the procedure was available at the event timestamp.
5. Select evidence appropriate to that procedure and claim.
6. Confirm that every evidence identifier resolves to a stored Evidence record.
7. Check authorization separately when representation or authority is involved.
8. Execute the documented procedure steps.
9. Record the result only from the procedure's allowed result set.
10. Record scope, limitations, evidence, procedure version and timestamp.
11. When a later event changes the current interpretation, reference the earlier event with supersedes rather than rewriting history.

## Structural validity versus semantic validity

Structural validation answers whether a single JSON record follows its schema.

Semantic resolution answers whether multiple valid records form a coherent bundle.

A machine-valid record is therefore not automatically a verified record.

## Result discipline

A verification status is meaningful only in the scope of the procedure that produced it.

The resolver rejects an event when its result is not permitted by the registered procedure. This prevents a procedure designed only for a source check from silently producing a broader verification status.

## Evidence discipline

Evidence is referenced by identifier rather than embedded directly into an event.

An Evidence record records what source or material was considered, when it was collected and any available integrity information. The existence of an Evidence record does not establish that its contents are true.

## Authorization discipline

Authorization is a separate concern from identity and evidence.

A verification of a website or document must not automatically be interpreted as authorization to act for a company. Authorization procedures and relationships will be introduced explicitly.

## Supersession and history

Verification events are append-oriented records.

In v0.1, each claim uses one linear supersession chain. A later event may supersede one earlier event for the same subject and claim. Cycles and parallel branches are rejected because their conflict semantics have not yet been defined.

Historical events should remain immutable in a production implementation.

## Current interpretation

The current event for a claim is the event that has not been superseded by another event.

The resolver reports both the current event result and the status stored in the identity record. A mismatch is reported instead of silently changing the identity.

## Staleness

An event is time-bound. An event occurring at time T does not guarantee that the result remains true indefinitely.

Future versions may add explicit freshness policies per procedure or claim type.

## Draft status

This is a protocol design document, not a certification standard or legal/regulatory framework.
