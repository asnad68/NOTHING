from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

NOTHING_ID_RE = re.compile(r"^NTH-[0-9]{6}$")
CLM_ID_RE = re.compile(r"^CLM-[0-9]{6}$")
EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class EnrollmentValidationError(ValueError):
    pass


def _clean_lines(values):
    out = []
    for value in values or []:
        value = str(value).strip()
        if value and value not in out:
            out.append(value)
    return out


def _validate_url(value: str, field: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise EnrollmentValidationError(f"{field} must be an absolute HTTP(S) URL")
    if len(value) > 500:
        raise EnrollmentValidationError(f"{field} is too long")
    return value


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class RegistrationDraft:
    name: str
    type: str = "business"
    website: str = ""
    domains: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    description: str = ""

    @classmethod
    def from_mapping(cls, payload: dict) -> "RegistrationDraft":
        if not isinstance(payload, dict):
            raise EnrollmentValidationError("registration must be an object")
        allowed = {
            "name",
            "type",
            "website",
            "domains",
            "channels",
            "description",
        }
        extra = set(payload) - allowed
        if extra:
            raise EnrollmentValidationError(
                f"unsupported registration properties: {sorted(extra)}"
            )
        name = str(payload.get("name", "")).strip()
        if not 1 <= len(name) <= 180:
            raise EnrollmentValidationError("name must contain 1-180 characters")
        identity_type = str(payload.get("type", "business")).strip()
        if identity_type not in {"business", "brand", "digital_channel"}:
            raise EnrollmentValidationError("unsupported registration type")
        website = _validate_url(
            str(payload.get("website", "")).strip(),
            "website",
        )
        domains = _clean_lines(payload.get("domains", []))
        channels = _clean_lines(payload.get("channels", []))
        if len(domains) > 50:
            raise EnrollmentValidationError("too many domains")
        if len(channels) > 50:
            raise EnrollmentValidationError("too many channels")
        description = str(payload.get("description", "")).strip()
        if len(description) > 2000:
            raise EnrollmentValidationError("description is too long")
        for domain in domains:
            if len(domain) > 253 or any(char.isspace() for char in domain):
                raise EnrollmentValidationError("domains must be non-empty host-like values")
        for channel in channels:
            if len(channel) > 500:
                raise EnrollmentValidationError("channel URL is too long")
        return cls(
            name,
            identity_type,
            website,
            tuple(domains),
            tuple(channels),
            description,
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "website": self.website,
            "domains": list(self.domains),
            "channels": list(self.channels),
            "description": self.description,
        }

    def canonical_json(self) -> str:
        return _canonical(self.as_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def validate_wallet_address(value: str) -> str:
    address = str(value or "").strip()
    if not EVM_ADDRESS_RE.fullmatch(address):
        raise EnrollmentValidationError("wallet_address must be a valid EVM address")
    return address.lower()


def normalize_chain_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]+", raw):
        raise EnrollmentValidationError("chain_id must be a hexadecimal EVM chain id")
    return raw


def _numeric_candidate(digest: str, offset: int = 0) -> str:
    start = (offset * 8) % (len(digest) - 8)
    number = int(digest[start : start + 8], 16) % 1_000_000
    return f"NTH-{number:06d}"


def build_organization_controlled_identity(
    draft: RegistrationDraft,
    nothing_id: str,
    *,
    authorization_method: str,
    controlled_domain: str,
    now: datetime | None = None,
) -> dict:
    if not NOTHING_ID_RE.fullmatch(nothing_id):
        raise EnrollmentValidationError("invalid Nothing ID")
    controlled_domain = controlled_domain.strip().lower().rstrip(".")
    if not controlled_domain or "." not in controlled_domain:
        raise EnrollmentValidationError("invalid controlled organization domain")
    now = now or datetime.now(timezone.utc)
    checked_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    digest = draft.digest()
    claims = []

    claims.append(
        {
            "claim_id": claim_id_for(digest, 0),
            "statement": (
                f"The registrant demonstrated control of the organization domain "
                f"'{controlled_domain}' and authenticated a principal bound to that domain."
            ),
            "status": "SOURCE-VERIFIED",
            "source": {
                "type": "domain_control",
                "reference": f"_nothing-challenge.{controlled_domain} (DNS TXT)",
                "checked_at": checked_at,
            },
            "authorization": {
                "status": "CONFIRMED",
                "method": authorization_method,
            },
            "valid_from": checked_at,
        }
    )
    claims.append(
        {
            "claim_id": claim_id_for(digest, 1),
            "statement": f"The organization-controlled record identifies the business/brand name '{draft.name}'.",
            "status": "SOURCE-VERIFIED",
            "source": {
                "type": "official_website" if draft.website else "domain_control",
                "reference": draft.website or f"_nothing-challenge.{controlled_domain} (DNS TXT)",
                "checked_at": checked_at,
            },
                "authorization": {
                    "status": "CONFIRMED",
                    "method": authorization_method,
                },
            "valid_from": checked_at,
        }
    )
    if draft.website:
        claims.append(
            {
                "claim_id": claim_id_for(digest, 2),
                "statement": f"The registrant submitted the organization website {draft.website}.",
                "status": "SOURCE-VERIFIED",
                "source": {
                    "type": "official_website",
                    "reference": draft.website,
                    "checked_at": checked_at,
                },
                "authorization": {
                    "status": "CONFIRMED",
                    "method": authorization_method,
                },
                "valid_from": checked_at,
            }
        )

    return {
        "nothing_id": nothing_id,
        "version": "0.1",
        "subject": {
            "name": draft.name,
            "type": draft.type,
            **({"website": draft.website} if draft.website else {}),
        },
        "claims": claims,
        "revocation": {"status": "NOT_REVOKED"},
    }


def candidate_nothing_ids(draft_digest: str, limit: int = 64) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{64}", draft_digest):
        raise EnrollmentValidationError(
            "draft_digest must be a lowercase SHA-256 digest"
        )
    if not 1 <= limit <= 64:
        raise EnrollmentValidationError("candidate limit must be between 1 and 64")
    candidates: list[str] = []
    for offset in range(limit):
        # Re-hash after the first eight digest windows so the allocator has
        # enough deterministic candidates without relying on a large integer.
        source = draft_digest if offset < 8 else hashlib.sha256(
            f"{draft_digest}:candidate:{offset}".encode("utf-8")
        ).hexdigest()
        candidate = _numeric_candidate(source, offset % 8)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def allocate_nothing_id(draft_digest: str, existing_ids=()) -> str:
    occupied = set(existing_ids)
    for candidate in candidate_nothing_ids(draft_digest):
        if candidate not in occupied:
            return candidate
    raise EnrollmentValidationError(
        "could not allocate a free Nothing ID from the deterministic candidate set"
    )


def claim_id_for(draft_digest: str, index: int) -> str:
    if index < 0:
        raise EnrollmentValidationError("claim index cannot be negative")
    raw = hashlib.sha256(
        f"{draft_digest}:claim:{index}".encode()
    ).hexdigest()
    return f"CLM-{int(raw[:8], 16) % 1_000_000:06d}"


def build_self_claimed_identity(
    draft: RegistrationDraft,
    nothing_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    if not NOTHING_ID_RE.fullmatch(nothing_id):
        raise EnrollmentValidationError("invalid Nothing ID")
    now = now or datetime.now(timezone.utc)
    digest = draft.digest()
    claims = []
    statements = [
        f"Registrant submitted the business/brand name '{draft.name}'.",
        *(
            [f"Registrant states the official website is {draft.website}."]
            if draft.website
            else []
        ),
        *(
            [
                "Registrant submitted these official domains: "
                + ", ".join(draft.domains)
                + "."
            ]
            if draft.domains
            else []
        ),
        *(
            [
                "Registrant submitted these official digital channels: "
                + ", ".join(draft.channels)
                + "."
            ]
            if draft.channels
            else []
        ),
    ]
    for index, statement in enumerate(statements):
        claims.append(
            {
                "claim_id": claim_id_for(digest, index),
                "statement": statement,
                "status": "SELF-CLAIMED",
            }
        )
    return {
        "nothing_id": nothing_id,
        "version": "0.1",
        "subject": {
            "name": draft.name,
            "type": draft.type,
            **({"website": draft.website} if draft.website else {}),
        },
        "claims": claims,
        "revocation": {"status": "NOT_REVOKED"},
    }
