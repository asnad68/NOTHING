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
import secrets
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
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
    build_organization_controlled_identity,
)
from src.nothing_postgres import PostgreSQLNothingStore
from src.nothing_payments import PaymentInvoice
from src.nothing_store import ConflictError, NotFoundError, StoreError
from src.nothing_identity_control import (
    IdentityControlError,
    verify_google_id_token,
    verify_google_workspace_principal,
    build_domain_challenge,
    build_siwe_message,
    domain_from_url,
    evaluate_official_claim,
    issue_domain_challenge_token,
    normalize_domain,
    verify_domain_challenge_token,
    verify_domain_txt,
    verify_siwe_signature,
)

HOST = os.getenv("NOTHING_ENROLLMENT_API_HOST", "0.0.0.0")
PORT = int(os.getenv("NOTHING_ENROLLMENT_API_PORT", os.getenv("PORT", "8090")))
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
IDENTITY_CONTROL_ENABLED = os.getenv("NOTHING_IDENTITY_CONTROL_ENABLED", "false").lower() in {"1", "true", "yes"}
WALLET_AUTH_ENABLED = os.getenv("NOTHING_WALLET_AUTH_ENABLED", "false").lower() in {"1", "true", "yes"}
WALLET_CHALLENGE_TTL_SECONDS = max(120, min(900, int(os.getenv("NOTHING_WALLET_CHALLENGE_TTL_SECONDS", "300"))))
PUBLIC_SITE_ORIGIN = os.getenv("NOTHING_PUBLIC_SITE_ORIGIN", "").strip().rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("NOTHING_GOOGLE_CLIENT_ID", "").strip()
OFFICIAL_REGISTRATION_REQUIRED = os.getenv("NOTHING_OFFICIAL_REGISTRATION_REQUIRED", "false").lower() in {"1", "true", "yes"}


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


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_identity_control() -> None:
    if not IDENTITY_CONTROL_ENABLED:
        raise RuntimeError("identity-control registration is not activated on this deployment")


def _require_wallet_auth() -> None:
    _require_identity_control()
    if not WALLET_AUTH_ENABLED:
        raise RuntimeError("wallet authentication is not activated on this deployment")


def _expected_public_origin() -> tuple[str, str]:
    if not PUBLIC_SITE_ORIGIN:
        raise RuntimeError("NOTHING_PUBLIC_SITE_ORIGIN is not configured")
    origin = PUBLIC_SITE_ORIGIN
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("NOTHING_PUBLIC_SITE_ORIGIN must be an absolute HTTPS URL")
    return origin, domain_from_url(origin)


def _verify_domain_control(domain: str, token: str) -> bool:
    if len(DOMAIN_CHALLENGE_SECRET) < 32:
        raise RuntimeError("domain verification is not configured")
    if not verify_domain_challenge_token(token, domain, DOMAIN_CHALLENGE_SECRET):
        return False
    challenge = build_domain_challenge(domain, challenge=token)
    try:
        import dns.resolver
    except ImportError as exc:
        raise RuntimeError("dnspython is required for DNS domain verification") from exc
    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = 5.0
    resolver.lifetime = 5.0
    return verify_domain_txt(challenge, resolver)


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()


def _registration_authorization(
    store: PostgreSQLNothingStore,
    challenge_id: str,
    wallet: str,
    registration: RegistrationDraft,
) -> dict[str, Any]:
    challenge = store.get_auth_challenge(challenge_id)
    if challenge["purpose"] not in {"wallet_siwe", "google_oidc"}:
        raise ConflictError("authentication challenge purpose is not valid for official registration")
    if challenge["authorization_status"] != "AUTHORIZED":
        raise ConflictError("official registration authorization is not active")
    if challenge["registration_consumed_at"]:
        raise ConflictError("official registration authorization has already been used")
    purpose = str(challenge["purpose"])
    if purpose == "wallet_siwe" and str(challenge["wallet_address"] or "").lower() != wallet.lower():
        raise ConflictError("official wallet authorization is bound to another wallet")
    if purpose not in {"wallet_siwe", "google_oidc"}:
        raise ConflictError("official registration authorization uses an unsupported method")
    if datetime.fromisoformat(str(challenge["expires_at"]).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ConflictError("official registration authorization has expired")
    authorization = challenge.get("authorization") or {}
    if authorization.get("registration_digest") != registration.digest():
        raise ConflictError("official registration authorization does not match this registration")
    domain = normalize_domain(str(authorization.get("domain", "")))
    registration_domains = {normalize_domain(value) for value in registration.domains}
    if domain not in registration_domains:
        raise ConflictError("registered domains do not include the authorized organization domain")
    return authorization


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
    *,
    authorization_challenge_id: str | None = None,
) -> dict[str, Any]:
    snapshot = billing.get_invoice(invoice_id=invoice_id, customer_ref=_customer_ref(wallet))
    if snapshot["status"] not in {"paid", "overpaid"} or not snapshot.get("entitlement"):
        raise ConflictError("payment has not activated the registration entitlement")

    authorization = None
    official = authorization_challenge_id is not None
    if OFFICIAL_REGISTRATION_REQUIRED and not official:
        raise ConflictError("official organization authorization is required for registration")
    if official:
        authorization = _registration_authorization(
            store,
            str(authorization_challenge_id),
            wallet,
            draft,
        )

    digest = draft.digest()
    for candidate in candidate_nothing_ids(digest, 64):
        try:
            store.get_identity(candidate)
            continue
        except NotFoundError:
            if official and authorization is not None:
                identity = build_organization_controlled_identity(
                    draft,
                    candidate,
                    authorization_method=str(authorization.get("authorization_method", "wallet_siwe+dns_txt")),
                    controlled_domain=str(authorization["domain"]),
                )
            else:
                identity = build_self_claimed_identity(draft, candidate)

            bundle = {"identities": [identity], "evidence": [], "verification_events": []}
            raw = json.dumps(
                bundle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if official:
                consumed = store.consume_auth_authorization(
                    str(authorization_challenge_id),
                    registration_digest=digest,
                    wallet_address=wallet,
                    now=_iso_z(datetime.now(timezone.utc)),
                    actor=ACTOR,
                )
                if not consumed:
                    raise ConflictError("official registration authorization could not be consumed")
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

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin or not ALLOWED_ORIGIN:
            return True
        return origin == ALLOWED_ORIGIN

    def _require_allowed_origin(self) -> bool:
        if self._origin_allowed():
            return True
        self._error(403, "ORIGIN_NOT_ALLOWED", "Browser origin is not authorized for this enrollment service.")
        return False

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._send(403, {"error": {"code": "ORIGIN_NOT_ALLOWED", "detail": "Browser origin is not authorized for this enrollment service.", "status": 403}})
            return
        self._send(204, {})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._limit():
            return
        if path in {"/healthz", "/readyz"}:
            try:
                store = PostgreSQLNothingStore.from_environment()
                try:
                    healthy = bool(store.health())
                finally:
                    store.close()
            except Exception:
                healthy = False
            if path == "/healthz":
                self._send(200 if healthy else 503, {"status": "ok" if healthy else "degraded", "enabled": ENABLED, "database": healthy})
            else:
                self._send(200 if healthy else 503, {"status": "ready" if healthy else "not_ready", "database": healthy})
            return
        if path == "/v1/organization/domain-challenge":
            if len(DOMAIN_CHALLENGE_SECRET) < 32:
                self._error(503, "DOMAIN_VERIFICATION_UNAVAILABLE", "Domain verification is not configured on this deployment.")
                return
            try:
                values = parse_qs(parsed.query, keep_blank_values=False)
                domain = normalize_domain(values.get("domain", [""])[0])
                challenge = issue_domain_challenge_token(
                    domain,
                    DOMAIN_CHALLENGE_SECRET,
                    ttl_seconds=DOMAIN_CHALLENGE_TTL_SECONDS,
                )
                self._send(200, {"data": {
                    "domain": challenge.domain,
                    "record_name": challenge.record_name,
                    "record_type": "TXT",
                    "record_value": challenge.record_value,
                    "challenge": challenge.challenge,
                    "challenge_digest": challenge.challenge_digest,
                }})
            except IdentityControlError as exc:
                self._error(400, "INVALID_DOMAIN", str(exc))
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
        if not self._require_allowed_origin():
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._limit():
            return
        if path == "/v1/auth/wallet/challenge":
            try:
                _require_wallet_auth()
                payload = _safe_json(_read_body(self))
                wallet = validate_wallet_address(payload.get("wallet_address", ""))
                public_origin, public_domain = _expected_public_origin()
                issued = datetime.now(timezone.utc).replace(microsecond=0)
                expires = issued + timedelta(seconds=WALLET_CHALLENGE_TTL_SECONDS)
                nonce = secrets.token_hex(24)
                message = build_siwe_message(
                    domain=public_domain,
                    address=wallet,
                    uri=public_origin,
                    chain_id=EVM_CHAIN_ID,
                    nonce=nonce,
                    issued_at=_iso_z(issued),
                    expiration_time=_iso_z(expires),
                )
                challenge_id = str(uuid.uuid4())
                store = PostgreSQLNothingStore.from_environment()
                try:
                    store.create_auth_challenge(
                        challenge_id=challenge_id,
                        purpose="wallet_siwe",
                        nonce=nonce,
                        wallet_address=wallet,
                        domain=public_domain,
                        uri=public_origin,
                        chain_id=EVM_CHAIN_ID,
                        message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
                        issued_at=_iso_z(issued),
                        expires_at=_iso_z(expires),
                        actor=ACTOR,
                    )
                finally:
                    store.close()
                self._send(200, {"data": {
                    "challenge_id": challenge_id,
                    "message": message,
                    "domain": public_domain,
                    "uri": public_origin,
                    "chain_id": EVM_CHAIN_ID,
                    "nonce": nonce,
                    "expires_at": _iso_z(expires),
                }})
            except EnrollmentValidationError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except RuntimeError as exc:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", str(exc))
            except StoreError:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "Authentication challenge storage is unavailable.")
            except Exception:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "The wallet authentication challenge could not be issued.")
            return

        if path == "/v1/organization/google-authorize":
            store = None
            try:
                _require_identity_control()
                if not GOOGLE_CLIENT_ID:
                    raise RuntimeError("Google authentication is not configured on this deployment")
                payload = _safe_json(_read_body(self))
                id_token = str(payload.get("id_token", "")).strip()
                domain_token = str(payload.get("domain_challenge", "")).strip()
                registration = RegistrationDraft.from_mapping(payload.get("registration", {}))
                requested_domain = normalize_domain(payload.get("requested_domain", ""))
                if requested_domain not in {normalize_domain(value) for value in registration.domains}:
                    raise EnrollmentValidationError("requested_domain must be one of the submitted official domains")
                if not id_token or not domain_token:
                    raise EnrollmentValidationError("id_token and domain_challenge are required")
                claims = verify_google_id_token(
                    id_token,
                    client_id=GOOGLE_CLIENT_ID,
                    expected_domain=requested_domain,
                )
                principal = verify_google_workspace_principal(
                    subject=str(claims["sub"]),
                    email=str(claims["email"]),
                    email_verified=bool(claims["email_verified"]),
                    hosted_domain=claims.get("hosted_domain"),
                    expected_domain=requested_domain,
                )
                controlled = _verify_domain_control(requested_domain, domain_token)
                decision = evaluate_official_claim(
                    brand_name=registration.name,
                    requested_domain=requested_domain,
                    domain_controlled=controlled,
                    principal=principal,
                    website_url=registration.website or None,
                )
                if not decision.allowed:
                    self._send(403, {"data": {
                        "authorized": False,
                        "decision": {
                            "state": decision.state,
                            "reason": decision.reason,
                            "domain": decision.domain,
                            "authentication_method": decision.authentication_method,
                        },
                    }})
                    return

                public_origin, _ = _expected_public_origin()
                now = datetime.now(timezone.utc)
                exp = int(claims["exp"])
                token_expiry = datetime.fromtimestamp(exp, timezone.utc)
                expires = min(now + timedelta(seconds=WALLET_CHALLENGE_TTL_SECONDS), token_expiry)
                if expires <= now:
                    raise ConflictError("Google ID token is expired")
                challenge_id = str(uuid.uuid4())
                nonce = secrets.token_hex(24)
                binding = f"{claims['sub']}|{registration.digest()}|{nonce}"
                message_hash = hashlib.sha256(binding.encode("utf-8")).hexdigest()
                authorization = {
                    "registration_digest": registration.digest(),
                    "brand_name": registration.name,
                    "domain": requested_domain,
                    "website": registration.website,
                    "decision_state": decision.state,
                    "principal_method": principal.method,
                    "principal_id_sha256": _hash_identifier(principal.subject),
                    "authorization_method": "google_oidc+dns_txt",
                    "authorized_at": _iso_z(now),
                }
                store = PostgreSQLNothingStore.from_environment()
                try:
                    store.create_auth_challenge(
                        challenge_id=challenge_id,
                        purpose="google_oidc",
                        nonce=nonce,
                        wallet_address=None,
                        domain=requested_domain,
                        uri=public_origin,
                        chain_id=EVM_CHAIN_ID,
                        message_sha256=message_hash,
                        issued_at=_iso_z(now),
                        expires_at=_iso_z(expires),
                        actor=ACTOR,
                    )
                    saved = store.authorize_google_challenge(
                        challenge_id,
                        nonce=nonce,
                        message_sha256=message_hash,
                        authorization=authorization,
                        now=_iso_z(now),
                        actor=ACTOR,
                    )
                finally:
                    store.close()
                if not saved:
                    raise ConflictError("Google authorization could not be persisted")
                self._send(200, {"data": {
                    "authorized": True,
                    "authorization_challenge_id": challenge_id,
                    "decision": {
                        "state": decision.state,
                        "reason": decision.reason,
                        "domain": decision.domain,
                        "authentication_method": decision.authentication_method,
                    },
                    "expires_at": _iso_z(expires),
                }})
            except IdentityControlError as exc:
                self._error(400, "IDENTITY_AUTHENTICATION_FAILED", str(exc))
            except EnrollmentValidationError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except (ConflictError, NotFoundError) as exc:
                self._error(409 if isinstance(exc, ConflictError) else 404, "IDENTITY_AUTHORIZATION_FAILED", str(exc))
            except RuntimeError as exc:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", str(exc))
            except StoreError:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "Identity authorization storage is unavailable.")
            except Exception:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "The Google organization authorization could not be completed.")
            finally:
                if store is not None:
                    store.close()
            return

        if path == "/v1/organization/wallet-authorize":
            store = None
            try:
                _require_wallet_auth()
                payload = _safe_json(_read_body(self))
                challenge_id = str(payload.get("challenge_id", "")).strip()
                message = str(payload.get("message", ""))
                signature = str(payload.get("signature", "")).strip()
                domain_token = str(payload.get("domain_challenge", "")).strip()
                registration = RegistrationDraft.from_mapping(payload.get("registration", {}))
                requested_domain = normalize_domain(payload.get("requested_domain", ""))
                if requested_domain not in {normalize_domain(value) for value in registration.domains}:
                    raise EnrollmentValidationError("requested_domain must be one of the submitted official domains")
                if not challenge_id or not message or not signature or not domain_token:
                    raise EnrollmentValidationError("challenge_id, message, signature and domain_challenge are required")

                store = PostgreSQLNothingStore.from_environment()
                challenge = store.get_auth_challenge(challenge_id)
                if challenge["purpose"] != "wallet_siwe" or challenge["authorization_status"] != "PENDING" or challenge["consumed_at"]:
                    raise ConflictError("authentication challenge is not available")
                now = datetime.now(timezone.utc)
                expires = datetime.fromisoformat(str(challenge["expires_at"]).replace("Z", "+00:00"))
                if expires <= now:
                    raise ConflictError("authentication challenge has expired")
                message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
                if message_hash != challenge["message_sha256"]:
                    raise IdentityControlError("SIWE message does not match the server-issued challenge")
                principal = verify_siwe_signature(
                    message=message,
                    signature=signature,
                    expected_domain=str(challenge["domain"]),
                    expected_uri=str(challenge["uri"]),
                    expected_chain_id=int(challenge["chain_id"]),
                    expected_nonce=str(challenge["nonce"]),
                    now=now,
                )
                if str(principal.wallet_address or "").lower() != str(challenge["wallet_address"] or "").lower():
                    raise IdentityControlError("wallet signature does not match the challenged wallet")

                controlled = _verify_domain_control(requested_domain, domain_token)
                decision = evaluate_official_claim(
                    brand_name=registration.name,
                    requested_domain=requested_domain,
                    domain_controlled=controlled,
                    principal=principal,
                    website_url=registration.website or None,
                )
                authorization_payload = {
                    "registration_digest": registration.digest(),
                    "brand_name": registration.name,
                    "domain": requested_domain,
                    "website": registration.website,
                    "decision_state": decision.state,
                    "principal_method": principal.method,
                    "principal_id_sha256": _hash_identifier(principal.subject),
                    "authorization_method": "wallet_siwe+dns_txt",
                    "authorized_at": _iso_z(now),
                }
                if decision.allowed:
                    saved = store.authorize_auth_challenge(
                        challenge_id,
                        nonce=str(challenge["nonce"]),
                        message_sha256=message_hash,
                        authorization=authorization_payload,
                        now=_iso_z(now),
                        actor=ACTOR,
                    )
                    if not saved:
                        raise ConflictError("official registration authorization was already consumed")
                    self._send(200, {"data": {
                        "authorized": True,
                        "challenge_id": challenge_id,
                        "decision": {
                            "state": decision.state,
                            "reason": decision.reason,
                            "domain": decision.domain,
                            "authentication_method": decision.authentication_method,
                        },
                        "expires_at": challenge["expires_at"],
                    }})
                else:
                    store.reject_auth_challenge(
                        challenge_id,
                        nonce=str(challenge["nonce"]),
                        message_sha256=message_hash,
                        decision=authorization_payload,
                        now=_iso_z(now),
                        actor=ACTOR,
                    )
                    self._send(403, {"data": {
                        "authorized": False,
                        "challenge_id": challenge_id,
                        "decision": {
                            "state": decision.state,
                            "reason": decision.reason,
                            "domain": decision.domain,
                            "authentication_method": decision.authentication_method,
                        },
                    }})
            except IdentityControlError as exc:
                self._error(400, "IDENTITY_AUTHENTICATION_FAILED", str(exc))
            except EnrollmentValidationError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except (ConflictError, NotFoundError) as exc:
                self._error(409 if isinstance(exc, ConflictError) else 404, "IDENTITY_AUTHORIZATION_FAILED", str(exc))
            except RuntimeError as exc:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", str(exc))
            except StoreError:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "Identity authorization storage is unavailable.")
            except Exception:
                self._error(503, "IDENTITY_CONTROL_UNAVAILABLE", "The official organization authorization could not be completed.")
            finally:
                if store is not None:
                    store.close()
            return

        if path == "/v1/organization/domain-verify":
            try:
                payload = _safe_json(_read_body(self))
                domain = normalize_domain(payload.get("domain", ""))
                token = str(payload.get("challenge", "")).strip()
                if len(DOMAIN_CHALLENGE_SECRET) < 32:
                    self._error(503, "DOMAIN_VERIFICATION_UNAVAILABLE", "Domain verification is not configured on this deployment.")
                    return
                if not verify_domain_challenge_token(token, domain, DOMAIN_CHALLENGE_SECRET):
                    self._send(400, {"data": {
                        "domain_controlled": False,
                        "reason": "The domain challenge is invalid or expired.",
                        "domain": domain,
                    }})
                    return
                controlled = _verify_domain_control(domain, token)
                challenge = build_domain_challenge(domain, challenge=token)
                self._send(200, {"data": {
                    "domain_controlled": controlled,
                    "domain": domain,
                    "record_name": challenge.record_name,
                    "method": "dns_txt",
                    "challenge_digest": challenge.challenge_digest,
                }})
            except IdentityControlError as exc:
                self._error(400, "INVALID_DOMAIN", str(exc))
            except EnrollmentValidationError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except RuntimeError as exc:
                self._error(503, "DOMAIN_VERIFICATION_UNAVAILABLE", str(exc))
            except Exception:
                self._error(503, "DOMAIN_VERIFICATION_UNAVAILABLE", "The domain verification service could not complete the DNS check.")
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

            authorization_challenge_id = str(payload.get("authorization_challenge_id", "")).strip() or None
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
                identity = _public_identity(
                    store,
                    billing,
                    invoice_id,
                    wallet,
                    registration,
                    authorization_challenge_id=authorization_challenge_id,
                )
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
