import sqlite3
import tempfile
import unittest
from pathlib import Path


class PaymentMigrationTests(unittest.TestCase):
    def test_payment_and_entitlement_migrations_apply(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            with sqlite3.connect(db) as c:
                c.executescript(
                    (root / "storage/migrations/002_payment_core.sql").read_text()
                )
                c.executescript(
                    (root / "storage/migrations/003_entitlement_core.sql").read_text()
                )
                tables = {
                    row[0]
                    for row in c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {"invoices", "payments", "payment_events", "entitlements"} <= tables
                )


if __name__ == "__main__":
    unittest.main()
