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
        self.assertEqual(row["version"], 6)

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
