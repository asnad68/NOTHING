import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.nothing_enrollment import (
    EnrollmentValidationError,
    RegistrationDraft,
    allocate_nothing_id,
    build_self_claimed_identity,
)

class EnrollmentTests(unittest.TestCase):
    def test_canonical_digest_is_stable(self):
        a = RegistrationDraft.from_mapping({"name":"Example","domains":["example.com","shop.example.com"]})
        b = RegistrationDraft.from_mapping({"name":"Example","domains":["example.com","shop.example.com"]})
        self.assertEqual(a.digest(), b.digest())

    def test_id_allocator_is_unique_against_existing(self):
        draft = RegistrationDraft.from_mapping({"name":"Example"})
        digest = draft.digest()
        first = allocate_nothing_id(digest)
        second = allocate_nothing_id(digest, {first})
        self.assertNotEqual(first, second)
        self.assertRegex(second, r"^NTH-[0-9]{6}$")

    def test_registration_is_self_claimed_not_verified(self):
        draft = RegistrationDraft.from_mapping({
            "name":"Example Company",
            "website":"https://example.com",
            "domains":["example.com"],
            "channels":["https://www.linkedin.com/company/example"],
        })
        identity = build_self_claimed_identity(draft, "NTH-000101")
        self.assertEqual(identity["nothing_id"], "NTH-000101")
        self.assertTrue(identity["claims"])
        self.assertTrue(all(c["status"] == "SELF-CLAIMED" for c in identity["claims"]))
        self.assertFalse(any(c["status"] == "VERIFIED" for c in identity["claims"]))

    def test_rejects_bad_website(self):
        with self.assertRaises(EnrollmentValidationError):
            RegistrationDraft.from_mapping({"name":"Example", "website":"not-a-url"})

if __name__ == "__main__":
    unittest.main()
