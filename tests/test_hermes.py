import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

from xmux_multiplexer.contracts import AgentTask, AgentState
from xmux_multiplexer.hermes import HermesAgent


class HermesTests(unittest.TestCase):
    def test_uses_query_file_stdin_and_returns_completed(self):
        agent = HermesAgent()
        task = AgentTask("t1", "key", "u", "tenant", "research", "Look into it")
        with patch("xmux_multiplexer.hermes.subprocess.run", return_value=CompletedProcess([], 0, "answer", "")) as run:
            receipt = agent.submit(task)
        self.assertEqual(receipt.state, AgentState.COMPLETED)
        self.assertTrue(receipt.remote_task_id.startswith("local:"))
        self.assertEqual(agent.result(receipt.remote_task_id).output, "answer")
        self.assertEqual(run.call_args.args[0], ["hermes", "chat", "--query-file", "-"])
        self.assertEqual(run.call_args.kwargs["input"], "Look into it\n\nresearch")

    def test_rejects_unsupported_capability_before_launch(self):
        task = AgentTask("t1", "key", "u", "tenant", "coding", "Build", frozenset({"coding"}))
        with patch("xmux_multiplexer.hermes.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "unsupported_capability"):
                HermesAgent().submit(task)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
