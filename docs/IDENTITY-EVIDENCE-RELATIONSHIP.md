# Identity → Claim → Evidence → Verification Event

NOTHING uses four separate concepts so that a technical record cannot accidentally become an unsupported trust assertion.

```text
Identity (NTH-XXXXXX)
        │
        └── Claim (CLM-XXXXXX)
                │
                ├── Evidence (EVD-XXXXXX)
                │
                └── Verification Event (VER-XXXXXX)
                         │
                         └── Result + Scope + Procedure + Time
```

## Identity

Answers: **Who or what is the subject?**

## Claim

Answers: **What statement is being made about the subject?**

## Evidence

Answers: **What source or material was considered?**

## Verification event

Answers: **What procedure evaluated the claim using the evidence, and what was the result?**

## Why the separation matters

A claim can exist without verification.

Evidence can exist without being sufficient for a claim.

A verification event can establish only a limited scope.

Therefore NOTHING must not use the following shortcut:

`record exists → business is trustworthy`

That shortcut would make the system technically simple but conceptually unreliable.

## Future implementation rule

The API should expose these relationships explicitly instead of flattening them into a single trust score.
