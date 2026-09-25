import tempfile
import unittest
from pathlib import Path

from src.entitlement_core import EntitlementCore
from src.payment_core import Invoice, PaymentCore, PaymentState
from src.xrp_worker import SyntheticXrpTransaction, XrpWorker


class PaymentE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "payments.db")
        self.payments = PaymentCore(self.db)
        self.entitlements = EntitlementCore(self.db, period_days=30)
        self.worker = XrpWorker(self.payments, self.entitlements)
        self.payments.create_invoice(
            Invoice(
                "INV-TEST-000001", "CUST-1", "PRO-30",
                "XRPL_TESTNET", "XRP", "rTESTDEST", 10_000_000, 2
            )
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_payment_confirms_and_entitlement_is_created_once(self):
        tx1 = SyntheticXrpTransaction(
            "INV-TEST-000001", "TX-001", "rTESTDEST", 10_000_000, 1,
            observed_at="2026-09-25T18:00:00Z"
        )
        self.assertEqual(self.worker.process(tx1), PaymentState.CONFIRMING)

        tx2 = SyntheticXrpTransaction(
            "INV-TEST-000001", "TX-001", "rTESTDEST", 10_000_000, 2,
            observed_at="2026-09-25T18:01:00Z"
        )
        self.assertEqual(self.worker.process(tx2), PaymentState.CONFIRMED)
        payment = self.payments.get_payment("INV-TEST-000001")
        self.assertIsNotNone(payment)
        self.assertIsNotNone(
            self.entitlements.get_by_payment(payment["payment_id"])
        )

        self.assertEqual(self.worker.process(tx2), PaymentState.CONFIRMED)
        with self.payments._connect() as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0], 1
            )

    def test_wrong_network_is_rejected(self):
        tx = SyntheticXrpTransaction(
            "INV-TEST-000001", "TX-002", "rTESTDEST", 10_000_000, 9,
            network="XRPL_MAINNET"
        )
        self.assertEqual(
            self.worker.process(tx), PaymentState.WRONG_NETWORK
        )

    def test_same_tx_cannot_credit_two_invoices(self):
        self.payments.create_invoice(
            Invoice(
                "INV-TEST-000002", "CUST-2", "PRO-30",
                "XRPL_TESTNET", "XRP", "rTESTDEST", 10_000_000, 0
            )
        )
        self.assertEqual(
            self.worker.process(
                SyntheticXrpTransaction(
                    "INV-TEST-000001", "TX-SHARED", "rTESTDEST", 10_000_000, 2
                )
            ),
            PaymentState.CONFIRMED,
        )
        self.assertEqual(
            self.worker.process(
                SyntheticXrpTransaction(
                    "INV-TEST-000002", "TX-SHARED", "rTESTDEST", 10_000_000, 2
                )
            ),
            PaymentState.DUPLICATE,
        )


if __name__ == "__main__":
    unittest.main()
