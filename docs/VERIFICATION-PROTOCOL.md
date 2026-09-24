# NOTHING Verification Protocol — Draft v0.1

## Objective

Define a repeatable procedure for turning a claim into a verification result without implying more certainty than the evidence supports.

## Verification event

A future verification event should identify:

- NOTHING ID
- Claim ID
- verifier or verification service
- method
- source/evidence reference
- checked-at timestamp
- result
- scope and limitations

## Procedure

1. Parse and validate the identity record.
2. Identify exactly which subject and claim are being evaluated.
3. Select evidence appropriate to that claim.
4. Check authorization separately when representation or authority is involved.
5. Record the result with evidence, method and timestamp.
6. Allow the result to become stale, be superseded, or be revoked.

## Status discipline

A verifier must not use VERIFIED merely because a JSON record is structurally valid.

Structural validity means the record follows the technical contract. Verification means a defined evidence-based procedure has been completed.

## Draft status

This is a design document, not a certification standard or legal/regulatory framework.
