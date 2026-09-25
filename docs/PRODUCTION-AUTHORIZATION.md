# NOTHING Production Authentication & Authorization

## Scope

The production write boundary is an OAuth 2.0 resource-server boundary.

It accepts signed JWT access tokens, not browser sessions and not OpenID Connect ID tokens.

The reference route remains:

\`POST /v1/ingestion/bundles\`

## Token profile

Production access tokens must use the JWT access-token profile expected by this service:

- asymmetric signature
- \`typ = at+jwt\` or \`application/at+jwt\`
- configured issuer
- configured audience for the NOTHING API
- \`sub\`
- \`client_id\`
- \`iat\`
- \`exp\`
- \`jti\`
- \`scope\`

The validator has an explicit signing-algorithm allow-list. Symmetric HMAC algorithms are rejected by configuration.

The service does not accept an ID Token as an access token merely because the ID Token happens to be a valid JWT.

## Validation pipeline

\`\`\`text
Authorization: Bearer <JWT>
        |
        +--> parse scheme
        +--> inspect typ + alg
        +--> resolve signing key from fixed HTTPS JWKS URI
        +--> verify signature
        +--> verify issuer
        +--> verify audience
        +--> verify exp / iat requirements
        +--> verify required token claims
        +--> construct principal
        |
        v
scope authorization
        |
        +--> nothing:ingest
        |
        v
authenticated ingestion
\`\`\`

The key source is operator-configured. The token does not control where its signing keys are fetched from.

## Configuration

Required:

- \`NOTHING_AUTH_MODE=oidc-jwt\`
- \`NOTHING_AUTH_ISSUER=https://...\`
- \`NOTHING_AUTH_AUDIENCE=...\`
- \`NOTHING_AUTH_JWKS_URI=https://...\`

Optional:

- \`NOTHING_AUTH_REQUIRED_SCOPE=nothing:ingest\`
- \`NOTHING_AUTH_ALLOWED_ALGORITHMS=RS256\`
- \`NOTHING_AUTH_CLOCK_SKEW_SECONDS=60\`
- \`NOTHING_AUTH_JWKS_CACHE_SECONDS=300\`
- \`NOTHING_AUTH_JWKS_TIMEOUT_SECONDS=5\`

The reference code defaults to RS256 only. A deployment that needs another asymmetric algorithm must configure it explicitly and ensure the identity provider's access-token policy matches.

The static bearer mode remains for local/reference use. It is not the production mode.

## Authorization model

The API does not trust a role sent by the caller.

The principal is built from cryptographically validated token claims:

- audit actor: \`<issuer>#<sub>\`
- subject: \`sub\`
- client application: \`client_id\`
- permissions: \`scope\`

The ingestion action currently requires:

\`nothing:ingest\`

Future actions should introduce separate scopes rather than reusing this permission for unrelated mutations.

Tenant-specific authorization is intentionally not inferred from a token claim in the current schema because the stored protocol graph is not yet tenant-partitioned. A future multi-tenant design must introduce explicit tenant ownership and database isolation before tenant claims are used as an authorization boundary.

## Key rotation

JWKS keys are cached by the PyJWT client and can refresh when an unknown key ID is encountered.

The service should therefore:

1. publish the new signing key before issuing tokens that use it
2. keep the old verification key available during the overlap period
3. rotate signing keys under the identity provider's published policy
4. monitor authentication failures during rotation

The application should not hard-code public signing keys into source control.

## Failure semantics

Authentication failures return:

- HTTP \`401\`
- \`application/problem+json\`
- \`WWW-Authenticate: Bearer ... error="invalid_token"\`

Valid authentication with insufficient permission returns:

- HTTP \`403\`
- \`application/problem+json\`

No validation detail about signatures, key IDs or claim mismatch is returned to the caller.

## Operational requirements

Production must add:

- TLS end-to-end or TLS termination at a trusted gateway
- managed secrets and configuration
- identity-provider monitoring
- clock synchronization
- centralized auth failure metrics
- audit access controls
- gateway abuse controls
- a defined credential revocation/disable procedure

For high-value environments, sender-constrained tokens such as DPoP can be considered as defense in depth against replay of stolen bearer credentials; DPoP does not replace HTTPS. citeturn866813search1

## Reference implementation boundary

The service validates access tokens but does not operate the authorization server.

Token issuance, user login, MFA policy, client registration and key management belong to the selected identity provider / authorization server.
