"""Persistence ports and a durable SQLite reference backend for NOTHING.

The storage layer deliberately does not define verification truth. It stores
validated protocol records, preserves historical identity revisions, enforces
immutable records where the protocol requires immutability, and provides the
bundle needed by src.nothing_protocol for deterministic resolution.

SQLite is a zero-cost persistent reference/staging backend. The public
production architecture targets a PostgreSQL-compatible deployment behind a
stateless API layer; the API depends on this storage port rather than on SQL.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from src.nothing_proof import (
    ENVELOPE_ID_RE,
    ProofError,
    sha256_hex as proof_sha256_hex,
    validate_envelope,
)
from src.nothing_protocol import (
    RelationshipError,
    resolve_claim_relationships,
    validate_procedure,
    validate_procedure_registry,
)
from src.nothing_verify import (
    EVENT_ID_RE,
    EVIDENCE_ID_RE,
    NOTHING_ID_RE,
    ValidationError,
    load_json,
    validate_evidence,
    validate_identity,
    validate_verification_event,
)

STORAGE_SCHEMA_VERSION = 5
DEFAULT_DB_PATH = Path("data/nothing.db")


class StoreError(RuntimeError):
    """Base class for persistence-layer failures."""


class NotFoundError(StoreError):
    """Raised when a requested resource does not exist."""


class ConflictError(StoreError):
    """Raised when an immutable resource is written with different content."""


@dataclass(frozen=True)
class StoredRecord:
    record: dict[str, Any]
    content_sha256: str
    recorded_at: str
    revision: int | None = None


@dataclass(frozen=True)
class IdentityBundle:
    identity: StoredRecord
    evidence: tuple[dict[str, Any], ...]
    events: tuple[StoredRecord, ...]
    registry: dict[str, Any]
    last_modified: str
    proofs: tuple[StoredRecord, ...]


@dataclass(frozen=True)
class IngestionResult:
    data: dict[str, Any]
    recorded_at: str
    replayed: bool


class NothingStore(Protocol):
    """Storage contract consumed by the API layer."""

    demo: bool

    def close(self) -> None:
        ...

    def health(self) -> bool:
        ...

    def get_identity(self, nothing_id: str) -> StoredRecord:
        ...

    def get_identity_revision(self, nothing_id: str, revision: int) -> StoredRecord:
        ...


    def get_proof(self, envelope_id: str) -> StoredRecord:
        ...

    def get_proofs_for_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> tuple[StoredRecord, ...]:
        ...

    def put_proof(
        self,
        envelope: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        ...

    def ingest_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        actor: str,
        idempotency_key: str,
        request_sha256: str,
        ingestion_id: str,
        recorded_at: str | None = None,
    ) -> IngestionResult:
        ...

    def create_auth_challenge(
        self,
        *,
        challenge_id: str,
        purpose: str,
        nonce: str,
        wallet_address: str | None,
        domain: str,
        uri: str,
        chain_id: int,
        message_sha256: str,
        issued_at: str,
        expires_at: str,
        recorded_at: str | None = None,
        actor: str = "auth",
    ) -> None:
        ...

    def get_auth_challenge(self, challenge_id: str) -> dict[str, Any]:
        ...

    def authorize_auth_challenge(
        self,
        challenge_id: str,
        *,
        nonce: str,
        message_sha256: str,
        authorization: Mapping[str, Any],
        now: str,
        actor: str = "auth",
    ) -> bool:
        ...

    def reject_auth_challenge(
        self,
        challenge_id: str,
        *,
        nonce: str,
        message_sha256: str,
        decision: Mapping[str, Any],
        now: str,
        actor: str = "auth",
    ) -> bool:
        ...

    def authorize_google_challenge(
        self,
        challenge_id: str,
        *,
        nonce: str,
        message_sha256: str,
        authorization: Mapping[str, Any],
        now: str,
        actor: str = "auth",
    ) -> bool:
        ...

    def authorize_google_challenge(
        self,
        challenge_id: str,
        *,
        nonce: str,
        message_sha256: str,
        authorization: Mapping[str, Any],
        now: str,
        actor: str = "auth",
    ) -> bool:
        challenge_id = str(challenge_id or "").strip()
        nonce = str(nonce or "").strip()
        message_sha256 = str(message_sha256 or "").strip().lower()
        actor = str(actor).strip()
        now = str(now).strip()
        if not challenge_id or not nonce or len(message_sha256) != 64 or not actor or not now:
            raise ValueError("invalid Google authentication authorization")
        payload_json = _canonical_json(dict(authorization))
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE auth_challenges
                    SET consumed_at = ?,
                        authorization_status = 'AUTHORIZED',
                        authorization_json = ?
                    WHERE challenge_id = ?
                      AND purpose = 'google_oidc'
                      AND nonce = ?
                      AND message_sha256 = ?
                      AND consumed_at IS NULL
                      AND authorization_status = 'PENDING'
                      AND expires_at > ?
                    """,
                    (now, payload_json, challenge_id, nonce, message_sha256, now),
                )
                changed = cursor.rowcount == 1
                if changed:
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            recorded_at, actor, action, record_type, record_id, details_json
                        ) VALUES (?, ?, 'AUTHORIZE', 'auth_challenge', ?, ?)
                        """,
                        (
                            now,
                            actor,
                            challenge_id,
                            json.dumps({"authorization_status": "AUTHORIZED", "method": "google_oidc"}, separators=(",", ":")),
                        ),
                    )
                connection.execute("COMMIT")
                return changed
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def consume_auth_authorization(
        self,
        challenge_id: str,
        *,
        registration_digest: str,
        wallet_address: str | None,
        now: str,
        actor: str = "auth",
    ) -> bool:
        challenge_id = str(challenge_id or "").strip()
        registration_digest = str(registration_digest or "").strip().lower()
        wallet_address = str(wallet_address or "").strip().lower() if wallet_address else None
        now = str(now).strip()
        actor = str(actor).strip()
        if not challenge_id or len(registration_digest) != 64 or not now or not actor:
            raise ValueError("invalid authentication authorization consumption")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT purpose, wallet_address, authorization_status,
                           authorization_json, registration_consumed_at, expires_at
                    FROM auth_challenges
                    WHERE challenge_id = ?
                    """,
                    (challenge_id,),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    raise NotFoundError(challenge_id)
                authorization = json.loads(row["authorization_json"]) if row["authorization_json"] else {}
                wallet_ok = (
                    row["purpose"] == "google_oidc"
                    or (
                        row["purpose"] == "wallet_siwe"
                        and wallet_address is not None
                        and str(row["wallet_address"] or "").lower() == wallet_address
                    )
                )
                valid = (
                    row["authorization_status"] == "AUTHORIZED"
                    and row["registration_consumed_at"] is None
                    and wallet_ok
                    and str(authorization.get("registration_digest", "")).lower() == registration_digest
                    and _parse_time(str(row["expires_at"])) > _parse_time(now)
                )
                if not valid:
                    connection.execute("ROLLBACK")
                    return False
                cursor = connection.execute(
                    """
                    UPDATE auth_challenges
                    SET registration_consumed_at = ?
                    WHERE challenge_id = ?
                      AND authorization_status = 'AUTHORIZED'
                      AND registration_consumed_at IS NULL
                    """,
                    (now, challenge_id),
                )
                changed = cursor.rowcount == 1
                if changed:
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            recorded_at, actor, action, record_type, record_id, details_json
                        ) VALUES (?, ?, 'CONSUME', 'auth_challenge', ?, ?)
                        """,
                        (
                            now,
                            actor,
                            challenge_id,
                            json.dumps({"registration_consumed": True}, separators=(",", ":")),
                        ),
                    )
                connection.execute("COMMIT")
                return changed
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    @staticmethod
    def _stored_identity(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"],
            revision=int(row["revision"]),
        )

    @staticmethod
    def _stored_evidence(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def _stored_event(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def _stored_procedure(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def _stored_proof(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"],
        )

    def get_proof(self, envelope_id: str) -> StoredRecord:
        if not ENVELOPE_ID_RE.fullmatch(envelope_id):
            raise ValidationError("envelope_id must match CRD-XXXXXX.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cryptographic_proofs WHERE envelope_id = ?",
                (envelope_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(envelope_id)
        envelope = json.loads(row["payload_json"])
        try:
            validate_envelope(envelope)
        except ProofError as exc:
            raise StoreError(f"stored proof {envelope_id} is invalid: {exc}") from exc
        return self._stored_proof(row)

    def get_proofs_for_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> tuple[StoredRecord, ...]:
        validators = {
            "identity": NOTHING_ID_RE,
            "evidence": EVIDENCE_ID_RE,
            "verification_event": EVENT_ID_RE,
        }
        pattern = validators.get(resource_type)
        if pattern is None or not pattern.fullmatch(resource_id):
            raise ValidationError("invalid proof resource selector")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM cryptographic_proofs
                WHERE resource_type = ? AND resource_id = ?
                ORDER BY envelope_id
                """,
                (resource_type, resource_id),
            ).fetchall()
        result = []
        for row in rows:
            envelope = json.loads(row["payload_json"])
            try:
                validate_envelope(envelope)
            except ProofError as exc:
                raise StoreError(
                    f"stored proof {row['envelope_id']} is invalid: {exc}"
                ) from exc
            result.append(self._stored_proof(row))
        return tuple(result)

    def put_proof(
        self,
        envelope: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        try:
            validate_envelope(envelope)
        except ProofError as exc:
            raise ValidationError(str(exc)) from exc
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        resource_type = envelope["resource_type"]
        resource_id = envelope["resource_id"]
        if resource_type == "identity":
            resource = self.get_identity(resource_id)
        elif resource_type == "evidence":
            resource = self.get_evidence(resource_id)
        else:
            resource = self.get_event(resource_id)

        expected_hash = proof_sha256_hex(resource.record)
        if envelope["resource_hash"] != expected_hash:
            raise ValidationError(
                "proof resource_hash does not match the current stored resource"
            )

        payload = copy.deepcopy(dict(envelope))
        payload_json = _canonical_json(payload)
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT content_sha256 FROM cryptographic_proofs WHERE envelope_id = ?",
                    (envelope["envelope_id"],),
                ).fetchone()
                if existing is not None:
                    if existing["content_sha256"] == digest:
                        connection.execute("COMMIT")
                        return False
                    raise ConflictError(
                        f"proof {envelope['envelope_id']} is immutable; create a new envelope ID"
                    )
                connection.execute(
                    """
                    INSERT INTO cryptographic_proofs(
                        envelope_id, version, resource_type, resource_id,
                        resource_hash, issuer_id, key_id, payload_json,
                        content_sha256, recorded_at, recorded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope["envelope_id"], envelope["version"],
                        resource_type, resource_id, envelope["resource_hash"],
                        envelope["issuer"]["issuer_id"], envelope["issuer"]["key_id"],
                        payload_json, digest, recorded, actor,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        content_sha256, details_json
                    ) VALUES (?, ?, 'APPEND', 'cryptographic_proof', ?, ?, ?)
                    """,
                    (
                        recorded, actor, envelope["envelope_id"], digest,
                        json.dumps(
                            {
                                "resource_type": resource_type,
                                "resource_id": resource_id,
                                "issuer_id": envelope["issuer"]["issuer_id"],
                                "key_id": envelope["issuer"]["key_id"],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_identity(self, nothing_id: str) -> StoredRecord:
        if not NOTHING_ID_RE.fullmatch(nothing_id):
            raise ValidationError("nothing_id must match NTH-XXXXXX.")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*
                FROM identity_heads h
                JOIN identity_revisions r
                  ON r.nothing_id = h.nothing_id AND r.revision = h.revision
                WHERE h.nothing_id = ?
                """,
                (nothing_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(nothing_id)
        return self._stored_identity(row)

    def get_identity_revision(self, nothing_id: str, revision: int) -> StoredRecord:
        if not NOTHING_ID_RE.fullmatch(nothing_id) or revision < 1:
            raise ValidationError("invalid identity revision selector")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM identity_revisions
                WHERE nothing_id = ? AND revision = ?
                """,
                (nothing_id, revision),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{nothing_id}@{revision}")
        return self._stored_identity(row)

    def put_identity(
        self,
        identity: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> int:
        validate_identity(identity)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        nothing_id = identity["nothing_id"]
        protocol_version = identity["version"]
        payload = copy.deepcopy(dict(identity))
        payload_json = _canonical_json(payload)
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT revision
                    FROM identity_revisions
                    WHERE nothing_id = ? AND content_sha256 = ?
                    """,
                    (nothing_id, digest),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return int(existing["revision"])

                head = connection.execute(
                    """
                    SELECT revision, content_sha256
                    FROM identity_heads
                    WHERE nothing_id = ?
                    """,
                    (nothing_id,),
                ).fetchone()
                revision = int(head["revision"]) + 1 if head else 1
                previous_hash = head["content_sha256"] if head else None

                connection.execute(
                    """
                    INSERT INTO identity_revisions(
                        nothing_id, revision, protocol_version, payload_json,
                        content_sha256, recorded_at, recorded_by,
                        previous_content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        nothing_id,
                        revision,
                        protocol_version,
                        payload_json,
                        digest,
                        recorded,
                        actor,
                        previous_hash,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO identity_heads(nothing_id, revision, content_sha256)
                    VALUES (?, ?, ?)
                    ON CONFLICT(nothing_id) DO UPDATE SET
                        revision = excluded.revision,
                        content_sha256 = excluded.content_sha256
                    """,
                    (nothing_id, revision, digest),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        revision, content_sha256, details_json
                    ) VALUES (?, ?, 'APPEND', 'identity', ?, ?, ?, ?)
                    """,
                    (
                        recorded,
                        actor,
                        nothing_id,
                        revision,
                        digest,
                        json.dumps(
                            {"protocol_version": protocol_version},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute("COMMIT")
                return revision
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_evidence(self, evidence_id: str) -> StoredRecord:
        if not EVIDENCE_ID_RE.fullmatch(evidence_id):
            raise ValidationError("evidence_id must match EVD-XXXXXX.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(evidence_id)
        return self._stored_evidence(row)

    def put_evidence(
        self,
        evidence: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_evidence(evidence)
        evidence_id = evidence["evidence_id"]
        payload = copy.deepcopy(dict(evidence))
        payload_json = _canonical_json(payload)
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT content_sha256 FROM evidence WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()
                if existing is not None:
                    if existing["content_sha256"] == digest:
                        connection.execute("COMMIT")
                        return False
                    raise ConflictError(
                        f"evidence {evidence_id} is immutable; create a new evidence ID"
                    )

                connection.execute(
                    """
                    INSERT INTO evidence(
                        evidence_id, protocol_version, payload_json,
                        content_sha256, recorded_at, recorded_by
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        evidence["version"],
                        payload_json,
                        digest,
                        recorded,
                        actor,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        revision, content_sha256, details_json
                    ) VALUES (?, ?, 'APPEND', 'evidence', ?, NULL, ?, NULL)
                    """,
                    (recorded, actor, evidence_id, digest),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_procedure(self, procedure_id: str, version: str) -> StoredRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM procedures
                WHERE procedure_id = ? AND version = ?
                """,
                (procedure_id, version),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{procedure_id}@{version}")
        return self._stored_procedure(row)

    def put_procedure(
        self,
        procedure: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_procedure(procedure)
        payload = copy.deepcopy(dict(procedure))
        payload_json = _canonical_json(payload)
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT content_sha256
                    FROM procedures
                    WHERE procedure_id = ? AND version = ?
                    """,
                    (procedure["id"], procedure["version"]),
                ).fetchone()
                if existing is not None:
                    if existing["content_sha256"] == digest:
                        connection.execute("COMMIT")
                        return False
                    raise ConflictError(
                        f"procedure {procedure['id']}@{procedure['version']} is immutable"
                    )

                connection.execute(
                    """
                    INSERT INTO procedures(
                        procedure_id, version, payload_json, content_sha256,
                        status, published_at, recorded_at, recorded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        procedure["id"],
                        procedure["version"],
                        payload_json,
                        digest,
                        procedure["status"],
                        procedure.get("published_at"),
                        recorded,
                        actor,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        revision, content_sha256, details_json
                    ) VALUES (?, ?, 'APPEND', 'procedure', ?, NULL, ?, NULL)
                    """,
                    (
                        recorded,
                        actor,
                        f"{procedure['id']}@{procedure['version']}",
                        digest,
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _load_events_for_subject(
        self,
        connection: sqlite3.Connection,
        subject: str,
    ) -> list[StoredRecord]:
        rows = connection.execute(
            """
            SELECT *
            FROM verification_events
            WHERE subject = ?
            ORDER BY occurred_at ASC, event_id ASC
            """,
            (subject,),
        ).fetchall()
        return [self._stored_event(row) for row in rows]

    def _load_evidence_for_events(
        self,
        connection: sqlite3.Connection,
        events: Sequence[StoredRecord],
    ) -> tuple[dict[str, Any], ...]:
        ids: list[str] = []
        for event in events:
            for evidence_id in event.record.get("evidence", []):
                if evidence_id not in ids:
                    ids.append(evidence_id)
        if not ids:
            return ()

        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT *
            FROM evidence
            WHERE evidence_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        by_id = {
            row["evidence_id"]: self._stored_evidence(row).record
            for row in rows
        }
        return tuple(by_id[evidence_id] for evidence_id in ids if evidence_id in by_id)

    def _load_registry(self, connection: sqlite3.Connection) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT *
            FROM procedures
            ORDER BY procedure_id ASC, version ASC
            """
        ).fetchall()
        return {
            "registry_id": "NOTHING-PROCEDURE-REGISTRY",
            "version": "0.1",
            "procedures": [
                self._stored_procedure(row).record
                for row in rows
            ],
        }

    def get_event(self, event_id: str) -> StoredRecord:
        if not EVENT_ID_RE.fullmatch(event_id):
            raise ValidationError("event_id must match VER-XXXXXX.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM verification_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(event_id)
        return self._stored_event(row)

    def put_event(
        self,
        event: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_verification_event(event)
        payload = copy.deepcopy(dict(event))
        payload_json = _canonical_json(payload)
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")

                existing = connection.execute(
                    """
                    SELECT content_sha256
                    FROM verification_events
                    WHERE event_id = ?
                    """,
                    (event["event_id"],),
                ).fetchone()
                if existing is not None:
                    if existing["content_sha256"] == digest:
                        connection.execute("COMMIT")
                        return False
                    raise ConflictError(
                        f"verification event {event['event_id']} is immutable; create a new event"
                    )

                identity_row = connection.execute(
                    "SELECT nothing_id FROM identity_heads WHERE nothing_id = ?",
                    (event["subject"],),
                ).fetchone()
                if identity_row is None:
                    raise NotFoundError(event["subject"])

                procedure_row = connection.execute(
                    """
                    SELECT *
                    FROM procedures
                    WHERE procedure_id = ? AND version = ?
                    """,
                    (event["procedure"]["id"], event["procedure"]["version"]),
                ).fetchone()
                if procedure_row is None:
                    raise NotFoundError(
                        f"{event['procedure']['id']}@{event['procedure']['version']}"
                    )

                evidence_ids = list(event.get("evidence", []))
                if evidence_ids:
                    placeholders = ",".join("?" for _ in evidence_ids)
                    evidence_rows = connection.execute(
                        f"""
                        SELECT *
                        FROM evidence
                        WHERE evidence_id IN ({placeholders})
                        """,
                        evidence_ids,
                    ).fetchall()
                    found_ids = {row["evidence_id"] for row in evidence_rows}
                    missing = [item for item in evidence_ids if item not in found_ids]
                    if missing:
                        raise NotFoundError(missing[0])

                if event.get("supersedes"):
                    prior_row = connection.execute(
                        """
                        SELECT subject, claim_id, occurred_at
                        FROM verification_events
                        WHERE event_id = ?
                        """,
                        (event["supersedes"],),
                    ).fetchone()
                    if prior_row is None:
                        raise NotFoundError(event["supersedes"])
                    if (
                        prior_row["subject"] != event["subject"]
                        or prior_row["claim_id"] != event["claim_id"]
                    ):
                        raise RelationshipError(
                            "superseded event must match subject and claim"
                        )

                identity_row = connection.execute(
                    """
                    SELECT r.*
                    FROM identity_heads h
                    JOIN identity_revisions r
                      ON r.nothing_id = h.nothing_id AND r.revision = h.revision
                    WHERE h.nothing_id = ?
                    """,
                    (event["subject"],),
                ).fetchone()
                if identity_row is None:
                    raise NotFoundError(event["subject"])

                current_identity = self._stored_identity(identity_row).record
                current_events = self._load_events_for_subject(
                    connection,
                    event["subject"],
                )

                evidence_by_id = {
                    item["evidence_id"]: item
                    for item in self._load_evidence_for_events(
                        connection,
                        current_events,
                    )
                }
                for row in evidence_rows if evidence_ids else ():
                    evidence_by_id[row["evidence_id"]] = self._stored_evidence(row).record

                current_events_with_new = list(current_events) + [
                    StoredRecord(
                        record=payload,
                        content_sha256=digest,
                        recorded_at=recorded,
                    )
                ]
                registry = self._load_registry(connection)
                resolve_claim_relationships(
                    current_identity,
                    list(evidence_by_id.values()),
                    [stored.record for stored in current_events_with_new],
                    registry,
                )

                connection.execute(
                    """
                    INSERT INTO verification_events(
                        event_id, protocol_version, subject, claim_id, occurred_at,
                        procedure_id, procedure_version, supersedes_event_id,
                        payload_json, content_sha256, recorded_at, recorded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["version"],
                        event["subject"],
                        event["claim_id"],
                        event["occurred_at"],
                        event["procedure"]["id"],
                        event["procedure"]["version"],
                        event.get("supersedes"),
                        payload_json,
                        digest,
                        recorded,
                        actor,
                    ),
                )

                for evidence_id in evidence_ids:
                    connection.execute(
                        """
                        INSERT INTO event_evidence(event_id, evidence_id)
                        VALUES (?, ?)
                        """,
                        (event["event_id"], evidence_id),
                    )

                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        revision, content_sha256, details_json
                    ) VALUES (?, ?, 'APPEND', 'verification_event', ?, NULL, ?, ?)
                    """,
                    (
                        recorded,
                        actor,
                        event["event_id"],
                        digest,
                        json.dumps(
                            {
                                "subject": event["subject"],
                                "claim_id": event["claim_id"],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def ingest_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        actor: str,
        idempotency_key: str,
        request_sha256: str,
        ingestion_id: str,
        recorded_at: str | None = None,
    ) -> IngestionResult:
        """Atomically validate, persist and deduplicate an authenticated bundle."""
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if (
            not isinstance(request_sha256, str)
            or len(request_sha256) != 64
            or any(ch not in "0123456789abcdefABCDEF" for ch in request_sha256)
        ):
            raise ValueError("request_sha256 must be a SHA-256 hex digest")
        if not isinstance(ingestion_id, str) or not ingestion_id.strip():
            raise ValueError("ingestion_id must be a non-empty string")

        expected_keys = {"identities", "evidence", "verification_events"}
        if set(bundle.keys()) != expected_keys:
            raise ValidationError(
                "ingestion bundle must contain exactly identities, evidence, and verification_events"
            )

        identities_raw = bundle["identities"]
        evidence_raw = bundle["evidence"]
        events_raw = bundle["verification_events"]
        if not all(
            isinstance(value, list)
            for value in (identities_raw, evidence_raw, events_raw)
        ):
            raise ValidationError("ingestion bundle collections must be arrays")

        identities = [copy.deepcopy(dict(item)) for item in identities_raw]
        evidence_records = [copy.deepcopy(dict(item)) for item in evidence_raw]
        events = [copy.deepcopy(dict(item)) for item in events_raw]

        for identity in identities:
            validate_identity(identity)
        for evidence in evidence_records:
            validate_evidence(evidence)
        for event in events:
            validate_verification_event(event)

        def ensure_unique(values: Sequence[str], label: str) -> None:
            if len(values) != len(set(values)):
                raise ValidationError(f"duplicate {label} in ingestion bundle")

        ensure_unique([item["nothing_id"] for item in identities], "nothing_id")
        ensure_unique([item["evidence_id"] for item in evidence_records], "evidence_id")
        ensure_unique([item["event_id"] for item in events], "event_id")

        if not identities and not evidence_records and not events:
            raise ValidationError("ingestion bundle must contain at least one record")

        recorded = recorded_at or _utc_now()
        identity_payloads = {item["nothing_id"]: item for item in identities}
        evidence_payloads = {item["evidence_id"]: item for item in evidence_records}
        event_payloads = {item["event_id"]: item for item in events}

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")

                existing_ingestion = connection.execute(
                    """
                    SELECT request_sha256, ingestion_id, result_json, recorded_at
                    FROM ingestion_idempotency
                    WHERE actor = ? AND idempotency_key = ?
                    """,
                    (actor, idempotency_key),
                ).fetchone()

                if existing_ingestion is not None:
                    if existing_ingestion["request_sha256"] != request_sha256:
                        raise ConflictError(
                            "Idempotency-Key has already been used with a different request"
                        )
                    connection.execute("COMMIT")
                    return IngestionResult(
                        data=json.loads(existing_ingestion["result_json"]),
                        recorded_at=existing_ingestion["recorded_at"],
                        replayed=True,
                    )

                identity_state: dict[str, dict[str, Any]] = {}
                identity_actions: list[dict[str, Any]] = []

                for nothing_id, identity in identity_payloads.items():
                    row = connection.execute(
                        """
                        SELECT r.*
                        FROM identity_heads h
                        JOIN identity_revisions r
                          ON r.nothing_id = h.nothing_id AND r.revision = h.revision
                        WHERE h.nothing_id = ?
                        """,
                        (nothing_id,),
                    ).fetchone()

                    digest = _content_hash(identity)
                    if row is None:
                        revision = 1
                        previous_hash = None
                        written = True
                    elif row["content_sha256"] == digest:
                        revision = int(row["revision"])
                        previous_hash = row["content_sha256"]
                        written = False
                    else:
                        revision = int(row["revision"]) + 1
                        previous_hash = row["content_sha256"]
                        written = True

                    identity_state[nothing_id] = identity
                    identity_actions.append(
                        {
                            "id": nothing_id,
                            "revision": revision,
                            "written": written,
                            "payload": identity,
                            "digest": digest,
                            "previous_hash": previous_hash,
                        }
                    )

                for event in events:
                    if event["subject"] in identity_state:
                        continue
                    row = connection.execute(
                        """
                        SELECT r.*
                        FROM identity_heads h
                        JOIN identity_revisions r
                          ON r.nothing_id = h.nothing_id AND r.revision = h.revision
                        WHERE h.nothing_id = ?
                        """,
                        (event["subject"],),
                    ).fetchone()
                    if row is None:
                        raise NotFoundError(event["subject"])
                    identity_state[event["subject"]] = self._stored_identity(row).record

                evidence_by_id: dict[str, dict[str, Any]] = {}
                existing_evidence_by_id: dict[str, sqlite3.Row] = {}
                if evidence_payloads:
                    placeholders = ",".join("?" for _ in evidence_payloads)
                    rows = connection.execute(
                        f"""
                        SELECT *
                        FROM evidence
                        WHERE evidence_id IN ({placeholders})
                        """,
                        list(evidence_payloads),
                    ).fetchall()
                    existing_evidence_by_id = {
                        row["evidence_id"]: row for row in rows
                    }

                evidence_actions: list[dict[str, Any]] = []
                for evidence_id, evidence in evidence_payloads.items():
                    digest = _content_hash(evidence)
                    existing = existing_evidence_by_id.get(evidence_id)
                    if existing is not None and existing["content_sha256"] != digest:
                        raise ConflictError(
                            f"evidence {evidence_id} is immutable; create a new evidence ID"
                        )
                    evidence_by_id[evidence_id] = evidence
                    evidence_actions.append(
                        {
                            "id": evidence_id,
                            "written": existing is None,
                            "payload": evidence,
                            "digest": digest,
                        }
                    )

                affected_subjects = sorted({event["subject"] for event in events})
                existing_events_by_subject: dict[str, list[StoredRecord]] = {}
                for subject in affected_subjects:
                    current_events = self._load_events_for_subject(
                        connection,
                        subject,
                    )
                    existing_events_by_subject[subject] = current_events
                    for stored in current_events:
                        for evidence_id in stored.record.get("evidence", []):
                            if evidence_id in evidence_by_id:
                                continue
                            row = connection.execute(
                                "SELECT * FROM evidence WHERE evidence_id = ?",
                                (evidence_id,),
                            ).fetchone()
                            if row is None:
                                raise NotFoundError(evidence_id)
                            evidence_by_id[evidence_id] = self._stored_evidence(row).record

                existing_event_by_id: dict[str, sqlite3.Row] = {}
                if event_payloads:
                    placeholders = ",".join("?" for _ in event_payloads)
                    rows = connection.execute(
                        f"""
                        SELECT *
                        FROM verification_events
                        WHERE event_id IN ({placeholders})
                        """,
                        list(event_payloads),
                    ).fetchall()
                    existing_event_by_id = {
                        row["event_id"]: row for row in rows
                    }

                event_actions: list[dict[str, Any]] = []
                new_events_by_subject: dict[str, list[dict[str, Any]]] = {}
                for event_id, event in event_payloads.items():
                    digest = _content_hash(event)
                    existing = existing_event_by_id.get(event_id)
                    if existing is not None:
                        if existing["content_sha256"] != digest:
                            raise ConflictError(
                                f"verification event {event_id} is immutable; create a new event"
                            )
                        written = False
                    else:
                        written = True
                        new_events_by_subject.setdefault(event["subject"], []).append(event)

                    for evidence_id in event.get("evidence", []):
                        if evidence_id not in evidence_by_id:
                            raise NotFoundError(evidence_id)

                    event_actions.append(
                        {
                            "id": event_id,
                            "written": written,
                            "payload": event,
                            "digest": digest,
                        }
                    )

                for event in events:
                    procedure_key = (
                        event["procedure"]["id"],
                        event["procedure"]["version"],
                    )
                    row = connection.execute(
                        """
                        SELECT 1
                        FROM procedures
                        WHERE procedure_id = ? AND version = ?
                        """,
                        procedure_key,
                    ).fetchone()
                    if row is None:
                        raise NotFoundError(
                            f"{procedure_key[0]}@{procedure_key[1]}"
                        )

                registry = self._load_registry(connection)
                for subject in affected_subjects:
                    candidate_events = list(existing_events_by_subject[subject])
                    candidate_events.extend(
                        StoredRecord(
                            record=event,
                            content_sha256=_content_hash(event),
                            recorded_at=recorded,
                        )
                        for event in new_events_by_subject.get(subject, [])
                    )
                    resolve_claim_relationships(
                        identity_state[subject],
                        list(evidence_by_id.values()),
                        [stored.record for stored in candidate_events],
                        registry,
                    )

                for action in identity_actions:
                    if not action["written"]:
                        continue
                    connection.execute(
                        """
                        INSERT INTO identity_revisions(
                            nothing_id, revision, protocol_version, payload_json,
                            content_sha256, recorded_at, recorded_by,
                            previous_content_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action["id"],
                            action["revision"],
                            action["payload"]["version"],
                            _canonical_json(action["payload"]),
                            action["digest"],
                            recorded,
                            actor,
                            action["previous_hash"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO identity_heads(nothing_id, revision, content_sha256)
                        VALUES (?, ?, ?)
                        ON CONFLICT(nothing_id) DO UPDATE SET
                            revision = excluded.revision,
                            content_sha256 = excluded.content_sha256
                        """,
                        (
                            action["id"],
                            action["revision"],
                            action["digest"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            recorded_at, actor, action, record_type, record_id,
                            revision, content_sha256, details_json
                        ) VALUES (?, ?, 'APPEND', 'identity', ?, ?, ?, ?)
                        """,
                        (
                            recorded,
                            actor,
                            action["id"],
                            action["revision"],
                            action["digest"],
                            json.dumps(
                                {
                                    "protocol_version": action["payload"]["version"],
                                    "ingestion_id": ingestion_id,
                                    "idempotency_key": idempotency_key,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                for action in evidence_actions:
                    if not action["written"]:
                        continue
                    evidence = action["payload"]
                    connection.execute(
                        """
                        INSERT INTO evidence(
                            evidence_id, protocol_version, payload_json,
                            content_sha256, recorded_at, recorded_by
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action["id"],
                            evidence["version"],
                            _canonical_json(evidence),
                            action["digest"],
                            recorded,
                            actor,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            recorded_at, actor, action, record_type, record_id,
                            revision, content_sha256, details_json
                        ) VALUES (?, ?, 'APPEND', 'evidence', ?, NULL, ?, ?)
                        """,
                        (
                            recorded,
                            actor,
                            action["id"],
                            action["digest"],
                            json.dumps(
                                {
                                    "ingestion_id": ingestion_id,
                                    "idempotency_key": idempotency_key,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                for action in event_actions:
                    if not action["written"]:
                        continue
                    event = action["payload"]
                    connection.execute(
                        """
                        INSERT INTO verification_events(
                            event_id, protocol_version, subject, claim_id, occurred_at,
                            procedure_id, procedure_version, supersedes_event_id,
                            payload_json, content_sha256, recorded_at, recorded_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action["id"],
                            event["version"],
                            event["subject"],
                            event["claim_id"],
                            event["occurred_at"],
                            event["procedure"]["id"],
                            event["procedure"]["version"],
                            event.get("supersedes"),
                            _canonical_json(event),
                            action["digest"],
                            recorded,
                            actor,
                        ),
                    )
                    for evidence_id in event.get("evidence", []):
                        connection.execute(
                            """
                            INSERT INTO event_evidence(event_id, evidence_id)
                            VALUES (?, ?)
                            """,
                            (action["id"], evidence_id),
                        )
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            recorded_at, actor, action, record_type, record_id,
                            revision, content_sha256, details_json
                        ) VALUES (?, ?, 'APPEND', 'verification_event', ?, NULL, ?, ?)
                        """,
                        (
                            recorded,
                            actor,
                            action["id"],
                            action["digest"],
                            json.dumps(
                                {
                                    "subject": event["subject"],
                                    "claim_id": event["claim_id"],
                                    "ingestion_id": ingestion_id,
                                    "idempotency_key": idempotency_key,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                result_data = {
                    "ingestion_id": ingestion_id,
                    "accepted": True,
                    "identities": [
                        {
                            "id": action["id"],
                            "revision": action["revision"],
                            "written": action["written"],
                        }
                        for action in identity_actions
                    ],
                    "evidence": [
                        {
                            "id": action["id"],
                            "written": action["written"],
                        }
                        for action in evidence_actions
                    ],
                    "verification_events": [
                        {
                            "id": action["id"],
                            "written": action["written"],
                        }
                        for action in event_actions
                    ],
                }

                connection.execute(
                    """
                    INSERT INTO audit_log(
                        recorded_at, actor, action, record_type, record_id,
                        revision, content_sha256, details_json
                    ) VALUES (?, ?, 'ACCEPT', 'ingestion', ?, NULL, ?, ?)
                    """,
                    (
                        recorded,
                        actor,
                        ingestion_id,
                        request_sha256,
                        json.dumps(
                            {
                                "idempotency_key": idempotency_key,
                                "identity_count": len(identity_actions),
                                "evidence_count": len(evidence_actions),
                                "verification_event_count": len(event_actions),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO ingestion_idempotency(
                        actor, idempotency_key, request_sha256, ingestion_id,
                        status_code, result_json, recorded_at
                    ) VALUES (?, ?, ?, ?, 200, ?, ?)
                    """,
                    (
                        actor,
                        idempotency_key,
                        request_sha256,
                        ingestion_id,
                        _canonical_json(result_data),
                        recorded,
                    ),
                )

                connection.execute("COMMIT")
                return IngestionResult(
                    data=result_data,
                    recorded_at=recorded,
                    replayed=False,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise



    def get_identity_bundle(self, nothing_id: str) -> IdentityBundle:
        with self._connect() as connection:
            identity_row = connection.execute(
                """
                SELECT r.*
                FROM identity_heads h
                JOIN identity_revisions r
                  ON r.nothing_id = h.nothing_id AND r.revision = h.revision
                WHERE h.nothing_id = ?
                """,
                (nothing_id,),
            ).fetchone()
            if identity_row is None:
                raise NotFoundError(nothing_id)

            identity = self._stored_identity(identity_row)
            events = self._load_events_for_subject(connection, nothing_id)
            evidence = self._load_evidence_for_events(connection, events)
            registry = self._load_registry(connection)
            proof_rows = connection.execute(
                """
                SELECT *
                FROM cryptographic_proofs
                WHERE resource_type = 'identity' AND resource_id = ?
                ORDER BY envelope_id
                """,
                (nothing_id,),
            ).fetchall()
            proofs = tuple(self._stored_proof(row) for row in proof_rows)

            timestamps = [identity.recorded_at]
            timestamps.extend(event.recorded_at for event in events)
            timestamps.extend(proof.recorded_at for proof in proofs)
            if evidence:
                placeholders = ",".join("?" for _ in evidence)
                evidence_ids = [item["evidence_id"] for item in evidence]
                evidence_rows = connection.execute(
                    f"""
                    SELECT recorded_at
                    FROM evidence
                    WHERE evidence_id IN ({placeholders})
                    """,
                    evidence_ids,
                ).fetchall()
                timestamps.extend(row["recorded_at"] for row in evidence_rows)

            procedure_keys = sorted(
                {
                    (
                        event.record["procedure"]["id"],
                        event.record["procedure"]["version"],
                    )
                    for event in events
                }
            )
            for procedure_id, version in procedure_keys:
                procedure_row = connection.execute(
                    """
                    SELECT recorded_at
                    FROM procedures
                    WHERE procedure_id = ? AND version = ?
                    """,
                    (procedure_id, version),
                ).fetchone()
                if procedure_row:
                    timestamps.append(procedure_row["recorded_at"])

        return IdentityBundle(
            identity=identity,
            evidence=evidence,
            events=tuple(events),
            registry=registry,
            last_modified=_max_time(timestamps),
            proofs=proofs,
        )

    def import_json_bundle(
        self,
        root: str | Path,
        *,
        actor: str = "json-import",
    ) -> dict[str, int]:
        """Import the repository's JSON bundle idempotently."""
        root_path = Path(root).expanduser().resolve()
        examples = root_path / "examples"
        procedure_path = root_path / "procedures" / "registry.json"

        counts = {
            "procedures_inserted": 0,
            "identities_added": 0,
            "evidence_inserted": 0,
            "events_inserted": 0,
            "proofs_inserted": 0,
        }

        registry = load_json(procedure_path)
        validate_procedure_registry(registry)
        for procedure in registry["procedures"]:
            if self.put_procedure(procedure, actor=actor):
                counts["procedures_inserted"] += 1

        for path in sorted(examples.glob("NTH-*.json")):
            identity = load_json(path)
            before = None
            try:
                before = self.get_identity(identity["nothing_id"])
            except NotFoundError:
                pass
            revision = self.put_identity(identity, actor=actor)
            if before is None or revision != before.revision:
                counts["identities_added"] += 1

        for path in sorted(examples.glob("EVD-*.json")):
            evidence = load_json(path)
            if self.put_evidence(evidence, actor=actor):
                counts["evidence_inserted"] += 1

        for path in sorted(examples.glob("VER-*.json")):
            event = load_json(path)
            if self.put_event(event, actor=actor):
                counts["events_inserted"] += 1

        for path in sorted(examples.glob("CRD-*.json")):
            envelope = load_json(path)
            if self.put_proof(envelope, actor=actor):
                counts["proofs_inserted"] += 1

        return counts


class FilesystemNothingStore:
    """The JSON example backend, retained for zero-setup demos/tests."""

    demo = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def close(self) -> None:
        return None

    def health(self) -> bool:
        return self.root.is_dir()

    def _examples(self) -> Path:
        return self.root / "examples"

    def _find(self, prefix: str, record_id: str, key: str) -> Path:
        exact = self._examples() / f"{record_id}.json"
        if exact.is_file():
            return exact
        for path in self._examples().glob(f"{prefix}-*.json"):
            try:
                record = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if record.get(key) == record_id:
                return path
        raise NotFoundError(record_id)

    def get_identity(self, nothing_id: str) -> StoredRecord:
        path = self._find("NTH", nothing_id, "nothing_id")
        record = load_json(path)
        validate_identity(record)
        return StoredRecord(
            record=record,
            content_sha256=_content_hash(record),
            recorded_at=datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            revision=1,
        )

    def get_identity_revision(self, nothing_id: str, revision: int) -> StoredRecord:
        if revision != 1:
            raise NotFoundError(f"{nothing_id}@{revision}")
        return self.get_identity(nothing_id)

    def get_evidence(self, evidence_id: str) -> StoredRecord:
        path = self._find("EVD", evidence_id, "evidence_id")
        record = load_json(path)
        validate_evidence(record)
        return StoredRecord(
            record=record,
            content_sha256=_content_hash(record),
            recorded_at=datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )

    def get_event(self, event_id: str) -> StoredRecord:
        path = self._find("VER", event_id, "event_id")
        record = load_json(path)
        validate_verification_event(record)
        return StoredRecord(
            record=record,
            content_sha256=_content_hash(record),
            recorded_at=datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )

    def get_procedure(self, procedure_id: str, version: str) -> StoredRecord:
        registry_path = self.root / "procedures" / "registry.json"
        registry = load_json(registry_path)
        validate_procedure_registry(registry)
        for procedure in registry["procedures"]:
            if procedure["id"] == procedure_id and procedure["version"] == version:
                return StoredRecord(
                    record=procedure,
                    content_sha256=_content_hash(procedure),
                    recorded_at=registry.get(
                        "updated_at",
                        datetime.fromtimestamp(
                            registry_path.stat().st_mtime, tz=timezone.utc
                        ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    ),
                )
        raise NotFoundError(f"{procedure_id}@{version}")
    def get_proof(self, envelope_id: str) -> StoredRecord:
        if not ENVELOPE_ID_RE.fullmatch(envelope_id):
            raise ValidationError("envelope_id must match CRD-XXXXXX.")
        path = self._find("CRD", envelope_id, "envelope_id")
        record = load_json(path)
        try:
            validate_envelope(record)
        except ProofError as exc:
            raise StoreError(f"proof {envelope_id} is invalid: {exc}") from exc
        return StoredRecord(
            record=record,
            content_sha256=_content_hash(record),
            recorded_at=datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )

    def get_proofs_for_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> tuple[StoredRecord, ...]:
        validators = {
            "identity": NOTHING_ID_RE,
            "evidence": EVIDENCE_ID_RE,
            "verification_event": EVENT_ID_RE,
        }
        pattern = validators.get(resource_type)
        if pattern is None or not pattern.fullmatch(resource_id):
            raise ValidationError("invalid proof resource selector")
        proofs = []
        for path in sorted(self._examples().glob("CRD-*.json")):
            try:
                record = load_json(path)
                validate_envelope(record)
            except (OSError, json.JSONDecodeError):
                continue
            except ProofError as exc:
                raise StoreError(f"invalid proof file: {path.name}: {exc}") from exc
            if record["resource_type"] == resource_type and record["resource_id"] == resource_id:
                proofs.append(
                    StoredRecord(
                        record=record,
                        content_sha256=_content_hash(record),
                        recorded_at=datetime.fromtimestamp(
                            path.stat().st_mtime, tz=timezone.utc
                        ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    )
                )
        return tuple(proofs)

    def put_proof(
        self,
        envelope: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        raise StoreError(
            "cryptographic proof writes require a durable persistence backend"
        )

    def ingest_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        actor: str,
        idempotency_key: str,
        request_sha256: str,
        ingestion_id: str,
        recorded_at: str | None = None,
    ) -> IngestionResult:
        raise StoreError(
            "authenticated write ingestion requires a durable persistence backend"
        )


    def get_identity_bundle(self, nothing_id: str) -> IdentityBundle:
        identity = self.get_identity(nothing_id)
        events: list[StoredRecord] = []
        for path in sorted(self._examples().glob("VER-*.json")):
            record = load_json(path)
            validate_verification_event(record)
            if record["subject"] == nothing_id:
                events.append(
                    StoredRecord(
                        record=record,
                        content_sha256=_content_hash(record),
                        recorded_at=datetime.fromtimestamp(
                            path.stat().st_mtime, tz=timezone.utc
                        ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    )
                )

        evidence_by_id: dict[str, StoredRecord] = {}
        for event in events:
            for evidence_id in event.record.get("evidence", []):
                if evidence_id not in evidence_by_id:
                    evidence_by_id[evidence_id] = self.get_evidence(evidence_id)

        registry_path = self.root / "procedures" / "registry.json"
        registry = load_json(registry_path)
        validate_procedure_registry(registry)

        timestamps = [identity.recorded_at]
        timestamps.extend(item.recorded_at for item in events)
        timestamps.extend(item.recorded_at for item in evidence_by_id.values())
        timestamps.append(
            registry.get(
                "updated_at",
                datetime.fromtimestamp(
                    registry_path.stat().st_mtime, tz=timezone.utc
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            )
        )

        proofs = self.get_proofs_for_resource("identity", nothing_id)
        timestamps.extend(item.recorded_at for item in proofs)

        return IdentityBundle(
            identity=identity,
            evidence=tuple(item.record for item in evidence_by_id.values()),
            events=tuple(events),
            registry=registry,
            last_modified=_max_time(timestamps),
            proofs=proofs,
        )


def make_store(
    *,
    backend: str,
    data_root: str | Path,
    db_path: str | Path | None = None,
    demo: bool = False,
) -> NothingStore:
    if backend == "filesystem":
        return FilesystemNothingStore(data_root)
    if backend == "sqlite":
        return SQLiteNothingStore(db_path or DEFAULT_DB_PATH, demo=demo)
    raise ValueError(f"unsupported storage backend: {backend}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize/import the NOTHING persistent storage backend."
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path",
    )
    parser.add_argument(
        "--import-root",
        default=None,
        help="Repository root containing examples/ and procedures/ to import",
    )
    parser.add_argument(
        "--actor",
        default="json-import",
        help="Audit actor recorded for imported data",
    )
    args = parser.parse_args()

    store = SQLiteNothingStore(args.db_path)
    if args.import_root:
        result = store.import_json_bundle(args.import_root, actor=args.actor)
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Initialized SQLite store at {store.db_path}")


if __name__ == "__main__":
    main()
