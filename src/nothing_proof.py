"""Cryptographic proof primitives for the NOTHING v0.1 protocol.

The module implements a small proof envelope:
- SHA-256 binds a signed envelope to an exact JSON resource.
- Ed25519 signs a deterministic canonical representation of the envelope.
- An issuer registry maps issuer/key identifiers to accepted public keys.

This is a project-specific v0.1 profile. It does not by itself make an issuer
legally authoritative, nor does it turn evidence into truth. It proves that a
particular issuer key signed a particular resource hash and that the key was
accepted by the configured issuer registry at verification time.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROOF_VERSION = "0.1"
PROOF_TYPE = "NOTHING-ED25519"
PROOF_PURPOSE = "assertion"

RESOURCE_TYPES = {
    "identity": re.compile(r"^NTH-[0-9]{6}$"),
    "evidence": re.compile(r"^EVD-[0-9]{6}$"),
    "verification_event": re.compile(r"^VER-[0-9]{6}$"),
}

ENVELOPE_ID_RE = re.compile(r"^CRD-[0-9]{6}$")
ISSUER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\.[0-9]+)?"
    r"(?:Z|z|[+-](?:0[0-9]|1[0-9]|2[0-3]):[0-5][0-9])$"
)


class ProofError(ValueError):
    """Raised when a NOTHING proof is malformed or cannot be verified."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str, field: str) -> bytes:
    if not isinstance(value, str) or not B64URL_RE.fullmatch(value):
        raise ProofError(f"{field} must be unpadded base64url")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, base64.binascii.Error) as exc:
        raise ProofError(f"{field} is not valid base64url") from exc


def _validate_json_value(value: Any, path: str = "$", seen: set[int] | None = None) -> None:
    """Enforce the small JSON subset used by this signing profile."""
    if seen is None:
        seen = set()

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if abs(value) > 9007199254740991:
            raise ProofError(f"{path} contains an integer outside the supported safe range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProofError(f"{path} contains a non-finite number")
        raise ProofError(
            f"{path} contains a float; NOTHING proof canonicalization v0.1 forbids floats"
        )
    if isinstance(value, list):
        marker = id(value)
        if marker in seen:
            raise ProofError(f"{path} contains a cyclic structure")
        seen.add(marker)
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", seen)
        seen.remove(marker)
        return
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise ProofError(f"{path} contains a cyclic structure")
        seen.add(marker)
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProofError(f"{path} contains a non-string object key")
            _validate_json_value(item, f"{path}.{key}", seen)
        seen.remove(marker)
        return
    raise ProofError(f"{path} contains an unsupported JSON type: {type(value).__name__}")


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", "surrogatepass")


def _canonicalize(value: Any) -> str:
    """Produce deterministic JSON for the supported NOTHING proof subset.

    Object keys are ordered by UTF-16 code units, matching the ordering model
    described by RFC 8785. Floating-point values are intentionally excluded.
    This is not claimed as full RFC 8785 conformance.
    """
    _validate_json_value(value)

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonicalize(item) for item in value) + "]"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda pair: _utf16_sort_key(pair[0]))
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False, separators=(",", ":"))
                + ":"
                + _canonicalize(item)
                for key, item in items
            )
            + "}"
        )
    raise ProofError("unsupported JSON value")


def canonical_bytes(value: Any) -> bytes:
    return _canonicalize(value).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair as unpadded base64url raw bytes.

    The private seed is returned to the caller and is never persisted here.
    """
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_bytes = private.private_bytes_raw()
    return _b64url_encode(private_bytes), _b64url_encode(public)


def _validate_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not DATETIME_RE.fullmatch(value):
        raise ProofError(f"{field} must be an ISO-8601 date-time with timezone")
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProofError(f"{field} is not a valid date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProofError(f"{field} must include a timezone")
    return parsed


def _validate_envelope(envelope: Mapping[str, Any]) -> None:
    if not isinstance(envelope, Mapping):
        raise ProofError("envelope must be an object")

    allowed = {
        "envelope_id", "version", "resource_type", "resource_id",
        "resource_hash", "issuer", "proof",
    }
    extras = set(envelope) - allowed
    if extras:
        raise ProofError(f"envelope has unsupported fields: {sorted(extras)}")
    for field in allowed:
        if field not in envelope:
            raise ProofError(f"envelope.{field} is required")

    if not ENVELOPE_ID_RE.fullmatch(str(envelope["envelope_id"])):
        raise ProofError("envelope.envelope_id must match CRD-XXXXXX")
    if envelope["version"] != PROOF_VERSION:
        raise ProofError("envelope.version must be 0.1")

    resource_type = envelope["resource_type"]
    resource_id = envelope["resource_id"]
    if resource_type not in RESOURCE_TYPES:
        raise ProofError("unsupported resource_type")
    if not isinstance(resource_id, str) or not RESOURCE_TYPES[resource_type].fullmatch(resource_id):
        raise ProofError("resource_id does not match resource_type")

    if not isinstance(envelope["resource_hash"], str) or not HEX64_RE.fullmatch(
        envelope["resource_hash"]
    ):
        raise ProofError("resource_hash must be 64 lowercase hexadecimal characters")

    issuer = envelope["issuer"]
    if not isinstance(issuer, Mapping) or set(issuer) != {"issuer_id", "key_id"}:
        raise ProofError("envelope.issuer must contain exactly issuer_id and key_id")
    if not ISSUER_ID_RE.fullmatch(str(issuer["issuer_id"])):
        raise ProofError("issuer_id format is invalid")
    if not KEY_ID_RE.fullmatch(str(issuer["key_id"])):
        raise ProofError("key_id format is invalid")

    proof = envelope["proof"]
    allowed_proof = {"type", "created", "proof_purpose", "signature"}
    if not isinstance(proof, Mapping) or set(proof) != allowed_proof:
        raise ProofError("envelope.proof has invalid fields")
    if proof["type"] != PROOF_TYPE:
        raise ProofError("unsupported proof type")
    _validate_datetime(proof["created"], "proof.created")
    if proof["proof_purpose"] != PROOF_PURPOSE:
        raise ProofError("unsupported proof purpose")

    signature = _b64url_decode(proof["signature"], "proof.signature")
    if len(signature) != 64:
        raise ProofError("Ed25519 signature must be 64 bytes")


def _signing_document(envelope: Mapping[str, Any]) -> dict[str, Any]:
    proof = envelope["proof"]
    return {
        "envelope_id": envelope["envelope_id"],
        "version": envelope["version"],
        "resource_type": envelope["resource_type"],
        "resource_id": envelope["resource_id"],
        "resource_hash": envelope["resource_hash"],
        "issuer": dict(envelope["issuer"]),
        "proof": {
            "type": proof["type"],
            "created": proof["created"],
            "proof_purpose": proof["proof_purpose"],
        },
    }


def create_envelope(
    resource: Mapping[str, Any],
    *,
    envelope_id: str,
    resource_type: str,
    resource_id: str,
    issuer_id: str,
    key_id: str,
    private_key_b64url: str,
    created_at: str,
) -> dict[str, Any]:
    """Create a signed proof envelope for an exact resource object."""
    if resource_type not in RESOURCE_TYPES:
        raise ProofError("unsupported resource_type")
    if not RESOURCE_TYPES[resource_type].fullmatch(resource_id):
        raise ProofError("resource_id does not match resource_type")
    if not ENVELOPE_ID_RE.fullmatch(envelope_id):
        raise ProofError("envelope_id must match CRD-XXXXXX")
    if not ISSUER_ID_RE.fullmatch(issuer_id):
        raise ProofError("issuer_id format is invalid")
    if not KEY_ID_RE.fullmatch(key_id):
        raise ProofError("key_id format is invalid")
    _validate_datetime(created_at, "created_at")

    private_bytes = _b64url_decode(private_key_b64url, "private_key")
    if len(private_bytes) != 32:
        raise ProofError("Ed25519 private key seed must be 32 bytes")
    private = Ed25519PrivateKey.from_private_bytes(private_bytes)

    envelope: dict[str, Any] = {
        "envelope_id": envelope_id,
        "version": PROOF_VERSION,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_hash": sha256_hex(resource),
        "issuer": {"issuer_id": issuer_id, "key_id": key_id},
        "proof": {
            "type": PROOF_TYPE,
            "created": created_at,
            "proof_purpose": PROOF_PURPOSE,
            "signature": "placeholder",
        },
    }

    signature = private.sign(canonical_bytes(_signing_document(envelope)))
    envelope["proof"]["signature"] = _b64url_encode(signature)
    _validate_envelope(envelope)
    return envelope


def verify_envelope(
    envelope: Mapping[str, Any],
    resource: Mapping[str, Any],
    issuer_registry: Mapping[str, Any],
    *,
    at_time: str | None = None,
) -> dict[str, Any]:
    """Verify structure, resource hash, issuer key and Ed25519 signature."""
    _validate_envelope(envelope)

    expected_hash = sha256_hex(resource)
    if envelope["resource_hash"] != expected_hash:
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": False,
                "issuer_key": False, "signature": False,
            },
            "reason": "resource hash does not match the signed envelope",
        }

    issuer_id = envelope["issuer"]["issuer_id"]
    key_id = envelope["issuer"]["key_id"]

    if not isinstance(issuer_registry, Mapping) or issuer_registry.get("version") != PROOF_VERSION:
        raise ProofError("unsupported issuer registry version")

    issuers = issuer_registry.get("issuers")
    if not isinstance(issuers, list):
        raise ProofError("issuer registry issuers must be an array")

    issuer = next(
        (item for item in issuers if isinstance(item, Mapping) and item.get("issuer_id") == issuer_id),
        None,
    )
    if issuer is None:
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": True,
                "issuer_key": False, "signature": False,
            },
            "reason": "issuer is not present in the configured registry",
        }

    if issuer.get("status") not in {"ACTIVE", "DEMO"}:
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": True,
                "issuer_key": False, "signature": False,
            },
            "reason": "issuer is not active",
        }

    keys = issuer.get("keys")
    if not isinstance(keys, list):
        raise ProofError("issuer keys must be an array")

    key = next(
        (item for item in keys if isinstance(item, Mapping) and item.get("key_id") == key_id),
        None,
    )
    if key is None:
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": True,
                "issuer_key": False, "signature": False,
            },
            "reason": "issuer key is not present in the registry",
        }

    if key.get("type") != "Ed25519" or key.get("status") != "ACTIVE":
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": True,
                "issuer_key": False, "signature": False,
            },
            "reason": "issuer key is not an active Ed25519 key",
        }

    public_key = _b64url_decode(key.get("public_key"), "issuer key public_key")
    if len(public_key) != 32:
        raise ProofError("Ed25519 public key must be 32 bytes")

    signature = _b64url_decode(envelope["proof"]["signature"], "proof.signature")
    created = _validate_datetime(envelope["proof"]["created"], "proof.created")

    if at_time is not None:
        observed = _validate_datetime(at_time, "at_time")
        valid_from = key.get("valid_from")
        valid_until = key.get("valid_until")

        if valid_from and observed < _validate_datetime(valid_from, "key.valid_from"):
            return {
                "valid": False,
                "checks": {
                    "structure": True, "resource_hash": True,
                    "issuer_key": False, "signature": False,
                },
                "reason": "issuer key was not yet valid at the requested time",
            }

        if valid_until and observed > _validate_datetime(valid_until, "key.valid_until"):
            return {
                "valid": False,
                "checks": {
                    "structure": True, "resource_hash": True,
                    "issuer_key": False, "signature": False,
                },
                "reason": "issuer key was no longer valid at the requested time",
            }

        if created > observed:
            return {
                "valid": False,
                "checks": {
                    "structure": True, "resource_hash": True,
                    "issuer_key": True, "signature": False,
                },
                "reason": "proof was created after the requested verification time",
            }

    verifier = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        verifier.verify(signature, canonical_bytes(_signing_document(envelope)))
    except Exception:
        return {
            "valid": False,
            "checks": {
                "structure": True, "resource_hash": True,
                "issuer_key": True, "signature": False,
            },
            "reason": "signature verification failed",
        }

    return {
        "valid": True,
        "checks": {
            "structure": True,
            "resource_hash": True,
            "issuer_key": True,
            "signature": True,
        },
        "issuer_id": issuer_id,
        "key_id": key_id,
        "created": envelope["proof"]["created"],
        "resource_hash": expected_hash,
    }
