import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from src.nothing_protocol import (
    RelationshipError,
    validate_procedure,
    validate_procedure_registry,
)
from src.nothing_verify import (
    ValidationError,
    load_json,
    validate_evidence,
    validate_identity,
    validate_verification_event,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schema"

SCHEMA_FILES = {
    "identity": SCHEMA_DIR / "identity.schema.json",
    "evidence": SCHEMA_DIR / "evidence.schema.json",
    "verification-event": SCHEMA_DIR / "verification-event.schema.json",
    "procedure": SCHEMA_DIR / "procedure.schema.json",
    "procedure-registry": SCHEMA_DIR / "procedure-registry.schema.json",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_validator(name: str) -> Draft202012Validator:
    schema = read_json(SCHEMA_FILES[name])
    registry = Registry()
    if name == "procedure-registry":
        procedure_schema = read_json(SCHEMA_FILES["procedure"])
        registry = registry.with_resource(
            procedure_schema["$id"],
            Resource.from_contents(procedure_schema),
        )
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def schema_valid(name: str, instance: dict) -> bool:
    return not list(make_validator(name).iter_errors(instance))


def assert_parity(test_case: unittest.TestCase, name: str, instance: dict, custom_validator) -> None:
    schema_result = schema_valid(name, instance)
    try:
        custom_validator(instance)
        custom_result = True
    except (ValidationError, RelationshipError):
        custom_result = False
    test_case.assertEqual(
        schema_result,
        custom_result,
        msg=f"schema/custom validation diverged for {name}",
    )


class SchemaParityTests(unittest.TestCase):
    def test_all_schemas_validate_against_2020_12_meta_contract(self) -> None:
        for name, path in SCHEMA_FILES.items():
            schema = read_json(path)
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_valid_fixtures_match_custom_validators(self) -> None:
        identity = load_json(ROOT / "tests/fixtures/valid-identity.json")
        evidence = load_json(ROOT / "examples/EVD-000001.json")
        event = load_json(ROOT / "examples/VER-000001.json")
        registry = load_json(ROOT / "procedures/registry.json")
        procedure = registry["procedures"][0]

        assert_parity(self, "identity", identity, validate_identity)
        assert_parity(self, "evidence", evidence, validate_evidence)
        assert_parity(self, "verification-event", event, validate_verification_event)
        assert_parity(self, "procedure", procedure, validate_procedure)
        assert_parity(self, "procedure-registry", registry, validate_procedure_registry)

    def test_identity_revocation_requires_status(self) -> None:
        record = load_json(ROOT / "tests/fixtures/valid-identity.json")
        record["revocation"] = {}
        assert_parity(self, "identity", record, validate_identity)

    def test_identity_website_must_be_a_uri(self) -> None:
        record = load_json(ROOT / "tests/fixtures/valid-identity.json")
        record["subject"]["website"] = "not a uri"
        assert_parity(self, "identity", record, validate_identity)

    def test_evidence_none_cannot_have_digest(self) -> None:
        evidence = load_json(ROOT / "examples/EVD-000001.json")
        evidence["integrity"]["digest"] = "0" * 64
        assert_parity(self, "evidence", evidence, validate_evidence)

    def test_event_requires_occurred_at(self) -> None:
        event = load_json(ROOT / "examples/VER-000001.json")
        del event["occurred_at"]
        assert_parity(self, "verification-event", event, validate_verification_event)

    def test_event_requires_procedure_id_and_version(self) -> None:
        event = load_json(ROOT / "examples/VER-000001.json")
        del event["procedure"]["id"]
        assert_parity(self, "verification-event", event, validate_verification_event)

        event = load_json(ROOT / "examples/VER-000001.json")
        del event["procedure"]["version"]
        assert_parity(self, "verification-event", event, validate_verification_event)

    def test_extra_fields_are_rejected_by_both_layers(self) -> None:
        identity = load_json(ROOT / "tests/fixtures/valid-identity.json")
        identity["unexpected"] = True
        assert_parity(self, "identity", identity, validate_identity)

        evidence = load_json(ROOT / "examples/EVD-000001.json")
        evidence["unexpected"] = True
        assert_parity(self, "evidence", evidence, validate_evidence)

        event = load_json(ROOT / "examples/VER-000001.json")
        event["unexpected"] = True
        assert_parity(self, "verification-event", event, validate_verification_event)

        registry = load_json(ROOT / "procedures/registry.json")
        procedure = copy.deepcopy(registry["procedures"][0])
        procedure["unexpected"] = True
        assert_parity(self, "procedure", procedure, validate_procedure)

    def test_naive_datetime_is_rejected_by_both_layers(self) -> None:
        evidence = load_json(ROOT / "examples/EVD-000001.json")
        evidence["collected_at"] = "2026-09-25T00:00:00"
        assert_parity(self, "evidence", evidence, validate_evidence)

        registry = load_json(ROOT / "procedures/registry.json")
        procedure = copy.deepcopy(registry["procedures"][0])
        procedure["published_at"] = "2026-09-25T00:00:00"
        assert_parity(self, "procedure", procedure, validate_procedure)

    def test_deprecated_procedure_requires_deprecated_at(self) -> None:
        registry = load_json(ROOT / "procedures/registry.json")
        procedure = copy.deepcopy(registry["procedures"][0])
        procedure["status"] = "DEPRECATED"
        procedure.pop("deprecated_at", None)
        assert_parity(self, "procedure", procedure, validate_procedure)


if __name__ == "__main__":
    unittest.main()
