import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from src.nothing_api import build_server
from src.nothing_store import SQLiteNothingStore

ROOT = Path(__file__).resolve().parents[1]


class PersistentApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "nothing.db"
        self.store = SQLiteNothingStore(self.db_path)
        self.store.import_json_bundle(ROOT, actor="persistent-api-test")
        self.server = build_server("127.0.0.1", 0, store=self.store)
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

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response, body
        finally:
            connection.close()

    def test_api_reads_from_sqlite_and_survives_restart(self):
        response, body = self.request("/v1/identity/NTH-000001")
        self.assertEqual(response.status, 200)
        first_payload = json.loads(body)
        first_etag = response.getheader("ETag")
        first_last_modified = response.getheader("Last-Modified")
        self.assertFalse(first_payload["meta"]["demo"])
        self.assertTrue(first_etag)
        self.assertTrue(first_last_modified)

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.store.close()

        reopened_store = SQLiteNothingStore(self.db_path)
        reopened_server = build_server(
            "127.0.0.1",
            0,
            store=reopened_store,
        )
        reopened_thread = threading.Thread(
            target=reopened_server.serve_forever,
            daemon=True,
        )
        reopened_thread.start()
        try:
            self.host, self.port = reopened_server.server_address
            response, body = self.request("/v1/identity/NTH-000001")
            self.assertEqual(response.status, 200)
            second_payload = json.loads(body)
            self.assertEqual(second_payload["data"], first_payload["data"])
            self.assertEqual(response.getheader("ETag"), first_etag)
            self.assertEqual(
                response.getheader("Last-Modified"),
                first_last_modified,
            )
        finally:
            reopened_server.shutdown()
            reopened_server.server_close()
            reopened_thread.join(timeout=5)
            reopened_store.close()


if __name__ == "__main__":
    unittest.main()
