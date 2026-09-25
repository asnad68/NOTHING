import unittest
from datetime import datetime, timedelta, timezone

from src.nothing_payments import (
    PaymentInvoice,
    PaymentObservation,
    chain_event_key,
    classify_invoice,
)


class PaymentDomainTests(unittest.TestCase):
    def invoice(self, **overrides):
        value = {
            "invoice_id": "inv-001",
            "customer_ref": "customer-001",
            "plan_code": "pro-monthly",
            "asset_code": "ETH",
            "network": "ethereum",
            "asset_kind": "native",
            "amount_atomic": 10**15,
            "asset_decimals": 18,
            "destination": "0xE1c90171271B5325beE02592ACc50A510448d03E",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
        value.update(overrides)
        return PaymentInvoice(**value)

    def observation(self, **overrides):
        value = {
            "network": "ethereum",
            "asset_code": "ETH",
            "asset_kind": "native",
            "destination": "0xE1c90171271B5325beE02592ACc50A510448d03E",
            "amount_atomic": 10**15,
            "chain_event_key": chain_event_key(
                network="ethereum",
                tx_hash="0xabc",
                asset_kind="native",
            ),
            "tx_hash": "0xabc",
            "block_reference": "finalized",
            "confirmation_count": 1,
            "finality_status": "final",
            "success": True,
            "observed_at": datetime.now(timezone.utc),
            "source": "trusted-evm-indexer",
        }
        value.update(overrides)
        return PaymentObservation(**value)

    def test_chain_event_keys_are_asset_specific(self):
        self.assertEqual(
            chain_event_key(
                network="ethereum",
                tx_hash="0xabc",
                asset_kind="native",
            ),
            "ethereum:0xabc",
        )
        self.assertEqual(
            chain_event_key(
                network="ethereum",
                tx_hash="0xabc",
                asset_kind="erc20",
                event_index=4,
            ),
            "ethereum:0xabc:log:4",
        )
        self.assertEqual(
            chain_event_key(
                network="bitcoin",
                tx_hash="abc",
                asset_kind="btc_utxo",
                event_index=2,
            ),
            "bitcoin:abc:vout:2",
        )

    def test_pending_payment_cannot_activate_entitlement(self):
        result = classify_invoice(
            self.invoice(),
            [self.observation(finality_status="pending", confirmation_count=0)],
            now=datetime.now(timezone.utc),
            required_confirmations=6,
            require_finality=True,
        )
        self.assertEqual(result.invoice_status, "confirming")
        self.assertFalse(result.eligible_for_entitlement)

    def test_underpayment_needs_more_money(self):
        result = classify_invoice(
            self.invoice(),
            [self.observation(amount_atomic=10**14)],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "underpaid")
        self.assertEqual(result.shortfall_atomic, 9 * 10**14)
        self.assertFalse(result.eligible_for_entitlement)

    def test_exact_payment_activates(self):
        result = classify_invoice(
            self.invoice(),
            [self.observation()],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "paid")
        self.assertTrue(result.eligible_for_entitlement)

    def test_overpayment_does_not_create_extra_time(self):
        result = classify_invoice(
            self.invoice(),
            [self.observation(amount_atomic=2 * 10**15)],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "overpaid")
        self.assertEqual(result.excess_atomic, 10**15)
        self.assertTrue(result.eligible_for_entitlement)

    def test_wrong_network_is_not_a_payment(self):
        result = classify_invoice(
            self.invoice(),
            [self.observation(network="ethereum-sepolia")],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "open")
        self.assertFalse(result.eligible_for_entitlement)

    def test_wrong_contract_is_not_a_payment(self):
        invoice = self.invoice(
            asset_code="USDT",
            asset_kind="erc20",
            asset_contract="0xToken",
        )
        result = classify_invoice(
            invoice,
            [self.observation(
                asset_code="USDT",
                asset_kind="erc20",
                asset_contract="0xOtherToken",
                amount_atomic=10**6,
            )],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "open")
        self.assertFalse(result.eligible_for_entitlement)

    def test_expired_invoice_never_auto_activates(self):
        invoice = self.invoice(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        result = classify_invoice(
            invoice,
            [self.observation()],
            now=datetime.now(timezone.utc),
            required_confirmations=0,
        )
        self.assertEqual(result.invoice_status, "expired")
        self.assertFalse(result.eligible_for_entitlement)


if __name__ == "__main__":
    unittest.main()
