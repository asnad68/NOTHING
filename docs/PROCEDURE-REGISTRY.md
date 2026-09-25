# NOTHING Verification Procedure Registry — Draft v0.1

## Purpose

The Procedure Registry defines the named, versioned procedures that verification events are allowed to cite.

A verification event must identify:

- procedure ID
- procedure version

The resolver then requires an exact (id, version) match in the registry.

This prevents a historical verification event from silently changing meaning when a procedure is edited later.

## Procedure identity

Example: NOTHING-BASIC-SOURCE-CHECK@0.1

The ID identifies the procedure family. The version identifies the exact procedure contract used for the event.

A later revision must receive a new version instead of rewriting the meaning of 0.1.

## Lifecycle states

### DRAFT

Design-stage procedure. The v0.1 resolver rejects verification events that cite a DRAFT procedure.

### ACTIVE

Available for new verification events.

### DEPRECATED

No longer intended for new events, but retained so historical events can still be interpreted.

### RETIRED

No longer available for new events and retained only for historical interpretation.

Lifecycle timestamps make the intended temporal boundary explicit.

## Procedure contract

Each procedure defines:

- method class
- allowed result statuses
- whether evidence is mandatory
- acceptable evidence categories
- ordered human-readable steps
- publication/lifecycle timestamps
- limitations and non-goals

## Result control

An event cannot claim a result that its registered procedure does not allow.

For example, the initial source-check procedure allows:

- SOURCE-VERIFIED
- NOT-VERIFIED
- INCONCLUSIVE

It does not authorize VERIFIED.

That distinction is intentional. A broad verification status should only become available when a procedure explicitly defines what it establishes.

## Temporal control

The resolver requires an event to occur:

- on or after the procedure's publication time
- on or before a deprecation time when the procedure is deprecated
- on or before a retirement time when the procedure is retired

This prevents a new event from citing a procedure after its declared lifecycle boundary.

## Historical stability

The registry is not an editor of historical events.

When procedure P@0.1 is superseded by P@0.2, existing events continue to point to P@0.1.

The production API must preserve the ability to resolve the historical procedure definition.

## Current registry

The prototype registry contains one controlled procedure:

NOTHING-BASIC-SOURCE-CHECK@0.1

The procedure is deliberately narrow. It does not establish legal status, financial condition, reputation, universal trustworthiness, or facts outside the declared scope.

## Future additions

Future procedures may cover areas such as:

- domain control
- authorization verification
- identity attribute checks

Each addition should have its own documented scope, evidence requirements and result semantics.

## Governance requirement

Before production deployment, the registry needs a formal governance process covering:

- who may publish procedures
- who may deprecate or retire them
- how versions are assigned
- how emergency changes are handled
- how historical definitions remain available
- how procedure authorship and authorization are represented
