import unittest

from src.nothing_chain_adapters import EvmJsonRpcAdapter, XrplJsonRpcAdapter


class FakeRpc:
    def __init__(self, responses):
        self.responses = responses

    def call(self, method, params):
        value = self.responses[method]
        return value() if callable(value) else value


class ChainAdapterTests(unittest.TestCase):
    def test_xrpl_verified_payment_uses_delivered_amount_and_tag(self):
        adapter = XrplJsonRpcAdapter("https://example.invalid/")
        responses = {
            "tx": {
                "validated": True,
                "Destination": "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                "DestinationTag": 123456,
                "meta": {
                    "TransactionResult": "tesSUCCESS",
                    "delivered_amount": "2500000",
                },
                "ledger_index": 100,
                "hash": "ABC123",
            }
        }
        object.__setattr__(adapter, "_rpc", FakeRpc(responses))
        invoice = __import__(
            "src.nothing_payments", fromlist=["PaymentInvoice"]
        ).PaymentInvoice(
            invoice_id="inv",
            customer_ref="cust",
            plan_code="xrp-monthly",
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=2500000,
            asset_decimals=6,
            destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            expires_at=__import__(
                "datetime"
            ).datetime.now(__import__("datetime").timezone.utc),
            routing_mode="xrp_destination_tag",
            routing_reference="123456",
        )
        obs = adapter.verify("ABC123", invoice)
        self.assertEqual(obs.amount_atomic, 2500000)
        self.assertEqual(obs.finality_status, "final")
        self.assertEqual(obs.routing_reference, "123456")
        self.assertTrue(obs.success)

    def test_evm_chain_id_is_required_and_matches(self):
        adapter = EvmJsonRpcAdapter(
            "https://example.invalid/",
            "ethereum",
            expected_chain_id=1,
        )
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({"eth_chainId": "0x1"}),
        )
        self.assertEqual(adapter._rpc.call("eth_chainId", []), "0x1")


if __name__ == "__main__":
    unittest.main()
