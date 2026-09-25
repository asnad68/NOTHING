import http.client
import json
import threading
import unittest

from src.nothing_api import build_server


class ReferenceApiHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = build_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        method: str = "GET",
    ):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response, body
        finally:
            connection.close()

    def test_identity_endpoint_returns_resolved_graph(self) -> None:
        response, body = self.request("/v1/identity/NTH-000001")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(response.getheader("X-NOTHING-Protocol-Version"), "0.1")
        self.assertTrue(response.getheader("ETag"))
        self.assertTrue(response.getheader("Last-Modified"))

        payload = json.loads(body)
        self.assertEqual(payload["data"]["id"], "NTH-000001")
        claim = payload["data"]["claims"][0]
        self.assertEqual(claim["id"], "CLM-000001")
        self.assertEqual(claim["current_verification"]["event_id"], "VER-000001")
        self.assertEqual(
            claim["current_verification"]["procedure"]["id"],
            "NOTHING-BASIC-SOURCE-CHECK",
        )

    def test_identity_invalid_id_returns_problem_json(self) -> None:
        response, body = self.request("/v1/identity/not-an-id")
        self.assertEqual(response.status, 400)
        self.assertEqual(response.getheader("Content-Type"), "application/problem+json; charset=utf-8")
        payload = json.loads(body)
        self.assertEqual(payload["code"], "INVALID_ID")
        self.assertEqual(payload["status"], 400)

    def test_identity_missing_record_returns_404(self) -> None:
        response, body = self.request("/v1/identity/NTH-999999")
        self.assertEqual(response.status, 404)
        payload = json.loads(body)
        self.assertEqual(payload["code"], "NOT_FOUND")
        self.assertEqual(payload["status"], 404)

    def test_evidence_event_and_procedure_endpoints(self) -> None:
        paths = (
            "/v1/evidence/EVD-000001",
            "/v1/verification-events/VER-000001",
            "/v1/procedures/NOTHING-BASIC-SOURCE-CHECK/0.1",
        )
        for path in paths:
            with self.subTest(path=path):
                response, body = self.request(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
                self.assertEqual(json.loads(body)["meta"]["protocol_version"], "0.1")

    def test_etag_conditional_request_returns_304(self) -> None:
        first_response, first_body = self.request("/v1/identity/NTH-000001")
        self.assertEqual(first_response.status, 200)
        etag = first_response.getheader("ETag")
        self.assertTrue(etag)

        second_response, second_body = self.request(
            "/v1/identity/NTH-000001",
            headers={"If-None-Match": etag},
        )
        self.assertEqual(second_response.status, 304)
        self.assertEqual(second_response.getheader("ETag"), etag)
        self.assertEqual(second_body, b"")

    def test_write_methods_are_rejected(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response, body = self.request("/v1/identity/NTH-000001", method=method)
                self.assertEqual(response.status, 405)
                self.assertEqual(response.getheader("Allow"), "GET, OPTIONS")
                self.assertEqual(body, b"")

    def test_options_returns_cors_preflight(self) -> None:
        response, body = self.request(
            "/v1/identity/NTH-000001",
            method="OPTIONS",
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(response.getheader("Access-Control-Allow-Methods"), "GET, OPTIONS")
        self.assertEqual(body, b"")

    def test_unknown_route_returns_404_problem(self) -> None:
        response, body = self.request("/v1/unknown")
        self.assertEqual(response.status, 404)
        payload = json.loads(body)
        self.assertEqual(payload["code"], "NOT_FOUND")

    def test_health_and_readiness_endpoints(self) -> None:
        response, body = self.request("/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["status"], "ok")

        response, body = self.request("/readyz")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["status"], "ready")


    def test_proxy_header_is_not_trusted_by_default(self) -> None:
        import src.nothing_api as api_module
        from email.message import Message
        from src.nothing_api import NothingApiHandler

        handler = object.__new__(NothingApiHandler)
        handler.headers = Message()
        handler.headers["X-Forwarded-For"] = "203.0.113.99"
        handler.client_address = ("198.51.100.7", 12345)

        old = api_module.TRUST_PROXY_HEADERS
        api_module.TRUST_PROXY_HEADERS = False
        try:
            self.assertEqual(handler._client_key(), "198.51.100.7")
            api_module.TRUST_PROXY_HEADERS = True
            self.assertEqual(handler._client_key(), "203.0.113.99")
        finally:
            api_module.TRUST_PROXY_HEADERS = old

if __name__ == "__main__":
    unittest.main()

