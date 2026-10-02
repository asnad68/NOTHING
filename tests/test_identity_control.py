import unittest

from src.nothing_identity_control import (
    IdentityControlError,
    build_domain_challenge,
    build_siwe_message,
    domain_from_email,
    domain_from_url,
    evaluate_official_claim,
    normalize_domain,
    verify_google_workspace_principal,
    wallet_principal,
)


class IdentityControlTests(unittest.TestCase):
    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("WWW.Example.COM."), "www.example.com")
        self.assertEqual(domain_from_email("Admin@Example.com"), "example.com")
        self.assertEqual(domain_from_url("https://example.com/path"), "example.com")

    def test_private_domain_rejected(self):
        for value in ("localhost", "example.local", "example.internal", "127.0.0.1"):
            with self.assertRaises(IdentityControlError):
                normalize_domain(value)

    def test_google_workspace_requires_verified_org_domain(self):
        principal = verify_google_workspace_principal(
            subject="google-sub-1",
            email="admin@apple.com",
            email_verified=True,
            hosted_domain="apple.com",
            expected_domain="apple.com",
        )
        self.assertEqual(principal.method, "google_workspace")
        self.assertEqual(principal.email_domain, "apple.com")

    def test_personal_google_address_cannot_be_bound_to_org_domain(self):
        with self.assertRaises(IdentityControlError):
            verify_google_workspace_principal(
                subject="google-sub-2",
                email="someone@gmail.com",
                email_verified=True,
                hosted_domain=None,
                expected_domain="apple.com",
            )

    def test_domain_challenge_is_stable_when_supplied(self):
        challenge = build_domain_challenge("apple.com", challenge="fixed-test-token-12345678901234567890")
        self.assertEqual(challenge.record_name, "_nothing-challenge.apple.com")
        self.assertEqual(challenge.record_value, "NOTHING-DOMAIN-VERIFICATION=fixed-test-token")
        self.assertEqual(len(challenge.challenge_digest), 64)

    def test_wallet_is_not_company_ownership_by_itself(self):
        principal = wallet_principal(
            subject="wallet-subject",
            address="0x1111111111111111111111111111111111111111",
        )
        decision = evaluate_official_claim(
            brand_name="Apple",
            requested_domain="apple.com",
            domain_controlled=False,
            principal=principal,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "AUTHENTICATED_BUT_NOT_ORGANIZATION_CONTROLLED")

    def test_wrong_domain_cannot_pass_apple_automatic_gate(self):
        principal = wallet_principal(
            subject="wallet-subject-2",
            address="0x2222222222222222222222222222222222222222",
        )
        decision = evaluate_official_claim(
            brand_name="Apple",
            requested_domain="example.com",
            domain_controlled=True,
            principal=principal,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "REQUIRES_AUTHORITATIVE_REVIEW")

    def test_direct_brand_domain_match_can_pass(self):
        principal = verify_google_workspace_principal(
            subject="google-sub-4",
            email="admin@apple.com",
            email_verified=True,
            hosted_domain="apple.com",
            expected_domain="apple.com",
        )
        decision = evaluate_official_claim(
            brand_name="Apple",
            requested_domain="apple.com",
            domain_controlled=True,
            principal=principal,
            website_url="https://www.apple.com",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.state, "ORGANIZATION_CONTROLLED")

    def test_website_outside_domain_rejected(self):
        principal = wallet_principal(
            subject="wallet-subject-3",
            address="0x3333333333333333333333333333333333333333",
        )
        decision = evaluate_official_claim(
            brand_name="Example",
            requested_domain="example.com",
            domain_controlled=True,
            principal=principal,
            website_url="https://example.evil.example",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "DOMAIN_MISMATCH")

    def test_siwe_message_binds_to_origin_and_nonce(self):
        message = build_siwe_message(
            domain="nothing.example",
            address="0x1111111111111111111111111111111111111111",
            uri="https://nothing.example/",
            chain_id=1,
            nonce="ABCDEFGH1234",
            issued_at="2026-10-02T20:00:00Z",
        )
        self.assertIn("nothing.example wants you to sign in", message)
        self.assertIn("Nonce: ABCDEFGH1234", message)

    def test_signed_domain_challenge_expires_and_rejects_tampering(self):
        from datetime import datetime, timedelta, timezone
        from src.nothing_identity_control import issue_domain_challenge_token, verify_domain_challenge_token
        now = datetime(2026, 10, 2, 20, 0, 0, tzinfo=timezone.utc)
        challenge = issue_domain_challenge_token(
            "apple.com",
            "x" * 40,
            now=now,
            ttl_seconds=1800,
        )
        self.assertTrue(verify_domain_challenge_token(challenge.challenge, "apple.com", "x" * 40, now=now))
        self.assertFalse(verify_domain_challenge_token(challenge.challenge, "example.com", "x" * 40, now=now))
        expired = now + timedelta(seconds=1801)
        self.assertFalse(verify_domain_challenge_token(challenge.challenge, "apple.com", "x" * 40, now=expired))

    def test_idn_domain_normalizes_to_ascii(self):
        self.assertEqual(normalize_domain("münich.example"), "xn--mnich-kva.example")

if __name__ == "__main__":
    unittest.main()