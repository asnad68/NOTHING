"""Core deterministic validation for the NOTHING v0.1 data models.

This module checks structural contracts only. Semantic relationships between
records are handled by src.nothing_protocol.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

NOTHING_ID_RE = re.compile(r"^NTH-[0-9]{6}$")
CLAIM_ID_RE = re.compile(r"^CLM-[0-9]{6}$")
EVIDENCE_ID_RE = re.compile(r"^EVD-[0-9]{6}$")
EVENT_ID_RE = re.compile(r"^VER-[0-9]{6}$")
SUBJECT_TYPES = {"business", "brand", "digital_channel", "authorized_agent", "other"}
STATUSES = {"VERIFIED", "SOURCE-VERIFIED", "SELF-CLAIMED", "REVOKED"}
SOURCE_TYPES = {
    "official_website",
    "public_record",
    "authorized_document",
    "third_party_source",
    "self_attestation",
}
AUTHORIZATION_STATUSES = {"CONFIRMED", "NOT_CONFIRMED"}
REVOCATION_STATUSES = {"NOT_REVOKED", "REVOKED"}
EVIDENCE_TYPES = {
    "web_page",
    "public_record",
    "authorized_document",
    "registry_record",
    "domain_control",
    "statement",
    "other",
}
INTEGRITY_METHODS = {"none", "sha256"}
EVENT_STATUSES = {
    "VERIFIED",
    "SOURCE-VERIFIED",
    "NOT-VERIFIED",
    "INCONCLUSIVE",
    "REVOKED",
}
VERIFIER_TYPES = {"automated", "human", "hybrid"}


class ValidationError(ValueError):
    """Raised when a record violates a NOTHING structural contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _optional_string(value: Any, field: str) -> None:
    if value is not None:
        _require(
            isinstance(value, str) and bool(value.strip()),
            f"{field} must be a non-empty string",
        )


def _required_string(value: Any, field: str) -> None:
    _require(
        isinstance(value, str) and bool(value.strip()),
        f"{field} must be a non-empty string",
    )


def _optional_datetime(value: Any, field: str) -> None:
    if value is not None:
        _require(isinstance(value, str), f"{field} must be an ISO-8601 string")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                f"{field} must be a valid ISO-8601 date-time"
            ) from exc


def _required_datetime(value: Any, field: str) -> None:
    _require(isinstance(value, str), f"{field} must be an ISO-8601 string")
    _optional_datetime(value, field)


def _optional_uri(value: Any, field: str) -> None:
    if value is not None:
        _required_string(value, field)
        parsed = urlparse(value)
        _require(bool(parsed.scheme), f"{field} must be a valid URI")


def _validate_source(source: Any) -> None:
    _require(isinstance(source, Mapping), "claim.source must be an object")
    if "type" in source:
        _require(
            source["type"] in SOURCE_TYPES,
            "claim.source.type is not supported",
        )
    _optional_string(source.get("reference"), "claim.source.reference")
    _optional_datetime(source.get("checked_at"), "claim.source.checked_at")


def _validate_authorization(authorization: Any) -> None:
    _require(
        isinstance(authorization, Mapping),
        "claim.authorization must be an object",
    )
    if "status" in authorization:
        _require(
            authorization["status"] in AUTHORIZATION_STATUSES,
            "claim.authorization.status is not supported",
        )
    _optional_string(authorization.get("method"), "claim.authorization.method")


def validate_identity(record: Mapping[str, Any]) -> None:
    """Validate deterministic structural rules for an identity record."""
    _require(isinstance(record, Mapping), "identity must be an object")
    _require(
        isinstance(record.get("nothing_id"), str)
        and NOTHING_ID_RE.fullmatch(record["nothing_id"]) is not None,
        "nothing_id must match NTH-XXXXXX",
    )
    _require(record.get("version") == "0.1", "version must be 0.1")

    subject = record.get("subject")
    _require(isinstance(subject, Mapping), "subject must be an object")
    _required_string(subject.get("name"), "subject.name")
    _require(
        subject.get("type") in SUBJECT_TYPES,
        "subject.type is not supported",
    )
    _optional_uri(subject.get("website"), "subject.website")

    claims = record.get("claims")
    _require(isinstance(claims, list), "claims must be an array")

    seen_claim_ids: set[str] = set()
    for claim in claims:
        _require(isinstance(claim, Mapping), "each claim must be an object")
        claim_id = claim.get("claim_id")
        _require(
            isinstance(claim_id, str)
            and CLAIM_ID_RE.fullmatch(claim_id) is not None,
            "claim_id must match CLM-XXXXXX",
        )
        _require(
            claim_id not in seen_claim_ids,
            f"duplicate claim_id: {claim_id}",
        )
        seen_claim_ids.add(claim_id)

        _required_string(claim.get("statement"), "claim.statement")
        _require(
            claim.get("status") in STATUSES,
            "claim.status is not supported",
        )

        if "source" in claim:
            _validate_source(claim["source"])
        if "authorization" in claim:
            _validate_authorization(claim["authorization"])

        _optional_datetime(claim.get("valid_from"), "claim.valid_from")
        _optional_datetime(claim.get("valid_until"), "claim.valid_until")

    if "revocation" in record:
        revocation = record["revocation"]
        _require(isinstance(revocation, Mapping), "revocation must be an object")
        _required_string(revocation.get("status"), "revocation.status")
        _require(
            revocation["status"] in REVOCATION_STATUSES,
            "revocation.status is not supported",
        )
        _optional_string(revocation.get("reason"), "revocation.reason")
        _optional_datetime(revocation.get("revoked_at"), "revocation.revoked_at")


def validate_evidence(record: Mapping[str, Any]) -> None:
    """Validate an EVD-XXXXXX evidence record."""
    _require(isinstance(record, Mapping), "evidence must be an object")
    evidence_id = record.get("evidence_id")
    _require(
        isinstance(evidence_id, str)
        and EVIDENCE_ID_RE.fullmatch(evidence_id) is not None,
        "evidence_id must match EVD-XXXXXX",
    )
    _require(record.get("version") == "0.1", "evidence version must be 0.1")
    _require(
        record.get("type") in EVIDENCE_TYPES,
        "evidence.type is not supported",
    )

    source = record.get("source")
    _require(isinstance(source, Mapping), "evidence.source must be an object")
    _required_string(
        source.get("reference"),
        "evidence.source.reference",
    )
    _optional_string(source.get("title"), "evidence.source.title")
    _optional_string(source.get("publisher"), "evidence.source.publisher")
    _optional_datetime(
        source.get("accessed_at"),
        "evidence.source.accessed_at",
    )

    _required_datetime(record.get("collected_at"), "evidence.collected_at")

    integrity = record.get("integrity")
    _require(
        isinstance(integrity, Mapping),
        "evidence.integrity is required",
    )
    method = integrity.get("method")
    _require(
        method in INTEGRITY_METHODS,
        "evidence.integrity.method is not supported",
    )

    digest = integrity.get("digest")
    if method == "sha256":
        _require(
            isinstance(digest, str)
            and re.fullmatch(r"[A-Fa-f0-9]{64}", digest) is not None,
            "sha256 evidence digest must be 64 hexadecimal characters",
        )
    else:
        _require(
            digest is None,
            "evidence.integrity.digest is not allowed when method is none",
        )

    _optional_string(record.get("notes"), "evidence.notes")


def validate_verification_event(record: Mapping[str, Any]) -> None:
    """Validate a VER-XXXXXX verification event."""
    _require(
        isinstance(record, Mapping),
        "verification event must be an object",
    )
    event_id = record.get("event_id")
    _require(
        isinstance(event_id, str)
        and EVENT_ID_RE.fullmatch(event_id) is not None,
        "event_id must match VER-XXXXXX",
    )
    _require(
        record.get("version") == "0.1",
        "verification event version must be 0.1",
    )
    _required_datetime(
        record.get("occurred_at"),
        "verification event occurred_at",
    )

    _require(
        isinstance(record.get("subject"), str)
        and NOTHING_ID_RE.fullmatch(record["subject"]) is not None,
        "verification event subject must match NTH-XXXXXX",
    )
    _require(
        isinstance(record.get("claim_id"), str)
        and CLAIM_ID_RE.fullmatch(record["claim_id"]) is not None,
        "verification event claim_id must match CLM-XXXXXX",
    )

    procedure = record.get("procedure")
    _require(
        isinstance(procedure, Mapping),
        "verification event procedure is required",
    )
    _required_string(procedure.get("id"), "procedure.id")
    _required_string(procedure.get("version"), "procedure.version")

    if "verifier" in record:
        verifier = record["verifier"]
        _require(
            isinstance(verifier, Mapping),
            "verification event verifier must be an object",
        )
        if "type" in verifier:
            _require(
                verifier["type"] in VERIFIER_TYPES,
                "verification event verifier.type is not supported",
            )
        _optional_string(
            verifier.get("identifier"),
            "verifier.identifier",
        )

    evidence = record.get("evidence", [])
    _require(
        isinstance(evidence, list),
        "verification event evidence must be an array",
    )
    seen: set[str] = set()
    for evidence_id in evidence:
        _require(
            isinstance(evidence_id, str)
            and EVIDENCE_ID_RE.fullmatch(evidence_id) is not None,
            "verification event evidence IDs must match EVD-XXXXXX",
        )
        _require(
            evidence_id not in seen,
            f"duplicate evidence ID: {evidence_id}",
        )
        seen.add(evidence_id)

    result = record.get("result")
    _require(
        isinstance(result, Mapping),
        "verification event result is required",
    )
    _require(
        result.get("status") in EVENT_STATUSES,
        "verification event result.status is not supported",
    )
    _required_string(
        result.get("scope"),
        "verification event result.scope",
    )
    _optional_string(result.get("reason"), "verification event result.reason")

    if "supersedes" in record:
        _require(
            isinstance(record["supersedes"], str)
            and EVENT_ID_RE.fullmatch(record["supersedes"]) is not None,
            "supersedes must match VER-XXXXXX",
        )


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_identity(path: str | Path) -> dict[str, Any]:
    return load_json(path)


def load_and_validate(path: str | Path) -> dict[str, Any]:
    record = load_identity(path)
    validate_identity(record)
    return record


def validation_result(record: Mapping[str, Any]) -> dict[str, Any]:
    try:
        validate_identity(record)
    except ValidationError as exc:
        return {
            "valid": False,
            "nothing_id": record.get("nothing_id"),
            "error": str(exc),
        }
    return {
        "valid": True,
        "nothing_id": record["nothing_id"],
        "version": record["version"],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate a NOTHING identity JSON record."
    )
    parser.add_argument("file")
    args = parser.parse_args()

    try:
        identity = load_and_validate(args.file)
        print(f"VALID: {identity['nothing_id']}")
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {exc}")
        raise SystemExit(1)
