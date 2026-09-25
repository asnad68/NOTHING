import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.nothing_store import ConflictError, SQLiteNothingStore

ROOT = Path(__file__).resolve().parents[1]


class SQLiteNothingStoreTests(unittest.TestCase):
    def make_store(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.tempdir = tempdir
        return SQLiteNothingStore(Path(tempdir.name) / "nothing.db")

    def load_fixture_bundle(self):
        return {
            "identity": json.loads(
                (ROOT / "examples/NTH-000001.json").read_text(encoding="utf-8")
            ),
            "evidence": json.loads(
                (ROOT / "examples/EVD-000001.json").read_text(encoding="utf-8")
            ),
            "event": json.loads(
                (ROOT / "examples/VER-000001.json").read_text(encoding="utf-8")
            ),
            "procedure": json.loads(
                (ROOT / "procedures/registry.json").read_text(encoding="utf-8")
            )["procedures"][0],
        }

    def seed(self, store):
        bundle = self.load_fixture_bundle()
        store.put_procedure(bundle["procedure"], actor="test")
        store.put_identity(bundle["identity"], actor="test")
        store.put_evidence(bundle["evidence"], actor="test")
        store.put_event(bundle["event"], actor="test")

    def test_migration_and_import_are_durable(self):
        store = self.make_store()
        result = store.import_json_bundle(ROOT, actor="test-import")

        self.assertEqual(result["procedures_inserted"], 1)
        self.assertEqual(result["identities_added"], 1)
        self.assertEqual(result["evidence_inserted"], 1)
        self.assertEqual(result["events_inserted"], 1)

        identity = store.get_identity("NTH-000001")
        self.assertEqual(identity.revision, 1)
        self.assertEqual(identity.record["nothing_id"], "NTH-000001")

        store.close()
        reopened = SQLiteNothingStore(Path(self.tempdir.name) / "nothing.db")
        self.assertTrue(reopened.health())
        reopened_identity = reopened.get_identity("NTH-000001")
        self.assertEqual(reopened_identity.content_sha256, identity.content_sha256)
        reopened.close()

    def test_identical_identity_is_idempotent_and_changes_create_revision(self):
        store = self.make_store()
        bundle = self.load_fixture_bundle()
        self.assertEqual(store.put_identity(bundle["identity"], actor="test"), 1)
        self.assertEqual(store.put_identity(bundle["identity"], actor="test"), 1)

        changed = copy.deepcopy(bundle["identity"])
        changed["subject"]["name"] = "NOTHING Experimental Identity v2"
        revision = store.put_identity(changed, actor="test")
        self.assertEqual(revision, 2)
        self.assertEqual(store.get_identity("NTH-000001").revision, 2)
        self.assertEqual(
            store.get_identity_revision("NTH-000001", 1).record["subject"]["name"],
            "NOTHING Experimental Identity",
        )

    def test_immutable_records_reject_changed_replacement(self):
        store = self.make_store()
        bundle = self.load_fixture_bundle()
        store.put_procedure(bundle["procedure"], actor="test")
        store.put_identity(bundle["identity"], actor="test")
        store.put_evidence(bundle["evidence"], actor="test")
        store.put_event(bundle["event"], actor="test")

        changed_evidence = copy.deepcopy(bundle["evidence"])
        changed_evidence["notes"] = "changed"
        with self.assertRaises(ConflictError):
            store.put_evidence(changed_evidence, actor="test")

        changed_event = copy.deepcopy(bundle["event"])
        changed_event["result"]["reason"] = "changed"
        with self.assertRaises(ConflictError):
            store.put_event(changed_event, actor="test")

        changed_procedure = copy.deepcopy(bundle["procedure"])
        changed_procedure["title"] = "changed"
        with self.assertRaises(ConflictError):
            store.put_procedure(changed_procedure, actor="test")

    def test_append_only_triggers_reject_direct_mutation(self):
        store = self.make_store()
        self.seed(store)

        with sqlite3.connect(store.db_path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE evidence SET payload_json = payload_json "
                    "WHERE evidence_id = 'EVD-000001'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM verification_events "
                    "WHERE event_id = 'VER-000001'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE identity_revisions SET payload_json = payload_json "
                    "WHERE nothing_id = 'NTH-000001' AND revision = 1"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_log")

    def test_bundle_resolution_is_persistent_and_scoped(self):
        store = self.make_store()
        self.seed(store)
        bundle = store.get_identity_bundle("NTH-000001")

        self.assertEqual(bundle.identity.record["nothing_id"], "NTH-000001")
        self.assertEqual(
            [item["evidence_id"] for item in bundle.evidence],
            ["EVD-000001"],
        )
        self.assertEqual(
            [item.record["event_id"] for item in bundle.events],
            ["VER-000001"],
        )
        self.assertEqual(
            bundle.events[0].record["procedure"]["id"],
            "NOTHING-BASIC-SOURCE-CHECK",
        )
        self.assertTrue(bundle.last_modified.endswith("Z"))



    def test_authenticated_ingestion_is_idempotent_and_persistent(self):
        store = self.make_store()
        procedure = self.load_fixture_bundle()["procedure"]
        store.put_procedure(procedure, actor="store-test")
        bundle = {
            "identities": [{
                "nothing_id": "NTH-222222",
                "version": "0.1",
                "subject": {
                    "name": "Atomic Store Test",
                    "type": "business",
                },
                "claims": [{
                    "claim_id": "CLM-222222",
                    "statement": "A source-backed test claim",
                    "status": "SOURCE-VERIFIED",
                    "source": {
                        "type": "official_website",
                        "reference": "https://store-test.example",
                        "checked_at": "2026-09-25T02:00:00Z",
                    },
                }],
                "revocation": {"status": "NOT_REVOKED"},
            }],
            "evidence": [{
                "evidence_id": "EVD-222222",
                "version": "0.1",
                "type": "web_page",
                "source": {
                    "reference": "https://store-test.example",
                    "accessed_at": "2026-09-25T02:00:00Z",
                },
                "collected_at": "2026-09-25T02:00:00Z",
                "integrity": {"method": "none"},
            }],
            "verification_events": [{
                "event_id": "VER-222222",
                "version": "0.1",
                "occurred_at": "2026-09-25T02:00:00Z",
                "subject": "NTH-222222",
                "claim_id": "CLM-222222",
                "procedure": {
                    "id": "NOTHING-BASIC-SOURCE-CHECK",
                    "version": "0.1",
                },
                "verifier": {
                    "type": "hybrid",
                    "identifier": "store-test",
                },
                "evidence": ["EVD-222222"],
                "result": {
                    "status": "SOURCE-VERIFIED",
                    "scope": "Store ingestion test scope",
                },
            }],
        }

        result = store.ingest_bundle(
            bundle,
            actor="store-test",
            idempotency_key="store-key",
            request_sha256="1" * 64,
            ingestion_id="ing-222222",
            recorded_at="2026-09-25T02:00:00Z",
        )
        self.assertFalse(result.replayed)
        self.assertEqual(result.data["ingestion_id"], "ing-222222")

        replay = store.ingest_bundle(
            bundle,
            actor="store-test",
            idempotency_key="store-key",
            request_sha256="1" * 64,
            ingestion_id="ing-should-not-win",
            recorded_at="2026-09-25T02:01:00Z",
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.data["ingestion_id"], "ing-222222")
        self.assertEqual(store.get_identity("NTH-222222").revision, 1)

        with sqlite3.connect(store.db_path) as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(version, 2)

if __name__ == "__main__":
    unittest.main()
