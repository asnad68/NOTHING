import time
import unittest
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.nothing_auth import (
    AuthenticatedPrincipal,
    AuthConfigurationError,
    OIDCJwtAuthenticator,
)


class FakeJWKClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.key)


class OIDCJwtAuthenticatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.private_pem = cls.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.public_pem = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def make_authenticator(self):
        return OIDCJwtAuthenticator(
            issuer="https://issuer.example",
            audience="https://api.example",
            jwks_uri="https://issuer.example/.well-known/jwks.json",
            required_scope="nothing:ingest",
            jwks_client=FakeJWKClient(self.public_pem),
        )

    def token(self, **overrides):
        now = int(time.time())
        payload = {
            "iss": "https://issuer.example",
            "sub": "service-001",
            "aud": "https://api.example",
            "exp": now + 300,
            "iat": now,
            "jti": "jti-001",
            "client_id": "client-001",
            "scope": "nothing:ingest",
        }
        payload.update(overrides)
        return jwt.encode(
            payload,
            self.private_pem,
            algorithm="RS256",
            headers={
                "kid": "key-001",
                "typ": "at+jwt",
            },
        )

    def test_valid_access_token_authenticates_and_authorizes(self):
        auth = self.make_authenticator()
        principal = auth.authenticate(
            f"Bearer {self.token()}"
        )
        self.assertIsInstance(principal, AuthenticatedPrincipal)
        self.assertEqual(
            principal.actor,
            "https://issuer.example#service-001",
        )
        self.assertEqual(principal.client_id, "client-001")
        self.assertTrue(
            auth.authorize(principal, "nothing:ingest")
        )

    def test_wrong_audience_is_rejected(self):
        auth = self.make_authenticator()
        principal = auth.authenticate(
            f"Bearer {self.token(aud='https://other.example')}"
        )
        self.assertIsNone(principal)

    def test_wrong_issuer_is_rejected(self):
        auth = self.make_authenticator()
        principal = auth.authenticate(
            f"Bearer {self.token(iss='https://attacker.example')}"
        )
        self.assertIsNone(principal)

    def test_missing_required_scope_is_forbidden(self):
        auth = self.make_authenticator()
        principal = auth.authenticate(
            f"Bearer {self.token(scope='openid')}"
        )
        self.assertIsInstance(principal, AuthenticatedPrincipal)
        self.assertFalse(
            auth.authorize(principal, "nothing:ingest")
        )

    def test_wrong_token_type_is_rejected(self):
        auth = self.make_authenticator()
        token = jwt.encode(
            {
                "iss": "https://issuer.example",
                "sub": "service-001",
                "aud": "https://api.example",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
                "jti": "jti-002",
                "client_id": "client-001",
                "scope": "nothing:ingest",
            },
            self.private_pem,
            algorithm="RS256",
            headers={
                "kid": "key-001",
                "typ": "JWT",
            },
        )
        self.assertIsNone(auth.authenticate(f"Bearer {token}"))

    def test_expired_token_is_rejected(self):
        auth = self.make_authenticator()
        principal = auth.authenticate(
            f"Bearer {self.token(exp=int(time.time()) - 1)}"
        )
        self.assertIsNone(principal)

    def test_hs256_configuration_is_rejected(self):
        with self.assertRaises(AuthConfigurationError):
            OIDCJwtAuthenticator(
                issuer="https://issuer.example",
                audience="https://api.example",
                jwks_uri="https://issuer.example/.well-known/jwks.json",
                allowed_algorithms=("HS256",),
                jwks_client=FakeJWKClient(self.public_pem),
            )

    def test_non_https_jwks_uri_is_rejected(self):
        with self.assertRaises(AuthConfigurationError):
            OIDCJwtAuthenticator(
                issuer="https://issuer.example",
                audience="https://api.example",
                jwks_uri="http://issuer.example/jwks",
                jwks_client=FakeJWKClient(self.public_pem),
            )


if __name__ == "__main__":
    unittest.main()
