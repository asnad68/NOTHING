"""Operator-only billing reconciliation CLI.

This module does not create or send blockchain transactions. It only reads
finalized unallocated payment events and can perform an explicitly requested
manual allocation, which is audited by the billing service.
"""

from __future__ import annotations

import argparse
import json
import os

from src.nothing_billing import SubscriptionBillingService
from src.nothing_postgres import PostgreSQLNothingStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or manually reconcile NOTHING payment events."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list-unallocated")
    list_parser.add_argument("--limit", type=int, default=100)

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--payment-event-id", required=True)
    reconcile.add_argument("--invoice-id", required=True)
    reconcile.add_argument("--actor", required=True)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    dsn = os.getenv("NOTHING_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("NOTHING_POSTGRES_DSN is required")

    store = PostgreSQLNothingStore(dsn, auto_migrate=False)
    try:
        service = SubscriptionBillingService(store, policies={})
        if args.command == "list-unallocated":
            print(
                json.dumps(
                    {"data": service.list_unallocated_payments(limit=args.limit)},
                    default=str,
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return

        result = service.manual_reconcile_payment(
            payment_event_id=args.payment_event_id,
            invoice_id=args.invoice_id,
            actor=args.actor,
        )
        print(
            json.dumps(
                {"data": result},
                default=str,
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
