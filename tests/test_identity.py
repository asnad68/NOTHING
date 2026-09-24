import json
import unittest
from pathlib import Path

from src.nothing_verify import load_and_validate, validation_result

ROOT = Path(__file__).resolve().parents[1]


class IdentityValidationTests(unittest.TestCase):
    def test_valid_fixture(self) -> None:
        record = load_and_validate(ROOT / "tests/fixtures/valid-identity.json")
        self.assertEqual(record["nothing_id"], "NTH-123456")

    def test_invalid_id_is_rejected(self) -> None:
        record = json.loads((ROOT / "tests/fixtures/invalid-identity.json").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
