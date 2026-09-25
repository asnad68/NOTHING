"""HTTP-facing validation and authentication helpers for NOTHING write ingestion.

This module deliberately does not persist data and does not implement verification
semantics. Protocol validation remains in the existing validators and resolver.
"""

from __future__ import annotations

from src.nothing_auth import AuthenticatedPrincipal

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

INGESTION_MAX_BODY_BYTES = max(
    1,
    int(os.getenv("NOTHING_INGESTION_MAX_BODY_BYTES", "1048576")),
)
INGESTION_MAX_RECORDS = max(
    1,
    int(os.getenv("NOTHING_INGESTION_MAX_RECORDS", "100")),
)
IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7E]{1,255}$")
BEARER_TOKEN_RE = re.compile(r"^[A-Za-z0-9\-._~+/]+=*$")
BUNDLE_KEYS = ("identities", "evidence", "verification_events")
DEFAULT_STATIC_SCOPE = "nothing:ingest"


class IngestionRequestError(ValueError):
    """Raised when an HTTP ingestion request is malformed."""


@dataclass(frozen=True)
class ParsedIngestionRequest:
    bundle: dict[str, list[dict[str, Any]]]
    request_sha256: str


class BearerAuthenticator:
    """Reference bearer-token authenticator."""

    def __init__(
        self,
        token: str | None = None,
        *,
        actor: str | None = None,
        scope: str = DEFAULT_STATIC_SCOPE,
    ) -> None:
        self.token = token if token is not None else os.getenv("NOTHING_INGESTION_TOKEN")
        self.actor = (
            actor
            if actor is not None
            else os.getenv("NOTHING_INGESTION_ACTOR", "authenticated-ingestion")
        )
        self.scope = scope.strip()
        if not self.scope:
            raise ValueError("scope must be non-empty")

    @property
    def configured(self) -> bool:
        return isinstance(self.token, str) and bool(self.token)

    def authenticate(
        self,
        authorization: str | None,
    ) -> AuthenticatedPrincipal | None:
        if not self.configured or not authorization:
            return None

        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None

        token = parts[1]
        if not BEARER_TOKEN_RE.fullmatch(token):
            return None

        if not hmac.compare_digest(token, self.token or ""):
            return None

        return AuthenticatedPrincipal(
            actor=self.actor,
            subject=self.actor,
            issuer="urn:nothing:static-bearer",
            client_id=self.actor,
            scopes=frozenset({self.scope}),
            claims={},
        )

    def authorize(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
    ) -> bool:
        return action == self.scope


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IngestionRequestError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _canonical_bundle(bundle: Mapping[str, Any]) -> str:
    id_keys = {
        "identities": "nothing_id",
        "evidence": "evidence_id",
        "verification_events": "event_id",
    }
    normalized: dict[str, Any] = {}
    for collection in BUNDLE_KEYS:
        items = list(bundle[collection])
        normalized[collection] = sorted(
            items,
            key=lambda item: item[id_keys[collection]],
        )
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_ingestion_request(
    body: bytes,
    *,
    content_type: str | None,
    idempotency_key: str | None,
    max_body_bytes: int = INGESTION_MAX_BODY_BYTES,
    max_records: int = INGESTION_MAX_RECORDS,
) -> ParsedIngestionRequest:
    if idempotency_key is None or not idempotency_key:
        raise IngestionRequestError("Idempotency-Key header is required")
    if not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
        raise IngestionRequestError(
            "Idempotency-Key must be 1-255 printable ASCII characters"
        )

    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise IngestionRequestError("Content-Type must be application/json")

    if len(body) > max_body_bytes:
        raise IngestionRequestError(
            f"request body exceeds the {max_body_bytes}-byte limit"
        )

    try:
        decoded = body.decode("utf-8")
        payload = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except UnicodeDecodeError as exc:
        raise IngestionRequestError("request body must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise IngestionRequestError(
            "request body must contain a valid JSON object"
        ) from exc

    if not isinstance(payload, dict):
        raise IngestionRequestError("request body must be a JSON object")

    if set(payload) != set(BUNDLE_KEYS):
        raise IngestionRequestError(
            "request body must contain exactly identities, evidence, and verification_events"
        )

    bundle: dict[str, list[dict[str, Any]]] = {}
    for collection in BUNDLE_KEYS:
        value = payload[collection]
        if not isinstance(value, list):
            raise IngestionRequestError(f"{collection} must be an array")
        if len(value) > max_records:
            raise IngestionRequestError(
                f"{collection} exceeds the {max_records}-record limit"
            )
        items: list[dict[str, Any]] = []
        id_field = {
            "identities": "nothing_id",
            "evidence": "evidence_id",
            "verification_events": "event_id",
        }[collection]
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise IngestionRequestError(
                    f"{collection}[{index}] must be a JSON object"
                )
            if not isinstance(item.get(id_field), str) or not item[id_field]:
                raise IngestionRequestError(
                    f"{collection}[{index}] must contain a non-empty {id_field}"
                )
            items.append(item)
        bundle[collection] = items

    total_records = sum(len(bundle[item]) for item in BUNDLE_KEYS)
    if total_records == 0:
        raise IngestionRequestError(
            "ingestion bundle must contain at least one record"
        )

    canonical = _canonical_bundle(bundle).encode("utf-8")
    return ParsedIngestionRequest(
        bundle=bundle,
        request_sha256=hashlib.sha256(canonical).hexdigest(),
    )
