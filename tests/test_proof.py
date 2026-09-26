import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from src.nothing_proof import (
    ProofError,
    canonical_bytes,
    create_envelope,
    generate_keypair,
    sha256_hex,
    verify_envelope,
)

ROOT = Path(__file__).resolve().parents[1]


class ProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = json.loads(
            (ROOT / "examples/NTH-000001.json").read_text(encoding="utf-8")
        )
        self.registry = json.loads(
            (ROOT / "trust/issuer-registry.json").read_text(encoding="utf-8")
        )
        self.envelope = json.loads(
            (ROOT / "examples/CRD-000001.json").read_text(encoding="utf-8")
        )

    def test_canonicalization_is_deterministic_and_nested(self) -> None:
        a = {"b": 2, "a": {"d": True, "c": [3, 1]}}
        b = {"a": {"c": [3, 1], "d": True}, "b": 2}
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))
        self.assertEqual(sha256_hex(a), sha256_hex(b))

    def test_float_is_rejected(self) -> None:
        with self.assertRaises(ProofError):
            canonical_bytes({"value": 1.5})

    def test_demo_proof_verifies(self) -> None:
        result = verify_envelope(
            self.envelope,
            self.identity,
            self.registry,
            at_time="2026-09-27T00:00:00Z",
        )
        self.assertTrue(result["valid"])
        self.assertTrue(all(result["checks"].values()))

    def test_tampered_resource_fails_hash_check(self) -> None:
        tampered = copy.deepcopy(self.identity)
        tampered["claims"][0]["statement"] = "tampered"
        result = verify_envelope(self.envelope, tampered, self.registry)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["resource_hash"])

    def test_tampered_signature_fails(self) -> None:
        envelope = copy.deepcopy(self.envelope)
        sig = envelope["proof"]["signature"]
        envelope["proof"]["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
        result = verify_envelope(envelope, self.identity, self.registry)
        self.assertFalse(result["valid"])
        self.assertTrue(result["checks"]["resource_hash"])
        self.assertTrue(result["checks"]["issuer_key"])
        self.assertFalse(result["checks"]["signature"])

    def test_unknown_issuer_fails_closed(self) -> None:
        envelope = copy.deepcopy(self.envelope)
        envelope["issuer"]["issuer_id"] = "NTH-UNKNOWN-ISSUER"
        result = verify_envelope(envelope, self.identity, self.registry)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["issuer_key"])

    def test_retired_key_fails_closed(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["issuers"][0]["keys"][0]["status"] = "RETIRED"
        result = verify_envelope(self.envelope, self.identity, registry)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["issuer_key"])

    def test_future_key_window_fails_at_observed_time(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["issuers"][0]["keys"][0]["valid_from"] = "2026-10-01T00:00:00Z"
        result = verify_envelope(
            self.envelope,
            self.identity,
            registry,
            at_time="2026-09-27T00:00:00Z",
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["issuer_key"])

    def test_wrong_resource_type_is_rejected(self) -> None:
        envelope = copy.deepcopy(self.envelope)
        envelope["resource_type"] = "evidence"
        with self.assertRaises(ProofError):
            verify_envelope(envelope, self.identity, self.registry)

    def test_new_key_can_sign_and_verify(self) -> None:
        private_key, public_key = generate_keypair()
        registry = copy.deepcopy(self.registry)
        registry["issuers"][0]["keys"].append(
            {
                "key_id": "temporary-test-key",
                "type": "Ed25519",
                "public_key": public_key,
                "status": "ACTIVE",
                "valid_from": "2026-09-27T00:00:00Z",
            }
        )
        envelope = create_envelope(
            self.identity,
            envelope_id="CRD-999999",
            resource_type="identity",
            resource_id="NTH-000001",
            issuer_id="NTH-DEMO-ISSUER",
            key_id="temporary-test-key",
            private_key_b64url=private_key,
            created_at="2026-09-27T00:00:00Z",
        )
        result = verify_envelope(
            envelope,
            self.identity,
            registry,
            at_time="2026-09-27T00:00:01Z",
        )
        self.assertTrue(result["valid"])

    def test_schema_files_are_valid_json_schema(self) -> None:
        for name in ("proof.schema.json", "issuer-registry.schema.json"):
            schema = json.loads((ROOT / "schema" / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)

    def test_demo_instances_match_schemas(self) -> None:
        proof_schema = json.loads(
            (ROOT / "schema/proof.schema.json").read_text(encoding="utf-8")
        )
        registry_schema = json.loads(
            (ROOT / "schema/issuer-registry.schema.json").read_text(encoding="utf-8")
        )
        proof_validator = Draft202012Validator(
            proof_schema, format_checker=FormatChecker()
        )
        registry_validator = Draft202012Validator(
            registry_schema, format_checker=FormatChecker()
        )
        self.assertEqual(list(proof_validator.iter_errors(self.envelope)), [])
        self.assertEqual(list(registry_validator.iter_errors(self.registry)), [])


if __name__ == "__main__":
    unittest.main()
