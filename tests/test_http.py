import io
import json
import unittest

from xmux_multiplexer.dispatch import Dispatcher
from xmux_multiplexer.http import create_app
from xmux_multiplexer.mock import MockAgent
from xmux_multiplexer.registry import AgentConfig, AgentRegistry


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.agent = MockAgent("a", frozenset({"research"}))
        config = AgentConfig("a", "https://example.org", frozenset({"research"}), frozenset({"alice"}))
        self.app = create_app(Dispatcher(AgentRegistry([config]), {"a": self.agent}), "secret")

    def request(self, path, data, token="secret"):
        body = json.dumps(data).encode()
        env = {"HTTP_AUTHORIZATION": "Bearer " + token, "CONTENT_LENGTH": str(len(body)),
               "wsgi.input": io.BytesIO(body), "PATH_INFO": path, "REQUEST_METHOD": "POST"}
        status = []
        result = b"".join(self.app(env, lambda code, headers: status.append(code)))
        return status[0], json.loads(result)

    def test_auth_and_scoped_discovery(self):
        self.assertTrue(self.request("/internal/agents/list", {"principal_id": "alice", "tenant_id": "t"}, "wrong")[0].startswith("401"))
        code, result = self.request("/internal/agents/list", {"principal_id": "bob", "tenant_id": "t"})
        self.assertEqual(result["agents"], [])
        self.assertTrue(code.startswith("200"))

    def test_dispatch_and_reject_coding(self):
        task = {"task_id": "t", "idempotency_key": "k", "intent": "research", "objective": "Research"}
        code, result = self.request("/internal/agents/dispatch", {"principal_id": "alice", "tenant_id": "t", "task": task})
        self.assertTrue(code.startswith("200"))
        self.assertEqual(result["agent_id"], "a")
        task["intent"] = "code_change"
        code, _ = self.request("/internal/agents/dispatch", {"principal_id": "alice", "tenant_id": "t", "task": task})
        self.assertTrue(code.startswith("403"))


if __name__ == "__main__":
    unittest.main()
