# Identity → Claim → Evidence → Verification Event → Procedure

NOTHING keeps these concepts separate so that a technical record cannot accidentally become an unsupported trust assertion.

```text
Identity (NTH-XXXXXX)
        │
        └── Claim (CLM-XXXXXX)
                │
                ├── Evidence (EVD-XXXXXX)
                │
                └── Verification Event (VER-XXXXXX)
                         │
                         └── Procedure ID + Version
```

## Identity

Answers: **Who or what is the subject?**

## Claim

Answers: **What statement is being made about the subject?**

## Evidence

Answers: **What source or material was considered?**

## Verification event

Answers: **What procedure evaluated the claim using the evidence, and what was the result?**

## Procedure

Answers: **Which exact, versioned method defines what the verification result means?**

## Resolver rules

The v0.1 relationship resolver checks that:

- every event belongs to the identity
- every event references an existing claim
- every evidence reference exists
- every event cites an exact registered procedure version
- the event result is allowed by that procedure
- evidence categories comply with procedure restrictions
- required evidence exists
- procedure lifecycle timestamps permit the event
- supersession relationships are chronological and acyclic
- each claim has a single current event in v0.1

## Why the separation matters

A claim can exist without verification.

Evidence can exist without being sufficient for a claim.

A verification event can establish only a limited scope.

A procedure can define a verification method without establishing that every possible fact about a business is true.

Therefore NOTHING must not use the shortcut:

`record exists → business is trustworthy`

## Status synchronization

The resolver does not rewrite the Identity record. It reports the status stored on the claim and the status derived from the current verification event.

A mismatch is surfaced explicitly instead of silently changing historical or identity data.

## v0.1 branching rule

A claim uses one linear supersession chain. Parallel branches are rejected until NOTHING defines an explicit conflict-resolution model.

## Production principle

The future API should expose the resolved graph explicitly rather than flattening it into one score, badge or opaque trust value.
