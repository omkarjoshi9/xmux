"""Durable AgentMail delivery correlation for the Instinct email transport."""

import hashlib
from contextlib import closing
import json
import sqlite3
from email.utils import parseaddr

from .agentmail import AgentMailClient


class DeliveryUncertain(RuntimeError):
    """The provider may have accepted the message; reconcile before retry."""


class AgentMailInstinct:
    def __init__(self, client: AgentMailClient, database: str, inbox_id: str, recipient: str):
        if not inbox_id or not recipient or "@" not in parseaddr(recipient)[1]:
            raise ValueError("inbox_and_recipient_required")
        self._client, self._database = client, database
        self._inbox, self._recipient = inbox_id, parseaddr(recipient)[1].lower()
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS instinct_delivery (
                correlation TEXT PRIMARY KEY, tenant TEXT NOT NULL, principal TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL, thread_id TEXT, message_id TEXT
            )""")

    def submit(self, *, tenant: str, principal: str, task_id: str, objective: str):
        if not all((tenant, principal, task_id, objective)):
            raise ValueError("missing_task_fields")
        correlation = hashlib.sha256(json.dumps([tenant, principal, task_id], separators=(",", ":")).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([self._inbox, self._recipient, objective], separators=(",", ":")).encode()).hexdigest()
        with closing(sqlite3.connect(self._database)) as db, db:
            cursor = db.execute("INSERT OR IGNORE INTO instinct_delivery VALUES (?, ?, ?, ?, 'sending', NULL, NULL)",
                                (correlation, tenant, principal, fingerprint))
            if cursor.rowcount == 0:
                row = db.execute("SELECT fingerprint, state, thread_id FROM instinct_delivery WHERE correlation = ?", (correlation,)).fetchone()
                if row[0] != fingerprint:
                    raise ValueError("idempotency_conflict")
                if row[1] != "accepted":
                    raise DeliveryUncertain("delivery_requires_reconciliation")
                return row[2]
        try:
            response = self._client.send(self._inbox, self._recipient, f"XMUX task {task_id}", objective, correlation)
            thread_id, message_id = response["thread_id"], response["message_id"]
            if not isinstance(thread_id, str) or not thread_id or not isinstance(message_id, str) or not message_id:
                raise ValueError("invalid_delivery_receipt")
        except Exception as exc:
            raise DeliveryUncertain("delivery_requires_reconciliation") from exc
        with closing(sqlite3.connect(self._database)) as db, db:
            db.execute("UPDATE instinct_delivery SET state='accepted', thread_id=?, message_id=? WHERE correlation=?",
                       (thread_id, message_id, correlation))
        return thread_id

    def result(self, *, tenant: str, principal: str, task_id: str):
        correlation = hashlib.sha256(json.dumps([tenant, principal, task_id], separators=(",", ":")).encode()).hexdigest()
        with closing(sqlite3.connect(self._database)) as db, db:
            row = db.execute("SELECT state, thread_id, message_id FROM instinct_delivery WHERE correlation = ? AND tenant = ? AND principal = ?",
                             (correlation, tenant, principal)).fetchone()
        if row is None:
            raise LookupError("unknown_task")
        if row[0] != "accepted":
            raise DeliveryUncertain("delivery_requires_reconciliation")
        thread = self._client.get_thread(self._inbox, row[1])
        for message in thread.get("messages", []):
            sender = parseaddr(message.get("from", ""))[1].lower()
            auth = message.get("authentication_results") or {}
            if (message.get("message_id") != row[2] and sender == self._recipient
                    and auth.get("dmarc") == "pass" and message.get("message_id")):
                return {"thread_id": row[1], "message_id": message["message_id"],
                        "text": message.get("extracted_text") or message.get("text", "")}
        return None
