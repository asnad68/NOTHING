import json
import unittest
from pathlib import Path

from src.nothing_verify import (
    ValidationError,
    load_and_validate,
    load_json,
    validate_evidence,
    validate_verification_event,
    validation_result,
)

ROOT = Path(__file__).resolve().parents[1]


class IdentityValidationTests(unittest.TestCase):
    def test_valid_fixture(self) -> None:
        record = load_and_validate(ROOT / "tests/fixtures/valid-identity.json")
        self.assertEqual(record["nothing_id"], "NTH-123456")

    def test_invalid_id_is_rejected(self) -> None:
        record = load_json(ROOT / "tests/fixtures/invalid-identity.json")
        self.assertFalse(validation_result(record)["valid"])

    def test_duplicate_claim_id_is_rejected(self) -> None:
        record = {
            "nothing_id": "NTH-123456",
            "version": "0.1",
            "subject": {"name": "Example", "type": "business"},
            "claims": [
                {"claim_id": "CLM-123456", "statement": "A", "status": "SELF-CLAIMED"},
                {"claim_id": "CLM-123456", "statement": "B", "status": "SELF-CLAIMED"},
            ],
        }
        self.assertFalse(validation_result(record)["valid"])

    def test_source_and_authorization(self) -> None:
        record = {
            "nothing_id": "NTH-654321",
            "version": "0.1",
            "subject": {"name": "Example Business", "type": "business"},
            "claims": [{
                "claim_id": "CLM-654321",
                "statement": "Example claim",
                "status": "SOURCE-VERIFIED",
                "source": {
                    "type": "official_website",
                    "reference": "https://example.com",
                    "checked_at": "2026-09-25T00:00:00Z",
                },
                "authorization": {"status": "CONFIRMED", "method": "test fixture"},
            }],
        }
        self.assertTrue(validation_result(record)["valid"])

    def test_bad_datetime_is_rejected(self) -> None:
        record = {
            "nothing_id": "NTH-123456",
            "version": "0.1",
            "subject": {"name": "Example", "type": "business"},
            "claims": [{
                "claim_id": "CLM-123456",
                "statement": "A",
                "status": "SELF-CLAIMED",
                "valid_from": "not-a-date",
            }],
        }
        self.assertFalse(validation_result(record)["valid"])

    def test_evidence_fixture(self) -> None:
        evidence = load_json(ROOT / "examples/EVD-000001.json")
        validate_evidence(evidence)

    def test_sha256_requires_digest(self) -> None:
        evidence = load_json(ROOT / "examples/EVD-000001.json")
        evidence["integrity"] = {"method": "sha256"}
        with self.assertRaises(ValidationError):
            validate_evidence(evidence)

    def test_verification_event_fixture(self) -> None:
        event = load_json(ROOT / "examples/VER-000001.json")
        validate_verification_event(event)

    def test_verification_event_rejects_bad_evidence_reference(self) -> None:
        event = load_json(ROOT / "examples/VER-000001.json")
        event["evidence"] = ["BAD-000001"]
        with self.assertRaises(ValidationError):
            validate_verification_event(event)

    def test_verification_event_requires_scope(self) -> None:
        event = load_json(ROOT / "examples/VER-000001.json")
        del event["result"]["scope"]
        with self.assertRaises(ValidationError):
            validate_verification_event(event)


if __name__ == "__main__":
    unittest.main()
