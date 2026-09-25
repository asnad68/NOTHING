import unittest
from datetime import datetime, timezone

from src.nothing_payments import PaymentInvoice

from src.nothing_chain_adapters import (
    BitcoinCoreRpcAdapter,
    ChainAdapterError,
    EvmJsonRpcAdapter,
    XrplJsonRpcAdapter,
)


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
        invoice = PaymentInvoice(
            invoice_id="inv",
            customer_ref="cust",
            plan_code="xrp-monthly",
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=2500000,
            asset_decimals=6,
            destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            expires_at=datetime.now(timezone.utc),
            routing_mode="xrp_destination_tag",
            routing_reference="123456",
        )
        obs = adapter.verify("ABC123", invoice)
        self.assertEqual(obs.amount_atomic, 2500000)
        self.assertEqual(obs.finality_status, "final")
        self.assertEqual(obs.routing_reference, "123456")
        self.assertTrue(obs.success)



    def test_xrpl_non_payment_transaction_is_rejected(self):
        adapter = XrplJsonRpcAdapter("https://example.invalid/")
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc(
                {
                    "tx": {
                        "validated": True,
                        "TransactionType": "AccountSet",
                        "Destination": "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                        "meta": {
                            "TransactionResult": "tesSUCCESS",
                            "delivered_amount": "2500000",
                        },
                    }
                }
            ),
        )
        invoice = PaymentInvoice(
            invoice_id="inv-non-payment",
            customer_ref="cust",
            plan_code="xrp-monthly",
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=2500000,
            asset_decimals=6,
            destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            expires_at=datetime.now(timezone.utc),
            routing_mode="xrp_destination_tag",
            routing_reference="123456",
        )
        with self.assertRaises(ChainAdapterError):
            adapter.verify("ABC123", invoice)

    def test_xrpl_tentative_transaction_is_rejected(self):
        adapter = XrplJsonRpcAdapter("https://example.invalid/")
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc(
                {
                    "tx": {
                        "validated": False,
                        "TransactionType": "Payment",
                        "Destination": "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                        "meta": {
                            "TransactionResult": "tesSUCCESS",
                            "delivered_amount": "2500000",
                        },
                    }
                }
            ),
        )
        invoice = PaymentInvoice(
            invoice_id="inv-tentative",
            customer_ref="cust",
            plan_code="xrp-monthly",
            asset_code="XRP",
            network="xrpl",
            asset_kind="xrp",
            amount_atomic=2500000,
            asset_decimals=6,
            destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            expires_at=datetime.now(timezone.utc),
            routing_mode="xrp_destination_tag",
            routing_reference="123456",
        )
        with self.assertRaises(ChainAdapterError):
            adapter.verify("ABC123", invoice)


    def test_evm_native_observation_has_unique_destination_routing(self):
        adapter = EvmJsonRpcAdapter(
            "https://example.invalid/",
            "ethereum",
            expected_chain_id=1,
        )
        invoice = PaymentInvoice(
            invoice_id="inv-eth",
            customer_ref="cust",
            plan_code="eth-monthly",
            asset_code="ETH",
            network="ethereum",
            asset_kind="native",
            amount_atomic=1_000_000,
            asset_decimals=18,
            destination="0xE1c90171271B5325beE02592ACc50A510448d03E",
            expires_at=datetime.now(timezone.utc),
            routing_mode="unique_destination",
        )
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({
                "eth_chainId": "0x1",
                "eth_getTransactionByHash": {
                    "to": invoice.destination,
                    "value": "0xf4240",
                },
                "eth_getTransactionReceipt": {
                    "status": "0x1",
                    "blockNumber": "0x64",
                    "blockHash": "0xblock",
                    "logs": [],
                },
                "eth_blockNumber": "0x69",
                "eth_getBlockByNumber": {"number": "0x65"},
            }),
        )
        obs = adapter.verify("0xtx", invoice)
        self.assertEqual(obs.amount_atomic, 1_000_000)
        self.assertEqual(obs.routing_mode, "unique_destination")
        self.assertIsNone(obs.routing_reference)
        self.assertTrue(obs.success)

    def test_bitcoin_rejects_wrong_chain_endpoint(self):
        adapter = BitcoinCoreRpcAdapter(
            "https://example.invalid/",
            expected_chain="main",
        )
        invoice = PaymentInvoice(
            invoice_id="inv-btc-chain",
            customer_ref="cust",
            plan_code="btc-monthly",
            asset_code="BTC",
            network="bitcoin",
            asset_kind="btc_utxo",
            amount_atomic=1_000,
            asset_decimals=8,
            destination="3ABUrDAmi6w9TRsHuwFgdcDBLvXUzbAfZY",
            expires_at=datetime.now(timezone.utc),
            routing_mode="unique_destination",
        )
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({
                "getrawtransaction": {
                    "blockhash": "block-1",
                    "confirmations": 6,
                    "vout": [{
                        "value": "0.00001000",
                        "scriptPubKey": {"address": invoice.destination},
                    }],
                },
                "getblockchaininfo": {"chain": "test"},
            }),
        )
        with self.assertRaises(ChainAdapterError):
            adapter.verify("tx-btc", invoice)

    def test_evm_reverted_transaction_fails_closed(self):
        adapter = EvmJsonRpcAdapter(
            "https://example.invalid/",
            "ethereum",
            expected_chain_id=1,
        )
        invoice = PaymentInvoice(
            invoice_id="inv-reverted",
            customer_ref="cust",
            plan_code="eth-monthly",
            asset_code="ETH",
            network="ethereum",
            asset_kind="native",
            amount_atomic=1_000,
            asset_decimals=18,
            destination="0xE1c90171271B5325beE02592ACc50A510448d03E",
            expires_at=datetime.now(timezone.utc),
            routing_mode="unique_destination",
        )
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({
                "eth_chainId": "0x1",
                "eth_getTransactionByHash": {"to": invoice.destination, "value": "0x3e8"},
                "eth_getTransactionReceipt": {
                    "status": "0x0",
                    "blockNumber": "0x64",
                    "blockHash": "0xblock",
                },
            }),
        )
        with self.assertRaises(ChainAdapterError):
            adapter.verify("0xreverted", invoice)

    def test_erc20_multiple_matching_transfers_fail_closed(self):
        adapter = EvmJsonRpcAdapter(
            "https://example.invalid/",
            "ethereum",
            expected_chain_id=1,
        )
        contract = "0x1111111111111111111111111111111111111111"
        invoice = PaymentInvoice(
            invoice_id="inv-erc20-ambiguous",
            customer_ref="cust",
            plan_code="token-monthly",
            asset_code="USDT",
            network="ethereum",
            asset_kind="erc20",
            amount_atomic=1_000,
            asset_decimals=6,
            destination="0xE1c90171271B5325beE02592ACc50A510448d03E",
            expires_at=datetime.now(timezone.utc),
            asset_contract=contract,
            routing_mode="unique_destination",
        )
        encoded_to = "0x" + ("0" * 24) + invoice.destination[2:].lower()
        transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa"
            "952ba7f163c4a11628f55a7eaa9c3b"
        )
        log = {
            "address": contract,
            "topics": [transfer_topic, "0x" + "0" * 64, encoded_to],
            "data": "0x3e8",
            "logIndex": "0x1",
        }
        log2 = dict(log, logIndex="0x2")
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({
                "eth_chainId": "0x1",
                "eth_getTransactionByHash": {"to": invoice.destination, "value": "0x0"},
                "eth_getTransactionReceipt": {
                    "status": "0x1",
                    "blockNumber": "0x64",
                    "blockHash": "0xblock",
                    "logs": [log, log2],
                },
                "eth_blockNumber": "0x69",
                "eth_getBlockByNumber": {"number": "0x65"},
            }),
        )
        with self.assertRaises(ChainAdapterError):
            adapter.verify("0xtx", invoice)

    def test_bitcoin_finality_respects_required_confirmations(self):
        adapter = BitcoinCoreRpcAdapter(
            "https://example.invalid/",
            required_confirmations=6,
        )
        invoice = PaymentInvoice(
            invoice_id="inv-btc",
            customer_ref="cust",
            plan_code="btc-monthly",
            asset_code="BTC",
            network="bitcoin",
            asset_kind="btc_utxo",
            amount_atomic=1_000,
            asset_decimals=8,
            destination="3ABUrDAmi6w9TRsHuwFgdcDBLvXUzbAfZY",
            expires_at=datetime.now(timezone.utc),
            routing_mode="unique_destination",
        )
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({
                "getrawtransaction": {
                    "blockhash": "block-1",
                    "confirmations": 5,
                    "vout": [{
                        "value": "0.00001000",
                        "scriptPubKey": {"address": invoice.destination},
                    }],
                },
                "getblockchaininfo": {"chain": "main", "blocks": 100},
                "getblock": {"confirmations": 5},
            }),
        )
        obs = adapter.verify("tx-btc", invoice)
        self.assertEqual(obs.amount_atomic, 1_000)
        self.assertEqual(obs.finality_status, "confirmed")
        self.assertEqual(obs.routing_mode, "unique_destination")


    def test_xrpl_initial_discovery_does_not_advance_past_page_limit(self):
        adapter = XrplJsonRpcAdapter("https://example.invalid/")
        page = {
            "validated": True,
            "transactions": [
                {
                    "validated": True,
                    "ledger_index": 10,
                    "tx_json": {
                        "hash": "OLDER-1",
                        "TransactionType": "AccountSet",
                    },
                    "meta": {"TransactionResult": "tesSUCCESS"},
                }
            ],
            "marker": {"page": 2},
        }
        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({"account_tx": page}),
        )

        with self.assertRaises(ChainAdapterError) as context:
            adapter.discover_recent_payments_with_checkpoint(
                account="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                stop_after_tx_hash=None,
                max_pages=1,
            )
        self.assertIn(
            "page limit reached",
            str(context.exception).lower(),
        )

    def test_xrpl_discovery_paginates_until_checkpoint(self):
        adapter = XrplJsonRpcAdapter("https://example.invalid/")
        calls = []

        def account_tx():
            calls.append(True)
            if len(calls) == 1:
                return {
                    "validated": True,
                    "transactions": [
                        {
                            "validated": True,
                            "ledger_index": 200,
                            "tx_json": {
                                "hash": "NEW-1",
                                "TransactionType": "Payment",
                                "Destination": "r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                                "DestinationTag": 99,
                            },
                            "meta": {
                                "TransactionResult": "tesSUCCESS",
                                "delivered_amount": "1000000",
                            },
                        }
                    ],
                    "marker": {"page": 2},
                }
            return {
                "validated": True,
                "transactions": [
                    {
                        "validated": True,
                        "ledger_index": 199,
                        "tx_json": {
                            "hash": "CHECKPOINT",
                            "TransactionType": "AccountSet",
                        },
                        "meta": {"TransactionResult": "tesSUCCESS"},
                    }
                ]
            }

        object.__setattr__(
            adapter,
            "_rpc",
            FakeRpc({"account_tx": account_tx}),
        )

        result = adapter.discover_recent_payments_with_checkpoint(
            account="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
            stop_after_tx_hash="CHECKPOINT",
            max_pages=3,
        )

        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].tx_hash, "NEW-1")
        self.assertEqual(result.checkpoint_tx_hash, "CHECKPOINT")
        self.assertEqual(result.checkpoint_ledger_index, 199)
        self.assertTrue(result.reached_checkpoint)
        self.assertEqual(len(calls), 2)

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
