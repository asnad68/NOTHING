# NOTHING Relationship Resolution — Draft v0.1

## Purpose

The resolver turns a set of separately stored records into a coherent protocol view without collapsing everything into a trust score.

The input set is:

Identity + Evidence records + Verification Events + Procedure Registry

## Resolution rules

A bundle is accepted only when all of the following are true:

1. The identity is structurally valid.
2. Every evidence record is structurally valid.
3. Every verification event is structurally valid.
4. Every event belongs to the identity's nothing_id.
5. Every event references an existing claim.
6. Every event references an exact registered procedure ID and version.
7. Every event result is permitted by that procedure.
8. Evidence references point to existing evidence records.
9. Required evidence is present when the procedure requires it.
10. Evidence types are compatible with procedure restrictions.
11. Superseded events exist and refer to the same subject and claim.
12. Supersession timestamps never move backwards.
13. Supersession contains no cycles.
14. A historical event has at most one direct successor in v0.1.
15. Each claim has at most one current event in v0.1.

## Current event

A verification event is current for a claim when it has not itself been superseded by a later event.

The prototype uses a single linear supersession chain per claim. Parallel verification branches are intentionally rejected in v0.1 because they would require an explicit conflict-resolution model.

## Identity status versus derived status

The resolver does not automatically rewrite the Identity record.

For each claim it reports:

- identity_status — the status currently stored on the Identity record
- current_status — the result from the current verification event, if one exists
- status_consistency

Possible consistency values are:

- NO_EVENT
- CONSISTENT
- STATUS_MISMATCH

A status mismatch is reported rather than silently corrected. This keeps the identity record immutable and makes synchronization an explicit future workflow.

## Unreferenced evidence

Evidence may be stored before it is used.

The resolver therefore reports unreferenced evidence instead of treating it as an error. This is useful for collection workflows while keeping the claim graph explicit.

## What the resolver does not prove

The resolver performs referential and protocol checks. It does not decide:

- whether an external source is truthful
- whether a person or company is legally trustworthy
- whether evidence is sufficient in the real world beyond the declared procedure
- whether a claim has legal effect
- whether a third party has consented to a representation

Those questions remain outside deterministic relationship resolution.

## Production implication

The future API should expose the resolved graph explicitly:

Identity → Claim → Evidence → Verification Event → Procedure

rather than flattening the result to one badge or score.
