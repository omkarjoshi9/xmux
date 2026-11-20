import unittest
from agent_routing import Agent, Capability as C, RoutingError, RoutingRequest, select_agent


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.agents = [Agent("z", frozenset({C.RESEARCH}), preference=3),
                       Agent("b", frozenset({C.RESEARCH}), preference=3),
                       Agent("coding", frozenset({C.CODING}), authorized=False),
                       Agent("schedule", frozenset({C.SCHEDULING}), healthy=False)]

    def test_preference_then_id_is_deterministic(self):
        self.assertEqual(select_agent(RoutingRequest(C.RESEARCH), reversed(self.agents)).agent.id, "b")

    def test_explicit_authorized_capable_healthy_target(self):
        self.assertEqual(select_agent(RoutingRequest(C.RESEARCH, "z"), self.agents).agent.id, "z")

    def test_invalid_explicit_target_never_falls_back(self):
        for target in ("missing", "coding", "schedule"):
            with self.subTest(target=target), self.assertRaises(RoutingError):
                select_agent(RoutingRequest(C.RESEARCH, target), self.agents)

    def test_no_automatic_candidate(self):
        with self.assertRaises(RoutingError):
            select_agent(RoutingRequest(C.CODING), self.agents)


if __name__ == "__main__":
    unittest.main()
