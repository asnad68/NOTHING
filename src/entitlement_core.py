"""Durable, idempotent subscription entitlement projection."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any


class EntitlementCore:
    def __init__(self, db_path: str, period_days: int = 30) -> None:
        if period_days <= 0:
            raise ValueError("period_days must be positive")
        self.db_path = db_path
        self.period_days = period_days
        with self._connect() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS entitlements (
                    entitlement_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    source_payment_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA busy_timeout = 5000")
        c.execute("PRAGMA synchronous = FULL")
        return c

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)

    def grant(self, payment_id: str, customer_id: str, plan_id: str) -> str:
        now = self._now()
        expires = now + timedelta(days=self.period_days)
        iso_now = now.isoformat().replace("+00:00", "Z")
        iso_expires = expires.isoformat().replace("+00:00", "Z")

        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                existing = c.execute(
                    "SELECT entitlement_id FROM entitlements WHERE source_payment_id = ?",
                    (payment_id,),
                ).fetchone()
                if existing:
                    c.execute("COMMIT")
                    return existing["entitlement_id"]

                entitlement_id = "ENT-" + uuid.uuid4().hex[:20].upper()
                c.execute(
                    """INSERT INTO entitlements(
                        entitlement_id, customer_id, plan_id, source_payment_id,
                        status, starts_at, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)""",
                    (
                        entitlement_id, customer_id, plan_id, payment_id,
                        iso_now, iso_expires, iso_now,
                    ),
                )
                c.execute("COMMIT")
                return entitlement_id
            except Exception:
                c.execute("ROLLBACK")
                raise

    def get_by_payment(self, payment_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM entitlements WHERE source_payment_id = ?",
                (payment_id,),
            ).fetchone()
            return dict(row) if row else None
