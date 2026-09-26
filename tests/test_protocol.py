import copy
import unittest
from pathlib import Path

from src.nothing_protocol import (
    RelationshipError,
    resolve_claim_relationships,
)

from src.nothing_verify import load_json

ROOT = Path(__file__).resolve().parents[1]


class ProtocolRelationshipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = load_json(ROOT / "examples/NTH-000001.json")
        self.evidence = [load_json(ROOT / "examples/EVD-000001.json")]
        self.event = load_json(ROOT / "examples/VER-000001.json")
        self.registry = load_json(ROOT / "procedures/registry.json")

    def resolve(self, events=None, evidence=None, identity=None):
        return resolve_claim_relationships(
            identity or self.identity,
            evidence if evidence is not None else self.evidence,
            events if events is not None else [self.event],
            self.registry,
        )

    def test_fixture_relationship_resolves(self) -> None:
        result = self.resolve()
        self.assertEqual(result["nothing_id"], "NTH-000001")
        claim = result["claims"]["CLM-000001"]
        self.assertEqual(claim["evidence_ids"], ["EVD-000001"])
        self.assertEqual(claim["current_event_id"], "VER-000001")
        self.assertEqual(claim["current_status"], "SOURCE-VERIFIED")
        self.assertEqual(claim["status_consistency"], "CONSISTENT")
        self.assertEqual(result["unreferenced_evidence"], [])

    def test_unknown_evidence_reference_is_rejected(self) -> None:
        event = copy.deepcopy(self.event)
        event["evidence"] = ["EVD-999999"]
        with self.assertRaises(RelationshipError):
            self.resolve(events=[event])

    def test_unknown_procedure_is_rejected(self) -> None:
        event = copy.deepcopy(self.event)
        event["procedure"]["id"] = "NOTHING-NOT-REGISTERED"
        with self.assertRaises(RelationshipError):
            self.resolve(events=[event])

    def test_draft_procedure_cannot_be_used_for_an_event(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["procedures"][0]["status"] = "DRAFT"
        with self.assertRaises(RelationshipError):
            resolve_claim_relationships(
                self.identity,
                self.evidence,
                [self.event],
                registry,
            )

    def test_procedure_result_allow_list_is_enforced(self) -> None:
        event = copy.deepcopy(self.event)
        event["result"]["status"] = "VERIFIED"
        with self.assertRaises(RelationshipError):
            self.resolve(events=[event])

    def test_event_must_belong_to_identity_subject(self) -> None:
        event = copy.deepcopy(self.event)
        event["subject"] = "NTH-999999"
        with self.assertRaises(RelationshipError):
            self.resolve(events=[event])

    def test_second_event_supersedes_first_and_becomes_current(self) -> None:
        second = copy.deepcopy(self.event)
        second["event_id"] = "VER-000002"
        second["occurred_at"] = "2026-09-26T00:00:00Z"
        second["supersedes"] = "VER-000001"
        events = [self.event, second]

        result = self.resolve(events=events)
        claim = result["claims"]["CLM-000001"]
        self.assertEqual(claim["verification_event_ids"], ["VER-000001", "VER-000002"])
        self.assertEqual(claim["current_event_id"], "VER-000002")

    def test_supersession_cannot_go_backwards_in_time(self) -> None:
        second = copy.deepcopy(self.event)
        second["event_id"] = "VER-000002"
        second["occurred_at"] = "2026-09-24T00:00:00Z"
        second["supersedes"] = "VER-000001"
        with self.assertRaises(RelationshipError):
            self.resolve(events=[self.event, second])

    def test_a_claim_cannot_have_two_current_events_in_v0_1(self) -> None:
        second = copy.deepcopy(self.event)
        second["event_id"] = "VER-000002"
        second["occurred_at"] = "2026-09-26T00:00:00Z"
        with self.assertRaises(RelationshipError):
            self.resolve(events=[self.event, second])

    def test_parallel_supersession_is_rejected(self) -> None:
        second = copy.deepcopy(self.event)
        second["event_id"] = "VER-000002"
        second["occurred_at"] = "2026-09-26T00:00:00Z"
        second["supersedes"] = "VER-000001"

        third = copy.deepcopy(self.event)
        third["event_id"] = "VER-000003"
        third["occurred_at"] = "2026-09-27T00:00:00Z"
        third["supersedes"] = "VER-000001"

        with self.assertRaises(RelationshipError):
            self.resolve(events=[self.event, second, third])

    def test_supersession_cycle_is_rejected(self) -> None:
        first = copy.deepcopy(self.event)
        first["event_id"] = "VER-000010"
        first["occurred_at"] = "2026-09-26T00:00:00Z"
        first["supersedes"] = "VER-000011"

        second = copy.deepcopy(self.event)
        second["event_id"] = "VER-000011"
        second["occurred_at"] = "2026-09-26T00:00:00Z"
        second["supersedes"] = "VER-000010"

        with self.assertRaises(RelationshipError):
            self.resolve(events=[first, second])

    def test_unreferenced_evidence_is_reported_not_rejected(self) -> None:
        extra = copy.deepcopy(self.evidence[0])
        extra["evidence_id"] = "EVD-000002"
        result = self.resolve(evidence=[self.evidence[0], extra])
        self.assertEqual(result["unreferenced_evidence"], ["EVD-000002"])

    def test_duplicate_procedure_registration_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["procedures"].append(copy.deepcopy(registry["procedures"][0]))
        with self.assertRaises(RelationshipError):
            resolve_claim_relationships(
                self.identity,
                self.evidence,
                [self.event],
                registry,
            )


if __name__ == "__main__":
    unittest.main()
