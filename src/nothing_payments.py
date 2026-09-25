"""Payment and subscription domain primitives for NOTHING.

This module deliberately contains no blockchain RPC client and no private-key
handling. Chain-specific adapters must produce trusted PaymentObservation values
from authoritative network data before persistence/entitlement activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


InvoiceStatus = Literal[
    "open",
    "confirming",
    "underpaid",
    "paid",
    "overpaid",
    "expired",
    "canceled",
    "review_required",
]

FinalityStatus = Literal["pending", "confirmed", "final", "orphaned"]
AssetKind = Literal["native", "erc20", "xrp", "btc_utxo"]
RoutingMode = Literal["unique_destination", "xrp_destination_tag", "manual_shared"]


class PaymentValidationError(ValueError):
    """Raised when a payment-domain value is malformed."""


@dataclass(frozen=True)
class PaymentInvoice:
    invoice_id: str
    customer_ref: str
    plan_code: str
    asset_code: str
    network: str
    asset_kind: AssetKind
    amount_atomic: int
    asset_decimals: int
    destination: str
    expires_at: datetime
    asset_contract: str | None = None
    routing_mode: RoutingMode = "manual_shared"
    routing_reference: str | None = None


@dataclass(frozen=True)
class PaymentObservation:
    network: str
    asset_code: str
    asset_kind: AssetKind
    destination: str
    amount_atomic: int
    chain_event_key: str
    tx_hash: str
    block_reference: str | None
    confirmation_count: int
    finality_status: FinalityStatus
    success: bool
    observed_at: datetime
    source: str
    asset_contract: str | None = None
    routing_mode: RoutingMode = "manual_shared"
    routing_reference: str | None = None


@dataclass(frozen=True)
class PaymentDecision:
    invoice_status: InvoiceStatus
    received_atomic: int
    due_atomic: int
    shortfall_atomic: int
    excess_atomic: int
    eligible_for_allocation: bool
    eligible_for_entitlement: bool
    reason: str


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PaymentValidationError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def validate_invoice(invoice: PaymentInvoice) -> None:
    _require_utc(invoice.expires_at, "expires_at")
    if not invoice.invoice_id.strip():
        raise PaymentValidationError("invoice_id is required")
    if not invoice.customer_ref.strip():
        raise PaymentValidationError("customer_ref is required")
    if not invoice.plan_code.strip():
        raise PaymentValidationError("plan_code is required")
    if not invoice.asset_code.strip() or not invoice.network.strip():
        raise PaymentValidationError("asset_code and network are required")
    if invoice.amount_atomic <= 0:
        raise PaymentValidationError("amount_atomic must be positive")
    if not 0 <= invoice.asset_decimals <= 36:
        raise PaymentValidationError("asset_decimals must be between 0 and 36")
    if not invoice.destination.strip():
        raise PaymentValidationError("destination is required")
    if invoice.asset_kind == "erc20" and not invoice.asset_contract:
        raise PaymentValidationError("ERC-20 payments require asset_contract")
    if invoice.routing_mode == "xrp_destination_tag" and not invoice.routing_reference:
        raise PaymentValidationError("XRP tagged invoices require routing_reference")
    if invoice.routing_mode == "unique_destination" and not invoice.destination:
        raise PaymentValidationError("unique_destination invoices require destination")


def validate_observation(observation: PaymentObservation) -> None:
    _require_utc(observation.observed_at, "observed_at")
    if not observation.network.strip():
        raise PaymentValidationError("network is required")
    if not observation.asset_code.strip():
        raise PaymentValidationError("asset_code is required")
    if not observation.destination.strip():
        raise PaymentValidationError("destination is required")
    if observation.amount_atomic <= 0:
        raise PaymentValidationError("amount_atomic must be positive")
    if not observation.chain_event_key.strip():
        raise PaymentValidationError("chain_event_key is required")
    if not observation.tx_hash.strip():
        raise PaymentValidationError("tx_hash is required")
    if observation.confirmation_count < 0:
        raise PaymentValidationError("confirmation_count cannot be negative")
    if not observation.source.strip():
        raise PaymentValidationError("source is required")
    if observation.asset_kind == "erc20" and not observation.asset_contract:
        raise PaymentValidationError("ERC-20 observations require asset_contract")
    if observation.routing_mode == "xrp_destination_tag" and not observation.routing_reference:
        raise PaymentValidationError("XRP tagged observations require routing_reference")


def chain_event_key(
    *,
    network: str,
    tx_hash: str,
    asset_kind: AssetKind,
    event_index: int | None = None,
) -> str:
    """Build the stable uniqueness key for a chain payment event.

    Native EVM transfers use the transaction hash. ERC-20 uses tx hash plus
    log index. Bitcoin uses tx hash plus vout index. XRPL uses the transaction
    hash for a Payment transaction.
    """
    if asset_kind == "erc20":
        if event_index is None or event_index < 0:
            raise PaymentValidationError(
                "ERC-20 event_index must be a non-negative log index"
            )
        return f"{network}:{tx_hash}:log:{event_index}"
    if asset_kind == "btc_utxo":
        if event_index is None or event_index < 0:
            raise PaymentValidationError(
                "Bitcoin event_index must be a non-negative vout index"
            )
        return f"{network}:{tx_hash}:vout:{event_index}"
    return f"{network}:{tx_hash}"


def classify_invoice(
    invoice: PaymentInvoice,
    observations: list[PaymentObservation],
    *,
    now: datetime,
    required_confirmations: int,
    require_finality: bool = True,
) -> PaymentDecision:
    """Calculate entitlement eligibility from authoritative observations.

    An observation is eligible only after exact network/asset/destination
    matching and the configured finality/confirmation policy.
    """
    validate_invoice(invoice)
    _require_utc(now, "now")

    if invoice.routing_mode == "manual_shared":
        return PaymentDecision(
            invoice_status="review_required",
            received_atomic=0,
            due_atomic=invoice.amount_atomic,
            shortfall_atomic=invoice.amount_atomic,
            excess_atomic=0,
            eligible_for_allocation=False,
            eligible_for_entitlement=False,
            reason="automatic settlement is disabled for a shared receiving destination",
        )

    if required_confirmations < 0:
        raise PaymentValidationError("required_confirmations cannot be negative")

    if now.astimezone(timezone.utc) > invoice.expires_at.astimezone(timezone.utc):
        return PaymentDecision(
            invoice_status="expired",
            received_atomic=0,
            due_atomic=invoice.amount_atomic,
            shortfall_atomic=invoice.amount_atomic,
            excess_atomic=0,
            eligible_for_allocation=False,
            eligible_for_entitlement=False,
            reason="invoice expired before settlement",
        )

    received = 0
    eligible = 0

    for observation in observations:
        validate_observation(observation)
        if (
            observation.network != invoice.network
            or observation.asset_code != invoice.asset_code
            or observation.asset_kind != invoice.asset_kind
            or observation.destination != invoice.destination
            or observation.asset_contract != invoice.asset_contract
            or observation.routing_mode != invoice.routing_mode
            or observation.routing_reference != invoice.routing_reference
            or not observation.success
            or observation.finality_status == "orphaned"
        ):
            continue

        finality_ok = (
            observation.finality_status == "final"
            if require_finality
            else observation.confirmation_count >= required_confirmations
        )
        if not finality_ok:
            continue

        received += observation.amount_atomic
        eligible += 1

    if eligible == 0:
        pending_match = any(
            observation.network == invoice.network
            and observation.asset_code == invoice.asset_code
            and observation.asset_kind == invoice.asset_kind
            and observation.destination == invoice.destination
            and observation.asset_contract == invoice.asset_contract
            and observation.routing_mode == invoice.routing_mode
            and observation.routing_reference == invoice.routing_reference
            and observation.success
            and observation.finality_status in {"pending", "confirmed"}
            for observation in observations
        )
        return PaymentDecision(
            invoice_status="confirming" if pending_match else "open",
            received_atomic=0,
            due_atomic=invoice.amount_atomic,
            shortfall_atomic=invoice.amount_atomic,
            excess_atomic=0,
            eligible_for_allocation=False,
            eligible_for_entitlement=False,
            reason=(
                "matching payment detected but finality threshold is not met"
                if pending_match
                else "no eligible payment observation"
            ),
        )

    if received < invoice.amount_atomic:
        return PaymentDecision(
            invoice_status="underpaid",
            received_atomic=received,
            due_atomic=invoice.amount_atomic,
            shortfall_atomic=invoice.amount_atomic - received,
            excess_atomic=0,
            eligible_for_allocation=True,
            eligible_for_entitlement=False,
            reason="finalized payment is below invoice amount",
        )

    excess = received - invoice.amount_atomic
    return PaymentDecision(
        invoice_status="overpaid" if excess else "paid",
        received_atomic=received,
        due_atomic=invoice.amount_atomic,
        shortfall_atomic=0,
        excess_atomic=excess,
        eligible_for_allocation=True,
        eligible_for_entitlement=True,
        reason="finalized matching payments fully cover the invoice",
    )
