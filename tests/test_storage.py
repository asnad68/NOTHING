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


if __name__ == "__main__":
    unittest.main()
