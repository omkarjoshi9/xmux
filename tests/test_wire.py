import unittest
from datetime import datetime, timezone

from xmux_multiplexer.contracts import AgentState, RemoteTaskReceipt
from xmux_multiplexer.wire import receipt_to_wire, task_from_wire


class WireTests(unittest.TestCase):
    def test_task_uses_trusted_identity(self):
        task = task_from_wire(
            {"schema_version": "1", "idempotency_key": "key", "intent": "research",
             "objective": "Investigate", "context_refs": ["ref"], "principal_id": "attacker"},
            task_id="task", principal_id="alice", tenant_id="team",
        )
        self.assertEqual(task.principal_id, "alice")
        self.assertNotIn("principal_id", task.payload)
        self.assertEqual(task.payload["context_refs"], ["ref"])

    def test_receipt_maps_shared_fields(self):
        receipt = RemoteTaskReceipt("agent", "remote", AgentState.RUNNING)
        result = receipt_to_wire(
            receipt, task_id="fa3946c9-137f-4bd9-bd60-03d88dc852d0",
            correlation_id="37e5a2ab-02c4-49f9-b3f3-7b31e108cbbb",
            accepted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result["schema_version"], "1")
        self.assertEqual(result["accepted_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(result["remote_task_id"], "remote")

    def test_naive_timestamp_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            receipt_to_wire(
                RemoteTaskReceipt("a", "r", AgentState.RUNNING),
                task_id="fa3946c9-137f-4bd9-bd60-03d88dc852d0",
                correlation_id="37e5a2ab-02c4-49f9-b3f3-7b31e108cbbb",
                accepted_at=datetime(2026, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
