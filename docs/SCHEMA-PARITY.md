# NOTHING Schema Parity Contract — v0.1

## Purpose

NOTHING has two structural validation layers:

1. JSON Schema for interoperable machine validation.
2. A dependency-free Python validator for local and application-side checks.

They must describe the same v0.1 structural contract. A record must not be accepted by one layer and rejected by the other for a reason that is supposed to be structural.

JSON Schema remains the interoperability contract. The Python validator is an implementation convenience and must not silently create a different data model.

## What parity covers

Parity tests cover:

- Identity records
- Evidence records
- Verification events
- Verification procedures
- The procedure registry
- Required fields
- Identifier patterns
- Enumerations
- ISO-8601 date-time constraints
- URI constraints where declared
- Conditional integrity rules
- Procedure lifecycle requirements

## What parity does not cover

Semantic relationships are intentionally tested outside JSON Schema:

- Does an event reference an existing claim?
- Does an evidence identifier exist?
- Does a procedure exist at the exact requested version?
- Is the event's subject the identity being resolved?
- Does an event supersede the correct historical event?
- Is the result permitted by the registered procedure?
- Is the supersession chain unique and acyclic?

These checks require multiple records and therefore belong to the protocol resolver rather than an individual-record schema.

## Test implementation

`tests/test_schema_parity.py` validates the schemas with jsonschema Draft 2020-12 and an explicit format checker, then compares the result with the custom validators.

The development dependency is pinned in `requirements-dev.txt`.

## Design rule

A future change that intentionally changes the structure of a v0.1 record must not silently modify only one validator. Either both representations are updated together, or a new protocol/schema version is introduced.

## Rationale

JSON Schema Draft 2020-12 is the current published JSON Schema specification. NOTHING uses that dialect so independent software can validate the same machine-readable contract. The project deliberately keeps semantic verification separate from structural validation.
