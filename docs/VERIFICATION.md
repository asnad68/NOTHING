# NOTHING Verification Model

## Principle

A verification label is meaningful only when the record explains what was checked and when.

NOTHING therefore separates:

1. the claim
2. the source/evidence
3. the verification procedure
4. the resulting status
5. the time at which the check occurred

## Minimum verification record

A future verifier should be able to answer:

- What exactly was claimed?
- Who or what is the subject?
- What source was checked?
- When was it checked?
- What procedure was used?
- What was the result?
- Has the claim since been revoked or superseded?

## Status semantics

### SELF-CLAIMED

The subject supplied the claim, but NOTHING has not independently established it.

### SOURCE-VERIFIED

The claim was checked against an identified source.

### VERIFIED

The claim passed a defined NOTHING verification procedure with sufficient evidence for the procedure's stated scope.

### REVOKED

The claim must no longer be treated as valid.

## Important limitation

A verification status is always scoped.

For example, verifying that a website is controlled by an organization does not automatically verify that the organization is financially sound, licensed, reputable, or authorized to perform every activity it claims.

NOTHING should never convert one verified fact into an unrelated general trust judgment.

## Future audit trail

A production implementation should preserve an append-only or otherwise tamper-evident history of material verification events.

The project should investigate cryptographic signatures and timestamping before treating the audit trail as authoritative.
