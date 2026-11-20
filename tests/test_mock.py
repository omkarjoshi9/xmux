import unittest

from xmux_multiplexer.contracts import AgentState, AgentTask
from xmux_multiplexer.mock import MockAgent


class MockAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = MockAgent("personal", frozenset({"research"}))
        self.task = AgentTask("task", "key", "user", "tenant", "research", "Investigate")

    def test_submit_and_result(self):
        receipt = self.agent.submit(self.task)
        self.assertEqual(self.agent.status(receipt.remote_task_id), AgentState.COMPLETED)
        self.assertEqual(self.agent.result(receipt.remote_task_id).result_refs,
                         (f"mock://{receipt.remote_task_id}",))

    def test_repeat_submission_returns_same_receipt(self):
        self.assertEqual(self.agent.submit(self.task), self.agent.submit(self.task))

    def test_unknown_reference(self):
        with self.assertRaises(KeyError):
            self.agent.result("unknown")

    def test_missing_capability(self):
        task = AgentTask("task", "key", "user", "tenant", "coding", "Build",
                         frozenset({"coding"}))
        with self.assertRaisesRegex(ValueError, "unsupported_capability"):
            self.agent.submit(task)


if __name__ == "__main__":
    unittest.main()
