"""PostgreSQL-backed subscription settlement orchestration.

Only trusted chain adapters should call settle_observation(). The method never
accepts payer-supplied finality as authority; it persists an adapter observation
and atomically allocates the payment and activates an entitlement.
"""

from __future__ import annotations

import json
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
            if not settlement_routing_reference:
                raise PaymentValidationError(
                    "xrp_destination_tag requires an invoice-specific tag"
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
                    or quote.get("settlement_destination") != settlement_destination
                    or quote.get("routing_mode") != settlement_routing_mode
                    or quote.get("routing_reference") != settlement_routing_reference
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
            quote = {
                "price_id": price_id,
                "plan_code": plan_code,
                "asset_code": price["asset_code"],
                "network": price["network"],
                "asset_kind": price["asset_kind"],
                "asset_contract": price["asset_contract"],
                "amount_atomic": str(price["amount_atomic"]),
                "asset_decimals": int(price["asset_decimals"]),
                "destination": settlement_destination or price["destination"],
                "expires_at": expires_utc.isoformat(),
                "routing_mode": (
                    settlement_routing_mode or price["routing_mode"]
                ),
                "routing_reference": settlement_routing_reference,
                "settlement_destination": (
                    settlement_destination or price["destination"]
                ),
            }

            connection.execute(
                """
                INSERT INTO billing_invoices(
                    invoice_id, customer_ref, plan_code,
                    asset_code, network, asset_kind, asset_contract,
                    amount_atomic, asset_decimals, destination,
                    status, client_idempotency_key, expires_at,
                    quote_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
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
                    settlement_destination or price["destination"],
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
                destination=settlement_destination or price["destination"],
                expires_at=expires_utc,
                asset_contract=price["asset_contract"],
                routing_mode=settlement_routing_mode or price["routing_mode"],
                routing_reference=settlement_routing_reference,
            )

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
                        source, first_observed_at, last_observed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
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
                    ),
                )
            else:
                immutable_fields = (
                    ("asset_code", payment["asset_code"], observation.asset_code),
                    ("asset_kind", payment["asset_kind"], observation.asset_kind),
                    ("asset_contract", payment["asset_contract"], observation.asset_contract),
                    ("destination", payment["destination"], observation.destination),
                    ("amount_atomic", int(payment["amount_atomic"]), observation.amount_atomic),
                    ("tx_hash", payment["tx_hash"], observation.tx_hash),
                )
                for field, old, new in immutable_fields:
                    if old != new:
                        raise ConflictError(
                            f"payment event identity changed for {field}"
                        )
                payment_event_id = payment["payment_event_id"]

                if (
                    payment["finality_status"] == "final"
                    and observation.finality_status == "orphaned"
                ):
                    raise ConflictError(
                        "a finalized payment cannot be silently changed to orphaned"
                    )

                connection.execute(
                    """
                    UPDATE payment_events
                    SET block_reference = %s,
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
                        observation.finality_status,
                        observation.success,
                        observation.source,
                        observation.observed_at,
                        payment_event_id,
                    ),
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

            current_observation = PaymentObservation(
                network=observation.network,
                asset_code=observation.asset_code,
                asset_kind=observation.asset_kind,
                destination=observation.destination,
                amount_atomic=observation.amount_atomic,
                chain_event_key=observation.chain_event_key,
                tx_hash=observation.tx_hash,
                block_reference=observation.block_reference,
                confirmation_count=observation.confirmation_count,
                finality_status=observation.finality_status,
                success=observation.success,
                observed_at=observation.observed_at,
                source=observation.source,
                asset_contract=observation.asset_contract,
            )

            decision = classify_invoice(
                invoice_model,
                [current_observation],
                now=now,
                required_confirmations=policy.required_confirmations,
                require_finality=policy.require_finality,
            )

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

            entitlement = connection.execute(
                """
                SELECT entitlement_id
                FROM subscription_entitlements
                WHERE invoice_id = %s
                FOR UPDATE
                """,
                (invoice_id,),
            ).fetchone()

            if entitlement is None:
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
                    FOR SHARE
                    """,
                    (invoice_model.customer_ref, invoice_model.plan_code, now),
                ).fetchone()
                starts_at = now
                if previous and previous["expires_at"] is not None:
                    starts_at = max(starts_at, previous["expires_at"])
                expires_at = starts_at.timestamp() + int(plan["duration_seconds"])
                expiry_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)

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
                        "excess_atomic": str(excess),
                    },
                )

            return self._settlement_snapshot(connection, invoice_id)

    def _settlement_snapshot(self, connection: Any, invoice_id: str) -> dict[str, Any]:
        invoice = connection.execute(
            """
            SELECT invoice_id, customer_ref, plan_code, asset_code, network,
                   asset_kind, asset_contract, amount_atomic,
                   asset_decimals, destination, status, expires_at, paid_at
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
