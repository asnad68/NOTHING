import unittest
from datetime import datetime, timezone

from src.nothing_chain_adapters import XrplDiscoveryResult
from src.nothing_payments import PaymentObservation
from src.nothing_xrpl_worker import run_once


class FakeStore:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.saved = []

    def get_payment_worker_checkpoint(self, worker_name, account):
        return self.checkpoint

    def set_payment_worker_checkpoint(
        self,
        worker_name,
        account,
        *,
        last_tx_hash,
        last_ledger_index,
    ):
        self.saved.append(
            (
                worker_name,
                account,
                last_tx_hash,
                last_ledger_index,
            )
        )


class FakeService:
    def __init__(self, store, *, fail=False):
        self.store = store
        self.fail = fail
        self.settled = []
        self.unmatched = []

    def settle_discovered_observation(self, *, observation, actor):
        if self.fail:
            raise RuntimeError("settlement failure")
        self.settled.append((observation.tx_hash, actor))
        return {"invoice_id": "invoice-1"} if observation.tx_hash == "MATCHED" else None

    def record_unmatched_observation(self, *, observation, actor):
        self.unmatched.append((observation.tx_hash, actor))


class FakeAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def discover_recent_payments_with_checkpoint(
        self,
        *,
        account,
        limit,
        stop_after_tx_hash,
        max_pages,
    ):
        self.calls.append(
            (account, limit, stop_after_tx_hash, max_pages)
        )
        return self.result


def observation(tx_hash):
    return PaymentObservation(
        network="xrpl",
        asset_code="XRP",
        asset_kind="xrp",
        destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
        amount_atomic=1_000_000,
        chain_event_key=f"xrpl:{tx_hash}",
        tx_hash=tx_hash,
        block_reference="100",
        confirmation_count=1,
        finality_status="final",
        success=True,
        observed_at=datetime.now(timezone.utc),
        source="test",
        routing_mode="xrp_destination_tag",
        routing_reference="7",
    )


class XrplWorkerTests(unittest.TestCase):
    def test_run_once_persists_checkpoint_after_all_observations(self):
        store = FakeStore()
        service = FakeService(store)
        adapter = FakeAdapter(
            XrplDiscoveryResult(
                observations=(observation("MATCHED"), observation("UNMATCHED")),
                newest_tx_hash="MATCHED",
                checkpoint_tx_hash="CHECKPOINT",
                checkpoint_ledger_index=99,
                reached_checkpoint=True,
            )
        )

        settled = run_once(
            adapter,
            service,
            account="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            actor="worker",
            worker_name="xrpl-test",
        )

        self.assertEqual(settled, 1)
        self.assertEqual(service.settled, [("MATCHED", "worker"), ("UNMATCHED", "worker")])
        self.assertEqual(service.unmatched, [("UNMATCHED", "worker")])
        self.assertEqual(
            store.saved,
            [(
                "xrpl-test",
                "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                "CHECKPOINT",
                99,
            )],
        )
        self.assertEqual(
            adapter.calls,
            [(
                "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                200,
                None,
                20,
            )],
        )

    def test_run_once_does_not_advance_checkpoint_when_settlement_fails(self):
        store = FakeStore()
        service = FakeService(store, fail=True)
        adapter = FakeAdapter(
            XrplDiscoveryResult(
                observations=(observation("MATCHED"),),
                newest_tx_hash="MATCHED",
                checkpoint_tx_hash="CHECKPOINT",
                checkpoint_ledger_index=99,
                reached_checkpoint=True,
            )
        )

        with self.assertRaises(RuntimeError):
            run_once(
                adapter,
                service,
                account="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                actor="worker",
                worker_name="xrpl-test",
            )

        self.assertEqual(store.saved, [])


if __name__ == "__main__":
    unittest.main()
