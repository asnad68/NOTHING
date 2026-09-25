"""Continuous XRPL payment discovery worker.

This process is receive-only. It reads validated XRP Payment transactions from
XRPL and delegates all invoice matching and entitlement changes to the
PostgreSQL billing service.
"""

from __future__ import annotations

import os
import time
from typing import Any

from src.nothing_billing import ConfirmationPolicy, SubscriptionBillingService
from src.nothing_chain_adapters import XrplJsonRpcAdapter
from src.nothing_postgres import PostgreSQLNothingStore


DEFAULT_XRPL_ACCOUNT = "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2"
DEFAULT_XRPL_RPC = "https://xrplcluster.com/"
DEFAULT_POLL_SECONDS = 5


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def build_service() -> tuple[PostgreSQLNothingStore, SubscriptionBillingService]:
    dsn = os.getenv("NOTHING_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("NOTHING_POSTGRES_DSN is required")

    store = PostgreSQLNothingStore(
        dsn,
        auto_migrate=False,
    )
    service = SubscriptionBillingService(
        store,
        policies={
            "xrpl": ConfirmationPolicy(
                required_confirmations=1,
                require_finality=True,
            )
        },
    )
    return store, service


def run_once(
    adapter: XrplJsonRpcAdapter,
    service: SubscriptionBillingService,
    *,
    account: str,
    actor: str,
) -> int:
    observations = adapter.discover_recent_payments(account=account)
    settled = 0
    for observation in observations:
        result = service.settle_discovered_observation(
            observation=observation,
            actor=actor,
        )
        if result is not None:
            settled += 1
    return settled


def main() -> None:
    account = os.getenv(
        "NOTHING_XRPL_ACCOUNT",
        DEFAULT_XRPL_ACCOUNT,
    ).strip()
    rpc_url = os.getenv(
        "NOTHING_XRPL_RPC_URL",
        DEFAULT_XRPL_RPC,
    ).strip()
    poll_seconds = _float_env(
        "NOTHING_PAYMENT_POLL_SECONDS",
        DEFAULT_POLL_SECONDS,
    )
    actor = os.getenv(
        "NOTHING_PAYMENT_ACTOR",
        "xrpl-payment-worker",
    ).strip()

    store, service = build_service()
    adapter = XrplJsonRpcAdapter(rpc_url)

    try:
        while True:
            try:
                settled = run_once(
                    adapter,
                    service,
                    account=account,
                    actor=actor,
                )
                print(
                    '{"event":"xrpl_payment_scan","settled":%d}' % settled,
                    flush=True,
                )
            except Exception as exc:
                print(
                    '{"event":"xrpl_payment_scan_error","error_type":"%s"}'
                    % type(exc).__name__,
                    flush=True,
                )
            time.sleep(poll_seconds)
    finally:
        store.close()


if __name__ == "__main__":
    main()
