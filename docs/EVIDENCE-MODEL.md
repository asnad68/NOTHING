# NOTHING Evidence Model — Draft v0.1

## Purpose

Evidence is the material or reference used during a verification process. Evidence is not the same thing as a claim, and the existence of an evidence record does not prove the claim by itself.

## Evidence identifier

Each evidence item uses an identifier such as `EVD-000001`.

The identifier is an application-level reference. It is not a legal document number and does not establish authenticity by itself.

## Evidence fields

- `evidence_id` — stable record identifier
- `version` — evidence schema version
- `type` — broad evidence category
- `source.reference` — location or reference supplied for the evidence
- `source.title` — optional human-readable title
- `source.publisher` — optional publisher/source name
- `source.accessed_at` — when a remote source was accessed
- `collected_at` — when the evidence record was collected
- `integrity` — optional integrity information
- `notes` — contextual limitations

## Integrity

The prototype supports:

- `none` — no cryptographic digest is recorded
- `sha256` — a SHA-256 digest is recorded

A digest can show that the bytes being compared are the same; it does not prove that the source itself is truthful or authoritative.

## Evidence quality

NOTHING intentionally does not reduce evidence to a universal score. Evidence suitability depends on the claim being tested.

For example, a domain-control check may be relevant to control of a website, but it does not by itself establish ownership of a company, financial strength, licensing status, or reputation.

## Lifecycle

Evidence may become unavailable, outdated, superseded, or disputed. Future implementations should preserve collection/access timestamps and support evidence lifecycle events without rewriting historical verification decisions.

## Privacy

Evidence references should minimize personal and sensitive information. The prototype does not require storing copies of identity documents or other sensitive material.

## Non-goal

An evidence record is not a certificate and is not a legal determination.
