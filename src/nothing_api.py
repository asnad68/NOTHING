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
import sys
import uuid
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from src.nothing_auth import (
    AuthenticatedPrincipal,
    AuthConfigurationError,
    OIDCJwtAuthenticator,
)
from src.nothing_ingestion import (
    BearerAuthenticator,
    IDEMPOTENCY_KEY_RE,
    IngestionRequestError,
    INGESTION_MAX_BODY_BYTES,
    INGESTION_MAX_RECORDS,
    parse_ingestion_request,
)
from src.nothing_billing import (
    ConfirmationPolicy,
    PaymentValidationError,
    SubscriptionBillingService,
)
from src.nothing_protocol import RelationshipError, resolve_claim_relationships
from src.nothing_store import (
    ConflictError,
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

BILLING_MAX_BODY_BYTES = max(
    1,
    int(os.getenv("NOTHING_BILLING_MAX_BODY_BYTES", "65536")),
)
BILLING_DEFAULT_EXPIRY_SECONDS = 900
BILLING_MIN_EXPIRY_SECONDS = 60
BILLING_MAX_EXPIRY_SECONDS = 86400
BILLING_SCOPE = os.getenv(
    "NOTHING_BILLING_REQUIRED_SCOPE",
    "nothing:billing",
).strip()

BILLING_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("NOTHING_BILLING_RATE_WINDOW_SECONDS", "60")
)
BILLING_RATE_LIMIT_MAX_REQUESTS = int(
    os.getenv("NOTHING_BILLING_RATE_LIMIT_MAX_REQUESTS", "20")
)
TRUST_PROXY_HEADERS = os.getenv(
    "NOTHING_TRUST_PROXY_HEADERS",
    "false",
).lower() in {"1", "true", "yes"}
TENANCY_MODE = os.getenv("NOTHING_TENANCY_MODE", "single-tenant").strip().lower()


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
BILLING_RATE_LIMITER = RateLimiter(
    BILLING_RATE_LIMIT_WINDOW_SECONDS,
    BILLING_RATE_LIMIT_MAX_REQUESTS,
)


class NothingHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_class,
        *,
        store: NothingStore,
        owns_store: bool,
        ingestion_authenticator: Any,
        billing_authenticator: Any | None,
        billing_service: SubscriptionBillingService | None,
    ):
        super().__init__(server_address, handler_class)
        self.store = store
        self.owns_store = owns_store
        self.ingestion_authenticator = ingestion_authenticator
        self.billing_authenticator = billing_authenticator
        self.billing_service = billing_service

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

    @property
    def ingestion_authenticator(self) -> BearerAuthenticator:
        return self.server.ingestion_authenticator  # type: ignore[attr-defined]

    @property
    def billing_authenticator(self) -> Any | None:
        return self.server.billing_authenticator  # type: ignore[attr-defined]

    @property
    def billing_service(self) -> SubscriptionBillingService | None:
        return self.server.billing_service  # type: ignore[attr-defined]

    def _request_id(self) -> str:
        request_id = getattr(self, "_nothing_request_id", None)
        if request_id is None:
            request_id = str(uuid.uuid4())
            self._nothing_request_id = request_id
        return request_id

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        record = {
            "event": "http_request",
            "request_id": self._request_id(),
            "client_ip": self.client_address[0],
            "method": self.command,
            "path": self.path.split("?", 1)[0],
            "status": str(code),
            "bytes": str(size),
        }
        sys.stdout.write(json.dumps(record, sort_keys=True) + "\\n")
        sys.stdout.flush()

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid logging Authorization, cookies or request bodies.
        record = {
            "event": "http_message",
            "request_id": self._request_id(),
            "message": fmt % args,
        }
        sys.stdout.write(json.dumps(record, sort_keys=True) + "\\n")
        sys.stdout.flush()

    def _client_key(self) -> str:
        if TRUST_PROXY_HEADERS:
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                first = forwarded.split(",")[0].strip()
                if first:
                    return first
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
        self.send_header("X-Request-ID", self._request_id())
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
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send(
            status,
            _error_payload(status, code, detail, instance),
            content_type="application/problem+json",
            allow_cache=False,
            extra_headers=extra_headers,
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

    def _check_billing_rate_limit(self) -> bool:
        if not BILLING_RATE_LIMITER.allow(
            f"billing:{self._client_key()}"
        ):
            self._send_problem(
                429,
                "RATE_LIMITED",
                "Too many billing requests.",
                retry_after=BILLING_RATE_LIMIT_WINDOW_SECONDS,
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
        if path == "/v1/billing/invoices":
            if not self._check_billing_rate_limit():
                return
            self._post_billing_invoice()
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

        if path == "/v1/billing/invoices":
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

        if (
            path == "/v1/billing/prices"
            or path == "/v1/billing/entitlements"
            or path.startswith("/v1/billing/invoices/")
        ):
            self.send_response(204)
            self.send_header("Allow", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type",
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


    @staticmethod
    def _parse_billing_request(body: bytes) -> dict[str, Any]:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON property: {key}")
                result[key] = value
            return result

        if len(body) > BILLING_MAX_BODY_BYTES:
            raise OverflowError("billing request body exceeds configured limit")
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("billing request must contain valid UTF-8 JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("billing request must be a JSON object")
        if set(payload) - {"plan_code", "price_id", "expires_in_seconds"}:
            raise ValueError("billing request contains unsupported properties")

        plan_code = payload.get("plan_code")
        price_id = payload.get("price_id")
        expires_in = payload.get(
            "expires_in_seconds",
            BILLING_DEFAULT_EXPIRY_SECONDS,
        )
        if not isinstance(plan_code, str) or not 1 <= len(plan_code.strip()) <= 100:
            raise ValueError("plan_code must be a non-empty string")
        if not isinstance(price_id, str):
            raise ValueError("price_id must be a UUID string")
        try:
            uuid.UUID(price_id)
        except ValueError as exc:
            raise ValueError("price_id must be a UUID string") from exc
        if isinstance(expires_in, bool) or not isinstance(expires_in, int):
            raise ValueError("expires_in_seconds must be an integer")
        if not BILLING_MIN_EXPIRY_SECONDS <= expires_in <= BILLING_MAX_EXPIRY_SECONDS:
            raise ValueError(
                f"expires_in_seconds must be between {BILLING_MIN_EXPIRY_SECONDS} "
                f"and {BILLING_MAX_EXPIRY_SECONDS}"
            )
        return {
            "plan_code": plan_code.strip(),
            "price_id": price_id,
            "expires_in_seconds": expires_in,
        }

    def _billing_principal(self) -> AuthenticatedPrincipal | None:
        self._cors_allowed = False
        if self.billing_authenticator is None:
            return None
        principal = self.billing_authenticator.authenticate(
            self.headers.get("Authorization")
        )
        if principal is None:
            self._send_problem(
                401,
                "UNAUTHORIZED",
                "A valid bearer access token is required for billing.",
                extra_headers={
                    "WWW-Authenticate": (
                        'Bearer realm="NOTHING billing", '
                        'error="invalid_token"'
                    ),
                },
            )
            return None
        if not self.billing_authenticator.authorize(
            principal,
            BILLING_SCOPE,
        ):
            self._send_problem(
                403,
                "FORBIDDEN",
                "The access token lacks the required billing permission.",
            )
            return None
        return principal

    def _post_billing_invoice(self) -> None:
        if self.billing_service is None:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is not configured for this deployment.",
            )
            return

        principal = self._billing_principal()
        if principal is None:
            return

        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_problem(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "Content-Type must be application/json.",
            )
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self._send_problem(400, "CONTENT_LENGTH_REQUIRED", "Content-Length is required.")
            return
        try:
            length = int(content_length)
        except ValueError:
            self._send_problem(400, "INVALID_CONTENT_LENGTH", "Content-Length must be a non-negative integer.")
            return
        if length < 0:
            self._send_problem(400, "INVALID_CONTENT_LENGTH", "Content-Length must be a non-negative integer.")
            return
        if length > BILLING_MAX_BODY_BYTES:
            self._send_problem(413, "PAYLOAD_TOO_LARGE", "The billing request body exceeds the configured size limit.")
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self._send_problem(400, "INCOMPLETE_REQUEST", "The request body ended before Content-Length was satisfied.")
            return

        idempotency_key = self.headers.get("Idempotency-Key")
        if idempotency_key is None or not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            self._send_problem(
                400,
                "IDEMPOTENCY_KEY_INVALID",
                "A valid Idempotency-Key header is required.",
            )
            return

        try:
            request = self._parse_billing_request(body)
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=request["expires_in_seconds"]
            )
            customer_ref = SubscriptionBillingService.customer_ref_for_actor(
                principal.actor
            )
            invoice = self.billing_service.create_invoice(
                customer_ref=customer_ref,
                plan_code=request["plan_code"],
                price_id=request["price_id"],
                client_idempotency_key=idempotency_key,
                expires_at=expires_at,
                actor=principal.actor,
            )
        except OverflowError as exc:
            self._send_problem(413, "PAYLOAD_TOO_LARGE", str(exc))
            return
        except (ValueError, PaymentValidationError) as exc:
            self._send_problem(400, "INVALID_BILLING_REQUEST", str(exc))
            return
        except ConflictError as exc:
            self._send_problem(409, "CONFLICT", str(exc))
            return
        except NotFoundError as exc:
            self._send_problem(404, "NOT_FOUND", str(exc))
            return
        except StoreError:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is temporarily unavailable.",
            )
            return

        try:
            snapshot = self.billing_service.get_invoice(
                invoice_id=invoice.invoice_id,
                customer_ref=SubscriptionBillingService.customer_ref_for_actor(
                    principal.actor
                ),
            )
        except StoreError:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is temporarily unavailable.",
            )
            return

        payload = {
            "data": self._billing_snapshot_view(snapshot),
            "meta": _meta(
                demo=self.store.demo,
                generated_at=datetime.now(timezone.utc).isoformat(),
            ),
        }
        self._send(
            200,
            payload,
            allow_cache=False,
        )

    @staticmethod
    def _iso_datetime(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value

    @classmethod
    def _billing_snapshot_view(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        amount_atomic = int(snapshot["amount_atomic"])
        decimals = int(snapshot["asset_decimals"])
        from decimal import Decimal

        response = dict(snapshot)
        response["amount_atomic"] = str(amount_atomic)
        response["amount"] = format(
            Decimal(amount_atomic).scaleb(-decimals).normalize(),
            "f",
        )
        response["expires_at"] = cls._iso_datetime(response["expires_at"])
        response["paid_at"] = cls._iso_datetime(response["paid_at"])

        entitlement = response.get("entitlement")
        if entitlement is not None:
            entitlement = dict(entitlement)
            for key in ("starts_at", "expires_at", "activated_at"):
                entitlement[key] = cls._iso_datetime(entitlement.get(key))
            response["entitlement"] = entitlement
        return response

    def _get_billing_invoice(self, invoice_id: str, instance: str) -> None:
        if self.billing_service is None:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is not configured for this deployment.",
                instance,
            )
            return
        principal = self._billing_principal()
        if principal is None:
            return
        try:
            snapshot = self.billing_service.get_invoice(
                invoice_id=invoice_id,
                customer_ref=SubscriptionBillingService.customer_ref_for_actor(
                    principal.actor
                ),
            )
        except PaymentValidationError as exc:
            self._send_problem(400, "INVALID_ID", str(exc), instance)
            return
        except NotFoundError:
            self._send_problem(404, "NOT_FOUND", "The requested invoice does not exist.", instance)
            return
        except StoreError:
            self._send_problem(503, "BILLING_UNAVAILABLE", "Billing persistence is temporarily unavailable.", instance)
            return

        self._send(
            200,
            {
                "data": self._billing_snapshot_view(snapshot),
                "meta": _meta(
                    demo=self.store.demo,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                ),
            },
            allow_cache=False,
        )

    def _get_billing_prices(self, instance: str) -> None:
        if self.billing_service is None:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is not configured for this deployment.",
                instance,
            )
            return
        principal = self._billing_principal()
        if principal is None:
            return
        try:
            prices = self.billing_service.list_prices()
        except StoreError:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is temporarily unavailable.",
                instance,
            )
            return

        self._send(
            200,
            {
                "data": {"prices": prices},
                "meta": _meta(
                    demo=self.store.demo,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                ),
            },
            allow_cache=False,
        )

    @classmethod
    def _billing_entitlement_view(cls, entitlement: dict[str, Any]) -> dict[str, Any]:
        result = dict(entitlement)
        for key in ("starts_at", "expires_at", "activated_at"):
            result[key] = cls._iso_datetime(result.get(key))
        return result

    def _get_billing_entitlements(self, instance: str) -> None:
        if self.billing_service is None:
            self._send_problem(
                503,
                "BILLING_UNAVAILABLE",
                "Billing persistence is not configured for this deployment.",
                instance,
            )
            return
        principal = self._billing_principal()
        if principal is None:
            return
        try:
            entitlements = self.billing_service.list_entitlements(
                customer_ref=SubscriptionBillingService.customer_ref_for_actor(
                    principal.actor
                ),
                active_only=True,
            )
        except (PaymentValidationError, StoreError) as exc:
            self._send_problem(503, "BILLING_UNAVAILABLE", str(exc), instance)
            return

        self._send(
            200,
            {
                "data": {
                    "entitlements": [
                        self._billing_entitlement_view(item)
                        for item in entitlements
                    ]
                },
                "meta": _meta(
                    demo=self.store.demo,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                ),
            },
            allow_cache=False,
        )

    def _post_ingestion_bundle(self) -> None:
        self._cors_allowed = False
        if not self.ingestion_authenticator.configured:
            self._send_problem(
                503,
                "WRITE_INGESTION_UNAVAILABLE",
                "Authenticated write ingestion is not configured.",
            )
            return

        principal = self.ingestion_authenticator.authenticate(
            self.headers.get("Authorization")
        )
        if principal is None:
            self._send_problem(
                401,
                "UNAUTHORIZED",
                "A valid bearer access token is required for write ingestion.",
                extra_headers={
                    "WWW-Authenticate": (
                        'Bearer realm="NOTHING ingestion", '
                        'error="invalid_token"'
                    ),
                },
            )
            return

        if not self.ingestion_authenticator.authorize(
            principal,
            "nothing:ingest",
        ):
            self._send_problem(
                403,
                "FORBIDDEN",
                "The access token lacks the required write-ingestion permission.",
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
                actor=principal.actor if isinstance(principal, AuthenticatedPrincipal) else self.ingestion_authenticator.actor,
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

        if path.startswith("/v1/billing/"):
            if not self._check_billing_rate_limit():
                return
        elif not self._check_rate_limit():
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

            if len(parts) == 3 and parts[:2] == ["v1", "billing"] and parts[2] == "prices":
                self._get_billing_prices(instance)
                return

            if len(parts) == 3 and parts[:2] == ["v1", "billing"] and parts[2] == "entitlements":
                self._get_billing_entitlements(instance)
                return

            if len(parts) == 4 and parts[:2] == ["v1", "billing"] and parts[2] == "invoices":
                self._get_billing_invoice(parts[3], instance)
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
    auth_mode: str | None = None,
    billing_authenticator: Any | None = None,
    billing_service: SubscriptionBillingService | None = None,
) -> NothingHttpServer:
    owns_store = store is None
    selected_backend = storage_backend or DEFAULT_BACKEND
    if TENANCY_MODE != "single-tenant":
        raise AuthConfigurationError(
            "multi-tenant deployment is not supported by the v0.1 data model; "
            "use NOTHING_TENANCY_MODE=single-tenant"
        )
    selected_auth_mode = (
        auth_mode
        or os.getenv("NOTHING_AUTH_MODE")
        or ("oidc-jwt" if selected_backend == "postgres" else "static-bearer")
    )
    if selected_backend == "postgres" and selected_auth_mode != "oidc-jwt":
        raise AuthConfigurationError(
            "the PostgreSQL production backend requires NOTHING_AUTH_MODE=oidc-jwt"
        )

    if store is None:
        if selected_backend == "sqlite":
            store = SQLiteNothingStore(
                db_path or DEFAULT_DB_PATH,
                demo=False,
            )
        elif selected_backend == "postgres":
            from src.nothing_postgres import PostgreSQLNothingStore

            store = PostgreSQLNothingStore.from_environment()
        elif selected_backend == "filesystem":
            store = FilesystemNothingStore(data_root or _repo_root())
        else:
            raise ValueError(f"unsupported storage backend: {selected_backend}")

    if selected_auth_mode == "oidc-jwt":
        ingestion_authenticator = OIDCJwtAuthenticator.from_environment()
        if billing_authenticator is None:
            billing_authenticator = OIDCJwtAuthenticator.from_environment(
                required_scope=os.getenv(
                    "NOTHING_BILLING_REQUIRED_SCOPE",
                    BILLING_SCOPE,
                ).strip()
            )
    elif selected_auth_mode == "static-bearer":
        ingestion_authenticator = BearerAuthenticator(
            ingestion_token,
            actor=ingestion_actor,
        )
        if billing_authenticator is None:
            billing_authenticator = BearerAuthenticator(
                os.getenv("NOTHING_BILLING_TOKEN"),
                actor=os.getenv(
                    "NOTHING_BILLING_ACTOR",
                    "authenticated-billing",
                ),
                scope=os.getenv(
                    "NOTHING_BILLING_REQUIRED_SCOPE",
                    BILLING_SCOPE,
                ),
            )
    else:
        raise AuthConfigurationError(
            "unsupported authentication mode: "
            f"{selected_auth_mode}"
        )

    if billing_service is None:
        try:
            from src.nothing_postgres import PostgreSQLNothingStore
            is_postgres = isinstance(store, PostgreSQLNothingStore)
        except ImportError:
            is_postgres = False
        if is_postgres:
            billing_service = SubscriptionBillingService(
                store,
                policies={
                    "xrpl": ConfirmationPolicy(
                        required_confirmations=1,
                        require_finality=True,
                    ),
                    "ethereum": ConfirmationPolicy(
                        required_confirmations=0,
                        require_finality=True,
                    ),
                    "bitcoin": ConfirmationPolicy(
                        required_confirmations=6,
                        require_finality=True,
                    ),
                },
            )

    return NothingHttpServer(
        (host, port),
        NothingApiHandler,
        store=store,
        owns_store=owns_store,
        ingestion_authenticator=ingestion_authenticator,
        billing_authenticator=billing_authenticator,
        billing_service=billing_service,
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
        choices=("filesystem", "sqlite", "postgres"),
        default=DEFAULT_BACKEND,
    )
    parser.add_argument(
        "--auth-mode",
        choices=("static-bearer", "oidc-jwt"),
        default=os.getenv(
            "NOTHING_AUTH_MODE",
            "oidc-jwt" if DEFAULT_BACKEND == "postgres" else "static-bearer",
        ),
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
        auth_mode=args.auth_mode,
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
