"""PostgreSQL-backed subscription settlement orchestration.

Only trusted chain adapters should call settle_observation(). The method never
accepts payer-supplied finality as authority; it persists an adapter observation
and atomically allocates the payment and activates an entitlement.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Mapping

from src.nothing_payments import (
    PaymentInvoice,
    PaymentObservation,
    PaymentValidationError,
    classify_invoice,
    validate_observation,
)
from src.nothing_postgres import (
    PostgreSQLNothingStore,
    _canonical_json,
    _utc_now,
)
from src.nothing_store import ConflictError, NotFoundError, StoreError


@dataclass(frozen=True)
class ConfirmationPolicy:
    required_confirmations: int
    require_finality: bool = True

    def __post_init__(self) -> None:
        if self.required_confirmations < 0:
            raise PaymentValidationError(
                "required_confirmations cannot be negative"
            )


def _retry_billing_transaction(function):
    @wraps(function)
    def wrapper(self: "SubscriptionBillingService", *args: Any, **kwargs: Any) -> Any:
        retries = self.store._serialization_retries
        for attempt in range(retries + 1):
            try:
                return function(self, *args, **kwargs)
            except (
                self.store._SerializationFailure,
                self.store._DeadlockDetected,
                self.store._UniqueViolation,
            ) as exc:
                if attempt >= retries:
                    raise StoreError(
                        "billing transaction could not complete after retries"
                    ) from exc
                delay = self.store._retry_backoff_seconds * (2 ** attempt)
                if delay:
                    time.sleep(delay)
        raise AssertionError("unreachable")
    return wrapper


class SubscriptionBillingService:
    """Atomic invoice -> payment allocation -> entitlement boundary."""

    def __init__(
        self,
        store: PostgreSQLNothingStore,
        *,
        policies: Mapping[str, ConfirmationPolicy],
    ) -> None:
        self.store = store
        self.policies = dict(policies)

    def policy_for(self, network: str) -> ConfirmationPolicy:
        try:
            return self.policies[network]
        except KeyError as exc:
            raise PaymentValidationError(
                f"no settlement confirmation policy configured for network {network}"
            ) from exc

    @_retry_billing_transaction
    def create_invoice(
        self,
        *,
        customer_ref: str,
        plan_code: str,
        price_id: str,
        client_idempotency_key: str,
        expires_at: datetime,
        actor: str,
        settlement_destination: str | None = None,
        settlement_routing_mode: str | None = None,
        settlement_routing_reference: str | None = None,
    ) -> PaymentInvoice:
        if not customer_ref.strip() or not plan_code.strip():
            raise PaymentValidationError(
                "customer_ref and plan_code are required"
            )
        if not client_idempotency_key.strip():
            raise PaymentValidationError(
                "client_idempotency_key is required"
            )
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise PaymentValidationError("expires_at must include a timezone")

        expires_utc = expires_at.astimezone(timezone.utc)

        if settlement_routing_mode not in {
            None, "unique_destination", "xrp_destination_tag", "manual_shared"
        }:
            raise PaymentValidationError("invalid settlement_routing_mode")

        if settlement_routing_mode == "unique_destination":
            if not settlement_destination:
                raise PaymentValidationError(
                    "unique_destination requires an invoice-specific destination"
                )
        if settlement_routing_mode == "xrp_destination_tag":
            if settlement_routing_reference is not None:
                try:
                    tag_value = int(settlement_routing_reference)
                except (TypeError, ValueError) as exc:
                    raise PaymentValidationError(
                        "XRP destination tag must be an integer"
                    ) from exc
                if not 1 <= tag_value <= (2**32 - 1):
                    raise PaymentValidationError(
                        "XRP destination tag must fit uint32 and be non-zero"
                    )

        with self.store._transaction(retryable=True) as connection:
            self.store._lock_keys(
                connection,
                [f"invoice-request:{customer_ref}:{client_idempotency_key}"],
            )
            existing = connection.execute(
                """
                SELECT *
                FROM billing_invoices
                WHERE customer_ref = %s
                  AND client_idempotency_key = %s
                FOR UPDATE
                """,
                (customer_ref, client_idempotency_key),
            ).fetchone()
            if existing is not None:
                quote = json.loads(existing["quote_json"])
                if (
                    quote.get("plan_code") != plan_code
                    or quote.get("price_id") != price_id
                    or (
                        settlement_destination is not None
                        and quote.get("settlement_destination") != settlement_destination
                    )
                    or (
                        settlement_routing_mode is not None
                        and quote.get("routing_mode") != settlement_routing_mode
                    )
                    or (
                        settlement_routing_reference is not None
                        and quote.get("routing_reference") != settlement_routing_reference
                    )
                ):
                    raise ConflictError(
                        "invoice idempotency key was already used for a different quote"
                    )
                return PaymentInvoice(
                    invoice_id=str(existing["invoice_id"]),
                    customer_ref=existing["customer_ref"],
                    plan_code=existing["plan_code"],
                    asset_code=existing["asset_code"],
                    network=existing["network"],
                    asset_kind=existing["asset_kind"],
                    amount_atomic=int(existing["amount_atomic"]),
                    asset_decimals=int(existing["asset_decimals"]),
                    destination=existing["destination"],
                    expires_at=existing["expires_at"],
                    asset_contract=existing["asset_contract"],
                    routing_mode=existing["routing_mode"],
                    routing_reference=existing["routing_reference"],
                )

            price = connection.execute(
                """
                SELECT p.plan_code, p.status AS plan_status,
                       bp.price_id, bp.asset_code, bp.network,
                       bp.asset_kind, bp.asset_contract,
                       bp.amount_atomic, bp.asset_decimals,
                       bp.destination, bp.routing_mode
                FROM billing_prices bp
                JOIN subscription_plans p ON p.plan_code = bp.plan_code
                WHERE bp.price_id = %s
                  AND bp.plan_code = %s
                  AND bp.active = TRUE
                FOR SHARE
                """,
                (price_id, plan_code),
            ).fetchone()
            if price is None or price["plan_status"] != "active":
                raise NotFoundError(
                    f"active billing price {price_id} for plan {plan_code} does not exist"
                )

            invoice_id = uuid.uuid4()
            effective_routing_mode = (
                settlement_routing_mode or price["routing_mode"]
            )
            effective_destination = (
                settlement_destination or price["destination"]
            )
            effective_routing_reference = settlement_routing_reference

            if effective_routing_mode == "xrp_destination_tag":
                if price["network"] != "xrpl" or price["asset_code"] != "XRP":
                    raise PaymentValidationError(
                        "xrp_destination_tag is valid only for XRPL XRP prices"
                    )
                if effective_routing_reference is None:
                    route_lock = (
                        f"xrp-route:{price['network']}:{effective_destination}"
                    )
                    self.store._lock_keys(connection, [route_lock])
                    for _ in range(20):
                        candidate = secrets.randbelow(2**32 - 1) + 1
                        used = connection.execute(
                            """
                            SELECT 1
                            FROM billing_invoices
                            WHERE network = %s
                              AND destination = %s
                              AND routing_mode = 'xrp_destination_tag'
                              AND routing_reference = %s
                            """,
                            (
                                price["network"],
                                effective_destination,
                                str(candidate),
                            ),
                        ).fetchone()
                        if used is None:
                            effective_routing_reference = str(candidate)
                            break
                    if effective_routing_reference is None:
                        raise StoreError(
                            "unable to allocate an unused XRP destination tag"
                        )

            quote = {
                "price_id": price_id,
                "plan_code": plan_code,
                "asset_code": price["asset_code"],
                "network": price["network"],
                "asset_kind": price["asset_kind"],
                "asset_contract": price["asset_contract"],
                "amount_atomic": str(price["amount_atomic"]),
                "asset_decimals": int(price["asset_decimals"]),
                "destination": effective_destination,
                "expires_at": expires_utc.isoformat(),
                "routing_mode": effective_routing_mode,
                "routing_reference": effective_routing_reference,
                "settlement_destination": effective_destination,
            }

            connection.execute(
                """
                INSERT INTO billing_invoices(
                    invoice_id, customer_ref, plan_code,
                    asset_code, network, asset_kind, asset_contract,
                    amount_atomic, asset_decimals, destination,
                    routing_mode, routing_reference,
                    status, client_idempotency_key, expires_at,
                    quote_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'open', %s, %s, %s
                )
                """,
                (
                    invoice_id,
                    customer_ref,
                    plan_code,
                    price["asset_code"],
                    price["network"],
                    price["asset_kind"],
                    price["asset_contract"],
                    price["amount_atomic"],
                    price["asset_decimals"],
                    effective_destination,
                    effective_routing_mode,
                    effective_routing_reference,
                    client_idempotency_key,
                    expires_utc,
                    _canonical_json(quote),
                ),
            )

            self._billing_audit(
                connection,
                actor,
                "INVOICE_CREATED",
                "invoice",
                str(invoice_id),
                quote,
            )

            return PaymentInvoice(
                invoice_id=str(invoice_id),
                customer_ref=customer_ref,
                plan_code=plan_code,
                asset_code=price["asset_code"],
                network=price["network"],
                asset_kind=price["asset_kind"],
                amount_atomic=int(price["amount_atomic"]),
                asset_decimals=int(price["asset_decimals"]),
                destination=effective_destination,
                expires_at=expires_utc,
                asset_contract=price["asset_contract"],
                routing_mode=effective_routing_mode,
                routing_reference=effective_routing_reference,
            )

    def _upsert_payment_event(
        self,
        connection: Any,
        observation: PaymentObservation,
    ) -> uuid.UUID:
        payment = connection.execute(
            """
            SELECT *
            FROM payment_events
            WHERE network = %s
              AND chain_event_key = %s
            FOR UPDATE
            """,
            (observation.network, observation.chain_event_key),
        ).fetchone()

        if payment is None:
            payment_event_id = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO payment_events(
                    payment_event_id, network, asset_code, asset_kind,
                    asset_contract, destination, amount_atomic,
                    chain_event_key, tx_hash, block_reference,
                    confirmation_count, finality_status, success,
                    source, first_observed_at, last_observed_at,
                    routing_mode, routing_reference
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    payment_event_id,
                    observation.network,
                    observation.asset_code,
                    observation.asset_kind,
                    observation.asset_contract,
                    observation.destination,
                    observation.amount_atomic,
                    observation.chain_event_key,
                    observation.tx_hash,
                    observation.block_reference,
                    observation.confirmation_count,
                    observation.finality_status,
                    observation.success,
                    observation.source,
                    observation.observed_at,
                    observation.observed_at,
                    observation.routing_mode,
                    observation.routing_reference,
                ),
            )
            return payment_event_id

        immutable_fields = (
            ("asset_code", payment["asset_code"], observation.asset_code),
            ("asset_kind", payment["asset_kind"], observation.asset_kind),
            ("asset_contract", payment["asset_contract"], observation.asset_contract),
            ("destination", payment["destination"], observation.destination),
            ("amount_atomic", int(payment["amount_atomic"]), observation.amount_atomic),
            ("tx_hash", payment["tx_hash"], observation.tx_hash),
            ("routing_mode", payment["routing_mode"], observation.routing_mode),
            ("routing_reference", payment["routing_reference"], observation.routing_reference),
        )
        for field, old, new in immutable_fields:
            if old != new:
                raise ConflictError(
                    f"payment event identity changed for {field}"
                )

        finality_rank = {
            "pending": 0,
            "confirmed": 1,
            "final": 2,
        }
        stored_finality = payment["finality_status"]
        observed_finality = observation.finality_status

        if stored_finality == "final":
            if observed_finality == "orphaned":
                raise ConflictError(
                    "a finalized payment cannot be silently changed to orphaned"
                )
            effective_finality = "final"
        elif stored_finality == "orphaned":
            if observed_finality != "orphaned":
                raise ConflictError(
                    "an orphaned payment requires explicit reconciliation before revival"
                )
            effective_finality = "orphaned"
        elif observed_finality == "orphaned":
            effective_finality = "orphaned"
        else:
            effective_finality = (
                observed_finality
                if finality_rank[observed_finality] >= finality_rank[stored_finality]
                else stored_finality
            )

        effective_success = (
            False
            if effective_finality == "orphaned"
            else bool(payment["success"] or observation.success)
        )

        connection.execute(
            """
            UPDATE payment_events
            SET block_reference = COALESCE(%s, block_reference),
                confirmation_count = GREATEST(
                    confirmation_count, %s
                ),
                finality_status = %s,
                success = %s,
                source = %s,
                last_observed_at = %s
            WHERE payment_event_id = %s
            """,
            (
                observation.block_reference,
                observation.confirmation_count,
                effective_finality,
                effective_success,
                observation.source,
                observation.observed_at,
                payment["payment_event_id"],
            ),
        )
        return payment["payment_event_id"]

    @_retry_billing_transaction
    def settle_observation(
        self,
        *,
        invoice_id: str,
        observation: PaymentObservation,
        actor: str,
    ) -> dict[str, Any]:
        validate_observation(observation)
        if not actor.strip():
            raise PaymentValidationError("actor is required")

        policy = self.policy_for(observation.network)

        with self.store._transaction(retryable=True) as connection:
            self.store._lock_keys(
                connection,
                [
                    f"payment:{observation.network}:"
                    f"{observation.chain_event_key}"
                ],
            )

            payment_event_id = self._upsert_payment_event(
                connection,
                observation,
            )
            invoice = connection.execute(
                """
                SELECT *
                FROM billing_invoices
                WHERE invoice_id = %s
                FOR UPDATE
                """,
                (invoice_id,),
            ).fetchone()
            if invoice is None:
                raise NotFoundError(f"invoice {invoice_id} does not exist")

            invoice_model = PaymentInvoice(
                invoice_id=str(invoice["invoice_id"]),
                customer_ref=invoice["customer_ref"],
                plan_code=invoice["plan_code"],
                asset_code=invoice["asset_code"],
                network=invoice["network"],
                asset_kind=invoice["asset_kind"],
                amount_atomic=int(invoice["amount_atomic"]),
                asset_decimals=int(invoice["asset_decimals"]),
                destination=invoice["destination"],
                expires_at=invoice["expires_at"],
                asset_contract=invoice["asset_contract"],
                routing_mode=invoice["routing_mode"],
                routing_reference=invoice["routing_reference"],
            )

            existing_allocation = connection.execute(
                """
                SELECT invoice_id
                FROM payment_allocations
                WHERE payment_event_id = %s
                """,
                (payment_event_id,),
            ).fetchone()
            if existing_allocation is not None:
                if str(existing_allocation["invoice_id"]) != invoice_id:
                    raise ConflictError(
                        "payment event is already allocated to another invoice"
                    )
                return self._settlement_snapshot(connection, invoice_id)

            now = datetime.now(timezone.utc)
            if now > invoice_model.expires_at.astimezone(timezone.utc):
                connection.execute(
                    """
                    UPDATE billing_invoices
                    SET status = 'review_required'
                    WHERE invoice_id = %s
                    """,
                    (invoice_id,),
                )
                self._billing_audit(
                    connection,
                    actor,
                    "PAYMENT_LATE",
                    "invoice",
                    invoice_id,
                    {"payment_event_id": str(payment_event_id)},
                )
                return self._settlement_snapshot(connection, invoice_id)

            persisted_payment = connection.execute(
                """
                SELECT *
                FROM payment_events
                WHERE payment_event_id = %s
                FOR UPDATE
                """,
                (payment_event_id,),
            ).fetchone()
            if persisted_payment is None:
                raise StoreError("payment event disappeared during settlement")

            current_observation = PaymentObservation(
                network=persisted_payment["network"],
                asset_code=persisted_payment["asset_code"],
                asset_kind=persisted_payment["asset_kind"],
                destination=persisted_payment["destination"],
                amount_atomic=int(persisted_payment["amount_atomic"]),
                chain_event_key=persisted_payment["chain_event_key"],
                tx_hash=persisted_payment["tx_hash"],
                block_reference=persisted_payment["block_reference"],
                confirmation_count=int(persisted_payment["confirmation_count"]),
                finality_status=persisted_payment["finality_status"],
                success=bool(persisted_payment["success"]),
                observed_at=persisted_payment["last_observed_at"],
                source=persisted_payment["source"],
                asset_contract=persisted_payment["asset_contract"],
                routing_mode=persisted_payment["routing_mode"],
                routing_reference=persisted_payment["routing_reference"],
            )

            decision = classify_invoice(
                invoice_model,
                [current_observation],
                now=now,
                required_confirmations=policy.required_confirmations,
                require_finality=policy.require_finality,
            )

            if invoice["status"] in {"canceled", "expired", "review_required"}:
                return self._settlement_snapshot(connection, invoice_id)

            if invoice_model.routing_mode == "manual_shared":
                connection.execute(
                    """
                    UPDATE billing_invoices
                    SET status = 'review_required'
                    WHERE invoice_id = %s
                    """,
                    (invoice_id,),
                )
                self._billing_audit(
                    connection,
                    actor,
                    "PAYMENT_REVIEW_REQUIRED",
                    "invoice",
                    invoice_id,
                    {
                        "reason": "shared destination has no invoice-specific routing",
                        "payment_event_id": str(payment_event_id),
                    },
                )
                return self._settlement_snapshot(connection, invoice_id)

            if decision.eligible_for_allocation:
                connection.execute(
                    """
                    INSERT INTO payment_allocations(
                        payment_event_id, invoice_id, allocated_atomic
                    ) VALUES (%s, %s, %s)
                    """,
                    (
                        payment_event_id,
                        invoice_id,
                        observation.amount_atomic,
                    ),
                )
                self._billing_audit(
                    connection,
                    actor,
                    "PAYMENT_ALLOCATED",
                    "invoice",
                    invoice_id,
                    {
                        "payment_event_id": str(payment_event_id),
                        "allocated_atomic": str(observation.amount_atomic),
                    },
                )

            snapshot = self._settlement_snapshot(connection, invoice_id)
            received_atomic = int(snapshot["received_atomic"])
            due_atomic = int(snapshot["amount_atomic"])

            if received_atomic < due_atomic:
                new_status = (
                    "confirming"
                    if decision.invoice_status == "confirming"
                    else "underpaid"
                )
                connection.execute(
                    """
                    UPDATE billing_invoices
                    SET status = %s
                    WHERE invoice_id = %s
                    """,
                    (new_status, invoice_id),
                )
                return self._settlement_snapshot(connection, invoice_id)

            excess = received_atomic - due_atomic
            final_status = "overpaid" if excess else "paid"

            connection.execute(
                """
                UPDATE billing_invoices
                SET status = %s, paid_at = COALESCE(paid_at, %s)
                WHERE invoice_id = %s
                """,
                (final_status, now, invoice_id),
            )

            self._ensure_entitlement(
                connection,
                invoice_model=invoice_model,
                invoice_id=invoice_id,
                now=now,
                actor=actor,
                excess_atomic=excess,
            )

            return self._settlement_snapshot(connection, invoice_id)

    def _ensure_entitlement(
        self,
        connection: Any,
        *,
        invoice_model: PaymentInvoice,
        invoice_id: str,
        now: datetime,
        actor: str,
        excess_atomic: int,
    ) -> str | None:
        entitlement = connection.execute(
            """
            SELECT entitlement_id
            FROM subscription_entitlements
            WHERE invoice_id = %s
            FOR UPDATE
            """,
            (invoice_id,),
        ).fetchone()
        if entitlement is not None:
            return str(entitlement["entitlement_id"])

        plan = connection.execute(
            """
            SELECT duration_seconds
            FROM subscription_plans
            WHERE plan_code = %s
            FOR SHARE
            """,
            (invoice_model.plan_code,),
        ).fetchone()
        if plan is None:
            raise NotFoundError(
                f"subscription plan {invoice_model.plan_code} does not exist"
            )

        previous = connection.execute(
            """
            SELECT MAX(expires_at) AS expires_at
            FROM subscription_entitlements
            WHERE customer_ref = %s
              AND plan_code = %s
              AND status = 'active'
              AND expires_at > %s
            """,
            (invoice_model.customer_ref, invoice_model.plan_code, now),
        ).fetchone()
        starts_at = now
        if previous and previous["expires_at"] is not None:
            starts_at = max(starts_at, previous["expires_at"])

        expiry_dt = datetime.fromtimestamp(
            starts_at.timestamp() + int(plan["duration_seconds"]),
            tz=timezone.utc,
        )
        entitlement_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO subscription_entitlements(
                entitlement_id, invoice_id, customer_ref, plan_code,
                status, starts_at, expires_at, activated_at
            ) VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                entitlement_id,
                invoice_id,
                invoice_model.customer_ref,
                invoice_model.plan_code,
                starts_at,
                expiry_dt,
                now,
            ),
        )
        self._billing_audit(
            connection,
            actor,
            "ENTITLEMENT_ACTIVATED",
            "entitlement",
            str(entitlement_id),
            {
                "invoice_id": invoice_id,
                "plan_code": invoice_model.plan_code,
                "excess_atomic": str(excess_atomic),
            },
        )
        return str(entitlement_id)

    @staticmethod
    @_retry_billing_transaction
    def expire_invoices(
        self,
        *,
        actor: str,
        now: datetime | None = None,
        limit: int = 500,
    ) -> int:
        if not actor.strip():
            raise PaymentValidationError("actor is required")
        if limit < 1 or limit > 5000:
            raise PaymentValidationError("limit must be between 1 and 5000")
        effective_now = now or datetime.now(timezone.utc)
        if effective_now.tzinfo is None or effective_now.utcoffset() is None:
            raise PaymentValidationError("now must include a timezone")

        with self.store._transaction(retryable=True) as connection:
            rows = connection.execute(
                """
                SELECT invoice_id
                FROM billing_invoices
                WHERE status IN ('open', 'confirming', 'underpaid')
                  AND expires_at < %s
                ORDER BY expires_at ASC
                LIMIT %s
                FOR UPDATE
                """,
                (effective_now.astimezone(timezone.utc), limit),
            ).fetchall()
            for row in rows:
                invoice_id = str(row["invoice_id"])
                connection.execute(
                    """
                    UPDATE billing_invoices
                    SET status = 'expired'
                    WHERE invoice_id = %s
                    """,
                    (row["invoice_id"],),
                )
                self._billing_audit(
                    connection,
                    actor,
                    "INVOICE_EXPIRED",
                    "invoice",
                    invoice_id,
                    {"expired_at": effective_now.astimezone(timezone.utc).isoformat()},
                )
            return len(rows)

    def customer_ref_for_actor(actor: str) -> str:
        if not actor.strip():
            raise PaymentValidationError("actor is required")
        digest = hashlib.sha256(actor.encode("utf-8")).hexdigest()
        return "cust-" + digest

    def list_prices(self) -> list[dict[str, Any]]:
        with self.store._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT bp.price_id, bp.plan_code,
                       p.duration_seconds,
                       bp.asset_code, bp.network, bp.asset_kind,
                       bp.asset_contract, bp.amount_atomic,
                       bp.asset_decimals, bp.routing_mode
                FROM billing_prices bp
                JOIN subscription_plans p ON p.plan_code = bp.plan_code
                WHERE bp.active = TRUE
                  AND p.status = 'active'
                ORDER BY bp.plan_code ASC, bp.asset_code ASC, bp.network ASC,
                         bp.price_id ASC
                """
            ).fetchall()

        from decimal import Decimal
        result: list[dict[str, Any]] = []
        for row in rows:
            amount_atomic = int(row["amount_atomic"])
            decimals = int(row["asset_decimals"])
            result.append(
                {
                    "price_id": str(row["price_id"]),
                    "plan_code": row["plan_code"],
                    "duration_seconds": int(row["duration_seconds"]),
                    "asset_code": row["asset_code"],
                    "network": row["network"],
                    "asset_kind": row["asset_kind"],
                    "asset_contract": row["asset_contract"],
                    "amount_atomic": str(row["amount_atomic"]),
                    "amount": format(
                        Decimal(amount_atomic).scaleb(-decimals).normalize(),
                        "f",
                    ),
                    "asset_decimals": decimals,
                    "routing_mode": row["routing_mode"],
                }
            )
        return result

    def get_invoice(
        self,
        *,
        invoice_id: str,
        customer_ref: str,
    ) -> dict[str, Any]:
        try:
            parsed_invoice_id = uuid.UUID(invoice_id)
        except (TypeError, ValueError) as exc:
            raise PaymentValidationError("invoice_id must be a UUID") from exc
        if not customer_ref.strip():
            raise PaymentValidationError("customer_ref is required")
        with self.store._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT invoice_id
                FROM billing_invoices
                WHERE invoice_id = %s
                  AND customer_ref = %s
                """,
                (parsed_invoice_id, customer_ref),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"invoice {invoice_id} does not exist")
            return self._settlement_snapshot(connection, invoice_id)

    def list_entitlements(
        self,
        *,
        customer_ref: str,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        if not customer_ref.strip():
            raise PaymentValidationError("customer_ref is required")
        with self.store._pool.connection() as connection:
            if active_only:
                rows = connection.execute(
                    """
                    SELECT entitlement_id, invoice_id, plan_code,
                           status, starts_at, expires_at, activated_at
                    FROM subscription_entitlements
                    WHERE customer_ref = %s
                      AND status = 'active'
                      AND expires_at > NOW()
                    ORDER BY expires_at DESC
                    """,
                    (customer_ref,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT entitlement_id, invoice_id, plan_code,
                           status, starts_at, expires_at, activated_at
                    FROM subscription_entitlements
                    WHERE customer_ref = %s
                    ORDER BY created_at DESC
                    """,
                    (customer_ref,),
                ).fetchall()
        return [
            {
                "entitlement_id": str(row["entitlement_id"]),
                "invoice_id": str(row["invoice_id"]),
                "plan_code": row["plan_code"],
                "status": row["status"],
                "starts_at": row["starts_at"],
                "expires_at": row["expires_at"],
                "activated_at": row["activated_at"],
            }
            for row in rows
        ]

    def _settlement_snapshot(self, connection: Any, invoice_id: str) -> dict[str, Any]:
        invoice = connection.execute(
            """
            SELECT invoice_id, customer_ref, plan_code, asset_code, network,
                   asset_kind, asset_contract, amount_atomic,
                   asset_decimals, destination, routing_mode,
                   routing_reference, status, expires_at, paid_at
            FROM billing_invoices
            WHERE invoice_id = %s
            """,
            (invoice_id,),
        ).fetchone()
        if invoice is None:
            raise NotFoundError(f"invoice {invoice_id} does not exist")

        total = connection.execute(
            """
            SELECT COALESCE(SUM(allocated_atomic), 0) AS received_atomic
            FROM payment_allocations
            WHERE invoice_id = %s
            """,
            (invoice_id,),
        ).fetchone()["received_atomic"]

        entitlement = connection.execute(
            """
            SELECT entitlement_id, status, starts_at, expires_at, activated_at
            FROM subscription_entitlements
            WHERE invoice_id = %s
            """,
            (invoice_id,),
        ).fetchone()

        due = int(invoice["amount_atomic"])
        received = int(total)
        return {
            "invoice_id": str(invoice["invoice_id"]),
            "customer_ref": invoice["customer_ref"],
            "plan_code": invoice["plan_code"],
            "asset_code": invoice["asset_code"],
            "network": invoice["network"],
            "asset_kind": invoice["asset_kind"],
            "asset_contract": invoice["asset_contract"],
            "amount_atomic": str(invoice["amount_atomic"]),
            "asset_decimals": int(invoice["asset_decimals"]),
            "destination": invoice["destination"],
            "routing_mode": invoice["routing_mode"],
            "routing_reference": invoice["routing_reference"],
            "status": invoice["status"],
            "expires_at": invoice["expires_at"],
            "paid_at": invoice["paid_at"],
            "received_atomic": str(received),
            "shortfall_atomic": str(max(0, due - received)),
            "excess_atomic": str(max(0, received - due)),
            "entitlement": None if entitlement is None else {
                "entitlement_id": str(entitlement["entitlement_id"]),
                "status": entitlement["status"],
                "starts_at": entitlement["starts_at"],
                "expires_at": entitlement["expires_at"],
                "activated_at": entitlement["activated_at"],
            },
        }

    @staticmethod
    def _billing_audit(
        connection: Any,
        actor: str,
        action: str,
        object_type: str,
        object_id: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO billing_audit_log(
                actor, action, object_type, object_id, details_json
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                actor,
                action,
                object_type,
                object_id,
                json.dumps(
                    details,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
        )


    @_retry_billing_transaction
    def manual_reconcile_payment(
        self,
        *,
        payment_event_id: str,
        invoice_id: str,
        actor: str,
    ) -> dict[str, Any]:
        if not actor.strip():
            raise PaymentValidationError("actor is required")
        try:
            payment_uuid = uuid.UUID(payment_event_id)
        except (TypeError, ValueError) as exc:
            raise PaymentValidationError("payment_event_id must be a UUID") from exc

        now = datetime.now(timezone.utc)

        with self.store._transaction(retryable=True) as connection:
            payment = connection.execute(
                """
                SELECT *
                FROM payment_events
                WHERE payment_event_id = %s
                FOR UPDATE
                """,
                (payment_uuid,),
            ).fetchone()
            if payment is None:
                raise NotFoundError(f"payment event {payment_event_id} does not exist")

            invoice = connection.execute(
                """
                SELECT *
                FROM billing_invoices
                WHERE invoice_id = %s
                FOR UPDATE
                """,
                (invoice_id,),
            ).fetchone()
            if invoice is None:
                raise NotFoundError(f"invoice {invoice_id} does not exist")
            if invoice["status"] == "canceled":
                raise ConflictError("canceled invoices cannot receive reconciled payments")
            if payment["finality_status"] != "final" or not payment["success"]:
                raise ConflictError(
                    "only successful finalized payments may be manually reconciled"
                )

            existing = connection.execute(
                """
                SELECT invoice_id
                FROM payment_allocations
                WHERE payment_event_id = %s
                """,
                (payment_uuid,),
            ).fetchone()
            if existing is not None:
                if str(existing["invoice_id"]) != invoice_id:
                    raise ConflictError(
                        "payment event is already allocated to another invoice"
                    )
                return self._settlement_snapshot(connection, invoice_id)

            if (
                payment["network"] != invoice["network"]
                or payment["asset_code"] != invoice["asset_code"]
                or payment["asset_kind"] != invoice["asset_kind"]
                or (
                    payment["asset_contract"] != invoice["asset_contract"]
                    if payment["asset_contract"] is not None
                    else invoice["asset_contract"] is not None
                )
                or payment["destination"] != invoice["destination"]
            ):
                raise ConflictError("payment and invoice asset/destination do not match")

            if invoice["routing_mode"] != "manual_shared" and (
                payment["routing_mode"] != invoice["routing_mode"]
                or payment["routing_reference"] != invoice["routing_reference"]
            ):
                raise ConflictError("payment and invoice routing do not match")

            invoice_model = PaymentInvoice(
                invoice_id=str(invoice["invoice_id"]),
                customer_ref=invoice["customer_ref"],
                plan_code=invoice["plan_code"],
                asset_code=invoice["asset_code"],
                network=invoice["network"],
                asset_kind=invoice["asset_kind"],
                amount_atomic=int(invoice["amount_atomic"]),
                asset_decimals=int(invoice["asset_decimals"]),
                destination=invoice["destination"],
                expires_at=invoice["expires_at"],
                asset_contract=invoice["asset_contract"],
                routing_mode=invoice["routing_mode"],
                routing_reference=invoice["routing_reference"],
            )

            connection.execute(
                """
                INSERT INTO payment_allocations(
                    payment_event_id, invoice_id, allocated_atomic
                ) VALUES (%s, %s, %s)
                """,
                (
                    payment_uuid,
                    invoice_id,
                    int(payment["amount_atomic"]),
                ),
            )
            self._billing_audit(
                connection,
                actor,
                "PAYMENT_MANUALLY_RECONCILED",
                "invoice",
                invoice_id,
                {
                    "payment_event_id": str(payment_uuid),
                    "allocated_atomic": str(payment["amount_atomic"]),
                },
            )

            snapshot = self._settlement_snapshot(connection, invoice_id)
            received_atomic = int(snapshot["received_atomic"])
            due_atomic = int(snapshot["amount_atomic"])
            if received_atomic < due_atomic:
                connection.execute(
                    """
                    UPDATE billing_invoices
                    SET status = 'underpaid'
                    WHERE invoice_id = %s
                    """,
                    (invoice_id,),
                )
                return self._settlement_snapshot(connection, invoice_id)

            excess = received_atomic - due_atomic
            connection.execute(
                """
                UPDATE billing_invoices
                SET status = %s,
                    paid_at = COALESCE(paid_at, %s)
                WHERE invoice_id = %s
                """,
                ("overpaid" if excess else "paid", now, invoice_id),
            )
            self._ensure_entitlement(
                connection,
                invoice_model=invoice_model,
                invoice_id=invoice_id,
                now=now,
                actor=actor,
                excess_atomic=excess,
            )
            return self._settlement_snapshot(connection, invoice_id)

    @_retry_billing_transaction
    def record_unmatched_observation(
        self,
        *,
        observation: PaymentObservation,
        actor: str,
    ) -> None:
        """Persist a trusted payment that could not be matched to an invoice."""
        validate_observation(observation)
        if not actor.strip():
            raise PaymentValidationError("actor is required")
        self.policy_for(observation.network)

        with self.store._transaction(retryable=True) as connection:
            self.store._lock_keys(
                connection,
                [
                    f"payment:{observation.network}:"
                    f"{observation.chain_event_key}"
                ],
            )
            payment_event_id = self._upsert_payment_event(
                connection,
                observation,
            )
            self._billing_audit(
                connection,
                actor,
                "PAYMENT_UNMATCHED",
                "payment_event",
                str(payment_event_id),
                {
                    "network": observation.network,
                    "tx_hash": observation.tx_hash,
                    "destination": observation.destination,
                    "routing_mode": observation.routing_mode,
                    "routing_reference": observation.routing_reference,
                },
            )

    def settle_discovered_observation(
        self,
        *,
        observation: PaymentObservation,
        actor: str,
    ) -> dict[str, Any] | None:
        """Find the uniquely routed open invoice for an observation."""
        with self.store._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT invoice_id
                FROM billing_invoices
                WHERE network = %s
                  AND asset_code = %s
                  AND asset_kind = %s
                  AND asset_contract IS NOT DISTINCT FROM %s
                  AND destination = %s
                  AND routing_mode = %s
                  AND routing_reference IS NOT DISTINCT FROM %s
                  AND status IN ('open', 'confirming', 'underpaid')
                  AND expires_at >= NOW()
                ORDER BY created_at ASC
                LIMIT 2
                """,
                (
                    observation.network,
                    observation.asset_code,
                    observation.asset_kind,
                    observation.asset_contract,
                    observation.destination,
                    observation.routing_mode,
                    observation.routing_reference,
                ),
            ).fetchall()
        if not row:
            return None
        if len(row) > 1:
            raise ConflictError(
                "multiple invoices share the same payment routing tuple"
            )
        return self.settle_observation(
            invoice_id=str(row[0]["invoice_id"]),
            observation=observation,
            actor=actor,
        )
