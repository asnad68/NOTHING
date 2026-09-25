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
DEFAULT_WORKER_NAME = "xrpl-payment-worker"
DEFAULT_DISCOVERY_LIMIT = 200
DEFAULT_MAX_DISCOVERY_PAGES = 20
DEFAULT_HEARTBEAT_FILE = "/tmp/nothing-xrpl-worker.heartbeat"


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _heartbeat(path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(time.time()))


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
    worker_name: str = DEFAULT_WORKER_NAME,
    discovery_limit: int = DEFAULT_DISCOVERY_LIMIT,
    max_discovery_pages: int = DEFAULT_MAX_DISCOVERY_PAGES,
) -> int:
    checkpoint = service.store.get_payment_worker_checkpoint(
        worker_name,
        account,
    )
    stop_after_tx_hash = (
        checkpoint["last_tx_hash"]
        if checkpoint is not None
        else None
    )

    discovery = adapter.discover_recent_payments_with_checkpoint(
        account=account,
        limit=discovery_limit,
        stop_after_tx_hash=stop_after_tx_hash,
        max_pages=max_discovery_pages,
    )

    settled = 0
    for observation in discovery.observations:
        result = service.settle_discovered_observation(
            observation=observation,
            actor=actor,
        )
        if result is None:
            service.record_unmatched_observation(
                observation=observation,
                actor=actor,
            )
        else:
            settled += 1

    if discovery.checkpoint_tx_hash is not None:
        service.store.set_payment_worker_checkpoint(
            worker_name,
            account,
            last_tx_hash=discovery.checkpoint_tx_hash,
            last_ledger_index=discovery.checkpoint_ledger_index,
        )

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
    heartbeat_file = os.getenv(
        "NOTHING_WORKER_HEARTBEAT_FILE",
        DEFAULT_HEARTBEAT_FILE,
    ).strip()
    worker_name = os.getenv(
        "NOTHING_XRPL_WORKER_NAME",
        DEFAULT_WORKER_NAME,
    ).strip()
    discovery_limit = _int_env(
        "NOTHING_XRPL_DISCOVERY_LIMIT",
        DEFAULT_DISCOVERY_LIMIT,
    )
    max_discovery_pages = _int_env(
        "NOTHING_XRPL_MAX_DISCOVERY_PAGES",
        DEFAULT_MAX_DISCOVERY_PAGES,
    )

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
                    worker_name=worker_name,
                    discovery_limit=discovery_limit,
                    max_discovery_pages=max_discovery_pages,
                )
                _heartbeat(heartbeat_file)
                print(
                    '{"event":"xrpl_payment_scan","settled":%d}' % settled,
                    flush=True,
                )
            except Exception as exc:
                # Do not refresh the liveness heartbeat on a failed scan.
                # Kubernetes will restart a worker that cannot observe XRPL
                # successfully for the configured heartbeat window.
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
