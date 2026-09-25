# NOTHING

## Business Identity & Verification Infrastructure

> **Your brand is yours. Your identity travels with it.**

NOTHING is an experimental project exploring a portable, interoperable and machine-verifiable identity layer for businesses, brands, digital channels and authorized agents.

The project is being developed under the working name:

**The Company That Does Nothing**

---

## The Problem

A modern company can have many identities across the digital world:

- Legal entity
- Brand
- Trademark
- Domains
- Websites
- Social accounts
- Apps
- Marketplaces
- Authorized representatives
- Distributors and partners
- AI agents
- Commerce endpoints

The difficult question is no longer only:

> **Who is this company?**

It is also:

> **Which digital identity actually belongs to it, and what is authorized?**

NOTHING explores a common verification layer that can connect these relationships and make them easier for both humans and machines to verify.

---

## The Core Idea

NOTHING is designed around five principles:

**Identity** — Give a business a stable digital identity reference.

**Evidence** — Record what a claim is based on.

**Verification** — Make the status of each claim understandable and checkable.

**Authorization** — Represent what a company or authorized party is allowed to do.

**Revocation** — Make it possible to show when an earlier assertion is no longer valid.

The long-term vision is:

> **One business identity. Many proofs. One simple way to verify.**

---

## NOTHING ID

Each identity may receive a stable reference such as:

`NTH-000001`

A Nothing ID is **not**:

- A government registration number
- A trademark registration
- A legal certification
- A replacement for company registration
- A replacement for domain registration
- A financial or banking credential

It is a project-level identifier that can reference a business identity record and its associated claims.

---

## Claim-Based Verification

NOTHING is intentionally designed to avoid a single, misleading "trust score".

Different claims may have different statuses.

Example:

```text
Legal Entity       VERIFIED
Domain Control     VERIFIED
Official Website   VERIFIED
Trademark          VERIFIED
Social Account     UNVERIFIED
Distributor        NOT VERIFIED
AI Agent           VERIFIED
```

This makes the system more precise than a single green badge.

---

## Verification Statuses

The initial prototype uses four basic states:

### VERIFIED

A claim has been verified using an appropriate verification method and evidence.

### SOURCE-VERIFIED

A claim has been checked against an authoritative or otherwise reliable external source.

### SELF-CLAIMED

The organization has provided the information, but the project has not yet completed sufficient independent verification.

### REVOKED

A previously valid assertion is no longer valid.

The exact verification rules will evolve as the project is tested.

---

## Nothing Passport™

**Nothing Passport** is the working name for the user-facing business identity record.

A future passport may connect:

```text
Legal Entity
      |
      +---- Brand
      +---- Trademark
      +---- Official Domains
      +---- Official Channels
      +---- Authorized People
      +---- Authorized Partners
      +---- Authorized AI Agents
      +---- Commerce Endpoints
```

The Passport is intended to make these relationships easier to understand and verify.

---

## Nothing Mark ◇

The project also explores a simple visual marker:

**◇**

The mark is not the product by itself.

Its purpose is to provide a simple human-facing entry point to verification.

A future implementation might allow a user to select:

**Brand ◇ → Verify Identity**

The visual mark only has meaning when it points to a verifiable record.

---

## For Machines

NOTHING is intended to be machine-readable as well as human-readable.

A future API may allow software, marketplaces, websites and AI agents to resolve a Nothing ID and inspect its claims.

Conceptual example:

```http
GET /v1/identity/NTH-000001
```

Conceptual response:

```json
{
  "id": "NTH-000001",
  "status": "VERIFIED",
  "brand": "Example Company",
  "official_domains": [
    "example.com"
  ],
  "revoked": false
}
```

The exact API and schema are experimental and will change during development.

---

## Security Principles

Security is part of the architecture from the beginning.

The project is designed around principles including:

- Least privilege
- Secure development practices
- Separation of secrets from source code
- Cryptographic signatures where appropriate
- Key rotation
- Credential revocation
- Auditability
- Rate limiting
- Abuse prevention
- Minimal data collection
- Dependency and supply-chain awareness

No private keys, passwords or production secrets belong in this repository.

---

## Privacy Principles

NOTHING follows a simple rule:

> **Collect less. Prove more.**

The prototype should avoid collecting unnecessary personal information.

NOTHING is not intended to become a repository of passports, identity documents, banking information or other highly sensitive personal data.

Privacy requirements will be reviewed for each jurisdiction before real-world commercial deployment.

---

## Legal Position

This repository contains an experimental prototype and research project.

NOTHING does not claim to be:

- A government authority
- A trademark office
- A certificate authority
- A qualified trust service provider
- A bank
- A payment institution
- A financial regulator
- A legal authority
- An official representative of any third-party brand

References to companies or standards do not imply endorsement, partnership or authorization.

Third-party trademarks remain the property of their respective owners.

---

## Payments

The prototype does **not** enable:

- Visa
- Mastercard
- Bank transfers
- Card checkout

The long-term commercial model may explore **cryptocurrency-only settlement**, subject to applicable legal, tax, sanctions, AML/CFT and virtual-asset-service requirements in the relevant jurisdictions.

**No real payment processing is active in the current prototype.**

---

## Technology Direction

NOTHING aims to build on open standards rather than create unnecessary proprietary systems.

Relevant areas of research include:

- W3C Verifiable Credentials
- OpenID for Verifiable Credentials
- GLEIF / Legal Entity Identifier ecosystem
- BIMI
- GS1 Digital Link
- Web security standards
- Modern cryptographic signing and key management

The project is intended to complement existing infrastructure rather than replace systems that already solve adjacent problems.

---

## Zero-Dollar Prototype

The initial development goal is:

> **Prove the product before spending money.**

The prototype is intended to run using free developer infrastructure where practical.

The first phase is not intended to require:

- Paid hosting
- Paid advertising
- Paid databases
- Employees
- Office space
- Payment processing
- Expensive enterprise software

Free-tier limits and provider policies can change over time.

---

## Project Roadmap

### Phase 0 — Concept

Define the problem, terminology, principles and legal boundaries.

### Phase 1 — Identity Schema

Define the structure of a Nothing ID and its claims.

### Phase 2 — Verification Engine

Build claim verification, evidence handling, status management and revocation.

### Phase 3 — Verify Web

Create a simple public verification page.

### Phase 4 — API

Allow software to query business identities programmatically.

### Phase 5 — Pilot

Test the system with real businesses that voluntarily participate.

### Phase 6 — Network

Explore integrations with marketplaces, business software and AI systems.

### Phase 7 — Commercial Infrastructure

Introduce paid enterprise services only after utility, security and legal requirements are established.

---

## Prototype Success Criteria

The first version should succeed at three things:

### For people

A normal user can understand the identity status within seconds.

### For businesses

A business can present a portable and updateable identity record.

### For machines

Software can query a structured record and receive a clear result.

If the prototype cannot achieve these three goals simply and reliably, the product needs to change.

---

## Development Philosophy

NOTHING follows:

> **Prove before spending.**  
> **Verify before claiming.**  
> **Minimize data.**  
> **Use open standards.**  
> **Build for humans and machines.**

The project should earn trust through transparent verification rather than through slogans.


---

## Technical Repository Structure

The prototype now has a deliberately separated foundation for identity, evidence, verification events and verification procedures:

```text
NOTHING/
├── schema/
│   ├── identity.schema.json
│   ├── evidence.schema.json
│   ├── verification-event.schema.json
│   ├── procedure.schema.json
│   └── procedure-registry.schema.json
├── procedures/
│   └── registry.json
├── examples/
│   ├── NTH-000001.json
│   ├── EVD-000001.json
│   └── VER-000001.json
├── src/
│   ├── nothing_verify.py
│   └── nothing_protocol.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── VERIFICATION.md
│   ├── VERIFICATION-PROTOCOL.md
│   ├── EVIDENCE-MODEL.md
│   ├── VERIFICATION-EVENT-MODEL.md
│   ├── IDENTITY-EVIDENCE-RELATIONSHIP.md
│   ├── SCHEMA-PARITY.md
│   ├── RELATIONSHIP-RESOLUTION.md
│   ├── PROCEDURE-REGISTRY.md
│   ├── THREAT-MODEL.md
│   └── ROADMAP.md
├── tests/
│   ├── fixtures/
│   ├── test_identity.py
│   ├── test_schema_parity.py
│   └── test_protocol.py
├── requirements-dev.txt
└── SECURITY.md
```

The architecture deliberately separates:

```text
Identity
   ↓
Claim
   ↓
Evidence
   ↓
Verification Event
   ↓
Versioned Procedure
```

The repository does not collapse these layers into a universal trust score.

### Local Prototype Validation

From the repository root:

```bash
python -m unittest discover -s tests -v
```

A single identity record can also be checked directly:

```bash
python src/nothing_verify.py examples/NTH-000001.json
```

The structural validator checks data contracts only. The protocol resolver checks cross-record relationships and procedure compatibility. Neither one independently proves that an external source is truthful.

### Schema Parity

Development dependencies are pinned in requirements-dev.txt.

The test suite validates the JSON Schema documents using Draft 2020-12, then checks that representative valid and invalid records produce matching results in the JSON Schema validator and the custom structural validators.

### Protocol Resolution

The resolver can assemble:

Identity + Evidence + Verification Events + Procedure Registry

and verify referential integrity, procedure/result compatibility, procedure lifecycle timing and verification-event supersession rules.

---

## Status

**Project:** NOTHING  
**Version:** 0.1  
**Stage:** Prototype / Research  
**Cost Target:** $0 for initial proof-of-concept  
**Primary Concept:** Business Identity Verification  
**Working Product Name:** Nothing Passport™  
**Human-facing Marker:** ◇

---

## License

The licensing model for the prototype is not yet finalized.

Until a license is added to this repository, please treat the source code as **all rights reserved** and do not redistribute or commercially reuse it without permission.

---

## Disclaimer

This project is experimental.

Nothing in this repository constitutes legal, financial, regulatory, cybersecurity or compliance advice.

Before commercial deployment, the project will require jurisdiction-specific legal, privacy, security and regulatory review.


## Engineering Guardrails

The project now has an automated test workflow, a deterministic structural validator, JSON Schema parity tests, a cross-record protocol resolver, a versioned verification procedure registry, a threat model and an explicit verification protocol. These are intentionally conservative foundations: technical validity is kept separate from evidence-based verification.

The next implementation work should preserve backward compatibility of the v0.1 identity contract. Breaking schema changes should use an explicit version rather than silently changing the meaning of existing records.
