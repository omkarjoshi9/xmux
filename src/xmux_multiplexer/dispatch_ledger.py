"""Durable dispatch reservations owned by the multiplexer.

A reservation never authorizes retry after interruption. The control plane must
reconcile UNKNOWN/RESERVED with the provider before requesting another attempt.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from .contracts import AgentTask


class DispatchConflict(ValueError):
    pass


class DispatchInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    state: str
    remote_ref: str | None = None


class DispatchLedger:
    def __init__(self, database: str):
        if not database or database == ":memory:":
            raise ValueError("durable_database_required")
        self.database = database
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS dispatch_reservations (
                    tenant TEXT NOT NULL, principal TEXT NOT NULL, operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL, remote_ref TEXT,
                    PRIMARY KEY (tenant, principal, operation, idempotency_key)
                )
            """)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS dispatch_task_owner "
                       "ON dispatch_reservations (tenant, principal, task_id)")

    @staticmethod
    def fingerprint(task: AgentTask, agent_id: str) -> str:
        # This local dispatch fingerprint is not the shared task.create JCS profile.
        document = {"task_id": task.task_id, "agent_id": agent_id, "intent": task.intent,
                    "objective": task.objective, "required_capabilities": sorted(task.required_capabilities),
                    "payload": task.payload}
        body = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def reserve(self, task: AgentTask, agent_id: str) -> Reservation:
        fingerprint = self.fingerprint(task, agent_id)
        key = (task.tenant_id, task.principal_id, "task.dispatch", task.idempotency_key)
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint, state, remote_ref FROM dispatch_reservations "
                "WHERE tenant=? AND principal=? AND operation=? AND idempotency_key=?", key,
            ).fetchone()
            if row is not None:
                if row[0] != fingerprint:
                    raise DispatchConflict("idempotency_conflict")
                if row[1] == "accepted":
                    return Reservation("accepted", row[2])
                raise DispatchInProgress("remote_outcome_unknown")
            try:
                db.execute(
                    "INSERT INTO dispatch_reservations VALUES (?, ?, ?, ?, ?, ?, 'reserved', NULL)",
                    (*key, task.task_id, fingerprint),
                )
            except sqlite3.IntegrityError as exc:
                raise DispatchConflict("task_already_reserved") from exc
        return Reservation("reserved")

    def accepted(self, task: AgentTask, agent_id: str, remote_ref: str) -> None:
        if not remote_ref:
            raise ValueError("remote_ref_required")
        key = (task.tenant_id, task.principal_id, "task.dispatch", task.idempotency_key)
        fingerprint = self.fingerprint(task, agent_id)
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            cursor = db.execute(
                "UPDATE dispatch_reservations SET state='accepted', remote_ref=? "
                "WHERE tenant=? AND principal=? AND operation=? AND idempotency_key=? "
                "AND fingerprint=? AND state='reserved'",
                (remote_ref, *key, fingerprint),
            )
            if cursor.rowcount != 1:
                raise DispatchConflict("reservation_not_found")

    def get_receipt(self, tenant: str, principal: str, task_id: str) -> dict | None:
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute(
                "SELECT state, remote_ref FROM dispatch_reservations "
                "WHERE tenant=? AND principal=? AND task_id=?",
                (tenant, principal, task_id),
            ).fetchone()
        if row is None:
            return None
        if row[0] != "accepted":
            raise DispatchInProgress("remote_outcome_unknown")
        return json.loads(row[1])
