import unittest

from xmux_multiplexer.registry import AgentConfig, AgentRegistry


class RegistryTests(unittest.TestCase):
    def config(self, **kwargs):
        values = dict(agent_id="research", endpoint="https://agent.example/api",
                      operations=frozenset({"research", "summarize"}),
                      principals=frozenset({"alice"}), credential_ref="vault:agents/research")
        values.update(kwargs)
        return AgentConfig(**values)

    def test_principal_and_capability_filtering(self):
        registry = AgentRegistry([self.config()])
        self.assertEqual(len(registry.discover_configs("alice", frozenset({"research"}))), 1)
        self.assertEqual(registry.discover_configs("bob"), ())
        self.assertEqual(registry.discover_configs("alice", frozenset({"admin"})), ())

    def test_rejects_inline_secrets_and_invalid_endpoint(self):
        with self.assertRaises(ValueError):
            self.config(endpoint="https://user:password@agent.example")
        with self.assertRaises(ValueError):
            self.config(credential_ref="plain-secret")

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate_agent_id"):
            AgentRegistry([self.config(), self.config()])
