import copy
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.nothing_billing import ConfirmationPolicy, SubscriptionBillingService
from src.nothing_payments import PaymentObservation, chain_event_key
from src.nothing_postgres import PostgreSQLNothingStore
from src.nothing_store import ConflictError, NotFoundError

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.getenv("NOTHING_TEST_POSTGRES_DSN"),
    "requires NOTHING_TEST_POSTGRES_DSN",
)
class PostgreSQLPersistenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["NOTHING_TEST_POSTGRES_DSN"]

    def setUp(self):
        self.store = PostgreSQLNothingStore(
            self.dsn,
            min_size=1,
            max_size=6,
            pool_timeout=5,
            statement_timeout_ms=15000,
            lock_timeout_ms=5000,
            serialization_retries=5,
            retry_backoff_seconds=0.01,
            auto_migrate=True,
        )
        self.store.import_json_bundle(ROOT, actor="postgres-integration")

    def tearDown(self):
        self.store.close()

    def make_bundle(self):
        identity = copy.deepcopy(
            self.store.get_identity("NTH-000001").record
        )
        identity["nothing_id"] = "NTH-777777"
        identity["subject"]["name"] = "PostgreSQL Concurrency Test"

        claim_id = "CLM-777777"
        evidence_id = "EVD-777777"
        event_id = "VER-777777"
        identity["claims"][0]["claim_id"] = claim_id

        evidence = copy.deepcopy(
            self.store.get_evidence("EVD-000001").record
        )
        evidence["evidence_id"] = evidence_id

        event = copy.deepcopy(
            self.store.get_event("VER-000001").record
        )
        event["event_id"] = event_id
        event["subject"] = identity["nothing_id"]
        event["claim_id"] = claim_id
        event["evidence"] = [evidence_id]

        return {
            "identities": [identity],
            "evidence": [evidence],
            "verification_events": [event],
        }

    def test_migrations_and_basic_reads(self):
        with self.store._pool.connection() as connection:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
        self.assertEqual(row["version"], 9)

        identity = self.store.get_identity("NTH-000001")
        self.assertEqual(identity.record["nothing_id"], "NTH-000001")
        self.assertTrue(self.store.health())

    def test_concurrent_same_idempotency_key_has_one_commit(self):
        bundle = self.make_bundle()
        key = "postgres-concurrent-ingestion"
        request_hash = "a" * 64

        def submit(index):
            return self.store.ingest_bundle(
                bundle,
                actor="postgres-concurrent-writer",
                idempotency_key=key,
                request_sha256=request_hash,
                ingestion_id=f"22222222-2222-4222-8222-{index:012d}",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(submit, range(1, 5)))

        ingestion_ids = {result.data["ingestion_id"] for result in results}
        self.assertEqual(len(ingestion_ids), 1)
        self.assertEqual(
            self.store.get_identity("NTH-777777").revision,
            1,
        )

        with self.store._pool.connection() as connection:
            identity_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM identity_revisions
                WHERE nothing_id = 'NTH-777777'
                """
            ).fetchone()["count"]
            event_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM verification_events
                WHERE event_id = 'VER-777777'
                """
            ).fetchone()["count"]
            evidence_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM evidence
                WHERE evidence_id = 'EVD-777777'
                """
            ).fetchone()["count"]

        self.assertEqual(identity_count, 1)
        self.assertEqual(event_count, 1)
        self.assertEqual(evidence_count, 1)

    def test_concurrent_identity_updates_do_not_lose_revisions(self):
        base = self.store.get_identity("NTH-000001").record

        def update(index):
            identity = copy.deepcopy(base)
            identity["subject"]["name"] = f"Concurrent Revision {index}"
            return self.store.put_identity(
                identity,
                actor="postgres-concurrent-update",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            revisions = list(executor.map(update, range(1, 5)))

        self.assertEqual(sorted(revisions), [2, 3, 4, 5])
        self.assertEqual(
            self.store.get_identity("NTH-000001").revision,
            5,
        )

    def test_payment_settlement_activates_entitlement_once(self):
        service = SubscriptionBillingService(
            self.store,
            policies={
                "ethereum": ConfirmationPolicy(
                    required_confirmations=0,
                    require_finality=True,
                )
            },
        )
        plan_code = "payment-test-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())

        with self.store._transaction(retryable=True) as connection:
            connection.execute(
                """
                INSERT INTO subscription_plans(
                    plan_code, duration_seconds, status
                ) VALUES (%s, %s, 'active')
                """,
                (plan_code, 30 * 86400),
            )
            connection.execute(
                """
                INSERT INTO billing_prices(
                    price_id, plan_code, asset_code, network, asset_kind,
                    asset_contract, amount_atomic, asset_decimals,
                    destination, active
                ) VALUES (
                    %s, %s, 'ETH', 'ethereum', 'native',
                    NULL, %s, 18, %s, TRUE
                )
                """,
                (
                    price_id,
                    plan_code,
                    10**15,
                    "0xE1c90171271B5325beE02592ACc50A510448d03E",
                ),
            )

        invoice = service.create_invoice(
            customer_ref="customer-payment-test",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="invoice-once",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            actor="test-billing",
            settlement_destination="0xUniqueTestDepositAddress",
            settlement_routing_mode="unique_destination",
        )

        observation = PaymentObservation(
            network="ethereum",
            asset_code="ETH",
            asset_kind="native",
            destination=invoice.destination,
            amount_atomic=10**15,
            chain_event_key=chain_event_key(
                network="ethereum",
                tx_hash="0xpaymenttest",
                asset_kind="native",
            ),
            tx_hash="0xpaymenttest",
            block_reference="finalized",
            confirmation_count=1,
            finality_status="final",
            success=True,
            observed_at=datetime.now(timezone.utc),
            source="trusted-test-indexer",
            routing_mode="unique_destination",
            routing_reference=None,
        )

        first = service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=observation,
            actor="trusted-test-indexer",
        )
        self.assertEqual(first["status"], "paid")
        self.assertIsNotNone(first["entitlement"])

        second = service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=observation,
            actor="trusted-test-indexer",
        )
        self.assertEqual(second["entitlement"], first["entitlement"])

        with self.store._pool.connection() as connection:
            allocation_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM payment_allocations
                WHERE invoice_id = %s
                """,
                (invoice.invoice_id,),
            ).fetchone()["count"]
            entitlement_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM subscription_entitlements
                WHERE invoice_id = %s
                """,
                (invoice.invoice_id,),
            ).fetchone()["count"]

        self.assertEqual(allocation_count, 1)
        self.assertEqual(entitlement_count, 1)


    def _insert_payment_plan(
        self,
        *,
        plan_code: str,
        price_id: str,
        asset_code: str,
        network: str,
        asset_kind: str,
        amount_atomic: int,
        asset_decimals: int,
        destination: str,
        routing_mode: str = "manual_shared",
    ) -> None:
        with self.store._transaction(retryable=True) as connection:
            connection.execute(
                """
                INSERT INTO subscription_plans(
                    plan_code, duration_seconds, status
                ) VALUES (%s, %s, 'active')
                """,
                (plan_code, 30 * 86400),
            )
            connection.execute(
                """
                INSERT INTO billing_prices(
                    price_id, plan_code, asset_code, network, asset_kind,
                    asset_contract, amount_atomic, asset_decimals,
                    destination, active, routing_mode
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    NULL, %s, %s, %s, TRUE, %s
                )
                """,
                (
                    price_id,
                    plan_code,
                    asset_code,
                    network,
                    asset_kind,
                    amount_atomic,
                    asset_decimals,
                    destination,
                    routing_mode,
                ),
            )


    def test_manual_reconciliation_activates_entitlement_for_shared_payment(self):
        plan_code = "manual-reconcile-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())
        destination = "3ABUrDAmi6w9TRsHuwFgdcDBLvXUzbAfZY"
        self._insert_payment_plan(
            plan_code=plan_code,
            price_id=price_id,
            asset_code="BTC",
            network="bitcoin",
            asset_kind="btc_utxo",
            amount_atomic=5_000,
            asset_decimals=8,
            destination=destination,
            routing_mode="manual_shared",
        )
        service = SubscriptionBillingService(
            self.store,
            policies={"bitcoin": ConfirmationPolicy(
                required_confirmations=6,
                require_finality=True,
            )},
        )
        invoice = service.create_invoice(
            customer_ref="manual-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="manual-reconcile-key",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            actor="billing-test",
        )

        event = PaymentObservation(
            network="bitcoin",
            asset_code="BTC",
            asset_kind="btc_utxo",
            destination=destination,
            amount_atomic=5_000,
            chain_event_key=chain_event_key(
                network="bitcoin",
                tx_hash="btc-manual-reconcile",
                asset_kind="btc_utxo",
                event_index=0,
            ),
            tx_hash="btc-manual-reconcile",
            block_reference="block-1",
            confirmation_count=6,
            finality_status="final",
            success=True,
            observed_at=datetime.now(timezone.utc),
            source="bitcoin-core-test",
            routing_mode="manual_shared",
            routing_reference=None,
        )
        service.record_unmatched_observation(
            observation=event,
            actor="billing-test",
        )
        queue = service.list_unallocated_payments(limit=10)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["tx_hash"], event.tx_hash)

        with self.store._pool.connection() as connection:
            payment_event_id = connection.execute(
                """
                SELECT payment_event_id
                FROM payment_events
                WHERE chain_event_key = %s
                """,
                (event.chain_event_key,),
            ).fetchone()["payment_event_id"]

        snapshot = service.manual_reconcile_payment(
            payment_event_id=str(payment_event_id),
            invoice_id=invoice.invoice_id,
            actor="manual-reviewer",
        )
        self.assertEqual(service.list_unallocated_payments(limit=10), [])
        self.assertEqual(snapshot["status"], "paid")
        self.assertEqual(snapshot["received_atomic"], "5000")
        self.assertIsNotNone(snapshot["entitlement"])
        self.assertEqual(snapshot["entitlement"]["status"], "active")

    def test_automatic_settlement_does_not_reopen_review_required_invoice(self):
        plan_code = "review-state-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())
        destination = "0x1111111111111111111111111111111111111111"
        self._insert_payment_plan(
            plan_code=plan_code,
            price_id=price_id,
            asset_code="ETH",
            network="ethereum",
            asset_kind="native",
            amount_atomic=1_000,
            asset_decimals=18,
            destination=destination,
            routing_mode="manual_shared",
        )
        service = SubscriptionBillingService(
            self.store,
            policies={"ethereum": ConfirmationPolicy(
                required_confirmations=0,
                require_finality=True,
            )},
        )
        invoice = service.create_invoice(
            customer_ref="review-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="review-state-key",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            actor="billing-test",
        )
        event = PaymentObservation(
            network="ethereum",
            asset_code="ETH",
            asset_kind="native",
            destination=destination,
            amount_atomic=1_000,
            chain_event_key=chain_event_key(
                network="ethereum",
                tx_hash="0xreview-state",
                asset_kind="native",
            ),
            tx_hash="0xreview-state",
            block_reference="0xblock",
            confirmation_count=10,
            finality_status="final",
            success=True,
            observed_at=datetime.now(timezone.utc),
            source="test-indexer",
            routing_mode="manual_shared",
            routing_reference=None,
        )
        first = service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=event,
            actor="worker",
        )
        second = service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=event,
            actor="worker",
        )
        self.assertEqual(first["status"], "review_required")
        self.assertEqual(second["status"], "review_required")
        self.assertIsNone(second["entitlement"])

    def test_expire_invoices_changes_only_open_states_and_audits(self):
        plan_code = "expiration-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())
        self._insert_payment_plan(
            plan_code=plan_code,
            price_id=price_id,
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=1_000_000,
            asset_decimals=6,
            destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            routing_mode="xrp_destination_tag",
        )
        service = SubscriptionBillingService(
            self.store,
            policies={"xrpl": ConfirmationPolicy(
                required_confirmations=1,
                require_finality=True,
            )},
        )
        invoice = service.create_invoice(
            customer_ref="expiration-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="expiration-key",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            actor="billing-test",
        )
        changed = service.expire_invoices(
            actor="expiry-worker",
            now=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        self.assertEqual(changed, 1)
        snapshot = service.get_invoice(
            invoice_id=invoice.invoice_id,
            customer_ref="expiration-customer",
        )
        self.assertEqual(snapshot["status"], "expired")
        with self.store._pool.connection() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM billing_audit_log
                WHERE object_id = %s
                  AND action = 'INVOICE_EXPIRED'
                """,
                (invoice.invoice_id,),
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_xrp_invoice_idempotent_replay_keeps_generated_tag(self):
        plan_code = "xrp-replay-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())
        destination = "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2"
        self._insert_payment_plan(
            plan_code=plan_code,
            price_id=price_id,
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=2_500_000,
            asset_decimals=6,
            destination=destination,
            routing_mode="xrp_destination_tag",
        )

        first = SubscriptionBillingService(
            self.store,
            policies={"xrpl": ConfirmationPolicy(
                required_confirmations=1,
                require_finality=True,
            )},
        ).create_invoice(
            customer_ref="xrp-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="xrp-replay-key",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            actor="test-billing",
        )
        second = SubscriptionBillingService(
            self.store,
            policies={"xrpl": ConfirmationPolicy(
                required_confirmations=1,
                require_finality=True,
            )},
        ).create_invoice(
            customer_ref="xrp-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="xrp-replay-key",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            actor="test-billing",
        )

        self.assertEqual(second.invoice_id, first.invoice_id)
        self.assertEqual(second.routing_mode, "xrp_destination_tag")
        self.assertEqual(second.routing_reference, first.routing_reference)
        self.assertIsNotNone(first.routing_reference)

    def test_final_payment_status_cannot_downgrade_on_stale_observation(self):
        service = SubscriptionBillingService(
            self.store,
            policies={"ethereum": ConfirmationPolicy(
                required_confirmations=0,
                require_finality=True,
            )},
        )
        plan_code = "finality-" + uuid.uuid4().hex[:12]
        price_id = str(uuid.uuid4())
        self._insert_payment_plan(
            plan_code=plan_code,
            price_id=price_id,
            asset_code="ETH",
            network="ethereum",
            asset_kind="native",
            amount_atomic=10**15,
            asset_decimals=18,
            destination="0x1111111111111111111111111111111111111111",
            routing_mode="unique_destination",
        )
        invoice = service.create_invoice(
            customer_ref="finality-customer",
            plan_code=plan_code,
            price_id=price_id,
            client_idempotency_key="finality-key",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            actor="test-billing",
            settlement_destination="0x2222222222222222222222222222222222222222",
            settlement_routing_mode="unique_destination",
        )

        observation = PaymentObservation(
            network="ethereum",
            asset_code="ETH",
            asset_kind="native",
            destination=invoice.destination,
            amount_atomic=10**15,
            chain_event_key=chain_event_key(
                network="ethereum",
                tx_hash="0xfinality",
                asset_kind="native",
            ),
            tx_hash="0xfinality",
            block_reference="0xblock-final",
            confirmation_count=12,
            finality_status="final",
            success=True,
            observed_at=datetime.now(timezone.utc),
            source="test-indexer",
            routing_mode="unique_destination",
            routing_reference=None,
        )
        service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=observation,
            actor="test-indexer",
        )

        stale = PaymentObservation(
            **{
                **observation.__dict__,
                "block_reference": "0xblock-stale",
                "confirmation_count": 3,
                "finality_status": "confirmed",
                "observed_at": datetime.now(timezone.utc),
            }
        )
        result = service.settle_observation(
            invoice_id=invoice.invoice_id,
            observation=stale,
            actor="stale-indexer",
        )
        self.assertEqual(result["status"], "paid")

        with self.store._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT finality_status, confirmation_count
                FROM payment_events
                WHERE chain_event_key = %s
                """,
                (observation.chain_event_key,),
            ).fetchone()
        self.assertEqual(row["finality_status"], "final")
        self.assertEqual(row["confirmation_count"], 12)

    def test_idempotency_key_reuse_with_different_fingerprint_conflicts(self):
        bundle = self.make_bundle()
        self.store.ingest_bundle(
            bundle,
            actor="postgres-conflict",
            idempotency_key="same-key",
            request_sha256="b" * 64,
            ingestion_id="33333333-3333-4333-8333-333333333333",
        )
        with self.assertRaises(ConflictError):
            self.store.ingest_bundle(
                bundle,
                actor="postgres-conflict",
                idempotency_key="same-key",
                request_sha256="c" * 64,
                ingestion_id="44444444-4444-4444-8444-444444444444",
            )


if __name__ == "__main__":
    unittest.main()
