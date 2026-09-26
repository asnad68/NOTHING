# NOTHING Cryptographic Proof Layer v0.1

## Purpose

This layer cryptographically binds a NOTHING resource to an issuer key. It separates integrity/authorship proof from the business meaning of the underlying claim.

A successful cryptographic check proves that the exact signed resource hash and proof metadata were signed by the configured issuer key. It does not prove that the underlying claim is true or that the issuer is legally authoritative.

## Envelope

The proof is detached from Identity, Evidence and Verification Event records:

~~~text
Resource
  |
  +--> deterministic canonical JSON
  |
  +--> SHA-256(resource)
             |
             v
       Proof Envelope
             |
             +--> issuer_id + key_id
             +--> created
             +--> Ed25519 signature
~~~

Detached proof keeps the existing v0.1 resource contracts stable.

## Canonicalization

RFC 8785 explains why deterministic JSON is required for repeatable hashing and signing. NOTHING v0.1 adopts its UTF-16 object-key ordering model but intentionally rejects floating-point numbers. Therefore this release is not claimed to be a full RFC 8785 implementation.

This conservative restriction prevents cross-language number-rendering ambiguity until a fully tested JCS implementation is adopted.

## Algorithm

The prototype uses Ed25519 through the Python cryptography library. Public keys are raw 32-byte values and private seeds are raw 32-byte values, encoded as unpadded base64url.

W3C's Data Integrity EdDSA Cryptosuites v1.0 is a Recommendation. NOTHING v0.1 does not claim W3C Data Integrity conformance; it defines a smaller envelope so the project's trust model can be tested before a standards-profile implementation is adopted.

## Issuer registry

Verification resolves:

~~~text
issuer_id + key_id -> issuer registry -> public key
~~~

The registry is local trust configuration, not a global authority.

Supported key lifecycle states are:

- ACTIVE
- RETIRED
- REVOKED

Keys may also declare valid-from and valid-until times.

## Verification order

1. Validate envelope structure.
2. Recompute the resource SHA-256 digest.
3. Resolve issuer and key.
4. Check issuer/key lifecycle and time validity.
5. Verify the Ed25519 signature.
6. Only then interpret application-level claims.

## Private-key rule

Private keys must never be committed to GitHub, fixtures or configuration tracked by source control. The demo proof was produced from a temporary key; only the public verification key remains in the repository.

## Threats addressed

- resource tampering after signing;
- signature substitution;
- unknown issuer/key;
- retired/revoked key use;
- time-window misuse;
- proof metadata manipulation.

## Not solved by cryptography

It does not by itself establish:

- legal existence of a business;
- trademark ownership;
- authority of an individual;
- truth of external evidence;
- legitimacy of a website;
- legitimacy of a commercial transaction.

Those remain responsibilities of evidence, verification procedures and authorization.

## Production gate

Before production use, NOTHING must add cross-runtime test vectors, an audited key-management process, issuer onboarding/governance, compromise and rotation drills, immutable issuer-registry change records, and proof verification in every public read path.
