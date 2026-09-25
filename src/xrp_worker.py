"""XRP worker boundary with a safe synthetic adapter.

Real XRPL connectivity is intentionally not enabled here. A future adapter
must validate the ledger/network, delivered amount, destination, transaction
identity and finality before creating a ChainObservation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.entitlement_core import EntitlementCore
from src.payment_core import ChainObservation, PaymentCore, PaymentState


@dataclass(frozen=True)
class SyntheticXrpTransaction:
    invoice_id: str
    tx_id: str
    destination: str
    amount_drops: int
    confirmations: int
    network: str = "XRPL_TESTNET"
    asset: str = "XRP"
    observed_at: str = "1970-01-01T00:00:00Z"


class XrpWorker:
    def __init__(self, payment_core: PaymentCore, entitlement_core: EntitlementCore) -> None:
        self.payment_core = payment_core
        self.entitlement_core = entitlement_core

    def process(self, tx: SyntheticXrpTransaction) -> PaymentState:
        state = self.payment_core.process_observation(
            tx.invoice_id,
            ChainObservation(
                tx_id=tx.tx_id,
                network=tx.network,
                asset=tx.asset,
                destination=tx.destination,
                amount_minor=tx.amount_drops,
                confirmations=tx.confirmations,
                observed_at=tx.observed_at,
            ),
        )
        if state == PaymentState.CONFIRMED:
            payment = self.payment_core.get_payment(tx.invoice_id)
            if payment is None:
                raise RuntimeError("confirmed payment disappeared")
            self.entitlement_core.grant(
                payment["payment_id"],
                payment["customer_id"],
                payment["plan_id"],
            )
        return state

    def run_synthetic(self, txs: Iterable[SyntheticXrpTransaction]) -> list[PaymentState]:
        return [self.process(tx) for tx in txs]
