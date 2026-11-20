"""Durable per-principal Instinct email mappings."""

import sqlite3
from contextlib import closing
from email.utils import parseaddr
from typing import Callable


class InstinctRecipientStore:
    def __init__(self, database: str, authorize_change: Callable[[str, str, str, str], bool]):
        if not database or database == ":memory:":
            raise ValueError("durable_database_required")
        self.database = database
        self._authorize_change = authorize_change
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS instinct_recipients (
                    tenant TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    PRIMARY KEY (tenant, principal)
                )
            """)

    def set(self, tenant: str, principal: str, recipient: str, proof: str) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (tenant, principal, recipient, proof)):
            raise ValueError("identity_mapping_invalid")
        address = parseaddr(recipient)[1].lower()
        if not address or "@" not in address or address != recipient.strip().lower():
            raise ValueError("instinct_recipient_invalid")
        if not self._authorize_change(tenant, principal, address, proof):
            raise PermissionError("identity_mapping_forbidden")
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute(
                "INSERT INTO instinct_recipients (tenant, principal, recipient) VALUES (?, ?, ?) "
                "ON CONFLICT (tenant, principal) DO UPDATE SET recipient=excluded.recipient",
                (tenant, principal, address),
            )

    def __call__(self, tenant: str, principal: str) -> str:
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            row = db.execute("SELECT recipient FROM instinct_recipients WHERE tenant=? AND principal=?",
                             (tenant, principal)).fetchone()
        if row is None:
            raise LookupError("instinct_recipient_not_configured")
        return row[0]
