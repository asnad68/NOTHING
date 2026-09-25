"""Scheduled billing maintenance tasks.

The process is intentionally separate from the public API and payment worker.
It uses the least-privileged payment database role to expire stale invoices and
records an audit event for every state transition.
"""

from __future__ import annotations

import os

from src.nothing_billing import SubscriptionBillingService
from src.nothing_postgres import PostgreSQLNothingStore


def main() -> None:
    dsn = os.getenv("NOTHING_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("NOTHING_POSTGRES_DSN is required")

    actor = os.getenv(
        "NOTHING_BILLING_MAINTENANCE_ACTOR",
        "billing-maintenance-worker",
    ).strip()
    limit = int(os.getenv("NOTHING_BILLING_EXPIRY_BATCH_SIZE", "500"))
    store = PostgreSQLNothingStore(dsn, auto_migrate=False)
    try:
        service = SubscriptionBillingService(store, policies={})
        changed = service.expire_invoices(
            actor=actor,
            limit=limit,
        )
        print(
            '{"event":"billing_expiration_scan","expired":%d}' % changed,
            flush=True,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
