import unittest

from xmux_multiplexer.contracts import AgentState, AgentTask
from xmux_multiplexer.paperclip import PaperclipAdapter


class FakeClient:
    def __init__(self):
        self.calls = []
    def post(self, path, body):
        self.calls.append(("POST", path, body))
        return {"id": "issue-1", "status": "todo"}
    def get(self, path):
        self.calls.append(("GET", path))
        return {"id": "issue-1", "status": "done"}


class PaperclipTests(unittest.TestCase):
    def test_submit_status_and_unsupported_result(self):
        client = FakeClient()
        adapter = PaperclipAdapter(client, "company", "agent")
        task = AgentTask("task", "key", "alice", "tenant", "delegate", "Investigate")
        receipt = adapter.submit(task)
        self.assertEqual(receipt.remote_task_id, "issue-1")
        self.assertEqual(receipt.state, AgentState.RUNNING)
        self.assertEqual(client.calls[0][1], "/api/companies/company/issues")
        self.assertEqual(adapter.status("issue-1"), AgentState.COMPLETED)
        with self.assertRaisesRegex(NotImplementedError, "paperclip_result_unsupported"):
            adapter.result("issue-1")

    def test_unsupported_capability_rejected(self):
        client = FakeClient()
        adapter = PaperclipAdapter(client, "company", "agent")
        task = AgentTask("task", "key", "alice", "tenant", "delegate", "Investigate",
                         frozenset({"schedule"}))
        with self.assertRaises(ValueError):
            adapter.submit(task)
        self.assertFalse(client.calls)


if __name__ == "__main__":
    unittest.main()
