"""Public paid-enrollment gateway for NOTHING.

The gateway is deployment-gated. It creates an invoice from an operator-defined
price, asks the browser wallet to send a native EVM payment, verifies the mined
transaction server-side, settles it through the existing Payment Core, and only
then writes a SELF-CLAIMED identity record.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.nothing_billing import ConfirmationPolicy, SubscriptionBillingService
from src.nothing_chain_adapters import EvmJsonRpcAdapter
from src.nothing_enrollment import (
    EnrollmentValidationError,
    RegistrationDraft,
    candidate_nothing_ids,
    validate_wallet_address,
    normalize_chain_id,
    build_self_claimed_identity,
)
from src.nothing_postgres import PostgreSQLNothingStore
from src.nothing_payments import PaymentInvoice
from src.nothing_store import ConflictError, NotFoundError, StoreError
from src.nothing_identity_control import (
    IdentityControlError,
    build_domain_challenge,
    issue_domain_challenge_token,
    normalize_domain,
    verify_domain_challenge_token,
    verify_domain_txt,
)

HOST = os.getenv("NOTHING_ENROLLMENT_API_HOST", "127.0.0.1")
PORT = int(os.getenv("NOTHING_ENROLLMENT_API_PORT", "8090"))
ENABLED = os.getenv("NOTHING_ENROLLMENT_ENABLED", "false").lower() in {"1", "true", "yes"}
PLAN_CODE = os.getenv("NOTHING_ENROLLMENT_PLAN_CODE", "business-registration").strip()
PRICE_ID = os.getenv("NOTHING_ENROLLMENT_PRICE_ID", "").strip()
VERIFY_BASE_URL = os.getenv("NOTHING_VERIFY_BASE_URL", "").strip().rstrip("/")
EVM_RPC_URL = os.getenv("NOTHING_ENROLLMENT_EVM_RPC_URL", "").strip()
EVM_NETWORK = os.getenv("NOTHING_ENROLLMENT_EVM_NETWORK", "ethereum").strip()
EVM_CHAIN_ID = int(os.getenv("NOTHING_ENROLLMENT_EVM_CHAIN_ID", "1"))
ACTOR = os.getenv("NOTHING_ENROLLMENT_ACTOR", "public-enrollment")
ALLOWED_ORIGIN = os.getenv("NOTHING_ENROLLMENT_ALLOWED_ORIGIN", "").strip()
MAX_BODY = max(1024, int(os.getenv("NOTHING_ENROLLMENT_MAX_BODY_BYTES", "65536")))
RATE_WINDOW = max(1, int(os.getenv("NOTHING_ENROLLMENT_RATE_WINDOW_SECONDS", "60")))
RATE_MAX = max(1, int(os.getenv("NOTHING_ENROLLMENT_RATE_LIMIT_MAX_REQUESTS", "10")))
DOMAIN_CHALLENGE_SECRET = os.getenv("NOTHING_DOMAIN_CHALLENGE_SECRET", "").strip()
DOMAIN_CHALLENGE_TTL_SECONDS = max(300, min(86400, int(os.getenv("NOTHING_DOMAIN_CHALLENGE_TTL_SECONDS", "1800"))))


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= RATE_WINDOW:
                started, count = now, 0
            if count >= RATE_MAX:
                self._windows[key] = (started, count)
                return False
            self._windows[key] = (started, count + 1)
            return True


RATE_LIMITER = RateLimiter()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_json(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BODY:
        raise EnrollmentValidationError("request body is too large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnrollmentValidationError("request body must be valid JSON") from exc
    if not isinstance(value, dict):
        raise EnrollmentValidationError("request body must be a JSON object")
    return value


def _customer_ref(wallet: str) -> str:
    return SubscriptionBillingService.customer_ref_for_actor(wallet.lower())


def _require_enabled() -> None:
    if not ENABLED:
        raise RuntimeError("public enrollment is not activated on this deployment")
    missing = []
    for key, value in (
        ("NOTHING_ENROLLMENT_PRICE_ID", PRICE_ID),
        ("NOTHING_ENROLLMENT_EVM_RPC_URL", EVM_RPC_URL),
        ("NOTHING_VERIFY_BASE_URL", VERIFY_BASE_URL),
    ):
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError("enrollment configuration is incomplete: " + ", ".join(missing))


def _services():
    store = PostgreSQLNothingStore.from_environment()
    billing = SubscriptionBillingService(
        store,
        policies={"ethereum": ConfirmationPolicy(required_confirmations=0, require_finality=True)},
    )
    return store, billing


def _configured_price(billing: SubscriptionBillingService) -> dict[str, Any]:
    for price in billing.list_prices():
        if price["price_id"] == PRICE_ID and price["plan_code"] == PLAN_CODE:
            return price
    raise NotFoundError("configured enrollment price does not exist")


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    header = handler.headers.get("Content-Length")
    if header is None:
        raise EnrollmentValidationError("Content-Length is required")
    try:
        length = int(header)
    except ValueError as exc:
        raise EnrollmentValidationError("Content-Length must be an integer") from exc
    if length < 0 or length > MAX_BODY:
        raise EnrollmentValidationError("request body is too large")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise EnrollmentValidationError("request body ended early")
    return body


def _public_identity(
    store: PostgreSQLNothingStore,
    billing: SubscriptionBillingService,
    invoice_id: str,
    wallet: str,
    draft: RegistrationDraft,
) -> dict[str, Any]:
    snapshot = billing.get_invoice(invoice_id=invoice_id, customer_ref=_customer_ref(wallet))
    if snapshot["status"] not in {"paid", "overpaid"} or not snapshot.get("entitlement"):
        raise ConflictError("payment has not activated the registration entitlement")
    digest = draft.digest()
    for candidate in candidate_nothing_ids(digest, 64):
        try:
            store.get_identity(candidate)
            continue
        except NotFoundError:
            identity = build_self_claimed_identity(draft, candidate)
            bundle = {"identities": [identity], "evidence": [], "verification_events": []}
            raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            stored = store.ingest_bundle(
                bundle,
                actor=ACTOR,
                idempotency_key=f"enrollment:{invoice_id}",
                request_sha256=hashlib.sha256(raw).hexdigest(),
                ingestion_id=str(uuid.uuid4()),
            )
            if getattr(stored, "replayed", False):
                return identity
            return identity
    raise EnrollmentValidationError("could not allocate a unique Nothing ID")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NOTHING-Enrollment/0.1"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if ALLOWED_ORIGIN and origin == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status: int, code: str, detail: str) -> None:
        self._send(status, {"error": {"code": code, "detail": detail, "status": status}})

    def _limit(self) -> bool:
        if RATE_LIMITER.allow(self.client_address[0]):
            return True
        self._error(429, "RATE_LIMITED", "Too many enrollment requests.")
        return False

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._limit():
            return
        if path == "/healthz":
            self._send(200, {"status": "ok", "enabled": ENABLED})
            return
        if path == "/v1/enrollment/config":
            try:
                _require_enabled()
                store, billing = _services()
                try:
                    price = _configured_price(billing)
                finally:
                    store.close()
                self._send(200, {"data": {
                    "enabled": True,
                    "mode": "evm-wallet-payment",
                    "plan_code": PLAN_CODE,
                    "price_id": PRICE_ID,
                    "network": price["network"],
                    "asset": price["asset_code"],
                    "asset_kind": price["asset_kind"],
                    "asset_decimals": price["asset_decimals"],
                    "amount": price["amount"],
                    "duration_seconds": price["duration_seconds"],
                    "verify_base_url": VERIFY_BASE_URL,
                }})
            except RuntimeError as exc:
                self._send(200, {"data": {"enabled": False, "mode": "activation-required", "reason": str(exc)}})
            except Exception as exc:
                self._error(503, "ENROLLMENT_UNAVAILABLE", str(exc))
            return
        self._error(404, "NOT_FOUND", "Enrollment endpoint does not exist.")

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._limit():
            return
        if path not in {"/v1/enrollment/quote", "/v1/enrollment/complete"}:
            self._error(404, "NOT_FOUND", "Enrollment endpoint does not exist.")
            return
        try:
            _require_enabled()
            payload = _safe_json(_read_body(self))
            registration = RegistrationDraft.from_mapping(payload.get("registration", {}))
            wallet = validate_wallet_address(payload.get("wallet_address", ""))
            if path == "/v1/enrollment/quote":
                chain_id = normalize_chain_id(payload.get("chain_id", ""))
                expected = hex(EVM_CHAIN_ID)
                if chain_id != expected.lower():
                    raise EnrollmentValidationError(f"switch wallet to chain {expected}")
                store, billing = _services()
                try:
                    price = _configured_price(billing)
                    if price["network"] != EVM_NETWORK or price["asset_kind"] != "native" or price["asset_code"] != "ETH" or price["routing_mode"] != "unique_destination":
                        raise RuntimeError("the configured enrollment price must be native ETH with an invoice-specific destination")
                    fingerprint = hashlib.sha256(
                        registration.canonical_json().encode("utf-8")
                    ).hexdigest()
                    idem = "enrollment-quote:" + hashlib.sha256(
                        (wallet + ":" + fingerprint + ":" + PRICE_ID).encode("utf-8")
                    ).hexdigest()
                    route_reference = hashlib.sha256(
                        (wallet + ":" + fingerprint + ":" + PRICE_ID + ":routing").encode("utf-8")
                    ).hexdigest()[:32]
                    invoice = billing.create_invoice(
                        customer_ref=_customer_ref(wallet),
                        plan_code=PLAN_CODE,
                        price_id=PRICE_ID,
                        client_idempotency_key=idem,
                        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=15),
                        actor=ACTOR,
                        client_request_fingerprint=fingerprint,
                        settlement_routing_mode="unique_destination",
                        settlement_routing_reference=route_reference,
                    )
                finally:
                    store.close()
                self._send(200, {"data": {
                    "invoice_id": invoice.invoice_id,
                    "network": invoice.network,
                    "asset": invoice.asset_code,
                    "asset_kind": invoice.asset_kind,
                    "amount_atomic": str(invoice.amount_atomic),
                    "asset_decimals": invoice.asset_decimals,
                    "amount_display": str(invoice.amount_atomic / (10 ** invoice.asset_decimals)),
                    "recipient": invoice.destination,
                    "chain_id": hex(EVM_CHAIN_ID),
                    "routing_reference": invoice.routing_reference,
                    "expires_at": invoice.expires_at.isoformat().replace("+00:00", "Z"),
                }})
                return

            tx_hash = str(payload.get("tx_hash", "")).strip()
            invoice_id = str(payload.get("invoice_id", "")).strip()
            if not invoice_id or not tx_hash:
                raise EnrollmentValidationError("invoice_id and tx_hash are required")
            store, billing = _services()
            try:
                snapshot = billing.get_invoice(invoice_id=invoice_id, customer_ref=_customer_ref(wallet))
                invoice = PaymentInvoice(
                    invoice_id=invoice_id,
                    customer_ref=_customer_ref(wallet),
                    plan_code=snapshot["plan_code"],
                    asset_code=snapshot["asset_code"],
                    network=snapshot["network"],
                    asset_kind=snapshot["asset_kind"],
                    amount_atomic=int(snapshot["amount_atomic"]),
                    asset_decimals=int(snapshot["asset_decimals"]),
                    destination=snapshot["destination"],
                    expires_at=snapshot["expires_at"],
                    asset_contract=snapshot.get("asset_contract"),
                    routing_mode=snapshot["routing_mode"],
                    routing_reference=snapshot.get("routing_reference"),
                )
                if invoice.network != EVM_NETWORK or invoice.asset_kind != "native" or invoice.asset_code != "ETH":
                    raise EnrollmentValidationError("invoice is not a supported native ETH enrollment invoice")
                if invoice.expires_at <= __import__("datetime").datetime.now(__import__("datetime").timezone.utc):
                    raise ConflictError("invoice has expired")
                adapter = EvmJsonRpcAdapter(EVM_RPC_URL, network=EVM_NETWORK, expected_chain_id=EVM_CHAIN_ID)
                tx = adapter._rpc.call("eth_getTransactionByHash", [tx_hash])
                if not isinstance(tx, dict) or not isinstance(tx.get("from"), str):
                    raise ConflictError("transaction sender could not be verified")
                if tx["from"].lower() != wallet.lower():
                    raise ConflictError("transaction sender does not match connected wallet")
                observation = adapter.verify(tx_hash, invoice)
                settlement = billing.settle_observation(invoice_id=invoice_id, observation=observation, actor=ACTOR)
                if settlement["status"] not in {"paid", "overpaid"}:
                    raise ConflictError("payment is not finally settled")
                identity = _public_identity(store, billing, invoice_id, wallet, registration)
            finally:
                store.close()
            verify_url = f"{VERIFY_BASE_URL}/verify.html?id={identity['nothing_id']}"
            self._send(200, {"data": {
                "identity": identity,
                "payment": settlement,
                "verify_url": verify_url,
                "hologram": {
                    "type": "web-badge",
                    "nothing_id": identity["nothing_id"],
                    "not_an_nft": True,
                },
            }})
        except EnrollmentValidationError as exc:
            self._error(400, "INVALID_REQUEST", str(exc))
        except (ConflictError, NotFoundError) as exc:
            self._error(409 if isinstance(exc, ConflictError) else 404, "ENROLLMENT_CONFLICT", str(exc))
        except RuntimeError as exc:
            self._error(503, "ENROLLMENT_DISABLED", str(exc))
        except StoreError:
            self._error(503, "ENROLLMENT_UNAVAILABLE", "Enrollment storage is temporarily unavailable.")
        except Exception:
            self._error(503, "ENROLLMENT_UNAVAILABLE", "The enrollment service could not complete the request.")


def build_server() -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, PORT), Handler)


if __name__ == "__main__":
    server = build_server()
    print(f"NOTHING enrollment gateway listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
