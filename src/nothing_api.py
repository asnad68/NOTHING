"""HTTP API for the NOTHING v1 contract.

The HTTP layer is storage-agnostic. The same API can run against the JSON demo
backend or the durable SQLite reference backend. Production deployments should
provide a PostgreSQL-backed implementation of the storage port.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
import threading
import time
from datetime import datetime
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from src.nothing_ingestion import (
    BearerAuthenticator,
    IngestionRequestError,
    INGESTION_MAX_BODY_BYTES,
    INGESTION_MAX_RECORDS,
    parse_ingestion_request,
)
from src.nothing_protocol import RelationshipError, resolve_claim_relationships
from src.nothing_store import (
    FilesystemNothingStore,
    NothingStore,
    NotFoundError,
    SQLiteNothingStore,
    StoreError,
)
from src.nothing_verify import (
    EVENT_ID_RE,
    EVIDENCE_ID_RE,
    NOTHING_ID_RE,
    ValidationError,
)

API_VERSION = "1"
PROTOCOL_VERSION = "0.1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_BACKEND = os.getenv("NOTHING_STORAGE_BACKEND", "filesystem")
DEFAULT_DB_PATH = Path(os.getenv("NOTHING_DB_PATH", "data/nothing.db")).expanduser()

RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("NOTHING_RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(
    os.getenv(
        "NOTHING_RATE_LIMIT_MAX_REQUESTS",
        os.getenv("NOTHING_RATE_MAX_REQUESTS", "120"),
    )
)
INGESTION_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("NOTHING_INGESTION_RATE_WINDOW_SECONDS", "60")
)
INGESTION_RATE_LIMIT_MAX_REQUESTS = int(
    os.getenv("NOTHING_INGESTION_RATE_LIMIT_MAX_REQUESTS", "30")
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _etag(payload_bytes: bytes) -> str:
    return '"' + hashlib.sha256(payload_bytes).hexdigest() + '"'


def _error_payload(
    status: int,
    code: str,
    detail: str,
    instance: str | None = None,
) -> dict[str, Any]:
    payload = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "code": code,
        "detail": detail,
    }
    if instance:
        payload["instance"] = instance
    return payload


def _iso_to_http_date(value: str) -> str:
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    timestamp = datetime.fromisoformat(normalized).timestamp()
    return formatdate(timestamp, usegmt=True)


def _identity_view(
    identity: dict[str, Any],
    events: list[dict[str, Any]],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    events_by_id = {event["event_id"]: event for event in events}

    claims = []
    for claim in identity["claims"]:
        report = resolution["claims"][claim["claim_id"]]
        event_id = report["current_event_id"]
        current_event = events_by_id.get(event_id) if event_id else None

        current_verification = None
        if current_event:
            current_verification = {
                "event_id": current_event["event_id"],
                "status": current_event["result"]["status"],
                "occurred_at": current_event["occurred_at"],
                "scope": current_event["result"]["scope"],
                "procedure": {
                    "id": current_event["procedure"]["id"],
                    "version": current_event["procedure"]["version"],
                },
                "evidence_ids": list(current_event.get("evidence", [])),
            }

        claims.append(
            {
                "id": claim["claim_id"],
                "statement": claim["statement"],
                "recorded_status": claim["status"],
                "current_verification": current_verification,
            }
        )

    return {
        "id": identity["nothing_id"],
        "version": identity["version"],
        "subject": identity["subject"],
        "claims": claims,
        "revocation": identity.get("revocation"),
    }


def _evidence_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["evidence_id"],
        "version": record["version"],
        "type": record["type"],
        "source": record["source"],
        "collected_at": record["collected_at"],
        "integrity": record["integrity"],
        **({"notes": record["notes"]} if "notes" in record else {}),
    }


def _event_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["event_id"],
        "version": record["version"],
        "occurred_at": record["occurred_at"],
        "subject": record["subject"],
        "claim_id": record["claim_id"],
        "procedure": record["procedure"],
        "verifier": record.get("verifier"),
        "evidence": list(record.get("evidence", [])),
        "result": record["result"],
        **({"supersedes": record["supersedes"]} if "supersedes" in record else {}),
    }


def _meta(*, demo: bool, generated_at: str) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": generated_at,
        "demo": demo,
    }


class RateLimiter:
    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = max(1, window_seconds)
        self.max_requests = max(1, max_requests)
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        with self._lock:
            started, count = self._windows.get(key, (current, 0))
            if current - started >= self.window_seconds:
                started, count = current, 0
            if count >= self.max_requests:
                self._windows[key] = (started, count)
                return False
            self._windows[key] = (started, count + 1)
            return True


RATE_LIMITER = RateLimiter(RATE_LIMIT_WINDOW_SECONDS, RATE_LIMIT_MAX_REQUESTS)
INGESTION_RATE_LIMITER = RateLimiter(
    INGESTION_RATE_LIMIT_WINDOW_SECONDS,
    INGESTION_RATE_LIMIT_MAX_REQUESTS,
)


class NothingHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_class,
        *,
        store: NothingStore,
        owns_store: bool,
        ingestion_authenticator: BearerAuthenticator,
    ):
        super().__init__(server_address, handler_class)
        self.store = store
        self.owns_store = owns_store
        self.ingestion_authenticator = ingestion_authenticator

    def server_close(self) -> None:
        super().server_close()
        if self.owns_store:
            self.store.close()


class NothingApiHandler(BaseHTTPRequestHandler):
    server_version = "NOTHING-Reference/0.2"
    protocol_version = "HTTP/1.1"

    @property
    def store(self) -> NothingStore:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _send(
        self,
        status: int,
        payload: dict[str, Any] | None,
        *,
        content_type: str = "application/json",
        etag: str | None = None,
        last_modified: str | None = None,
        allow_cache: bool = True,
        retry_after: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = b"" if payload is None else _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-NOTHING-Protocol-Version", PROTOCOL_VERSION)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Cache-Control",
            "public, max-age=60" if allow_cache else "no-store",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "_cors_allowed", True):
            self.send_header("Access-Control-Allow-Origin", "*")
        if etag:
            self.send_header("ETag", etag)
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        for header_name, header_value in (extra_headers or {}).items():
            self.send_header(header_name, header_value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_problem(
        self,
        status: int,
        code: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        self._send(
            status,
            _error_payload(status, code, detail, instance),
            content_type="application/problem+json",
            allow_cache=False,
        )

    def _check_rate_limit(self) -> bool:
        if not RATE_LIMITER.allow(self._client_key()):
            self._send_problem(
                429,
                "RATE_LIMITED",
                "Too many requests for this reference server.",
                retry_after=RATE_LIMIT_WINDOW_SECONDS,
            )
            return False
        return True

    def _check_ingestion_rate_limit(self) -> bool:
        if not INGESTION_RATE_LIMITER.allow(
            f"ingestion:{self._client_key()}"
        ):
            self._send_problem(
                429,
                "RATE_LIMITED",
                "Too many write-ingestion requests.",
                retry_after=INGESTION_RATE_LIMIT_WINDOW_SECONDS,
            )
            return False
        return True

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/v1/ingestion/bundles":
            if not self._check_ingestion_rate_limit():
                return
            self._post_ingestion_bundle()
            return
        self._method_not_allowed()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/v1/ingestion/bundles":
            self.send_response(204)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, Idempotency-Key",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(204)
        self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "If-None-Match, Content-Type",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()


    def _post_ingestion_bundle(self) -> None:
        self._cors_allowed = False
        if not self.ingestion_authenticator.configured:
            self._send_problem(
                503,
                "WRITE_INGESTION_UNAVAILABLE",
                "Authenticated write ingestion is not configured.",
            )
            return

        if not self.ingestion_authenticator.authenticate(
            self.headers.get("Authorization")
        ):
            self._send_problem(
                401,
                "UNAUTHORIZED",
                "A valid bearer credential is required for write ingestion.",
                extra_headers={
                    "WWW-Authenticate": 'Bearer realm="NOTHING ingestion"',
                },
            )
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self._send_problem(
                400,
                "CONTENT_LENGTH_REQUIRED",
                "Content-Length is required for write ingestion.",
            )
            return

        try:
            length = int(content_length)
        except ValueError:
            self._send_problem(
                400,
                "INVALID_CONTENT_LENGTH",
                "Content-Length must be a non-negative integer.",
            )
            return

        if length < 0:
            self._send_problem(
                400,
                "INVALID_CONTENT_LENGTH",
                "Content-Length must be a non-negative integer.",
            )
            return

        if length > INGESTION_MAX_BODY_BYTES:
            self._send_problem(
                413,
                "PAYLOAD_TOO_LARGE",
                "The ingestion request body exceeds the configured size limit.",
            )
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self._send_problem(
                400,
                "INCOMPLETE_REQUEST",
                "The request body ended before Content-Length was satisfied.",
            )
            return

        try:
            parsed = parse_ingestion_request(
                body,
                content_type=self.headers.get("Content-Type"),
                idempotency_key=self.headers.get("Idempotency-Key"),
                max_body_bytes=INGESTION_MAX_BODY_BYTES,
                max_records=INGESTION_MAX_RECORDS,
            )
        except IngestionRequestError as exc:
            message = str(exc)
            if message == "Idempotency-Key header is required":
                code = "IDEMPOTENCY_KEY_REQUIRED"
                status = 400
            elif message.startswith("Idempotency-Key"):
                code = "IDEMPOTENCY_KEY_INVALID"
                status = 400
            elif message == "Content-Type must be application/json":
                code = "UNSUPPORTED_MEDIA_TYPE"
                status = 415
            elif "body exceeds" in message:
                code = "PAYLOAD_TOO_LARGE"
                status = 413
            else:
                code = "INVALID_INGESTION_REQUEST"
                status = 400
            self._send_problem(status, code, message)
            return

        idempotency_key = self.headers.get("Idempotency-Key") or ""
        ingestion_id = str(uuid.uuid4())

        try:
            result = self.store.ingest_bundle(
                parsed.bundle,
                actor=self.ingestion_authenticator.actor,
                idempotency_key=idempotency_key,
                request_sha256=parsed.request_sha256,
                ingestion_id=ingestion_id,
            )
        except ConflictError as exc:
            self._send_problem(409, "CONFLICT", str(exc))
            return
        except (ValidationError, RelationshipError) as exc:
            self._send_problem(
                422,
                "INVALID_INGESTION_BUNDLE",
                str(exc),
            )
            return
        except NotFoundError:
            self._send_problem(
                422,
                "INVALID_INGESTION_BUNDLE",
                "The ingestion bundle references a record that does not exist.",
            )
            return
        except StoreError:
            self._send_problem(
                503,
                "WRITE_INGESTION_UNAVAILABLE",
                "The ingestion persistence service is temporarily unavailable.",
            )
            return

        payload = {
            "data": result.data,
            "meta": _meta(
                demo=self.store.demo,
                generated_at=result.recorded_at,
            ),
        }
        extra_headers = {
            "X-NOTHING-Ingestion-ID": result.data["ingestion_id"],
        }
        if result.replayed:
            extra_headers["Idempotent-Replay"] = "true"

        self._send(
            200,
            payload,
            allow_cache=False,
            extra_headers=extra_headers,
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        instance = parsed.path

        if path == "/healthz":
            self._send(
                200,
                {"status": "ok", "api_version": API_VERSION},
                allow_cache=False,
            )
            return

        if path == "/readyz":
            ready = self.store.health()
            self._send(
                200 if ready else 503,
                {
                    "status": "ready" if ready else "not_ready",
                    "api_version": API_VERSION,
                },
                allow_cache=False,
            )
            return

        if not self._check_rate_limit():
            return

        try:
            parts = [unquote(p) for p in path.split("/") if p]

            if len(parts) == 3 and parts[:2] == ["v1", "identity"]:
                self._get_identity(parts[2], instance)
                return

            if len(parts) == 3 and parts[:2] == ["v1", "evidence"]:
                self._get_evidence(parts[2], instance)
                return

            if len(parts) == 3 and parts[:2] == ["v1", "verification-events"]:
                self._get_event(parts[2], instance)
                return

            if len(parts) == 4 and parts[:2] == ["v1", "procedures"]:
                self._get_procedure(parts[2], parts[3], instance)
                return

            self._send_problem(
                404,
                "NOT_FOUND",
                "The requested API resource does not exist.",
                instance,
            )
        except (OSError, json.JSONDecodeError, ValidationError, RelationshipError, StoreError) as exc:
            self._send_problem(
                503,
                "TEMPORARILY_UNAVAILABLE",
                f"Verification data could not be resolved: {exc}",
                instance,
            )

    def _get_identity(self, nothing_id: str, instance: str) -> None:
        if not NOTHING_ID_RE.fullmatch(nothing_id):
            self._send_problem(
                400,
                "INVALID_ID",
                "nothing_id must match NTH-XXXXXX.",
                instance,
            )
            return

        try:
            bundle = self.store.get_identity_bundle(nothing_id)
        except NotFoundError:
            self._send_problem(
                404,
                "NOT_FOUND",
                "The requested Nothing ID does not exist.",
                instance,
            )
            return

        identity = bundle.identity.record
        events = [item.record for item in bundle.events]
        resolution = resolve_claim_relationships(
            identity,
            list(bundle.evidence),
            events,
            bundle.registry,
        )
        payload = {
            "data": _identity_view(identity, events, resolution),
            "meta": _meta(
                demo=self.store.demo,
                generated_at=bundle.last_modified,
            ),
        }
        self._serve_json(payload, bundle.last_modified)

    def _get_evidence(self, evidence_id: str, instance: str) -> None:
        if not EVIDENCE_ID_RE.fullmatch(evidence_id):
            self._send_problem(
                400,
                "INVALID_ID",
                "evidence_id must match EVD-XXXXXX.",
                instance,
            )
            return

        try:
            stored = self.store.get_evidence(evidence_id)
        except NotFoundError:
            self._send_problem(
                404,
                "NOT_FOUND",
                "The requested evidence record does not exist.",
                instance,
            )
            return

        payload = {
            "data": _evidence_view(stored.record),
            "meta": _meta(
                demo=self.store.demo,
                generated_at=stored.recorded_at,
            ),
        }
        self._serve_json(payload, stored.recorded_at)

    def _get_event(self, event_id: str, instance: str) -> None:
        if not EVENT_ID_RE.fullmatch(event_id):
            self._send_problem(
                400,
                "INVALID_ID",
                "event_id must match VER-XXXXXX.",
                instance,
            )
            return

        try:
            stored = self.store.get_event(event_id)
        except NotFoundError:
            self._send_problem(
                404,
                "NOT_FOUND",
                "The requested verification event does not exist.",
                instance,
            )
            return

        try:
            identity_bundle = self.store.get_identity_bundle(stored.record["subject"])
            resolution = resolve_claim_relationships(
                identity_bundle.identity.record,
                list(identity_bundle.evidence),
                [event.record for event in identity_bundle.events],
                identity_bundle.registry,
            )
            report = resolution["claims"][stored.record["claim_id"]]
            if event_id not in report["verification_event_ids"]:
                raise RelationshipError(
                    "verification event is not resolvable from its subject bundle"
                )
        except NotFoundError:
            self._send_problem(
                503,
                "TEMPORARILY_UNAVAILABLE",
                "The event's subject bundle could not be resolved.",
                instance,
            )
            return

        payload = {
            "data": _event_view(stored.record),
            "meta": _meta(
                demo=self.store.demo,
                generated_at=stored.recorded_at,
            ),
        }
        self._serve_json(payload, stored.recorded_at)

    def _get_procedure(self, procedure_id: str, version: str, instance: str) -> None:
        if not procedure_id.startswith("NOTHING-") or not version:
            self._send_problem(
                400,
                "INVALID_ID",
                "procedure_id or version is invalid.",
                instance,
            )
            return

        try:
            stored = self.store.get_procedure(procedure_id, version)
        except NotFoundError:
            self._send_problem(
                404,
                "NOT_FOUND",
                "The requested procedure version does not exist.",
                instance,
            )
            return

        payload = {
            "data": stored.record,
            "meta": _meta(
                demo=self.store.demo,
                generated_at=stored.recorded_at,
            ),
        }
        self._serve_json(payload, stored.recorded_at)

    def _serve_json(self, payload: dict[str, Any], last_modified_iso: str) -> None:
        body = _json_bytes(payload)
        etag = _etag(body)
        last_modified = _iso_to_http_date(last_modified_iso)
        if self.headers.get("If-None-Match", "").strip() == etag:
            self._send(
                304,
                None,
                etag=etag,
                last_modified=last_modified,
            )
            return

        self._send(
            200,
            payload,
            etag=etag,
            last_modified=last_modified,
        )


def build_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    store: NothingStore | None = None,
    storage_backend: str | None = None,
    data_root: str | Path | None = None,
    db_path: str | Path | None = None,
    ingestion_token: str | None = None,
    ingestion_actor: str | None = None,
) -> NothingHttpServer:
    owns_store = store is None
    if store is None:
        selected_backend = storage_backend or DEFAULT_BACKEND
        if selected_backend == "sqlite":
            store = SQLiteNothingStore(
                db_path or DEFAULT_DB_PATH,
                demo=False,
            )
        elif selected_backend == "filesystem":
            store = FilesystemNothingStore(data_root or _repo_root())
        else:
            raise ValueError(f"unsupported storage backend: {selected_backend}")

    return NothingHttpServer(
        (host, port),
        NothingApiHandler,
        store=store,
        owns_store=owns_store,
        ingestion_authenticator=BearerAuthenticator(
            ingestion_token,
            actor=ingestion_actor,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the NOTHING read-only API server."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("NOTHING_API_HOST", DEFAULT_HOST),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NOTHING_API_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--storage-backend",
        choices=("filesystem", "sqlite"),
        default=DEFAULT_BACKEND,
    )
    parser.add_argument(
        "--data-root",
        default=os.getenv("NOTHING_DATA_ROOT", str(_repo_root())),
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("NOTHING_DB_PATH", str(DEFAULT_DB_PATH)),
    )
    args = parser.parse_args()

    server = build_server(
        args.host,
        args.port,
        storage_backend=args.storage_backend,
        data_root=args.data_root,
        db_path=args.db_path,
    )
    print(
        f"NOTHING API listening on http://{args.host}:{args.port} "
        f"(storage={args.storage_backend})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping NOTHING API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
