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

    def test_all_required_capabilities_must_match(self):
        agents = [
            Agent("research", frozenset({C.RESEARCH}), preference=10),
            Agent("both", frozenset({C.RESEARCH, C.SCHEDULING})),
        ]
        request = RoutingRequest(C.RESEARCH, additional_capabilities=frozenset({C.SCHEDULING}))
        self.assertEqual(select_agent(request, agents).agent.id, "both")
        with self.assertRaises(RoutingError):
            select_agent(RoutingRequest(C.RESEARCH, "research", frozenset({C.SCHEDULING})), agents)


if __name__ == "__main__":
    unittest.main()
