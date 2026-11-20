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


    def test_end_to_end_status_results_and_duplicate(self):
        refs = {}
        self.app = create_app(self.app_dispatcher(), "secret", references=refs)
        task = {"task_id": "one", "idempotency_key": "key", "intent": "research", "objective": "Research"}
        body = {"principal_id": "alice", "tenant_id": "t", "task": task}
        self.assertTrue(self.request("/internal/agents/dispatch", body)[0].startswith("200"))
        self.assertTrue(self.request("/internal/agents/dispatch", body)[0].startswith("409"))
        lookup = {"principal_id": "alice", "tenant_id": "t", "task_id": "one"}
        self.assertEqual(self.request("/internal/agents/status", lookup)[1]["state"], "completed")
        result = self.request("/internal/agents/results", lookup)[1]["result"]
        self.assertEqual(result["result_refs"][0][:7], "mock://")
        lookup["tenant_id"] = "other"
        self.assertTrue(self.request("/internal/agents/status", lookup)[0].startswith("403"))

    def app_dispatcher(self):
        config = AgentConfig("a", "https://example.org", frozenset({"research"}), frozenset({"alice"}))
        return Dispatcher(AgentRegistry([config]), {"a": self.agent})


if __name__ == "__main__":
    unittest.main()
