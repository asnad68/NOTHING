"""Persistent payment core with strict state transitions and idempotency.

This module is network-agnostic. Chain workers supply validated observations;
Payment Core decides whether an invoice is payable, confirming, final,
duplicated, or rejected. Amounts are integers in the asset's smallest unit.
""" 
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class PaymentState(StrEnum):
    CREATED = "CREATED"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    DETECTED = "DETECTED"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    UNDERPAID = "UNDERPAID"
    OVERPAID = "OVERPAID"
    WRONG_ASSET = "WRONG_ASSET"
    WRONG_NETWORK = "WRONG_NETWORK"
    DUPLICATE = "DUPLICATE"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


FINAL_STATES = {
    PaymentState.CONFIRMED,
    PaymentState.UNDERPAID,
    PaymentState.OVERPAID,
    PaymentState.WRONG_ASSET,
    PaymentState.WRONG_NETWORK,
    PaymentState.EXPIRED,
    PaymentState.REJECTED,
}


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    customer_id: str
    plan_id: str
    network: str
    asset: str
    destination: str
    expected_amount_minor: int
    required_confirmations: int
    expires_at: str | None = None


@dataclass(frozen=True)
class ChainObservation:
    tx_id: str
    network: str
    asset: str
    destination: str
    amount_minor: int
    confirmations: int
    observed_at: str


class PaymentError(RuntimeError):
    pass


class PaymentCore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    network TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    expected_amount_minor INTEGER NOT NULL CHECK(expected_amount_minor > 0),
                    required_confirmations INTEGER NOT NULL CHECK(required_confirmations >= 0),
                    expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    invoice_id TEXT NOT NULL UNIQUE REFERENCES invoices(invoice_id),
                    tx_id TEXT UNIQUE,
                    network TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    observed_amount_minor INTEGER NOT NULL,
                    confirmations INTEGER NOT NULL CHECK(confirmations >= 0),
                    state TEXT NOT NULL,
                    rejection_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payment_events (
                    event_id TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL REFERENCES payments(payment_id),
                    state TEXT NOT NULL,
                    tx_id TEXT,
                    occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_payments_tx ON payments(tx_id);
                CREATE INDEX IF NOT EXISTS idx_payment_events_payment ON payment_events(payment_id, occurred_at);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_invoice(invoice: Invoice) -> None:
        if invoice.expected_amount_minor <= 0:
            raise PaymentError("expected_amount_minor must be positive")
        if invoice.required_confirmations < 0:
            raise PaymentError("required_confirmations cannot be negative")
        fields = (
            invoice.invoice_id, invoice.customer_id, invoice.plan_id,
            invoice.network, invoice.asset, invoice.destination,
        )
        if not all(isinstance(v, str) and v.strip() for v in fields):
            raise PaymentError("invoice contains empty required fields")

    def create_invoice(self, invoice: Invoice) -> None:
        self._validate_invoice(invoice)
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    """INSERT INTO invoices(
                        invoice_id, customer_id, plan_id, network, asset, destination,
                        expected_amount_minor, required_confirmations, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        invoice.invoice_id, invoice.customer_id, invoice.plan_id,
                        invoice.network, invoice.asset, invoice.destination,
                        invoice.expected_amount_minor, invoice.required_confirmations,
                        invoice.expires_at,
                    ),
                )
                c.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                c.execute("ROLLBACK")
                raise PaymentError(f"invoice already exists: {invoice.invoice_id}") from exc

    def _event(
        self, c: sqlite3.Connection, payment_id: str, state: PaymentState,
        tx_id: str | None, observed_at: str, details: dict[str, Any],
    ) -> None:
        c.execute(
            """INSERT INTO payment_events(
                event_id, payment_id, state, tx_id, occurred_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex, payment_id, state.value, tx_id, observed_at,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
            ),
        )

    def process_observation(self, invoice_id: str, obs: ChainObservation) -> PaymentState:
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                invoice = c.execute(
                    "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
                ).fetchone()
                if invoice is None:
                    raise PaymentError(f"unknown invoice: {invoice_id}")

                existing_tx = c.execute(
                    "SELECT * FROM payments WHERE tx_id = ?", (obs.tx_id,)
                ).fetchone()
                if existing_tx is not None and existing_tx["invoice_id"] != invoice_id:
                    self._event(
                        c, existing_tx["payment_id"], PaymentState.DUPLICATE,
                        obs.tx_id, obs.observed_at,
                        {
                            "reason": "transaction already belongs to another invoice",
                            "invoice_id": invoice_id,
                        },
                    )
                    c.execute("COMMIT")
                    return PaymentState.DUPLICATE

                payment = c.execute(
                    "SELECT * FROM payments WHERE invoice_id = ?", (invoice_id,)
                ).fetchone()
                if payment is not None:
                    if payment["tx_id"] == obs.tx_id:
                        if payment["state"] in {s.value for s in FINAL_STATES}:
                            c.execute("COMMIT")
                            return PaymentState(payment["state"])
                        result = self._evaluate(c, invoice, payment, obs)
                        c.execute("COMMIT")
                        return result
                    if payment["state"] == PaymentState.CONFIRMED.value:
                        self._event(
                            c, payment["payment_id"], PaymentState.DUPLICATE,
                            obs.tx_id, obs.observed_at,
                            {
                                "reason": "invoice already settled",
                                "existing_tx": payment["tx_id"],
                            },
                        )
                        c.execute("COMMIT")
                        return PaymentState.DUPLICATE
                    raise PaymentError(f"invoice already has an in-flight payment: {invoice_id}")

                payment_id = uuid.uuid4().hex
                state = PaymentState.CONFIRMING
                rejection = None
                if obs.network != invoice["network"]:
                    state, rejection = PaymentState.WRONG_NETWORK, "WRONG_NETWORK"
                elif obs.asset != invoice["asset"]:
                    state, rejection = PaymentState.WRONG_ASSET, "WRONG_ASSET"
                elif obs.destination != invoice["destination"]:
                    state, rejection = PaymentState.REJECTED, "WRONG_DESTINATION"
                elif obs.amount_minor < invoice["expected_amount_minor"]:
                    state, rejection = PaymentState.UNDERPAID, "UNDERPAID"
                elif obs.amount_minor > invoice["expected_amount_minor"]:
                    state, rejection = PaymentState.OVERPAID, "OVERPAID"
                elif obs.confirmations >= invoice["required_confirmations"]:
                    state = PaymentState.CONFIRMED

                now = self._now()
                c.execute(
                    """INSERT INTO payments(
                        payment_id, invoice_id, tx_id, network, asset, destination,
                        observed_amount_minor, confirmations, state, rejection_code,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payment_id, invoice_id, obs.tx_id, obs.network, obs.asset,
                        obs.destination, obs.amount_minor, obs.confirmations,
                        state.value, rejection, now, now,
                    ),
                )
                self._event(
                    c, payment_id, state, obs.tx_id, obs.observed_at,
                    {
                        "confirmations": obs.confirmations,
                        "amount_minor": obs.amount_minor,
                    },
                )
                c.execute("COMMIT")
                return state
            except Exception:
                c.execute("ROLLBACK")
                raise

    def _evaluate(
        self, c: sqlite3.Connection, invoice: sqlite3.Row,
        payment: sqlite3.Row, obs: ChainObservation,
    ) -> PaymentState:
        if payment["tx_id"] != obs.tx_id:
            return PaymentState.DUPLICATE
        if obs.network != invoice["network"]:
            return PaymentState.WRONG_NETWORK
        if obs.asset != invoice["asset"]:
            return PaymentState.WRONG_ASSET
        if obs.destination != invoice["destination"]:
            return PaymentState.REJECTED
        if obs.amount_minor != invoice["expected_amount_minor"]:
            return (
                PaymentState.UNDERPAID
                if obs.amount_minor < invoice["expected_amount_minor"]
                else PaymentState.OVERPAID
            )

        state = (
            PaymentState.CONFIRMED
            if obs.confirmations >= invoice["required_confirmations"]
            else PaymentState.CONFIRMING
        )
        c.execute(
            "UPDATE payments SET confirmations = ?, state = ?, updated_at = ? WHERE payment_id = ?",
            (obs.confirmations, state.value, self._now(), payment["payment_id"]),
        )
        self._event(
            c, payment["payment_id"], state, obs.tx_id, obs.observed_at,
            {
                "confirmations": obs.confirmations,
                "amount_minor": obs.amount_minor,
            },
        )
        return state

    def get_payment(self, invoice_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(
                """SELECT p.*, i.customer_id, i.plan_id, i.required_confirmations
                   FROM payments p
                   JOIN invoices i ON i.invoice_id = p.invoice_id
                   WHERE p.invoice_id = ?""",
                (invoice_id,),
            ).fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        return None
