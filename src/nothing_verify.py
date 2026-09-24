"""Minimal dependency-free validator for the NOTHING v0.1 identity record."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

NOTHING_ID_RE = re.compile(r"^NTH-[0-9]{6}$")
CLAIM_ID_RE = re.compile(r"^CLM-[0-9]{6}$")
SUBJECT_TYPES = {"business", "brand", "digital_channel", "authorized_agent", "other"}
STATUSES = {"VERIFIED", "SOURCE-VERIFIED", "SELF-CLAIMED", "REVOKED"}
REVOCATION_STATUSES = {"NOT_REVOKED", "REVOKED"}


class ValidationError(ValueError):
    """Raised when an identity record does not satisfy the v0.1 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_identity(record: dict[str, Any]) -> None:
    """Validate the core structural rules of a NOTHING v0.1 identity.

    This intentionally implements only the deterministic rules needed for the
    prototype. It does not decide whether external evidence is truthful.
    """
    _require(isinstance(record, dict), "identity must be an object")
    _require(NOTHING_ID_RE.fullmatch(str(record.get("nothing_id", ""))) is not None,
             "nothing_id must match NTH-XXXXXX")
    _require(record.get("version") == "0.1", "version must be 0.1")

    subject = record.get("subject")
    _require(isinstance(subject, dict), "subject must be an object")
    _require(isinstance(subject.get("name"), str) and bool(subject["name"].strip()),
             "subject.name must be a non-empty string")
    _require(subject.get("type") in SUBJECT_TYPES,
             "subject.type is not a supported value")

    claims = record.get("claims")
    _require(isinstance(claims, list), "claims must be an array")

    seen_claim_ids: set[str] = set()
    for claim in claims:
        _require(isinstance(claim, dict), "each claim must be an object")
        claim_id = claim.get("claim_id", "")
        _require(isinstance(claim_id, str) and CLAIM_ID_RE.fullmatch(claim_id) is not None,
                 "claim_id must match CLM-XXXXXX")
        _require(claim_id not in seen_claim_ids, f"duplicate claim_id: {claim_id}")
        seen_claim_ids.add(claim_id)
        _require(isinstance(claim.get("statement"), str) and bool(claim["statement"].strip()),
                 "claim.statement must be a non-empty string")
        _require(claim.get("status") in STATUSES, "claim.status is not supported")

    if "revocation" in record:
        revocation = record["revocation"]
        _require(isinstance(revocation, dict), "revocation must be an object")
        _require(revocation.get("status") in REVOCATION_STATUSES,
                 "revocation.status is not supported")


def load_and_validate(path: str | Path) -> dict[str, Any]:
    """Load a JSON identity record and validate it."""
    file_path = Path(path)
    record = json.loads(file_path.read_text(encoding="utf-8"))
    validate_identity(record)
    return record


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate a NOTHING identity JSON record.")
    parser.add_argument("file", help="Path to the identity JSON record")
    args = parser.parse_args()

    try:
        identity = load_and_validate(args.file)
        print(f"VALID: {identity['nothing_id']}")
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {exc}")
        raise SystemExit(1)
