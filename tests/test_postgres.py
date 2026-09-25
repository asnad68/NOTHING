import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.nothing_postgres import PostgreSQLNothingStore
from src.nothing_store import NotFoundError

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
        self.assertEqual(row["version"], 3)

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

    def test_idempotency_key_reuse_with_different_fingerprint_conflicts(self):
        bundle = self.make_bundle()
        self.store.ingest_bundle(
            bundle,
            actor="postgres-conflict",
            idempotency_key="same-key",
            request_sha256="b" * 64,
            ingestion_id="33333333-3333-4333-8333-333333333333",
        )
        with self.assertRaises(Exception):
            self.store.ingest_bundle(
                bundle,
                actor="postgres-conflict",
                idempotency_key="same-key",
                request_sha256="c" * 64,
                ingestion_id="44444444-4444-4444-8444-444444444444",
            )


if __name__ == "__main__":
    unittest.main()
