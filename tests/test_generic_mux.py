import io
import json
import unittest

from xmux_multiplexer.contracts import AgentState, AgentTask
from xmux_multiplexer.generic_mux import HttpMuxAgent, HttpMuxConfig


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self):
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return Response(json.dumps({"id": "remote", "state": "done", "refs": ["https://example.org/pr/1"]}).encode())


class GenericMuxTests(unittest.TestCase):
    def setUp(self):
        self.config = HttpMuxConfig("mux", "https://mux.example", "/jobs", "/jobs/{remote_id}",
                                    "id", "state", {"done": AgentState.COMPLETED}, "refs", "env:MUX_TOKEN")
        self.opener = Opener()
        self.agent = HttpMuxAgent(self.config, lambda ref: "test-token", lambda task: True, self.opener)
        self.task = AgentTask("task", "key", "alice", "team", "code_change", "Implement",
                              frozenset({"coding"}), {"repository": "repo", "branch": "feature"})

    def test_submit_and_retrieve(self):
        receipt = self.agent.submit(self.task)
        self.assertEqual(receipt.remote_task_id, "remote")
        self.assertEqual(self.agent.result("remote").result_refs, ("https://example.org/pr/1",))
        self.assertEqual(self.agent.status("remote"), AgentState.COMPLETED)
        self.assertEqual(self.opener.requests[0][0].get_method(), "POST")

    def test_approval_required_before_network(self):
        agent = HttpMuxAgent(self.config, lambda ref: "token", lambda task: False, self.opener)
        with self.assertRaises(PermissionError):
            agent.submit(self.task)
        self.assertEqual(self.opener.requests, [])

    def test_invalid_endpoints_rejected(self):
        with self.assertRaises(ValueError):
            HttpMuxConfig("mux", "https://user:secret@example.org", "/jobs", "/jobs/{remote_id}",
                          "id", "state", {}, "refs", "env:TOKEN")


if __name__ == "__main__":
    unittest.main()
