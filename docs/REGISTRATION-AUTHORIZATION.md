# NOTHING Registration Authentication & Authorization

## Required trust model

NOTHING must never treat a successful login as proof that the logged-in person is entitled to register a brand.

Authentication establishes a principal. Authorization establishes whether that principal may make an official organization claim.

```text
Authentication
    |
    +--> Google Workspace identity
    |
    +--> Sign-In with Ethereum
    |
    v
Authenticated principal
    |
    v
Independent domain control
    |
    +--> DNS TXT challenge
    |
    v
Brand/domain binding
    |
    +--> direct brand-domain match
    |
    +--> authoritative registry/manual review
    v
Organization-controlled claim
```

## Google

Google authentication should use a cryptographically validated OpenID Connect ID token.

Required signals for the automatic organization path:

- valid signature and issuer
- expected OAuth client audience
- non-expired token
- `email_verified = true`
- Google Workspace `hd` equal to the organization domain
- email domain equal to the same organization domain
- independent DNS control of that domain

A personal `@gmail.com` identity cannot satisfy an official claim for `apple.com` because its email and hosted domain are not bound to `apple.com`.

## Wallet

Wallet authentication should use Sign-In with Ethereum (SIWE / ERC-4361). The server must issue a nonce and verify the returned message and signature against the exact HTTPS origin, chain ID, nonce and time window.

A wallet proves control of a cryptographic account. It does not prove company ownership by itself.

Therefore:

```text
wallet + no domain control -> reject official registration
wallet + domain control -> continue to brand/domain binding
```

Contract wallets should be supported later with ERC-1271.

## Domain control

The first automated organization-control method is DNS TXT:

```text
_nothing-challenge.example.com
TXT "NOTHING-DOMAIN-VERIFICATION=<signed-expiring-challenge>"
```

The challenge is signed by a server-side secret, expires quickly and is verified before DNS is queried. The secret is never sent to the browser as a separate credential.

DNS control is intentionally independent of the login provider.

## Official-name policy

Names that are not sufficiently connected to the controlled domain must not be automatically marked official.

For example:

```text
Brand: Apple
Authenticated account: someone@gmail.com
Controlled domain: example.com
Result: reject official registration
```

Where a legal entity and brand have different names, NOTHING should use an authoritative company/trademark/registry source or a manual review path instead of weak string matching.

## Payment separation

Payment must never be used as company-ownership evidence.

The paid enrollment gateway keeps payment separate from organization authorization. A self-claimed enrollment may be paid and remain self-claimed. An official enrollment must carry a previously authorized, registration-bound organization-control challenge; that authorization is consumed once when the identity record is written.

## One-time authorization

Official registration authorizations are persisted server-side in PostgreSQL and SQLite reference storage. Wallet challenges use a server-issued SIWE nonce; Google authorizations store only a hash of the external token and the authorization decision, not the raw token.

An authorization is bound to the registration digest and, for wallet authentication, to the challenged wallet. Final registration consumes the authorization once. Replaying the authentication or changing the registration data therefore cannot silently reuse the same authorization.

## Activation gates

The public prototype currently keeps wallet access and official registration disabled by default.

Before enabling official registration in production:

1. configure Google OAuth client credentials and allowed origins
2. configure the domain-challenge secret in a managed secret store
3. deploy the API behind HTTPS
4. enable DNS control verification
5. enable the SIWE verifier
6. add an explicit official-registration mode
7. test negative cases such as personal Gmail + protected brand, unrelated domain + brand, replayed nonce and expired domain challenge
8. complete the external MetaMask false-positive review for the public domain

## Security principle

Do not build:

```text
Google login -> typed brand name -> VERIFIED
Wallet connection -> typed brand name -> VERIFIED
Payment -> company ownership
```

Those flows are not strong enough for the purpose of an identity and verification infrastructure.