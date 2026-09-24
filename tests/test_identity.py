import json
import unittest
from pathlib import Path

from src.nothing_verify import ValidationError, load_and_validate, validate_identity

ROOT = Path(__file__).resolve().parents[1]


class IdentityValidationTests(unittest.TestCase):
    def test_valid_fixture(self) -> None:
        record = load_and_validate(ROOT / "tests/fixtures/valid-identity.json")
        self.assertEqual(record["nothing_id"], "NTH-123456")

    def test_invalid_id_is_rejected(self) -> None:
        record = json.loads((ROOT / "tests/fixtures/invalid-identity.json").read_text())
        with self.assertRaises(ValidationError):
            validate_identity(record)

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
        with self.assertRaises(ValidationError):
            validate_identity(record)


if __name__ == "__main__":
    unittest.main()
