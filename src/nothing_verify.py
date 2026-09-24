"""Core deterministic validation for the NOTHING v0.1 identity model."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

NOTHING_ID_RE = re.compile(r"^NTH-[0-9]{6}$")
CLAIM_ID_RE = re.compile(r"^CLM-[0-9]{6}$")
SUBJECT_TYPES = {"business", "brand", "digital_channel", "authorized_agent", "other"}
STATUSES = {"VERIFIED", "SOURCE-VERIFIED", "SELF-CLAIMED", "REVOKED"}
SOURCE_TYPES = {"official_website", "public_record", "authorized_document", "third_party_source", "self_attestation"}
AUTHORIZATION_STATUSES = {"CONFIRMED", "NOT_CONFIRMED"}
REVOCATION_STATUSES = {"NOT_REVOKED", "REVOKED"}


class ValidationError(ValueError):
    """Raised when a record violates the v0.1 structural contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _optional_string(value: Any, field: str) -> None:
    if value is not None:
        _require(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")


def _optional_datetime(value: Any, field: str) -> None:
    if value is not None:
        _require(isinstance(value, str), f"{field} must be an ISO-8601 string")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"{field} must be a valid ISO-8601 date-time") from exc


def _validate_source(source: Any) -> None:
    _require(isinstance(source, Mapping), "claim.source must be an object")
    if "type" in source:
        _require(source["type"] in SOURCE_TYPES, "claim.source.type is not supported")
    _optional_string(source.get("reference"), "claim.source.reference")
    _optional_datetime(source.get("checked_at"), "claim.source.checked_at")


def _validate_authorization(authorization: Any) -> None:
    _require(isinstance(authorization, Mapping), "claim.authorization must be an object")
    if "status" in authorization:
        _require(
            authorization["status"] in AUTHORIZATION_STATUSES,
            "claim.authorization.status is not supported",
        )
    _optional_string(authorization.get("method"), "claim.authorization.method")


def validate_identity(record: Mapping[str, Any]) -> None:
    """Validate deterministic structural rules only."""
    _require(isinstance(record, Mapping), "identity must be an object")
    _require(
        isinstance(record.get("nothing_id"), str)
        and NOTHING_ID_RE.fullmatch(record["nothing_id"]) is not None,
        "nothing_id must match NTH-XXXXXX",
    )
    _require(record.get("version") == "0.1", "version must be 0.1")

    subject = record.get("subject")
    _require(isinstance(subject, Mapping), "subject must be an object")
    _require(
        isinstance(subject.get("name"), str) and bool(subject["name"].strip()),
        "subject.name must be a non-empty string",
    )
    _require(subject.get("type") in SUBJECT_TYPES, "subject.type is not supported")
    _optional_string(subject.get("website"), "subject.website")

    claims = record.get("claims")
    _require(isinstance(claims, list), "claims must be an array")

    seen_claim_ids: set[str] = set()
    for claim in claims:
        _require(isinstance(claim, Mapping), "each claim must be an object")
        claim_id = claim.get("claim_id")
        _require(
            isinstance(claim_id, str) and CLAIM_ID_RE.fullmatch(claim_id) is not None,
            "claim_id must match CLM-XXXXXX",
        )
        _require(claim_id not in seen_claim_ids, f"duplicate claim_id: {claim_id}")
        seen_claim_ids.add(claim_id)
        _require(
            isinstance(claim.get("statement"), str) and bool(claim["statement"].strip()),
            "claim.statement must be a non-empty string",
        )
        _require(claim.get("status") in STATUSES, "claim.status is not supported")
        if "source" in claim:
            _validate_source(claim["source"])
        if "authorization" in claim:
            _validate_authorization(claim["authorization"])
        _optional_datetime(claim.get("valid_from"), "claim.valid_from")
        _optional_datetime(claim.get("valid_until"), "claim.valid_until")

    if "revocation" in record:
        revocation = record["revocation"]
        _require(isinstance(revocation, Mapping), "revocation must be an object")
        _require(revocation.get("status") in REVOCATION_STATUSES, "revocation.status is not supported")
        _optional_string(revocation.get("reason"), "revocation.reason")
        _optional_datetime(revocation.get("revoked_at"), "revocation.revoked_at")


def load_identity(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_and_validate(path: str | Path) -> dict[str, Any]:
    record = load_identity(path)
    validate_identity(record)
    return record


def validation_result(record: Mapping[str, Any]) -> dict[str, Any]:
    try:
        validate_identity(record)
    except ValidationError as exc:
        return {"valid": False, "nothing_id": record.get("nothing_id"), "error": str(exc)}
    return {"valid": True, "nothing_id": record["nothing_id"], "version": record["version"]}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate a NOTHING identity JSON record.")
    parser.add_argument("file")
    args = parser.parse_args()
    try:
        identity = load_and_validate(args.file)
        print(f"VALID: {identity['nothing_id']}")
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {exc}")
        raise SystemExit(1)
