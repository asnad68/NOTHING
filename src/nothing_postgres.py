"""Production PostgreSQL persistence backend for NOTHING.

The adapter keeps the same logical storage contract as the SQLite reference
backend while using PostgreSQL-native pooling, JSON/text payload storage,
transaction isolation and advisory locks for concurrent writers.
"""

from __future__ import annotations

import copy
import json
import os
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from src.nothing_protocol import (
    RelationshipError,
    resolve_claim_relationships,
    validate_procedure,
    validate_procedure_registry,
)
from src.nothing_store import (
    ConflictError,
    IdentityBundle,
    IngestionResult,
    NotFoundError,
    NothingStore,
    StoreError,
    StoredRecord,
    _canonical_json,
    _content_hash,
    _max_time,
    _utc_now,
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

STORAGE_SCHEMA_VERSION = 3
DEFAULT_POOL_MIN_SIZE = 2
DEFAULT_POOL_MAX_SIZE = 10
DEFAULT_POOL_TIMEOUT_SECONDS = 10
DEFAULT_STATEMENT_TIMEOUT_MS = 15000
DEFAULT_LOCK_TIMEOUT_MS = 5000
DEFAULT_SERIALIZATION_RETRIES = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 0.05


class PostgreSQLNotConfiguredError(StoreError):
    """Raised when the PostgreSQL driver or DSN is missing."""



def _retry_serializable_method(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapper(self: "PostgreSQLNothingStore", *args: Any, **kwargs: Any) -> Any:
        retries = self._serialization_retries
        for attempt in range(retries + 1):
            try:
                return function(self, *args, **kwargs)
            except (
                self._SerializationFailure,
                self._DeadlockDetected,
            ) as exc:
                if attempt >= retries:
                    raise StoreError(
                        "PostgreSQL serializable transaction could not complete after retries"
                    ) from exc
                delay = self._retry_backoff_seconds * (2**attempt)
                if delay:
                    time.sleep(delay)
        raise AssertionError("unreachable")


def _translate_database_errors(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapper(self: "PostgreSQLNothingStore", *args: Any, **kwargs: Any) -> Any:
        try:
            return function(self, *args, **kwargs)
        except StoreError:
            raise
        except self._psycopg.Error as exc:
            raise StoreError(
                "PostgreSQL persistence operation failed"
            ) from exc

    return wrapper


class PostgreSQLNothingStore:
    """Durable multi-instance PostgreSQL implementation of NothingStore."""

    demo = False

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = DEFAULT_POOL_MIN_SIZE,
        max_size: int = DEFAULT_POOL_MAX_SIZE,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT_SECONDS,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
        serialization_retries: int = DEFAULT_SERIALIZATION_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        if not dsn or not dsn.strip():
            raise PostgreSQLNotConfiguredError(
                "NOTHING_POSTGRES_DSN is required"
            )

        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise PostgreSQLNotConfiguredError(
                'install "psycopg[binary,pool]" for PostgreSQL production support'
            ) from exc

        self._psycopg = psycopg
        self._SerializationFailure = psycopg.errors.SerializationFailure
        self._DeadlockDetected = psycopg.errors.DeadlockDetected
        self._UniqueViolation = psycopg.errors.UniqueViolation
        self._pool = ConnectionPool(
            conninfo=dsn,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "application_name": os.getenv(
                    "NOTHING_POSTGRES_APPLICATION_NAME",
                    "nothing-api",
                ),
            },
            min_size=max(1, min_size),
            max_size=max(max(1, min_size), max_size),
            timeout=max(0.1, pool_timeout),
            max_waiting=max(
                0,
                int(os.getenv("NOTHING_POSTGRES_MAX_WAITING", "100")),
            ),
            max_lifetime=float(
                os.getenv("NOTHING_POSTGRES_MAX_LIFETIME_SECONDS", "3600")
            ),
            max_idle=float(
                os.getenv("NOTHING_POSTGRES_MAX_IDLE_SECONDS", "600")
            ),
            open=True,
            check=ConnectionPool.check_connection,
        )
        self._statement_timeout_ms = max(0, statement_timeout_ms)
        self._lock_timeout_ms = max(0, lock_timeout_ms)
        self._serialization_retries = max(0, serialization_retries)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._migrate()

    @classmethod
    def from_environment(cls) -> "PostgreSQLNothingStore":
        return cls(
            os.getenv("NOTHING_POSTGRES_DSN", "").strip(),
            min_size=int(
                os.getenv(
                    "NOTHING_POSTGRES_POOL_MIN_SIZE",
                    str(DEFAULT_POOL_MIN_SIZE),
                )
            ),
            max_size=int(
                os.getenv(
                    "NOTHING_POSTGRES_POOL_MAX_SIZE",
                    str(DEFAULT_POOL_MAX_SIZE),
                )
            ),
            pool_timeout=float(
                os.getenv(
                    "NOTHING_POSTGRES_POOL_TIMEOUT_SECONDS",
                    str(DEFAULT_POOL_TIMEOUT_SECONDS),
                )
            ),
            statement_timeout_ms=int(
                os.getenv(
                    "NOTHING_POSTGRES_STATEMENT_TIMEOUT_MS",
                    str(DEFAULT_STATEMENT_TIMEOUT_MS),
                )
            ),
            lock_timeout_ms=int(
                os.getenv(
                    "NOTHING_POSTGRES_LOCK_TIMEOUT_MS",
                    str(DEFAULT_LOCK_TIMEOUT_MS),
                )
            ),
            serialization_retries=int(
                os.getenv(
                    "NOTHING_POSTGRES_SERIALIZATION_RETRIES",
                    str(DEFAULT_SERIALIZATION_RETRIES),
                )
            ),
            retry_backoff_seconds=float(
                os.getenv(
                    "NOTHING_POSTGRES_RETRY_BACKOFF_SECONDS",
                    str(DEFAULT_RETRY_BACKOFF_SECONDS),
                )
            ),
        )

    def close(self) -> None:
        self._pool.close()

    def health(self) -> bool:
        try:
            with self._pool.connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def _migration_paths(self) -> list[Path]:
        root = Path(__file__).resolve().parents[1]
        migration_dir = root / "storage" / "migrations" / "postgres"
        return sorted(migration_dir.glob("*.sql"))

    def _migrate(self) -> None:
        paths = self._migration_paths()
        if not paths:
            raise PostgreSQLNotConfiguredError(
                "PostgreSQL migrations are missing from storage/migrations/postgres"
            )

        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 73939133)
                    )
                    """,
                    ("nothing:migrations",),
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
                ).fetchone()
                current = int(row["version"])

            migrations: list[tuple[int, Path]] = []
            for path in paths:
                try:
                    version = int(path.name.split("_", 1)[0])
                except (TypeError, ValueError) as exc:
                    raise PostgreSQLNotConfiguredError(
                        f"invalid PostgreSQL migration filename: {path.name}"
                    ) from exc
                migrations.append((version, path))

            for version, path in migrations:
                if version <= current:
                    continue
                sql = path.read_text(encoding="utf-8")
                with connection.transaction():
                    connection.execute(sql)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version)
                        VALUES (%s)
                        """,
                        (version,),
                    )

    @contextmanager
    def _transaction(
        self,
        *,
        retryable: bool,
    ) -> Iterator[Any]:
        del retryable
        with self._pool.connection() as connection:
            with connection.transaction():
                if self._statement_timeout_ms:
                    connection.execute(
                        "SELECT set_config(%s, %s, true)",
                        ("statement_timeout", f"{self._statement_timeout_ms}ms"),
                    )
                if self._lock_timeout_ms:
                    connection.execute(
                        "SELECT set_config(%s, %s, true)",
                        ("lock_timeout", f"{self._lock_timeout_ms}ms"),
                    )
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                )
                yield connection

    @staticmethod
    def _stored_identity(row: Mapping[str, Any]) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"].isoformat().replace("+00:00", "Z")
            if hasattr(row["recorded_at"], "isoformat")
            else row["recorded_at"],
            revision=int(row["revision"]),
        )

    @staticmethod
    def _stored_evidence(row: Mapping[str, Any]) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"].isoformat().replace("+00:00", "Z")
            if hasattr(row["recorded_at"], "isoformat")
            else row["recorded_at"],
        )

    @staticmethod
    def _stored_event(row: Mapping[str, Any]) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"].isoformat().replace("+00:00", "Z")
            if hasattr(row["recorded_at"], "isoformat")
            else row["recorded_at"],
        )

    @staticmethod
    def _stored_procedure(row: Mapping[str, Any]) -> StoredRecord:
        return StoredRecord(
            record=json.loads(row["payload_json"]),
            content_sha256=row["content_sha256"],
            recorded_at=row["recorded_at"].isoformat().replace("+00:00", "Z")
            if hasattr(row["recorded_at"], "isoformat")
            else row["recorded_at"],
        )

    @staticmethod
    def _recorded_sql_time(value: str) -> str:
        return value

    def _load_events_for_subject(
        self,
        connection: Any,
        subject: str,
    ) -> list[StoredRecord]:
        rows = connection.execute(
            """
            SELECT *
            FROM verification_events
            WHERE subject = %s
            ORDER BY occurred_at ASC, event_id ASC
            """,
            (subject,),
        ).fetchall()
        return [self._stored_event(row) for row in rows]

    def _load_evidence_for_events(
        self,
        connection: Any,
        events: Sequence[StoredRecord],
    ) -> tuple[dict[str, Any], ...]:
        ids: list[str] = []
        for event in events:
            for evidence_id in event.record.get("evidence", []):
                if evidence_id not in ids:
                    ids.append(evidence_id)
        if not ids:
            return ()

        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            WHERE evidence_id = ANY(%s)
            """,
            (ids,),
        ).fetchall()
        by_id = {
            row["evidence_id"]: self._stored_evidence(row).record
            for row in rows
        }
        return tuple(
            by_id[evidence_id]
            for evidence_id in ids
            if evidence_id in by_id
        )

    def _load_registry(self, connection: Any) -> dict[str, Any]:
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

    @_translate_database_errors
    def get_identity(self, nothing_id: str) -> StoredRecord:
        if not NOTHING_ID_RE.fullmatch(nothing_id):
            raise ValidationError("nothing_id must match NTH-XXXXXX.")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT r.*
                FROM identity_heads h
                JOIN identity_revisions r
                  ON r.nothing_id = h.nothing_id
                 AND r.revision = h.revision
                WHERE h.nothing_id = %s
                """,
                (nothing_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(nothing_id)
        return self._stored_identity(row)

    @_translate_database_errors
    def get_identity_revision(
        self,
        nothing_id: str,
        revision: int,
    ) -> StoredRecord:
        if not NOTHING_ID_RE.fullmatch(nothing_id) or revision < 1:
            raise ValidationError("invalid identity revision selector")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM identity_revisions
                WHERE nothing_id = %s AND revision = %s
                """,
                (nothing_id, revision),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{nothing_id}@{revision}")
        return self._stored_identity(row)

    @_translate_database_errors
    def get_evidence(self, evidence_id: str) -> StoredRecord:
        if not EVIDENCE_ID_RE.fullmatch(evidence_id):
            raise ValidationError("evidence_id must match EVD-XXXXXX.")
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM evidence WHERE evidence_id = %s",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(evidence_id)
        return self._stored_evidence(row)

    @_translate_database_errors
    def get_event(self, event_id: str) -> StoredRecord:
        if not EVENT_ID_RE.fullmatch(event_id):
            raise ValidationError("event_id must match VER-XXXXXX.")
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM verification_events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(event_id)
        return self._stored_event(row)

    @_translate_database_errors
    def get_procedure(
        self,
        procedure_id: str,
        version: str,
    ) -> StoredRecord:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM procedures
                WHERE procedure_id = %s AND version = %s
                """,
                (procedure_id, version),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{procedure_id}@{version}")
        return self._stored_procedure(row)

    @_retry_serializable_method
    @_translate_database_errors
    def put_identity(
        self,
        identity: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> int:
        validate_identity(identity)
        if not actor or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        payload = copy.deepcopy(dict(identity))
        nothing_id = payload["nothing_id"]
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._transaction(retryable=True) as connection:
            self._lock_keys(
                connection,
                [f"identity:{nothing_id}"],
            )
            existing = connection.execute(
                """
                SELECT revision
                FROM identity_revisions
                WHERE nothing_id = %s AND content_sha256 = %s
                """,
                (nothing_id, digest),
            ).fetchone()
            if existing is not None:
                return int(existing["revision"])

            head = connection.execute(
                """
                SELECT revision, content_sha256
                FROM identity_heads
                WHERE nothing_id = %s
                FOR UPDATE
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
                ) VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s, %s)
                """,
                (
                    nothing_id,
                    revision,
                    payload["version"],
                    _canonical_json(payload),
                    digest,
                    recorded,
                    actor,
                    previous_hash,
                ),
            )
            connection.execute(
                """
                INSERT INTO identity_heads(
                    nothing_id, revision, content_sha256
                ) VALUES (%s, %s, %s)
                ON CONFLICT (nothing_id) DO UPDATE SET
                    revision = EXCLUDED.revision,
                    content_sha256 = EXCLUDED.content_sha256
                """,
                (nothing_id, revision, digest),
            )
            self._audit(
                connection,
                recorded,
                actor,
                "APPEND",
                "identity",
                nothing_id,
                revision,
                digest,
                {"protocol_version": payload["version"]},
            )
            return revision

    @_retry_serializable_method
    @_translate_database_errors
    def put_evidence(
        self,
        evidence: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_evidence(evidence)
        if not actor or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        payload = copy.deepcopy(dict(evidence))
        evidence_id = payload["evidence_id"]
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._transaction(retryable=True) as connection:
            self._lock_keys(connection, [f"evidence:{evidence_id}"])
            existing = connection.execute(
                """
                SELECT content_sha256
                FROM evidence
                WHERE evidence_id = %s
                FOR UPDATE
                """,
                (evidence_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] == digest:
                    return False
                raise ConflictError(
                    f"evidence {evidence_id} is immutable; create a new evidence ID"
                )

            connection.execute(
                """
                INSERT INTO evidence(
                    evidence_id, protocol_version, payload_json,
                    content_sha256, recorded_at, recorded_by
                ) VALUES (%s, %s, %s, %s, %s::timestamptz, %s)
                """,
                (
                    evidence_id,
                    payload["version"],
                    _canonical_json(payload),
                    digest,
                    recorded,
                    actor,
                ),
            )
            self._audit(
                connection,
                recorded,
                actor,
                "APPEND",
                "evidence",
                evidence_id,
                None,
                digest,
                None,
            )
            return True

    @_retry_serializable_method
    @_translate_database_errors
    def put_procedure(
        self,
        procedure: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_procedure(procedure)
        if not actor or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        payload = copy.deepcopy(dict(procedure))
        procedure_id = payload["id"]
        version = payload["version"]
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()

        with self._transaction(retryable=True) as connection:
            self._lock_keys(
                connection,
                [f"procedure:{procedure_id}@{version}"],
            )
            existing = connection.execute(
                """
                SELECT content_sha256
                FROM procedures
                WHERE procedure_id = %s AND version = %s
                FOR UPDATE
                """,
                (procedure_id, version),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] == digest:
                    return False
                raise ConflictError(
                    f"procedure {procedure_id}@{version} is immutable"
                )

            connection.execute(
                """
                INSERT INTO procedures(
                    procedure_id, version, payload_json, content_sha256,
                    status, published_at, recorded_at, recorded_by
                ) VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s::timestamptz, %s)
                """,
                (
                    procedure_id,
                    version,
                    _canonical_json(payload),
                    digest,
                    payload["status"],
                    payload.get("published_at"),
                    recorded,
                    actor,
                ),
            )
            self._audit(
                connection,
                recorded,
                actor,
                "APPEND",
                "procedure",
                f"{procedure_id}@{version}",
                None,
                digest,
                None,
            )
            return True

    @_retry_serializable_method
    @_translate_database_errors
    def put_event(
        self,
        event: Mapping[str, Any],
        *,
        actor: str = "system",
        recorded_at: str | None = None,
    ) -> bool:
        validate_verification_event(event)
        if not actor or not actor.strip():
            raise ValueError("actor must be a non-empty string")

        payload = copy.deepcopy(dict(event))
        digest = _content_hash(payload)
        recorded = recorded_at or _utc_now()
        subject = payload["subject"]

        with self._transaction(retryable=True) as connection:
            self._lock_keys(connection, [f"identity:{subject}"])
            current_identity = self._locked_identity_or_missing(
                connection,
                subject,
            )
            self._validate_event_references(connection, payload)
            current_events = self._load_events_for_subject(
                connection,
                subject,
            )
            evidence_by_id = {
                item["evidence_id"]: item
                for item in self._load_evidence_for_events(
                    connection,
                    current_events,
                )
            }
            for evidence_id in payload.get("evidence", []):
                row = connection.execute(
                    "SELECT * FROM evidence WHERE evidence_id = %s",
                    (evidence_id,),
                ).fetchone()
                if row is not None:
                    evidence_by_id[evidence_id] = self._stored_evidence(row).record

            existing = connection.execute(
                """
                SELECT content_sha256
                FROM verification_events
                WHERE event_id = %s
                FOR UPDATE
                """,
                (payload["event_id"],),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] == digest:
                    return False
                raise ConflictError(
                    f"verification event {payload['event_id']} is immutable; create a new event"
                )

            new_events = list(current_events)
            new_events.append(
                StoredRecord(
                    record=payload,
                    content_sha256=digest,
                    recorded_at=recorded,
                )
            )
            registry = self._load_registry(connection)
            resolve_claim_relationships(
                current_identity,
                list(evidence_by_id.values()),
                [stored.record for stored in new_events],
                registry,
            )

            self._insert_event(connection, payload, digest, recorded, actor)
            return True

    @_translate_database_errors
    def get_identity_bundle(self, nothing_id: str) -> IdentityBundle:
        if not NOTHING_ID_RE.fullmatch(nothing_id):
            raise ValidationError("nothing_id must match NTH-XXXXXX.")

        with self._pool.connection() as connection:
            identity_row = connection.execute(
                """
                SELECT r.*
                FROM identity_heads h
                JOIN identity_revisions r
                  ON r.nothing_id = h.nothing_id
                 AND r.revision = h.revision
                WHERE h.nothing_id = %s
                """,
                (nothing_id,),
            ).fetchone()
            if identity_row is None:
                raise NotFoundError(nothing_id)

            identity = self._stored_identity(identity_row)
            events = self._load_events_for_subject(connection, nothing_id)
            evidence = self._load_evidence_for_events(connection, events)
            registry = self._load_registry(connection)

            timestamps = [identity.recorded_at]
            timestamps.extend(event.recorded_at for event in events)
            if evidence:
                evidence_ids = [item["evidence_id"] for item in evidence]
                rows = connection.execute(
                    """
                    SELECT recorded_at
                    FROM evidence
                    WHERE evidence_id = ANY(%s)
                    """,
                    (evidence_ids,),
                ).fetchall()
                timestamps.extend(
                    row["recorded_at"].isoformat().replace("+00:00", "Z")
                    if hasattr(row["recorded_at"], "isoformat")
                    else row["recorded_at"]
                    for row in rows
                )

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
                    WHERE procedure_id = %s AND version = %s
                    """,
                    (procedure_id, version),
                ).fetchone()
                if procedure_row:
                    recorded_at = procedure_row["recorded_at"]
                    timestamps.append(
                        recorded_at.isoformat().replace("+00:00", "Z")
                        if hasattr(recorded_at, "isoformat")
                        else recorded_at
                    )

        return IdentityBundle(
            identity=identity,
            evidence=evidence,
            events=tuple(events),
            registry=registry,
            last_modified=_max_time(timestamps),
        )

    def _lock_keys(self, connection: Any, keys: Sequence[str]) -> None:
        for key in sorted(set(keys)):
            connection.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(%s, 73939133)
                )
                """,
                (key,),
            )

    def _locked_identity_or_missing(
        self,
        connection: Any,
        subject: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT r.*
            FROM identity_heads h
            JOIN identity_revisions r
              ON r.nothing_id = h.nothing_id
             AND r.revision = h.revision
            WHERE h.nothing_id = %s
            FOR UPDATE
            """,
            (subject,),
        ).fetchone()
        if row is None:
            raise NotFoundError(subject)
        return self._stored_identity(row).record

    def _validate_event_references(
        self,
        connection: Any,
        event: Mapping[str, Any],
    ) -> None:
        if connection.execute(
            """
            SELECT 1 FROM procedures
            WHERE procedure_id = %s AND version = %s
            """,
            (event["procedure"]["id"], event["procedure"]["version"]),
        ).fetchone() is None:
            raise NotFoundError(
                f"{event['procedure']['id']}@{event['procedure']['version']}"
            )

        evidence_ids = list(event.get("evidence", []))
        if evidence_ids:
            rows = connection.execute(
                """
                SELECT evidence_id
                FROM evidence
                WHERE evidence_id = ANY(%s)
                """,
                (evidence_ids,),
            ).fetchall()
            found = {row["evidence_id"] for row in rows}
            missing = [item for item in evidence_ids if item not in found]
            if missing:
                raise NotFoundError(missing[0])

        if event.get("supersedes"):
            row = connection.execute(
                """
                SELECT subject, claim_id
                FROM verification_events
                WHERE event_id = %s
                """,
                (event["supersedes"],),
            ).fetchone()
            if row is None:
                raise NotFoundError(event["supersedes"])
            if (
                row["subject"] != event["subject"]
                or row["claim_id"] != event["claim_id"]
            ):
                raise RelationshipError(
                    "superseded event must match subject and claim"
                )

    def _insert_event(
        self,
        connection: Any,
        event: Mapping[str, Any],
        digest: str,
        recorded: str,
        actor: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO verification_events(
                event_id, protocol_version, subject, claim_id, occurred_at,
                procedure_id, procedure_version, supersedes_event_id,
                payload_json, content_sha256, recorded_at, recorded_by
            ) VALUES (
                %s, %s, %s, %s, %s::timestamptz,
                %s, %s, %s, %s, %s, %s::timestamptz, %s
            )
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
                _canonical_json(event),
                digest,
                recorded,
                actor,
            ),
        )
        for evidence_id in event.get("evidence", []):
            connection.execute(
                """
                INSERT INTO event_evidence(event_id, evidence_id)
                VALUES (%s, %s)
                """,
                (event["event_id"], evidence_id),
            )
        self._audit(
            connection,
            recorded,
            actor,
            "APPEND",
            "verification_event",
            event["event_id"],
            None,
            digest,
            {
                "subject": event["subject"],
                "claim_id": event["claim_id"],
            },
        )

    def _audit(
        self,
        connection: Any,
        recorded: str,
        actor: str,
        action: str,
        record_type: str,
        record_id: str,
        revision: int | None,
        content_sha256: str | None,
        details: Mapping[str, Any] | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(
                recorded_at, actor, action, record_type, record_id,
                revision, content_sha256, details_json
            ) VALUES (%s::timestamptz, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                recorded,
                actor,
                action,
                record_type,
                record_id,
                revision,
                content_sha256,
                json.dumps(
                    details,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if details is not None
                else None,
            ),
        )

    @_retry_serializable_method
    @_translate_database_errors
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
        if not actor or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if (
            not isinstance(request_sha256, str)
            or len(request_sha256) != 64
            or any(ch not in "0123456789abcdefABCDEF" for ch in request_sha256)
        ):
            raise ValueError("request_sha256 must be a SHA-256 hex digest")
        if not ingestion_id or not ingestion_id.strip():
            raise ValueError("ingestion_id must be a non-empty string")

        expected_keys = {
            "identities",
            "evidence",
            "verification_events",
        }
        if set(bundle.keys()) != expected_keys:
            raise ValidationError(
                "ingestion bundle must contain exactly identities, evidence, and verification_events"
            )

        identities = [
            copy.deepcopy(dict(item))
            for item in bundle["identities"]
        ]
        evidence_records = [
            copy.deepcopy(dict(item))
            for item in bundle["evidence"]
        ]
        events = [
            copy.deepcopy(dict(item))
            for item in bundle["verification_events"]
        ]

        for identity in identities:
            validate_identity(identity)
        for evidence in evidence_records:
            validate_evidence(evidence)
        for event in events:
            validate_verification_event(event)

        def ensure_unique(
            values: Sequence[str],
            label: str,
        ) -> None:
            if len(values) != len(set(values)):
                raise ValidationError(
                    f"duplicate {label} in ingestion bundle"
                )

        ensure_unique(
            [item["nothing_id"] for item in identities],
            "nothing_id",
        )
        ensure_unique(
            [item["evidence_id"] for item in evidence_records],
            "evidence_id",
        )
        ensure_unique(
            [item["event_id"] for item in events],
            "event_id",
        )

        if not identities and not evidence_records and not events:
            raise ValidationError(
                "ingestion bundle must contain at least one record"
            )

        recorded = recorded_at or _utc_now()
        identity_payloads = {
            item["nothing_id"]: item for item in identities
        }
        evidence_payloads = {
            item["evidence_id"]: item for item in evidence_records
        }
        event_payloads = {
            item["event_id"]: item for item in events
        }

        affected_subjects = sorted(
            {
                *identity_payloads.keys(),
                *(event["subject"] for event in events),
            }
        )

        with self._transaction(retryable=True) as connection:
            # Lock the idempotency namespace first, then subject keys in lexical
            # order. This deterministic order materially reduces deadlock risk.
            self._lock_keys(
                connection,
                [
                    f"idempotency:{actor}|{idempotency_key}",
                ],
            )
            self._lock_keys(
                connection,
                [f"identity:{subject}" for subject in affected_subjects],
            )

            existing_ingestion = connection.execute(
                """
                SELECT request_sha256, ingestion_id, result_json, recorded_at
                FROM ingestion_idempotency
                WHERE actor = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (actor, idempotency_key),
            ).fetchone()
            if existing_ingestion is not None:
                if existing_ingestion["request_sha256"] != request_sha256:
                    raise ConflictError(
                        "Idempotency-Key has already been used with a different request"
                    )
                recorded_value = existing_ingestion["recorded_at"]
                recorded_text = (
                    recorded_value.isoformat().replace("+00:00", "Z")
                    if hasattr(recorded_value, "isoformat")
                    else recorded_value
                )
                return IngestionResult(
                    data=json.loads(existing_ingestion["result_json"]),
                    recorded_at=recorded_text,
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
                      ON r.nothing_id = h.nothing_id
                     AND r.revision = h.revision
                    WHERE h.nothing_id = %s
                    FOR UPDATE
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
                identity_state[event["subject"]] = (
                    self._locked_identity_or_missing(
                        connection,
                        event["subject"],
                    )
                )

            evidence_by_id: dict[str, dict[str, Any]] = {}
            existing_evidence_by_id: dict[str, Any] = {}
            if evidence_payloads:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM evidence
                    WHERE evidence_id = ANY(%s)
                    FOR UPDATE
                    """,
                    (list(evidence_payloads),),
                ).fetchall()
                existing_evidence_by_id = {
                    row["evidence_id"]: row
                    for row in rows
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

            existing_events_by_subject: dict[str, list[StoredRecord]] = {}
            for subject in sorted(
                {event["subject"] for event in events}
            ):
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
                            "SELECT * FROM evidence WHERE evidence_id = %s",
                            (evidence_id,),
                        ).fetchone()
                        if row is None:
                            raise NotFoundError(evidence_id)
                        evidence_by_id[evidence_id] = (
                            self._stored_evidence(row).record
                        )

            if event_payloads:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM verification_events
                    WHERE event_id = ANY(%s)
                    FOR UPDATE
                    """,
                    (list(event_payloads),),
                ).fetchall()
                existing_event_by_id = {
                    row["event_id"]: row for row in rows
                }
            else:
                existing_event_by_id = {}

            event_actions: list[dict[str, Any]] = []
            new_events_by_subject: dict[str, list[dict[str, Any]]] = {}

            # Validate all references before any inserts. Existing immutable
            # event IDs are replayable only when their content is identical.
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
                    new_events_by_subject.setdefault(
                        event["subject"],
                        [],
                    ).append(event)

                for evidence_id in event.get("evidence", []):
                    if evidence_id not in evidence_by_id:
                        row = connection.execute(
                            "SELECT * FROM evidence WHERE evidence_id = %s",
                            (evidence_id,),
                        ).fetchone()
                        if row is None:
                            raise NotFoundError(evidence_id)
                        evidence_by_id[evidence_id] = (
                            self._stored_evidence(row).record
                        )

                if connection.execute(
                    """
                    SELECT 1
                    FROM procedures
                    WHERE procedure_id = %s AND version = %s
                    """,
                    (
                        event["procedure"]["id"],
                        event["procedure"]["version"],
                    ),
                ).fetchone() is None:
                    raise NotFoundError(
                        f"{event['procedure']['id']}@{event['procedure']['version']}"
                    )

                event_actions.append(
                    {
                        "id": event_id,
                        "written": written,
                        "payload": event,
                        "digest": digest,
                    }
                )

            registry = self._load_registry(connection)
            for subject in sorted(
                {event["subject"] for event in events}
            ):
                candidate_events = list(
                    existing_events_by_subject[subject]
                )
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
                payload = action["payload"]
                connection.execute(
                    """
                    INSERT INTO identity_revisions(
                        nothing_id, revision, protocol_version, payload_json,
                        content_sha256, recorded_at, recorded_by,
                        previous_content_sha256
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s::timestamptz, %s, %s
                    )
                    """,
                    (
                        action["id"],
                        action["revision"],
                        payload["version"],
                        _canonical_json(payload),
                        action["digest"],
                        recorded,
                        actor,
                        action["previous_hash"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO identity_heads(
                        nothing_id, revision, content_sha256
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (nothing_id) DO UPDATE SET
                        revision = EXCLUDED.revision,
                        content_sha256 = EXCLUDED.content_sha256
                    """,
                    (
                        action["id"],
                        action["revision"],
                        action["digest"],
                    ),
                )
                self._audit(
                    connection,
                    recorded,
                    actor,
                    "APPEND",
                    "identity",
                    action["id"],
                    action["revision"],
                    action["digest"],
                    {
                        "protocol_version": payload["version"],
                        "ingestion_id": ingestion_id,
                        "idempotency_key": idempotency_key,
                    },
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
                    ) VALUES (
                        %s, %s, %s, %s, %s::timestamptz, %s
                    )
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
                self._audit(
                    connection,
                    recorded,
                    actor,
                    "APPEND",
                    "evidence",
                    action["id"],
                    None,
                    action["digest"],
                    {
                        "ingestion_id": ingestion_id,
                        "idempotency_key": idempotency_key,
                    },
                )

            # Insert supersession chains in dependency order so a new event can
            # reference another new event in the same bundle without a FK race.
            remaining = {
                action["id"]: action
                for action in event_actions
                if action["written"]
            }
            inserted_event_ids: set[str] = set()
            while remaining:
                progress = False
                for event_id in sorted(remaining):
                    event = remaining[event_id]["payload"]
                    supersedes = event.get("supersedes")
                    if (
                        supersedes
                        and supersedes in remaining
                        and supersedes not in inserted_event_ids
                    ):
                        continue
                    self._insert_event(
                        connection,
                        event,
                        remaining[event_id]["digest"],
                        recorded,
                        actor,
                    )
                    inserted_event_ids.add(event_id)
                    del remaining[event_id]
                    progress = True
                    break
                if not progress:
                    raise RelationshipError(
                        "verification event supersession graph contains a cycle or unresolved dependency"
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

            self._audit(
                connection,
                recorded,
                actor,
                "ACCEPT",
                "ingestion",
                ingestion_id,
                None,
                request_sha256,
                {
                    "idempotency_key": idempotency_key,
                    "identity_count": len(identity_actions),
                    "evidence_count": len(evidence_actions),
                    "verification_event_count": len(event_actions),
                },
            )

            connection.execute(
                """
                INSERT INTO ingestion_idempotency(
                    actor, idempotency_key, request_sha256, ingestion_id,
                    status_code, result_json, recorded_at
                ) VALUES (
                    %s, %s, %s, %s::uuid, 200, %s, %s::timestamptz
                )
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

            return IngestionResult(
                data=result_data,
                recorded_at=recorded,
                replayed=False,
            )

    def import_json_bundle(
        self,
        root: str | Path,
        *,
        actor: str = "json-import",
    ) -> dict[str, int]:
        """Import the repository prototype dataset into PostgreSQL."""
        root_path = Path(root).expanduser().resolve()
        examples = root_path / "examples"
        procedure_path = root_path / "procedures" / "registry.json"

        counts = {
            "procedures_inserted": 0,
            "identities_added": 0,
            "evidence_inserted": 0,
            "events_inserted": 0,
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

        return counts
