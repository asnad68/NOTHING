# NOTHING Crypto Proof Stage

## Stage

**Cryptographic Proof Layer v0.1**

## Goal

Convert a mutable JSON presentation into a resource whose integrity and signer can be independently checked.

## Delivered

- Ed25519 proof generation and verification
- SHA-256 resource binding
- deterministic object-key ordering
- strict number handling for the signing profile
- issuer/key registry
- proof envelope schema
- issuer registry schema
- public demo key discovery document
- tamper and key-lifecycle tests
- payment work frozen as a separate phase boundary

## Current controlled integration

Proof verification is now surfaced in the public Verify Web demo. The browser recalculates the SHA-256 resource binding, resolves the published demo issuer/key registry, and attempts Ed25519 signature verification through Web Crypto. When the browser cannot perform Ed25519 verification, the UI reports the limitation instead of claiming success.

The production API read path still needs a durable proof resource/attachment model before it can publish proof results.

## Production gates

- cross-runtime canonicalization test vectors;
- external interoperability testing;
- secure signing-key management;
- trusted issuer governance;
- revocation and audit controls;
- legal and privacy review.
