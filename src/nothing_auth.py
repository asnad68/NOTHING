"""Production authentication and authorization for the NOTHING write boundary.

The production path treats the API as an OAuth 2.0 resource server accepting
JWT access tokens. Tokens are validated against a configured JWKS endpoint and
must be audience-restricted, issuer-bound and explicitly typed as access
tokens. Authorization is scope-based.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse

DEFAULT_ALLOWED_ALGORITHMS = ("RS256",)
DEFAULT_REQUIRED_SCOPE = "nothing:ingest"
DEFAULT_CLOCK_SKEW_SECONDS = 60
DEFAULT_JWKS_CACHE_SECONDS = 300
DEFAULT_JWKS_TIMEOUT_SECONDS = 5


class AuthConfigurationError(ValueError):
    """Raised when production authentication is incorrectly configured."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    actor: str
    subject: str
    issuer: str
    client_id: str
    scopes: frozenset[str]
    claims: dict[str, Any]

    @property
    def tenant_id(self) -> str | None:
        value = self.claims.get("tenant_id")
        return value if isinstance(value, str) and value else None


class IngestionAuthenticator(Protocol):
    def authenticate(
        self,
        authorization: str | None,
    ) -> AuthenticatedPrincipal | None:
        ...

    def authorize(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
    ) -> bool:
        ...


class OIDCJwtAuthenticator:
    """JWT access-token validator for the production write boundary.

    This class intentionally consumes a fixed JWKS URL rather than discovering a
    JWKS URL from arbitrary token content. Discovery is an operator configuration
    concern. In production the URL must use HTTPS.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        allowed_algorithms: Sequence[str] = DEFAULT_ALLOWED_ALGORITHMS,
        required_scope: str = DEFAULT_REQUIRED_SCOPE,
        clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
        jwks_cache_seconds: int = DEFAULT_JWKS_CACHE_SECONDS,
        jwks_timeout_seconds: float = DEFAULT_JWKS_TIMEOUT_SECONDS,
        jwks_client: Any | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_uri = jwks_uri
        self.allowed_algorithms = tuple(sorted(set(allowed_algorithms)))
        self.required_scope = required_scope
        self.clock_skew_seconds = max(0, clock_skew_seconds)

        if not self.issuer.startswith("https://"):
            raise AuthConfigurationError(
                "NOTHING_AUTH_ISSUER must use https://"
            )
        if not self.audience:
            raise AuthConfigurationError(
                "NOTHING_AUTH_AUDIENCE must be non-empty"
            )
        parsed = urlparse(self.jwks_uri)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AuthConfigurationError(
                "NOTHING_AUTH_JWKS_URI must be an absolute HTTPS URL"
            )
        if not self.allowed_algorithms:
            raise AuthConfigurationError(
                "at least one JWT signing algorithm must be configured"
            )
        if any(
            algorithm.upper().startswith("HS")
            for algorithm in self.allowed_algorithms
        ):
            raise AuthConfigurationError(
                "symmetric HMAC algorithms are not permitted for production access tokens"
            )
        if not self.required_scope:
            raise AuthConfigurationError(
                "required authorization scope must be non-empty"
            )

        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as exc:
            raise AuthConfigurationError(
                "PyJWT[crypto] is required for production JWT authentication"
            ) from exc

        self._jwt = jwt
        self._jwks = jwks_client or PyJWKClient(
            self.jwks_uri,
            cache_jwk_set=True,
            lifespan=max(1, jwks_cache_seconds),
            timeout=max(0.1, jwks_timeout_seconds),
            cache_keys=True,
        )

    @property
    def configured(self) -> bool:
        return True

    @classmethod
    def from_environment(cls) -> "OIDCJwtAuthenticator":
        issuer = os.getenv("NOTHING_AUTH_ISSUER", "").strip()
        audience = os.getenv("NOTHING_AUTH_AUDIENCE", "").strip()
        jwks_uri = os.getenv("NOTHING_AUTH_JWKS_URI", "").strip()

        algorithms = tuple(
            item.strip()
            for item in os.getenv(
                "NOTHING_AUTH_ALLOWED_ALGORITHMS",
                ",".join(DEFAULT_ALLOWED_ALGORITHMS),
            ).split(",")
            if item.strip()
        )

        return cls(
            issuer=issuer,
            audience=audience,
            jwks_uri=jwks_uri,
            allowed_algorithms=algorithms,
            required_scope=os.getenv(
                "NOTHING_AUTH_REQUIRED_SCOPE",
                DEFAULT_REQUIRED_SCOPE,
            ).strip(),
            clock_skew_seconds=int(
                os.getenv(
                    "NOTHING_AUTH_CLOCK_SKEW_SECONDS",
                    str(DEFAULT_CLOCK_SKEW_SECONDS),
                )
            ),
            jwks_cache_seconds=int(
                os.getenv(
                    "NOTHING_AUTH_JWKS_CACHE_SECONDS",
                    str(DEFAULT_JWKS_CACHE_SECONDS),
                )
            ),
            jwks_timeout_seconds=float(
                os.getenv(
                    "NOTHING_AUTH_JWKS_TIMEOUT_SECONDS",
                    str(DEFAULT_JWKS_TIMEOUT_SECONDS),
                )
            ),
        )

    @staticmethod
    def _bearer_token(authorization: str | None) -> str | None:
        if not authorization:
            return None
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]

    def authenticate(
        self,
        authorization: str | None,
    ) -> AuthenticatedPrincipal | None:
        token = self._bearer_token(authorization)
        if token is None:
            return None

        try:
            header = self._jwt.get_unverified_header(token)
            token_type = header.get("typ")
            if token_type not in ("at+jwt", "application/at+jwt"):
                return None

            algorithm = header.get("alg")
            if algorithm not in self.allowed_algorithms:
                return None

            signing_key = self._jwks.get_signing_key_from_jwt(token)

            claims = self._jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.allowed_algorithms),
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.clock_skew_seconds,
                options={
                    "require": [
                        "iss",
                        "sub",
                        "aud",
                        "exp",
                        "iat",
                        "jti",
                        "client_id",
                    ],
                },
            )
        except Exception:
            # Deliberately collapse all token parsing/signature/claim failures
            # into the same authentication result to avoid oracle-like detail.
            return None

        subject = claims.get("sub")
        client_id = claims.get("client_id")
        issuer = claims.get("iss")
        if not all(
            isinstance(value, str) and value
            for value in (subject, client_id, issuer)
        ):
            return None
        if issuer.rstrip("/") != self.issuer:
            return None

        scope_claim = claims.get("scope", "")
        if not isinstance(scope_claim, str):
            return None
        scopes = frozenset(scope_claim.split())

        actor = f"{issuer.rstrip('/')}#{subject}"
        return AuthenticatedPrincipal(
            actor=actor,
            subject=subject,
            issuer=issuer.rstrip("/"),
            client_id=client_id,
            scopes=scopes,
            claims=dict(claims),
        )

    def authorize(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
    ) -> bool:
        if action == "nothing:ingest":
            return self.required_scope in principal.scopes
        return False
