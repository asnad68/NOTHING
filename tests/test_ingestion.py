import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from src.nothing_api import build_server
from src.nothing_store import SQLiteNothingStore

ROOT = Path(__file__).resolve().parents[1]


class AuthenticatedWriteIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "nothing.db"
        self.store = SQLiteNothingStore(self.db_path)
        self.store.import_json_bundle(ROOT, actor="ingestion-test-import")
        self.server = build_server(
            "127.0.0.1",
            0,
            store=self.store,
            ingestion_token="test-ingestion-token",
            ingestion_actor="test-ingestor",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.store.close()
        self.tempdir.cleanup()

    def request(self, method, path, *, headers=None, body=b""):
        connection = http.client.HTTPConnection(
            self.host,
            self.port,
            timeout=5,
        )
        try:
            connection.request(
                method,
                path,
                body=body,
                headers=headers or {},
            )
            response = connection.getresponse()
            payload = response.read()
            return response, payload
        finally:
            connection.close()

    @staticmethod
    def identity_bundle():
        identity = {
            "nothing_id": "NTH-123456",
            "version": "0.1",
            "subject": {
                "name": "Ingestion Test Business",
                "type": "business",
                "website": "https://ingestion.example",
            },
            "claims": [
                {
                    "claim_id": "CLM-123456",
                    "statement": "The ingestion test identity is published at the declared website.",
                    "status": "SOURCE-VERIFIED",
                    "source": {
                        "type": "official_website",
                        "reference": "https://ingestion.example",
                        "checked_at": "2026-09-25T01:00:00Z",
                    },
                }
            ],
            "revocation": {"status": "NOT_REVOKED"},
        }
        evidence = {
            "evidence_id": "EVD-123456",
            "version": "0.1",
            "type": "web_page",
            "source": {
                "reference": "https://ingestion.example",
                "title": "Ingestion Test Source",
                "publisher": "Ingestion Test",
                "accessed_at": "2026-09-25T01:00:00Z",
            },
            "collected_at": "2026-09-25T01:00:00Z",
            "integrity": {"method": "none"},
        }
        event = {
            "event_id": "VER-123456",
            "version": "0.1",
            "occurred_at": "2026-09-25T01:00:00Z",
            "subject": "NTH-123456",
            "claim_id": "CLM-123456",
            "procedure": {
                "id": "NOTHING-BASIC-SOURCE-CHECK",
                "version": "0.1",
            },
            "verifier": {
                "type": "hybrid",
                "identifier": "ingestion-test",
            },
            "evidence": ["EVD-123456"],
            "result": {
                "status": "SOURCE-VERIFIED",
                "scope": "The declared website supports the test claim.",
                "reason": "Authenticated ingestion integration test.",
            },
        }
        return {
            "identities": [identity],
            "evidence": [evidence],
            "verification_events": [event],
        }

    def post_bundle(
        self,
        bundle,
        key="ingestion-key-001",
        *,
        token="test-ingestion-token",
        content_type="application/json",
    ):
        body = json.dumps(
            bundle,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Idempotency-Key": key,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self.request(
            "POST",
            "/v1/ingestion/bundles",
            headers=headers,
            body=body,
        )

    def test_authenticated_bundle_is_atomic_and_readable(self):
        response, body = self.post_bundle(self.identity_bundle())
        self.assertEqual(response.status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["data"]["accepted"])
        self.assertEqual(
            payload["data"]["identities"][0],
            {"id": "NTH-123456", "revision": 1, "written": True},
        )
        self.assertEqual(
            response.getheader("X-NOTHING-Ingestion-ID"),
            payload["data"]["ingestion_id"],
        )
        self.assertIsNone(response.getheader("Idempotent-Replay"))

        response, body = self.request("GET", "/v1/identity/NTH-123456")
        self.assertEqual(response.status, 200)
        identity_payload = json.loads(body)
        self.assertEqual(
            identity_payload["data"]["id"],
            "NTH-123456",
        )
        self.assertEqual(
            identity_payload["data"]["claims"][0]["current_verification"]["event_id"],
            "VER-123456",
        )

    def test_same_idempotency_key_replays_without_new_revision(self):
        bundle = self.identity_bundle()
        first_response, first_body = self.post_bundle(bundle)
        second_response, second_body = self.post_bundle(bundle)
        self.assertEqual(first_response.status, 200)
        self.assertEqual(second_response.status, 200)
        self.assertEqual(first_body, second_body)
        self.assertEqual(
            second_response.getheader("Idempotent-Replay"),
            "true",
        )
        self.assertEqual(
            self.store.get_identity("NTH-123456").revision,
            1,
        )

    def test_same_key_with_different_request_is_conflict(self):
        bundle = self.identity_bundle()
        self.post_bundle(bundle, key="same-key")
        changed = copy.deepcopy(bundle)
        changed["identities"][0]["subject"]["name"] = "Changed Name"
        response, body = self.post_bundle(changed, key="same-key")
        self.assertEqual(response.status, 409)
        payload = json.loads(body)
        self.assertEqual(payload["code"], "CONFLICT")
        self.assertEqual(
            self.store.get_identity("NTH-123456").revision,
            1,
        )

    def test_unauthenticated_request_gets_401(self):
        response, payload = self.post_bundle(
            self.identity_bundle(),
            key="unauthenticated-key",
            token=None,
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(
            response.getheader("WWW-Authenticate"),
            'Bearer realm="NOTHING ingestion", error="invalid_token"',
        )
        self.assertEqual(json.loads(payload)["code"], "UNAUTHORIZED")

    def test_invalid_token_gets_401(self):
        response, payload = self.post_bundle(
            self.identity_bundle(),
            key="invalid-token",
            token="wrong-token",
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(payload)["code"], "UNAUTHORIZED")

    def test_missing_idempotency_key_is_rejected(self):
        bundle = self.identity_bundle()
        body = json.dumps(bundle).encode("utf-8")
        response, payload = self.request(
            "POST",
            "/v1/ingestion/bundles",
            headers={
                "Authorization": "Bearer test-ingestion-token",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body=body,
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(
            json.loads(payload)["code"],
            "IDEMPOTENCY_KEY_REQUIRED",
        )

    def test_invalid_content_type_gets_415(self):
        response, payload = self.post_bundle(
            self.identity_bundle(),
            key="bad-content-type",
            content_type="text/plain",
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(
            json.loads(payload)["code"],
            "UNSUPPORTED_MEDIA_TYPE",
        )

    def test_invalid_bundle_rolls_back_everything(self):
        bundle = self.identity_bundle()
        bundle["verification_events"][0]["evidence"] = ["EVD-999999"]
        response, payload = self.post_bundle(bundle, key="atomic-failure")
        self.assertEqual(response.status, 422)
        self.assertEqual(
            json.loads(payload)["code"],
            "INVALID_INGESTION_BUNDLE",
        )
        response, _ = self.request("GET", "/v1/identity/NTH-123456")
        self.assertEqual(response.status, 404)
        with self.assertRaises(Exception):
            self.store.get_evidence("EVD-123456")
        with self.assertRaises(Exception):
            self.store.get_event("VER-123456")

    def test_immutable_existing_evidence_is_not_overwritten(self):
        bundle = self.identity_bundle()
        replacement = copy.deepcopy(bundle["evidence"][0])
        replacement["evidence_id"] = "EVD-000001"
        replacement["notes"] = "attempted replacement"
        bundle["evidence"][0] = replacement
        bundle["verification_events"][0]["evidence"] = ["EVD-000001"]
        response, payload = self.post_bundle(
            bundle,
            key="immutable-evidence",
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(
            json.loads(payload)["code"],
            "CONFLICT",
        )

    def test_duplicate_json_property_is_rejected(self):
        body = (
            b'{"identities":[],"identities":[],"evidence":[],'
            b'"verification_events":[{"event_id":"VER-123456"}]}'
        )
        response, payload = self.request(
            "POST",
            "/v1/ingestion/bundles",
            headers={
                "Authorization": "Bearer test-ingestion-token",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Idempotency-Key": "duplicate-json",
            },
            body=body,
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(
            json.loads(payload)["code"],
            "INVALID_INGESTION_REQUEST",
        )

    def test_ingestion_options_does_not_enable_wildcard_cors(self):
        response, body = self.request(
            "OPTIONS",
            "/v1/ingestion/bundles",
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.getheader("Access-Control-Allow-Origin"),
            None,
        )
        self.assertEqual(body, b"")

    def test_filesystem_backend_does_not_accept_writes(self):
        filesystem_server = build_server(
            "127.0.0.1",
            0,
            ingestion_token="test-ingestion-token",
        )
        thread = threading.Thread(
            target=filesystem_server.serve_forever,
            daemon=True,
        )
        thread.start()
        host, port = filesystem_server.server_address
        try:
            bundle = self.identity_bundle()
            body = json.dumps(bundle).encode("utf-8")
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request(
                    "POST",
                    "/v1/ingestion/bundles",
                    body=body,
                    headers={
                        "Authorization": "Bearer test-ingestion-token",
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                        "Idempotency-Key": "filesystem-write",
                    },
                )
                response = connection.getresponse()
                payload = response.read()
            finally:
                connection.close()
            self.assertEqual(response.status, 503)
            self.assertEqual(
                json.loads(payload)["code"],
                "WRITE_INGESTION_UNAVAILABLE",
            )
        finally:
            filesystem_server.shutdown()
            filesystem_server.server_close()
            thread.join(timeout=5)

    def test_public_identity_post_remains_rejected(self):
        response, body = self.request(
            "POST",
            "/v1/identity/NTH-000001",
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, OPTIONS")
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
