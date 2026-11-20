# agent-routing

Small, dependency-free Python package for deterministic agent selection. It does not dispatch work or retain state; the XMUX integration layer supplies current agent records and performs dispatch after selection.

```python
from agent_routing import Agent, Capability, RoutingRequest, select_agent

agents = [Agent("hermes", frozenset({Capability.RESEARCH}), preference=10)]
decision = select_agent(RoutingRequest(Capability.RESEARCH), agents)
# decision.agent is the selected typed Agent record; decision.explicit identifies explicit routing.
```

`Capability` currently maps `research`, `coding`, and `scheduling`. Integrations should map their supported operations to these values (and extend the enum deliberately for new task classes). An `Agent` requires a stable unique `id`, supported capabilities, authorization and health state, and an integer preference (higher wins). `RoutingRequest.target_id` optionally requests a specific agent. Explicit targets are checked for uniqueness, authorization, capability, and health; invalid explicit targets raise `RoutingError` and never fall back. Automatic routing considers only eligible agents, then sorts by descending preference and ascending stable ID. No eligible agent also raises `RoutingError`.

This API is pure and typed: integrations can adapt their own registry records into `Agent` and `RoutingRequest`, call `select_agent`, then use the returned agent ID for dispatch and audit. Python 3.9+; no runtime dependencies.

Use `RoutingRequest.additional_capabilities` to require multiple capabilities. Every required capability must be present.
