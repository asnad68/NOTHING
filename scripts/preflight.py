#!/usr/bin/env python3
"""NOTHING repository preflight.

This command is intentionally read-only. It validates the repository's public
protocol fixtures and cryptographic demo before a release/deployment job can
publish them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator, FormatChecker

from src.nothing_proof import ProofError, verify_envelope
from src.nothing_protocol import resolve_claim_relationships, validate_procedure_registry
from src.nothing_verify import (
    ValidationError,
    validate_evidence,
    validate_identity,
    validate_verification_event,
)

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_schema(path: Path) -> None:
    Draft202012Validator.check_schema(read_json(path))


def assert_schema_valid(schema_path: Path, instance_path: Path) -> None:
    schema = read_json(schema_path)
    instance = read_json(instance_path)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance)
    )
    if errors:
        details = "; ".join(error.message for error in errors[:5])
        raise AssertionError(f"{instance_path}: schema validation failed: {details}")


def main() -> int:
    schema_paths = [
        ROOT / "schema/identity.schema.json",
        ROOT / "schema/evidence.schema.json",
        ROOT / "schema/verification-event.schema.json",
        ROOT / "schema/procedure.schema.json",
        ROOT / "schema/procedure-registry.schema.json",
        ROOT / "schema/proof.schema.json",
        ROOT / "schema/issuer-registry.schema.json",
    ]
    for path in schema_paths:
        check_schema(path)

    identity = read_json(ROOT / "examples/NTH-000001.json")
    evidence = read_json(ROOT / "examples/EVD-000001.json")
    event = read_json(ROOT / "examples/VER-000001.json")
    registry = read_json(ROOT / "procedures/registry.json")
    proof = read_json(ROOT / "examples/CRD-000001.json")
    issuer_registry = read_json(ROOT / "trust/issuer-registry.json")

    validate_identity(identity)
    validate_evidence(evidence)
    validate_verification_event(event)
    validate_procedure_registry(registry)

    resolve_claim_relationships(identity, [evidence], [event], registry)

    proof_result = verify_envelope(
        proof,
        identity,
        issuer_registry,
        at_time=proof["proof"]["created"],
    )
    if not proof_result["valid"]:
        raise AssertionError(f"cryptographic proof verification failed: {proof_result['reason']}")

    public_proof = read_json(ROOT / "site/data/demo-proof.json")
    public_registry = read_json(ROOT / "site/.well-known/nothing-keys.json")
    if public_proof != proof:
        raise AssertionError("site/data/demo-proof.json diverges from examples/CRD-000001.json")
    if public_registry != issuer_registry:
        raise AssertionError(
            "site/.well-known/nothing-keys.json diverges from trust/issuer-registry.json"
        )

    assert_schema_valid(
        ROOT / "schema/proof.schema.json",
        ROOT / "examples/CRD-000001.json",
    )
    assert_schema_valid(
        ROOT / "schema/issuer-registry.schema.json",
        ROOT / "trust/issuer-registry.json",
    )

    print("NOTHING PREFLIGHT: OK")
    print("Validated: schemas, protocol fixtures, relationship graph, cryptographic proof, public demo parity")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, ProofError, ValidationError, json.JSONDecodeError, OSError) as exc:
        print(f"NOTHING PREFLIGHT: FAILED — {exc}")
        raise SystemExit(1)
