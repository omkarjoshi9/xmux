import unittest

from xmux_multiplexer.contracts import AgentTask
from xmux_multiplexer.dispatch import ApprovedCodingScope, DispatchContext, Dispatcher, UnknownDispatch
from xmux_multiplexer.mock import MockAgent
from xmux_multiplexer.registry import AgentConfig, AgentRegistry


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.agent = MockAgent("agent", frozenset({"coding", "research"}))
        config = AgentConfig("agent", "https://agent.example", frozenset({"coding", "research"}),
                             frozenset({"alice"}))
        self.dispatcher = Dispatcher(AgentRegistry([config]), {"agent": self.agent})
        self.context = DispatchContext("alice", "team")

    def task(self, **values):
        args = dict(task_id="task", idempotency_key="key", principal_id="alice", tenant_id="team",
                    intent="research", objective="Investigate", required_capabilities=frozenset({"research"}))
        args.update(values)
        return AgentTask(**args)

    def test_authorized_research(self):
        self.assertEqual(self.dispatcher.submit(self.task(), self.context).agent_id, "agent")

    def test_identity_and_coding_scope_rejected(self):
        with self.assertRaises(PermissionError):
            self.dispatcher.submit(self.task(principal_id="bob"), self.context)
        coding = self.task(intent="code_change", required_capabilities=frozenset({"coding"}),
                           payload={"repository": "repo", "branch": "feature", "allowed_tools": ["write"]})
        with self.assertRaises(PermissionError):
            self.dispatcher.submit(coding, self.context)
        approved = DispatchContext("alice", "team", ApprovedCodingScope("repo", "feature", frozenset({"write"})))
        self.assertEqual(self.dispatcher.submit(coding, approved).agent_id, "agent")

    def test_timeout_does_not_trigger_second_submission(self):
        calls = []
        def timeout(task):
            calls.append(task)
            raise TimeoutError()
        self.agent.submit = timeout
        with self.assertRaises(UnknownDispatch):
            self.dispatcher.submit(self.task(), self.context)
        self.assertEqual(len(calls), 1)

    def test_explicit_ineligible_target_rejected(self):
        with self.assertRaises(LookupError):
            self.dispatcher.submit(self.task(), self.context, "other")


if __name__ == "__main__":
    unittest.main()
