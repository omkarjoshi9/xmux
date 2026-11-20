import unittest

from xmux_multiplexer.contracts import AgentTask
from xmux_multiplexer.t3 import T3Bridge


class Client:
    def __init__(self): self.calls = []
    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {"threadId": "thread-1"}


class T3Tests(unittest.TestCase):
    def test_approved_request_uses_explicit_worktree_and_approval(self):
        client = Client()
        bridge = T3Bridge(client, "project", "codex", "model", lambda task, project, branch: True)
        task = AgentTask("task", "idempotent", "user", "tenant", "coding", "Implement", frozenset({"coding"}))
        self.assertEqual(bridge.submit(task, base_branch="main", branch="feat/test").response["threadId"], "thread-1")
        args = client.calls[0][1]
        self.assertEqual(args["runtimeMode"], "approval-required")
        self.assertEqual(args["newWorktree"]["branch"], "feat/test")
        self.assertFalse(args["newWorktree"]["runSetupScript"])

    def test_denied_scope_does_not_call_mcp(self):
        client = Client()
        bridge = T3Bridge(client, "project", "codex", "model", lambda task, project, branch: False)
        task = AgentTask("task", "key", "user", "tenant", "coding", "Implement")
        with self.assertRaises(PermissionError):
            bridge.submit(task, base_branch="main", branch="feat/test")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
