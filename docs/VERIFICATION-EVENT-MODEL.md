# NOTHING Verification Event Model — Draft v0.1

## Purpose

A verification event is the auditable record of a specific evaluation of a specific claim at a specific time using a defined procedure and identified evidence.

The event answers:

> What was checked, how was it checked, what evidence was used, what was the result, and what exactly does that result mean?

## Event identifier

Each event uses an identifier such as `VER-000001`.

Events are append-oriented records. A later event should supersede an earlier event rather than silently rewriting its historical meaning.

## Required relationships

A verification event references:

- one NOTHING subject (`NTH-XXXXXX`)
- one claim (`CLM-XXXXXX`)
- one procedure identifier and version
- zero or more evidence identifiers
- one result with an explicit scope

## Result semantics

### VERIFIED

The defined verification procedure completed successfully within its stated scope and the available evidence satisfied that procedure.

### SOURCE-VERIFIED

The claim was checked against an identified source, but the status should not be interpreted as a broader independent verification beyond the documented scope.

### NOT-VERIFIED

The procedure did not establish the claim.

### INCONCLUSIVE

Available information was insufficient to make the defined determination.

### REVOKED

A previous result or claim should no longer be treated as valid.

## Scope is mandatory

A result without scope is unsafe. NOTHING must state what was established and, equally importantly, what was not established.

## Supersession

When a later verification changes the current interpretation, the later event may reference the earlier event through `supersedes`. Historical events should remain immutable in a production implementation.

## Verifier

The prototype supports three verifier modes:

- `automated`
- `human`
- `hybrid`

A verifier identifier is optional in the prototype but should become important when a production trust/governance model is defined.

## Time

`occurred_at` records when the verification event occurred. This does not imply that the result remains true forever.

## Security principle

A verification event is evidence of a verification process. It is not, by itself, proof that every statement associated with an identity is true.

## Draft status

This is a protocol design document for NOTHING v0.1. It is not a certification standard, legal opinion, or regulatory framework.
