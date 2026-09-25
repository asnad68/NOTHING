import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.nothing_api import build_server
from src.nothing_ingestion import BearerAuthenticator
from src.nothing_payments import PaymentInvoice
from src.nothing_store import SQLiteNothingStore


class FakeBillingService:
    def __init__(self):
        self.invoice = None
        self.last_customer_ref = None

    def create_invoice(self, **kwargs):
        self.last_customer_ref = kwargs["customer_ref"]
        if self.invoice is None:
            self.invoice = PaymentInvoice(
                invoice_id="11111111-1111-4111-8111-111111111111",
                customer_ref=kwargs["customer_ref"],
                plan_code=kwargs["plan_code"],
                asset_code="XRP",
                network="xrpl",
                asset_kind="xrp",
                amount_atomic=2_500_000,
                asset_decimals=6,
                destination="r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2",
                expires_at=kwargs["expires_at"],
                routing_mode="xrp_destination_tag",
                routing_reference="12345",
            )
        return self.invoice

    def get_invoice(self, *, invoice_id, customer_ref):
        if self.invoice is None:
            raise RuntimeError("missing invoice")
        if customer_ref != self.invoice.customer_ref:
            raise RuntimeError("wrong customer")
        return {
            "invoice_id": self.invoice.invoice_id,
            "customer_ref": self.invoice.customer_ref,
            "plan_code": self.invoice.plan_code,
            "asset_code": self.invoice.asset_code,
            "network": self.invoice.network,
            "asset_kind": self.invoice.asset_kind,
            "asset_contract": None,
            "amount_atomic": str(self.invoice.amount_atomic),
            "asset_decimals": self.invoice.asset_decimals,
            "destination": self.invoice.destination,
            "routing_mode": self.invoice.routing_mode,
            "routing_reference": self.invoice.routing_reference,
            "status": "open",
            "expires_at": self.invoice.expires_at,
            "paid_at": None,
            "received_atomic": "0",
            "shortfall_atomic": str(self.invoice.amount_atomic),
            "excess_atomic": "0",
            "entitlement": None,
        }

    def list_entitlements(self, *, customer_ref, active_only):
        if self.invoice is None or customer_ref != self.invoice.customer_ref:
            return []
        return [{
            "entitlement_id": "22222222-2222-4222-8222-222222222222",
            "invoice_id": self.invoice.invoice_id,
            "plan_code": self.invoice.plan_code,
            "status": "active",
            "starts_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
            "activated_at": datetime.now(timezone.utc),
        }]


class BillingApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteNothingStore(Path(self.tempdir.name) / "nothing.db")
        self.billing = FakeBillingService()
        self.billing_auth = BearerAuthenticator(
            "billing-token",
            actor="customer-principal",
            scope="nothing:billing",
        )
        self.ingestion_auth = BearerAuthenticator(
            "ingestion-token",
            actor="ingestor",
        )
        self.server = build_server(
            "127.0.0.1",
            0,
            store=self.store,
            auth_mode="static-bearer",
            billing_authenticator=self.billing_auth,
            billing_service=self.billing,
            ingestion_token="ingestion-token",
            ingestion_actor="ingestor",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.tempdir.cleanup()

    def request(self, method, path, *, token="billing-token", body=None, headers=None):
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        if body is not None:
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=hdrs)
            response = connection.getresponse()
            payload = response.read()
            return response, payload
        finally:
            connection.close()

    def post_invoice(self, payload, *, key="invoice-key", token="billing-token"):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.request(
            "POST",
            "/v1/billing/invoices",
            token=token,
            body=body,
            headers={"Idempotency-Key": key},
        )

    def test_create_invoice_does_not_accept_client_customer_or_destination(self):
        response, body = self.post_invoice(
            {
                "plan_code": "xrp-monthly",
                "price_id": "33333333-3333-4333-8333-333333333333",
                "customer_ref": "attacker",
                "destination": "attacker-destination",
            }
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(body)["code"], "INVALID_BILLING_REQUEST")
        self.assertIsNone(self.billing.invoice)

    def test_create_invoice_returns_serializable_payment_instructions(self):
        response, body = self.post_invoice({
            "plan_code": "xrp-monthly",
            "price_id": "33333333-3333-4333-8333-333333333333",
        })
        self.assertEqual(response.status, 200)
        payload = json.loads(body)
        data = payload["data"]
        self.assertEqual(data["invoice_id"], self.billing.invoice.invoice_id)
        self.assertEqual(data["amount"], "2.5")
        self.assertEqual(data["amount_atomic"], "2500000")
        self.assertEqual(data["routing_reference"], "12345")
        self.assertEqual(data["status"], "open")
        self.assertTrue(data["expires_at"].endswith("Z"))

    def test_billing_scope_is_required(self):
        response, body = self.post_invoice(
            {
                "plan_code": "xrp-monthly",
                "price_id": "33333333-3333-4333-8333-333333333333",
            },
            token="ingestion-token",
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(body)["code"], "UNAUTHORIZED")

    def test_invoice_and_entitlement_reads_are_customer_scoped(self):
        self.post_invoice({
            "plan_code": "xrp-monthly",
            "price_id": "33333333-3333-4333-8333-333333333333",
        })

        response, body = self.request(
            "GET",
            "/v1/billing/invoices/11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["data"]["invoice_id"], self.billing.invoice.invoice_id)

        response, body = self.request(
            "GET",
            "/v1/billing/entitlements",
        )
        self.assertEqual(response.status, 200)
        data = json.loads(body)["data"]["entitlements"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "active")

    def test_billing_options_is_not_wildcard_cors(self):
        response, body = self.request(
            "OPTIONS",
            "/v1/billing/invoices",
            token=None,
        )
        self.assertEqual(response.status, 204)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        self.assertIn(
            "Authorization",
            response.getheader("Access-Control-Allow-Headers") or "",
        )


if __name__ == "__main__":
    unittest.main()
